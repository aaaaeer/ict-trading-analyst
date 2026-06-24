import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import anthropic


_TZ_BG = ZoneInfo("Europe/Sofia")

_SESSIONS = [
    ("Asian session",            0,  0,  5,  0),  # 00:00–05:00 UTC
    ("London Open Kill Zone",    7,  0, 10,  0),  # 07:00–10:00 UTC
    ("New York Open Kill Zone", 12,  0, 15,  0),  # 12:00–15:00 UTC
    ("London Close",            15,  0, 16,  0),  # 15:00–16:00 UTC
    ("NY Close / Dead Zone",    16,  0, 23, 59),  # 16:00–midnight UTC
]


def _utc_to_bg(h_utc: int, m_utc: int) -> str:
    """Convert a UTC HH:MM to a Bulgaria local time string, respecting DST."""
    # Use today's date so DST is resolved correctly
    now_bg = datetime.now(_TZ_BG)
    offset_hours = int(now_bg.utcoffset().total_seconds() // 3600)
    bg_h = (h_utc + offset_hours) % 24
    return f"{bg_h:02d}:{m_utc:02d}"


def _session_context() -> str:
    """Return current UTC + Bulgaria time, and which killzones are active / upcoming."""
    now_utc = datetime.now(timezone.utc)
    now_bg  = datetime.now(_TZ_BG)
    h_utc   = now_utc.hour + now_utc.minute / 60

    offset_h = int(now_bg.utcoffset().total_seconds() // 3600)
    tz_label = now_bg.strftime("%Z")  # e.g. "EEST" or "EET"

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
        return (f"{name} "
                f"[UTC {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} | "
                f"Bulgaria {_utc_to_bg(sh, sm)}–{_utc_to_bg(eh, em)}]")

    lines = [
        f"Current time: {now_utc.strftime('%H:%M')} UTC  /  {now_bg.strftime('%H:%M')} Bulgaria ({tz_label}, UTC+{offset_h})",
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
    lines.append("- If a session is CURRENTLY ACTIVE, say 'NOW — <session> open until HH:MM Bulgaria'")
    lines.append("- If upcoming, give the Bulgaria start time")
    lines.append("- NEVER mention a session already listed as finished today")
    lines.append("- If all killzones done, say 'No killzone today — Asian session tomorrow at 03:00 Bulgaria'")
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

    is_jpy = "JPY" in str(market_data.get("ticker", "")).upper()
    pip      = 0.01  if is_jpy else 0.0001
    max_sl   = 30    if is_jpy else 30     # pips
    max_tp1  = 60    if is_jpy else 60     # pips
    max_tp2  = 150   if is_jpy else 150    # pips
    max_entry_dist = 40 if is_jpy else 30  # pips — beyond this use WAIT_REVERSAL

    prompt = f"""You are an ICT (Inner Circle Trader) INTRADAY / DAY TRADING analyst.
This is a SHORT-TERM setup tool. You are looking for moves of 20–150 pips maximum, completed within 1–2 trading sessions.
You are NOT doing swing trading. You are NOT targeting multi-day or multi-week moves.

--- THIS SESSION ---
{_session_context()}

--- ASSET & PRICE ---
Asset: {market_data.get("ticker", "")}  |  Current price: {current}
Pip size: {pip}  (1 pip = {pip})

--- ANALYSIS DATA ---
{json.dumps(context, indent=2)}

Overall direction: {direction}

---
=== HARD LIMITS — INTRADAY DAY TRADING ONLY ===
{"JPY pair rules:" if is_jpy else "Non-JPY pair rules:"}
  Stop Loss:  MAX {max_sl} pips ({max_sl * pip:.3f} price units) — tight, behind nearest OB/FVG edge or swing point
  TP1:        MAX {max_tp1} pips ({max_tp1 * pip:.3f}) — target nearest INTRADAY liquidity: session high/low, equal highs/lows, OB/FVG on the same day
  TP2:        MAX {max_tp2} pips ({max_tp2 * pip:.3f}) — target PDH, PDL, or opposite session extreme
  Entry zone: MAX {max_entry_dist} pips ({max_entry_dist * pip:.3f}) from current price for LIMIT_ORDER

DO NOT target swing highs/lows from days or weeks ago.
DO NOT set SL wider than {max_sl} pips.
DO NOT set TP further than {max_tp2} pips.
If no intraday target exists within {max_tp2} pips, use WAIT_REVERSAL and explain what needs to happen first.

Liquidity targets to look for (in order of priority):
  1. Equal highs / equal lows visible on the 5M or 15M chart (within 50–80 pips)
  2. Current session high or low (London H/L, NY H/L, Asia H/L marked on chart)
  3. PDH or PDL (previous day high/low — 1 day ago only)
  These are the ICT day trade targets. Nothing beyond these.

---
=== DECISION ===
Choose ONE:

A) ENTER_NOW — price is AT or INSIDE a high-confluence OB/FVG right now.
B) LIMIT_ORDER — valid zone is within {max_entry_dist} pips. Entry = zone edge (top for bearish, bottom for bullish).
C) WAIT_REVERSAL — zone is too far, or LTF (5M/15M) structure not yet confirmed.
   State exactly what must happen: e.g. "Wait for 5M CHoCH bearish and 5M FVG fill below 195.30".

---
=== TRADE PLAN ===
ENTRY — exact price
STOP LOSS — beyond OB/FVG edge or 5M swing point. MAX {max_sl} pips from entry.
TP1 — nearest intraday liquidity, MAX {max_tp1} pips from entry.
TP2 — next intraday draw, MAX {max_tp2} pips from entry.

TIMING:
  - If a session IS CURRENTLY ACTIVE → "NOW — <session> open until HH:MM Bulgaria"
  - If upcoming → give Bulgaria start time
  - Never mention a session already listed as finished today

Rules:
- All prices must be exact numbers
- Bearish: SL > entry > TP1 > TP2
- Bullish: SL < entry < TP1 < TP2
- JPY: 3 decimal places; others: 5
- RR = abs(entry - tp) / abs(entry - sl), rounded to 1dp

Return ONLY valid JSON — no other text:
{{
  "action": "ENTER_NOW|LIMIT_ORDER|WAIT_REVERSAL",
  "action_reason": "<1 sentence>",
  "wait_for": "<null or condition to wait for>",
  "trade_time": "<e.g. 'NOW — NY Open Kill Zone open until 18:00 Bulgaria'>",
  "trade_time_reason": "<1 sentence>",
  "entry": <number or null>,
  "sl": <number or null>,
  "tp1": <number or null>,
  "tp2": <number or null>,
  "rr1": <number or null>,
  "rr2": <number or null>,
  "invalidation": "<price level>",
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
