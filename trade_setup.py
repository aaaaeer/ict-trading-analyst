import json
import re
from datetime import datetime, timezone
import anthropic


_SESSIONS = [
    ("Asian session",           0,  0,  5,  0),   # 00:00–05:00 UTC
    ("London Open Kill Zone",   7,  0, 10,  0),   # 07:00–10:00 UTC
    ("New York Open Kill Zone", 12, 0, 15,  0),   # 12:00–15:00 UTC
    ("London Close",            15, 0, 16,  0),   # 15:00–16:00 UTC
    ("NY Close / Dead Zone",    16, 0, 23, 59),   # 16:00–midnight UTC
]

# Bulgaria is UTC+3 in summer (EEST), UTC+2 in winter (EET)
_BULGARIA_OFFSET = 3  # EEST (May–Oct)


def _session_context() -> str:
    """Return current UTC + Bulgaria time, and which killzones are active / upcoming."""
    now_utc = datetime.now(timezone.utc)
    h_utc = now_utc.hour + now_utc.minute / 60  # decimal hours UTC

    bg_hour = (now_utc.hour + _BULGARIA_OFFSET) % 24
    bg_min  = now_utc.minute
    bg_time = f"{bg_hour:02d}:{bg_min:02d}"

    active, upcoming, passed = [], [], []
    for name, sh, sm, eh, em in _SESSIONS:
        start = sh + sm / 60
        end   = eh + em / 60
        if start <= h_utc < end:
            active.append((name, sh, sm, eh, em))
        elif h_utc < start:
            upcoming.append((name, sh, sm, eh, em))
        else:
            passed.append((name, sh, sm, eh, em))

    def fmt(name, sh, sm, eh, em):
        bg_s = (sh + _BULGARIA_OFFSET) % 24
        bg_e = (eh + _BULGARIA_OFFSET) % 24
        return (f"{name} "
                f"[UTC {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} | "
                f"Bulgaria {bg_s:02d}:{sm:02d}–{bg_e:02d}:{em:02d}]")

    lines = [
        f"Current time: {now_utc.strftime('%H:%M')} UTC  /  {bg_time} Bulgaria (UTC+3)",
        f"Day: {now_utc.strftime('%A')}",
    ]
    if active:
        lines.append(">> CURRENTLY ACTIVE: " + ", ".join(fmt(*s) for s in active))
    if upcoming:
        lines.append("Still upcoming today: " + ", ".join(fmt(*s) for s in upcoming))
    if passed:
        lines.append("Already finished today: " + ", ".join(fmt(*s) for s in passed))
    lines.append("")
    lines.append("RULES FOR trade_time field:")
    lines.append("- If a session is CURRENTLY ACTIVE, say 'NOW — <session name> is open until HH:MM Bulgaria'")
    lines.append("- If a session is upcoming, give the Bulgaria start time")
    lines.append("- NEVER mention a session that is already finished today")
    lines.append("- If all killzones for today are done, say 'No killzone remaining today — wait for Asian session tomorrow'")
    return "\n".join(lines)


def _build_confluence_zones(chart_data: dict, current_price: float | None) -> list[dict]:
    """
    Group all ICT elements by price proximity and score each zone.
    Returns a list of zones sorted by signal count (strongest first).
    """
    if not current_price:
        return []

    tolerance = current_price * 0.0015  # 0.15% tolerance to group nearby levels

    raw_signals: list[dict] = []

    def add(direction: str, label: str, top: float, bottom: float):
        raw_signals.append({
            "direction": direction,
            "label": label,
            "top": top,
            "bottom": bottom,
            "mid": (top + bottom) / 2,
        })

    for item in (chart_data.get("fvgs") or []):
        if isinstance(item, dict):
            add(item.get("direction", ""), "FVG (red box)", item.get("top", 0), item.get("bottom", 0))

    for item in (chart_data.get("large_fvgs") or []):
        if isinstance(item, dict):
            add(item.get("direction", ""), "Large FVG", item.get("top", 0), item.get("bottom", 0))

    for item in (chart_data.get("order_blocks") or []):
        if isinstance(item, dict):
            add(item.get("direction", ""), "OB", item.get("top", 0), item.get("bottom", 0))

    # Cluster signals into zones
    zones: list[dict] = []
    used = [False] * len(raw_signals)

    for i, sig in enumerate(raw_signals):
        if used[i] or not sig["top"] or not sig["bottom"]:
            continue
        cluster = [sig]
        used[i] = True
        for j, other in enumerate(raw_signals):
            if used[j] or not other["mid"]:
                continue
            if abs(other["mid"] - sig["mid"]) <= tolerance * 2:
                cluster.append(other)
                used[j] = True

        tops    = [s["top"]    for s in cluster if s["top"]]
        bottoms = [s["bottom"] for s in cluster if s["bottom"]]
        zone_top = max(tops)
        zone_bot = min(bottoms)
        zone_mid = (zone_top + zone_bot) / 2

        bearish_count = sum(1 for s in cluster if "bearish" in s["direction"].lower())
        bullish_count = sum(1 for s in cluster if "bullish" in s["direction"].lower())
        direction = "bearish" if bearish_count >= bullish_count else "bullish"
        signals   = [s["label"] for s in cluster]
        strength  = len(cluster)

        # Position relative to price
        if current_price > zone_top:
            position = "below_price"      # zone is below current price
        elif current_price < zone_bot:
            position = "above_price"      # zone is above current price
        else:
            position = "at_price"         # price is inside the zone

        zones.append({
            "top":         round(zone_top, 5),
            "bottom":      round(zone_bot, 5),
            "mid":         round(zone_mid, 5),
            "direction":   direction,
            "signals":     signals,
            "strength":    strength,
            "position":    position,
            "stars":       "★" * min(strength, 5),
        })

    zones.sort(key=lambda z: z["strength"], reverse=True)
    return zones


def generate_trade_setup(
    chart_data: dict,
    market_data: dict,
    bias: dict,
) -> dict:
    direction = bias.get("direction", "NEUTRAL")
    current   = market_data.get("current_price")

    confluence_zones = _build_confluence_zones(chart_data, current)

    if direction == "NEUTRAL":
        return {
            "direction":       "NEUTRAL",
            "action":          "NO_TRADE",
            "entry":           None,
            "sl":              None,
            "tp1":             None,
            "tp2":             None,
            "rr1":             None,
            "rr2":             None,
            "confluence_zones": confluence_zones,
            "notes":           "Bias is neutral — no trade. Wait for a clearer directional signal.",
        }

    context = {
        "asset":            market_data.get("ticker"),
        "direction":        direction,
        "bias_score":       bias.get("score"),
        "current_price":    current,
        "pdh":              market_data.get("pdh"),
        "pdl":              market_data.get("pdl"),
        "pdc":              market_data.get("pdc"),
        "asian_high":       market_data.get("asian_high"),
        "asian_low":        market_data.get("asian_low"),
        "london_high":      market_data.get("london_high"),
        "london_low":       market_data.get("london_low"),
        "structure":        chart_data.get("structure"),
        "trend":            chart_data.get("trend"),
        "htf_bias":         chart_data.get("htf_bias"),
        "ltf_bias":         chart_data.get("ltf_bias"),
        "fvgs":             chart_data.get("fvgs", []),
        "large_fvgs":       chart_data.get("large_fvgs", []),
        "order_blocks":     chart_data.get("order_blocks", []),
        "liquidity":        chart_data.get("liquidity"),
        "confluence_notes": chart_data.get("confluence_notes", ""),
        "confluence_zones": confluence_zones,
        "per_chart":        chart_data.get("charts", []),
    }

    prompt = f"""You are a precise ICT (Inner Circle Trader) trade planner.

--- SESSION / TIME CONTEXT ---
{_session_context()}

Analysis data:
{json.dumps(context, indent=2)}

Overall direction: {direction}
Current price: {current}

---
--- ENTRY DISTANCE RULES ---
Asset: {market_data.get("ticker", "")}
Current price: {current}
Max reasonable LIMIT_ORDER distance from current price:
  - JPY pairs (e.g. GBPJPY): 40 pips = 0.40 price units
  - Other pairs: 30 pips = 0.0030 price units
If the nearest valid entry zone is FURTHER than the max distance above, use WAIT_REVERSAL instead of LIMIT_ORDER — the retracement is too large to set a passive order. State what price action would bring it into range.

---
DECISION — choose ONE of these three actions:

A) ENTER_NOW — price is currently AT or INSIDE a high-confluence ICT zone (OB/FVG) aligned with bias.

B) LIMIT_ORDER — price has NOT reached the zone yet, but the zone is within {40 if "JPY" in str(market_data.get("ticker","")).upper() else 30} pips.
   Entry = zone top (bearish) or zone bottom (bullish).

C) WAIT_REVERSAL — no nearby zone, zone is too far (>40 pips JPY / >30 pips other), or LTF structure unconfirmed.
   Describe exactly what price action must happen before an entry is valid.

---
TRADE PLAN:

ENTRY — exact price
STOP LOSS
  - Bearish: above OB/FVG top or recent swing high
  - Bullish: below OB/FVG bottom or recent swing low
TP1 — nearest liquidity (1:1–1:3 RR)
TP2 — major draw on liquidity (PDH/PDL, session high/low, equal highs/lows)

TIMING — use the SESSION / TIME CONTEXT above to determine trade_time:
  - If a session IS CURRENTLY ACTIVE → say "NOW — <session> open until HH:MM Bulgaria"
  - If the next killzone is upcoming → give its Bulgaria start time
  - Do NOT mention any session already listed as "Already finished today"
  - If no killzone remains today → say "No killzone today — Asia opens tomorrow at 03:00 Bulgaria"

Rules:
- All prices must be exact numbers
- Bearish: SL > entry > TP1 > TP2
- Bullish: SL < entry < TP1 < TP2
- JPY pairs: 3 decimal places; others: 5
- RR = abs(entry - tp) / abs(entry - sl), rounded to 1dp

Return ONLY valid JSON — no other text:
{{
  "action": "ENTER_NOW|LIMIT_ORDER|WAIT_REVERSAL",
  "action_reason": "<1 sentence explaining the action choice>",
  "wait_for": "<null or what to wait for if WAIT_REVERSAL>",
  "trade_time": "<e.g. 'NOW — NY Open Kill Zone open until 18:00 Bulgaria' or 'London Close opens 18:00 Bulgaria'>",
  "trade_time_reason": "<1 sentence why this session suits this setup>",
  "entry": <number or null if WAIT_REVERSAL>,
  "sl": <number or null if WAIT_REVERSAL>,
  "tp1": <number or null if WAIT_REVERSAL>,
  "tp2": <number or null if WAIT_REVERSAL>,
  "rr1": <number or null>,
  "rr2": <number or null>,
  "invalidation": "<price that kills the thesis>",
  "entry_notes": "<1 sentence>",
  "sl_notes": "<1 sentence>",
  "tp_notes": "<1 sentence>"
}}"""

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    # Robust JSON extraction
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start: i + 1])
                        data["direction"] = direction
                        data["confluence_zones"] = confluence_zones
                        return data
                    except json.JSONDecodeError:
                        break

    return {
        "direction":        direction,
        "action":           "WAIT_REVERSAL",
        "action_reason":    "Could not parse trade setup",
        "entry":            None,
        "sl":               None,
        "tp1":              None,
        "tp2":              None,
        "rr1":              None,
        "rr2":              None,
        "confluence_zones": confluence_zones,
        "error":            "Could not parse trade setup from Claude response",
    }
