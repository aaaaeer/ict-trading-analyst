import base64
import io
import json
import re
from pathlib import Path
import anthropic

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

NATIVE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

VISUAL_LEGEND = """
CHART VISUAL LEGEND — read these elements exactly as drawn:

1. RED FILLED BOXES → FVG (Fair Value Gap)
   - A red shaded/filled rectangle = a Fair Value Gap (price imbalance area)
   - IMPORTANT: The red colour is just the indicator's display colour. It does NOT mean the FVG is bearish.
   - Determine the FVG direction from CONTEXT:
       * If the box is ABOVE current price → bearish FVG (supply imbalance, price may react DOWN from here)
       * If the box is BELOW current price → bullish FVG (demand imbalance, price may bounce UP from here)
       * If price is INSIDE the box → actively being filled (note this)
   - Read the top and bottom price of each box from the Y-axis

2. GREEN AND RED LINES → Market Structure (CHoCH / BOS)
   - A labelled line marked "CHoCH" (Change of Character) = structure shift / trend reversal signal
   - A labelled line marked "BOS" (Break of Structure) = trend continuation confirmation
   - GREEN line = bullish structure event (price broke upward)
   - RED line = bearish structure event (price broke downward)
   - Read the label text AND the colour to determine direction
   - Read the exact price level of each line

3. SMALL BLUE BOXES → Order Blocks (OB)
   - Small blue shaded rectangles = order blocks (last OB before a significant move)
   - Determine direction from position:
       * Below current price → bullish OB (demand zone, expect price to bounce UP)
       * Above current price → bearish OB (supply zone, expect price to reject DOWN)
   - Read the top and bottom price of each box

4. LARGE BLUE BOXES WITH A HORIZONTAL LINE THROUGH THE MIDDLE → Large FVG
   - A large blue rectangle with a visible midline = a significant Fair Value Gap
   - The midline = the 50% equilibrium level (strong magnetic draw)
   - Determine direction the same way as small FVGs: position relative to current price
       * Above price → bearish large FVG (resistance / supply)
       * Below price → bullish large FVG (support / demand)
   - Read the top, midline, and bottom prices

5. SESSION LABELS / BOXES (London, NY, Asia, "L H", "L L", "NY H", "NY L") → Session High/Low reference levels
   - These boxes or horizontal lines labelled with session names mark the HIGH and LOW of a PAST session
   - They are NOT indicators of the currently active session — they are historical liquidity reference points
   - "London H" or "L H" = the high price reached during the London session (a sell-side liquidity level above)
   - "London L" or "L L" = the low price reached during the London session (a buy-side liquidity level below)
   - "NY H" / "NY L" = New York session high and low (liquidity levels)
   - "Asia H" / "Asia L" = Asian session high and low (Asian range boundary — often swept at London open)
   - Record these as liquidity levels, NOT as the current killzone
   - The "killzone" field should reflect which session is likely active based on the candle activity visible,
     NOT based on the session labels on the chart

For ALL elements: read the actual price numbers from the chart's Y-axis scale.
If a price label is visible on or near the box/line, record it exactly.
If no label is visible, estimate from the Y-axis.
"""

SINGLE_PROMPT = """You are an expert ICT (Inner Circle Trader) INTRADAY analyst with precise visual reading ability.
You are analysing for DAY TRADING setups — moves of 20–150 pips within a single trading session.
Focus on: 5M/15M entries, OBs and FVGs visible on the current chart, intraday liquidity (session highs/lows, equal highs/lows), and same-day structure shifts (CHoCH/BOS).
Ignore multi-week trends. Identify what is happening TODAY in the current session.

""" + VISUAL_LEGEND + """

Analyse this chart using the ICT framework:
1. What is the STRUCTURE? (HH+HL = bullish, LH+LL = bearish, note the most recent CHoCH/BOS)
2. Is price in PREMIUM (above range midpoint = sell area) or DISCOUNT (below midpoint = buy area)?
3. What is the DRAW ON LIQUIDITY — where is price most likely heading? (Equal highs/BSL above = bullish draw, equal lows/SSL below = bearish draw)
4. What OBs and FVGs are at relevant levels for an entry?
5. Has there been a DISPLACEMENT (strong impulsive move) that created a POI?

Return ONLY valid JSON, no other text, matching this structure exactly:
{
  "asset": "ticker if visible or null",
  "timeframe": "e.g. 1H or 15M",
  "structure": "HH+HL bullish | LH+LL bearish | mixed — include the most recent CHoCH/BOS label and price",
  "trend": "bullish|bearish|ranging",
  "premium_discount": "premium|discount|equilibrium",
  "draw_on_liquidity": "e.g. 'Equal highs at 215.50 — BSL bullish draw' or 'Session low 213.20 — SSL bearish draw'",
  "displacement": "e.g. 'Bearish displacement on 15M created FVG at 215.10-215.35' or 'none visible'",
  "fvgs": [{"direction": "bullish (below price = demand) or bearish (above price = supply)", "top": 0.0, "bottom": 0.0}],
  "large_fvgs": [{"direction": "bullish|bearish", "top": 0.0, "midline": 0.0, "bottom": 0.0}],
  "order_blocks": [{"direction": "bullish (below price) or bearish (above price)", "top": 0.0, "bottom": 0.0}],
  "liquidity": "list ALL visible session H/L labels and equal highs/lows with exact prices",
  "killzone": "London|NY|Asia|none — based on candle activity patterns, NOT session label boxes",
  "poi_levels": ["list price levels of key OBs and FVGs"]
}"""

MULTI_PROMPT = """You are an expert ICT (Inner Circle Trader) INTRADAY analyst performing multi-timeframe analysis for DAY TRADING.
You are looking for setups that complete within 1–2 trading sessions (20–150 pip moves).
Use the higher timeframes (1D/4H/1H) ONLY for directional bias. All actual entry signals come from the lower timeframes (15M/5M).
Focus on: current session OBs/FVGs, intraday CHoCH/BOS, same-day liquidity (session highs/lows, equal highs/lows).
Do NOT focus on multi-week swing levels or targets more than 150 pips away.
You have been given {n} trading chart screenshots. Treat them in order — Chart 1 is the highest timeframe, last is lowest.

""" + VISUAL_LEGEND + """

For EACH chart apply the ICT framework — answer these in order:
  1. What is the STRUCTURE? (HH+HL bullish | LH+LL bearish — note most recent CHoCH/BOS label and price)
  2. Is price in PREMIUM (above range midpoint) or DISCOUNT (below midpoint)?
  3. What is the DRAW ON LIQUIDITY this session? (Equal highs/BSL above = bullish draw, equal lows/SSL below = bearish draw)
  4. What OBs/FVGs exist? Classify by POSITION relative to current price (above price = bearish/supply, below = bullish/demand)
  5. Any DISPLACEMENT move visible? (Strong impulsive candle creating FVG/OB)

Per-chart fields to extract:
  asset, timeframe (1W/1D/4H/1H/15M/5M/1M),
  structure: "HH+HL bullish" | "LH+LL bearish" | "mixed" — include most recent CHoCH/BOS,
  trend: bullish|bearish|ranging,
  premium_discount: premium|discount|equilibrium,
  draw_on_liquidity: string describing the target (e.g. "Equal lows 213.20 — SSL bearish draw"),
  displacement: string describing any recent strong impulsive move,
  fvgs: [{{"direction": "bullish (below price) or bearish (above price)", "top": price, "bottom": price}}],
  large_fvgs: [{{"direction": "bullish|bearish", "top": price, "midline": price, "bottom": price}}],
  order_blocks: [{{"direction": "bullish (below price) or bearish (above price)", "top": price, "bottom": price}}],
  liquidity: ALL visible session H/L labels and equal highs/lows with exact prices,
  killzone: London|NY|Asia|none — from candle activity patterns only,
  poi_levels: list of key OB/FVG price levels

Then synthesise across all timeframes:
  htf_bias: directional bias from higher TFs (1D/4H) — bullish|bearish|neutral
  ltf_bias: short-term bias from lower TFs (15M/5M) — bullish|bearish|neutral
  premium_discount: overall — is price in premium or discount on the HTF?
  draw_on_liquidity: the main intraday liquidity target price is heading toward
  confluence_notes: where TFs agree/conflict, which OBs/FVGs stack across timeframes

Return ONLY valid JSON — no extra text:
{{
  "asset": "...",
  "timeframe": "multi-timeframe",
  "htf_bias": "bullish|bearish|neutral",
  "ltf_bias": "bullish|bearish|neutral",
  "premium_discount": "premium|discount|equilibrium",
  "draw_on_liquidity": "...",
  "combined_trend": "...",
  "confluence_notes": "...",
  "structure": "...",
  "displacement": "...",
  "fvgs": [],
  "large_fvgs": [],
  "order_blocks": [],
  "liquidity": "...",
  "trend": "...",
  "killzone": "...",
  "poi_levels": [],
  "charts": [
    {{
      "chart_index": 0,
      "asset": "...",
      "timeframe": "...",
      "structure": "...",
      "trend": "...",
      "premium_discount": "...",
      "draw_on_liquidity": "...",
      "displacement": "...",
      "fvgs": [],
      "large_fvgs": [],
      "order_blocks": [],
      "liquidity": "...",
      "killzone": "...",
      "poi_levels": []
    }}
  ]
}}"""


def _to_png_b64(path: str) -> str:
    """Convert any image format to base64-encoded PNG."""
    p = Path(path)
    if _PIL:
        with Image.open(p) as img:
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.standard_b64encode(buf.getvalue()).decode()
    # No PIL — send raw bytes and hope the format is natively supported
    return base64.standard_b64encode(p.read_bytes()).decode()


def _media_type(path: str) -> str:
    if _PIL:
        return "image/png"  # PIL always converts to PNG
    suffix = Path(path).suffix.lower()
    return NATIVE_TYPES.get(suffix, "image/png")


def _parse_json(text: str) -> dict:
    # 1. Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    # 2. Try the whole cleaned string first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Find outermost { ... } using brace counting (handles nested objects)
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
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break

    # 4. Log what came back so the user can see it
    preview = text[:300].replace("\n", " ")
    print(f"\n[chart_vision] Could not parse JSON. Claude returned:\n  {preview}\n")
    return {"raw": text, "error": "Could not parse JSON from vision response"}


def analyze_charts(image_paths: list[str]) -> dict:
    """
    Analyse one or more chart screenshots with Claude vision.
    Pass charts in order from highest to lowest timeframe.
    Returns a consolidated dict compatible with ict_engine / output.
    """
    if not image_paths:
        return {"error": "No images provided"}

    client = anthropic.Anthropic()

    # ── Single chart ─────────────────────────────────────────────────────────
    if len(image_paths) == 1:
        b64 = _to_png_b64(image_paths[0])
        result = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SINGLE_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _media_type(image_paths[0]),
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": "Return the ICT analysis as valid JSON only, no extra text."},
                ],
            }],
        )
        return _parse_json(result.content[0].text)

    # ── Multiple charts ───────────────────────────────────────────────────────
    content: list[dict] = []
    for i, path in enumerate(image_paths):
        content.append({"type": "text", "text": f"Chart {i + 1} — {Path(path).name}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",  # PIL always outputs PNG
                "data": _to_png_b64(path),
            },
        })
    content.append({
        "type": "text",
        "text": "Return the multi-timeframe ICT analysis as valid JSON only, no extra text.",
    })

    result = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=MULTI_PROMPT.format(n=len(image_paths)),
        messages=[{"role": "user", "content": content}],
    )

    data = _parse_json(result.content[0].text)

    # Back-fill top-level keys from HTF chart if Claude omitted them
    charts = data.get("charts", [])
    if charts:
        primary = charts[0]
        for key in ("asset", "timeframe", "structure", "fvgs", "order_blocks",
                    "liquidity", "trend", "killzone", "poi_levels"):
            if not data.get(key):
                data[key] = primary.get(key, "")

    return data
