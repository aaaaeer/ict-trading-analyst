import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import anthropic

_TZ_BG = ZoneInfo("Europe/Sofia")

_SESSIONS = [
    ("Asian session",            0,  0,  5,  0),
    ("London Open Kill Zone",    7,  0, 10,  0),
    ("New York Open Kill Zone", 12,  0, 15,  0),
    ("London Close",            15,  0, 16,  0),
    ("NY Close / Dead Zone",    16,  0, 23, 59),
]


def _utc_to_bg(h_utc: int, m_utc: int) -> str:
    now_bg = datetime.now(_TZ_BG)
    offset_hours = int(now_bg.utcoffset().total_seconds() // 3600)
    bg_h = (h_utc + offset_hours) % 24
    return f"{bg_h:02d}:{m_utc:02d}"


def _session_context() -> str:
    now_utc = datetime.now(timezone.utc)
    now_bg  = datetime.now(_TZ_BG)
    h_utc   = now_utc.hour + now_utc.minute / 60
    offset_h = int(now_bg.utcoffset().total_seconds() // 3600)
    tz_label = now_bg.strftime("%Z")

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
        return (f"{name} [UTC {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} | "
                f"Bulgaria {_utc_to_bg(sh, sm)}–{_utc_to_bg(eh, em)}]")

    lines = [
        f"Current time: {now_utc.strftime('%H:%M')} UTC  /  {now_bg.strftime('%H:%M')} Bulgaria ({tz_label}, UTC+{offset_h})",
        f"Day: {now_utc.strftime('%A')}",
    ]
    if active:
        lines.append("CURRENTLY ACTIVE: " + ", ".join(fmt(*s) for s in active))
    if upcoming:
        lines.append("Still upcoming today: " + ", ".join(fmt(*s) for s in upcoming))
    if passed:
        lines.append("Already finished today: " + ", ".join(fmt(*s) for s in passed))
    lines.append("")
    lines.append("RULE: trade_time must only reference ACTIVE or UPCOMING sessions. Never mention sessions in 'Already finished today'.")
    lines.append("If all killzones are done, say: 'No killzone today — Asian session tomorrow at 03:00 Bulgaria'")
    return "\n".join(lines)


def _build_confluence_zones(chart_data: dict, current_price: float | None) -> list[dict]:
    if not current_price:
        return []

    tolerance = current_price * 0.0015
    raw: list[dict] = []

    def add(direction: str, label: str, top: float, bottom: float):
        if top and bottom:
            raw.append({"direction": direction, "label": label,
                        "top": top, "bottom": bottom, "mid": (top + bottom) / 2})

    for item in (chart_data.get("fvgs") or []):
        if isinstance(item, dict):
            add(item.get("direction", ""), "FVG", item.get("top", 0), item.get("bottom", 0))

    for item in (chart_data.get("large_fvgs") or []):
        if isinstance(item, dict):
            add(item.get("direction", ""), "Large FVG", item.get("top", 0), item.get("bottom", 0))

    for item in (chart_data.get("order_blocks") or []):
        if isinstance(item, dict):
            add(item.get("direction", ""), "OB", item.get("top", 0), item.get("bottom", 0))

    zones: list[dict] = []
    used = [False] * len(raw)

    for i, sig in enumerate(raw):
        if used[i]:
            continue
        cluster = [sig]
        used[i] = True
        for j, other in enumerate(raw):
            if used[j]:
                continue
            if abs(other["mid"] - sig["mid"]) <= tolerance * 2:
                cluster.append(other)
                used[j] = True

        tops    = [s["top"]    for s in cluster]
        bottoms = [s["bottom"] for s in cluster]
        zone_top = max(tops)
        zone_bot = min(bottoms)
        zone_mid = (zone_top + zone_bot) / 2

        bear_n = sum(1 for s in cluster if "bearish" in s["direction"].lower())
        bull_n = sum(1 for s in cluster if "bullish" in s["direction"].lower())
        direction = "bearish" if bear_n > bull_n else "bullish"
        strength  = len(cluster)

        position = ("at_price" if zone_bot <= current_price <= zone_top
                    else "below_price" if current_price > zone_top
                    else "above_price")

        zones.append({
            "top":       round(zone_top, 5),
            "bottom":    round(zone_bot, 5),
            "mid":       round(zone_mid, 5),
            "direction": direction,
            "signals":   [s["label"] for s in cluster],
            "strength":  strength,
            "position":  position,
            "stars":     "★" * min(strength, 5),
        })

    zones.sort(key=lambda z: z["strength"], reverse=True)
    return zones


def generate_ict_analysis(
    chart_data: dict,
    market_data: dict,
    news: dict,
    calendar: dict,
) -> dict:
    """
    Single Claude call that follows the full ICT Daily Model.
    Returns: bias direction/score/confidence + ICT model/checklist/narrative + trade plan.
    Replaces both ict_engine.py and trade_setup.py.
    """
    current          = market_data.get("current_price")
    confluence_zones = _build_confluence_zones(chart_data, current)
    session_ctx      = _session_context()

    is_jpy         = "JPY" in str(market_data.get("ticker", "")).upper()
    pip            = 0.01 if is_jpy else 0.0001
    max_sl         = 30
    max_tp1        = 60
    max_tp2        = 150
    max_entry_dist = 40 if is_jpy else 30
    dp_note        = "3 decimal places (JPY pair)" if is_jpy else "5 decimal places"

    context = {
        "asset":             market_data.get("ticker"),
        "current_price":     current,
        "pdh":               market_data.get("pdh"),
        "pdl":               market_data.get("pdl"),
        "pdc":               market_data.get("pdc"),
        "asian_high":        market_data.get("asian_high"),
        "asian_low":         market_data.get("asian_low"),
        "london_high":       market_data.get("london_high"),
        "london_low":        market_data.get("london_low"),
        "intraday_trend":    market_data.get("intraday_trend"),
        "htf_bias":          chart_data.get("htf_bias"),
        "ltf_bias":          chart_data.get("ltf_bias"),
        "combined_trend":    chart_data.get("combined_trend"),
        "structure":         chart_data.get("structure"),
        "trend":             chart_data.get("trend"),
        "premium_discount":  chart_data.get("premium_discount"),
        "draw_on_liquidity": chart_data.get("draw_on_liquidity"),
        "displacement":      chart_data.get("displacement"),
        "fvgs":              chart_data.get("fvgs", []),
        "large_fvgs":        chart_data.get("large_fvgs", []),
        "order_blocks":      chart_data.get("order_blocks", []),
        "liquidity":         chart_data.get("liquidity"),
        "killzone":          chart_data.get("killzone"),
        "poi_levels":        chart_data.get("poi_levels", []),
        "confluence_notes":  chart_data.get("confluence_notes"),
        "confluence_zones":  confluence_zones,
        "per_chart":         chart_data.get("charts", []),
        "news_sentiment":    news.get("overall_sentiment", "neutral"),
        "news_score":        news.get("sentiment_score", 0),
        "high_impact_events":len(calendar.get("events", [])),
        "has_imminent_event":calendar.get("has_imminent", False),
        "calendar_events":   calendar.get("events", [])[:4],
    }

    prompt = f"""You are an expert ICT (Inner Circle Trader) INTRADAY analyst.
You trade using the ICT Daily Model — intraday moves of 20–150 pips, completed within 1–2 sessions.

╔══ SESSION CONTEXT ══╗
{session_ctx}

Asset: {market_data.get("ticker", "")}  |  Current price: {current}  |  Price decimals: {dp_note}
Pip size: {pip}

╔══ ALL ANALYSIS DATA ══╗
{json.dumps(context, indent=2)}

╔═════════════════════════════════════════════════════════════════╗
FOLLOW THE ICT DAILY MODEL — 6 STEPS IN ORDER:
╚═════════════════════════════════════════════════════════════════╝

STEP 1 — HTF DAILY BIAS (read from 1D/4H structure in per_chart or htf_bias field)
  HH+HL pattern = BULLISH delivery expected today (price will seek BSL above)
  LH+LL pattern = BEARISH delivery expected today (price will seek SSL below)
  Most recent CHoCH OVERRIDES previous structure — a bearish CHoCH ends a bullish structure.
  If HTF structure is genuinely unclear or mixed → direction = NEUTRAL, action = NO_TRADE.
  → Set: daily_bias (plain English), direction (BULLISH/BEARISH/NEUTRAL), score (-100 to +100)

STEP 2 — DRAW ON LIQUIDITY (where is price being DELIVERED this session?)
  BSL (equal highs, session high, PDH above price) = bullish draw → price heading UP
  SSL (equal lows, session low, PDL below price)  = bearish draw → price heading DOWN
  SWEPT liquidity = price already delivered there → now look for REVERSAL targeting other side
  → Confirm the draw aligns with direction from Step 1

STEP 3 — PREMIUM / DISCOUNT ZONE
  Find the 50% midpoint of the most recent relevant swing range (visible on the HTF chart).
  Price ABOVE 50% = PREMIUM → ideal zone to SELL from
  Price BELOW 50% = DISCOUNT → ideal zone to BUY from
  If direction is BULLISH but price is in PREMIUM → not in the right zone yet → lean WAIT_REVERSAL
  If direction is BEARISH but price is in DISCOUNT → not in the right zone yet → lean WAIT_REVERSAL
  → Set: ict_checklist.in_premium_discount = true (correct zone) / false (wrong zone)

STEP 4 — MANIPULATION CHECK (has the stop hunt happened?)
  Look for a SWEEP of a nearby liquidity pool:
    • Price ran ABOVE equal highs or session high → swept BSL (if direction bearish, this triggers sell)
    • Price ran BELOW equal lows or session low → swept SSL (if direction bullish, this triggers buy)
    • Price broke above PDH or below PDL then reversed
  Use the displacement field, liquidity field, and per-chart FVG/OB data to judge this.
  → Set: ict_checklist.manipulation_swept = true/false
  Note: If manipulation has NOT happened → the ICT model is not yet active → WAIT_REVERSAL

STEP 5 — DISPLACEMENT (has the impulsive move created the entry zone?)
  After the manipulation sweep, was there a STRONG IMPULSIVE MOVE in the opposite direction?
  This is a large candle (or series of candles) that leaves behind a visible FVG or creates a clean OB.
  The FVG or OB from this displacement move is the ENTRY ZONE.
  → Set: ict_checklist.displacement_visible = true/false
  Note: If displacement has NOT happened → WAIT_REVERSAL (state what to watch for)

STEP 6 — ENTRY TRIGGER (is it time to enter?)
  Is price now AT or APPROACHING the FVG/OB created by the displacement in Step 5?
  Has there been a LTF (5M or 15M) CHoCH confirming the reversal at that zone?

  Choose action:
  ENTER_NOW → price IS currently inside or touching the displacement FVG/OB + LTF CHoCH confirmed
  LIMIT_ORDER → displacement FVG/OB identified, price within {max_entry_dist} pips, waiting for price to reach it
  WAIT_REVERSAL → steps 4 or 5 not yet confirmed — describe EXACTLY what must happen:
    e.g. "Wait for price to sweep equal highs at 215.80. After sweep, watch for 5M bearish displacement + 5M CHoCH below 215.60, then enter short at 5M FVG retest."
  NO_TRADE → NEUTRAL direction / genuinely conflicting HTF signals

╔══ HARD LIMITS (intraday day trading ONLY) ═╗
  SL:    MAX {max_sl} pips ({max_sl * pip:.3f}) — behind OB/FVG edge or 5M swing point
  TP1:   MAX {max_tp1} pips ({max_tp1 * pip:.3f}) — nearest intraday liquidity (session H/L, equal H/L on same day)
  TP2:   MAX {max_tp2} pips ({max_tp2 * pip:.3f}) — next intraday draw (PDH/PDL of this day only)
  DO NOT target multi-day swing highs/lows or levels from days/weeks ago.
  DO NOT set TP beyond {max_tp2} pips. DO NOT set SL beyond {max_sl} pips.
  Prices: {dp_note}
  Bullish trade: SL < entry < TP1 < TP2
  Bearish trade: SL > entry > TP1 > TP2
  RR = abs(entry − tp) / abs(entry − sl), rounded to 1dp
╚════════════════════════════════════════╝

Return ONLY valid JSON — no extra text. All 6 steps must be reflected in the output:
{{
  "direction": "BULLISH|BEARISH|NEUTRAL",
  "confidence": "HIGH|MEDIUM|LOW",
  "score": 0,
  "daily_bias": "plain English HTF narrative, e.g. '4H LH+LL bearish, BOS at 215.00 — BEARISH delivery expected'",
  "ict_model": "ICT Sell Model Active|ICT Buy Model Active|Waiting for manipulation|No model forming",
  "ict_narrative": "3-4 sentence plain-English ICT story: what the chart shows, where price is in the model, and what to watch for next",
  "ict_checklist": {{
    "htf_structure": true,
    "draw_identified": true,
    "in_premium_discount": true,
    "manipulation_swept": false,
    "displacement_visible": false,
    "ltf_choch_confirmed": false
  }},
  "action": "ENTER_NOW|LIMIT_ORDER|WAIT_REVERSAL|NO_TRADE",
  "action_reason": "1 sentence explaining why this action",
  "wait_for": null,
  "trade_time": "e.g. 'NOW — NY Open Kill Zone until 18:00 Bulgaria' or 'London Open tomorrow at 10:00 Bulgaria'",
  "trade_time_reason": "1 sentence",
  "entry": null,
  "sl": null,
  "tp1": null,
  "tp2": null,
  "rr1": null,
  "rr2": null,
  "invalidation": "price level as string",
  "entry_notes": "1 sentence — why this exact entry (name the OB/FVG)",
  "sl_notes": "1 sentence — why this SL placement",
  "tp_notes": "1 sentence — what liquidity is being targeted",
  "reasoning": [
    "Step 1 — HTF: ...",
    "Step 2 — Draw: ...",
    "Step 3 — P/D: ...",
    "Step 4 — Manipulation: ...",
    "Step 5 — Displacement: ...",
    "Step 6 — Entry: ..."
  ],
  "breakdown": {{
    "htf_structure": 0,
    "draw_on_liquidity": 0,
    "premium_discount": 0,
    "fvg_ob": 0,
    "ltf_confirmation": 0,
    "pdh_pdl": 0,
    "news": 0
  }}
}}"""

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start: i + 1])
                        data["confluence_zones"] = confluence_zones
                        data.setdefault("direction", "NEUTRAL")
                        data.setdefault("action", "WAIT_REVERSAL")
                        data.setdefault("reasoning", [])
                        data.setdefault("breakdown", {})
                        data.setdefault("ict_checklist", {k: False for k in (
                            "htf_structure", "draw_identified", "in_premium_discount",
                            "manipulation_swept", "displacement_visible", "ltf_choch_confirmed",
                        )})
                        return data
                    except json.JSONDecodeError:
                        break

    preview = text[:300].replace("\n", " ")
    print(f"\n[ict_analysis] Could not parse JSON. Claude returned:\n  {preview}\n")
    return {
        "direction": "NEUTRAL",
        "confidence": "LOW",
        "score": 0,
        "daily_bias": "Could not parse ICT analysis",
        "ict_model": "No model forming",
        "ict_narrative": "Analysis error — could not parse response from Claude. Check your API connection and try again.",
        "ict_checklist": {k: False for k in (
            "htf_structure", "draw_identified", "in_premium_discount",
            "manipulation_swept", "displacement_visible", "ltf_choch_confirmed",
        )},
        "action": "NO_TRADE",
        "action_reason": "Analysis error — see above",
        "wait_for": None,
        "trade_time": None,
        "trade_time_reason": None,
        "entry": None, "sl": None, "tp1": None, "tp2": None, "rr1": None, "rr2": None,
        "invalidation": "N/A",
        "entry_notes": "", "sl_notes": "", "tp_notes": "",
        "reasoning": [f"Error: could not parse ICT analysis. Raw: {text[:200]}"],
        "breakdown": {},
        "confluence_zones": confluence_zones,
    }
