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
   - A red shaded/filled rectangle on the chart = a bearish FVG (imbalance to the downside)
   - Read the top and bottom price of the box as the FVG range
   - These are areas price may return to fill before continuing down

2. GREEN AND RED LINES → Market Structure (CHoCH / BOS)
   - A labelled line marked "CHoCH" (Change of Character) = structure shift, potential trend reversal
   - A labelled line marked "BOS" (Break of Structure) = trend continuation confirmation
   - Green CHoCH/BOS line = bullish structure break
   - Red CHoCH/BOS line = bearish structure break
   - Read the price level of the line and whether it is bullish or bearish

3. SMALL BLUE BOXES → Order Blocks (OB)
   - Small blue shaded rectangles = order blocks
   - If the box is below current price = bullish OB (demand zone, potential support)
   - If the box is above current price = bearish OB (supply zone, potential resistance)
   - Read the price range of each box

4. LARGE BLUE BOXES WITH A HORIZONTAL LINE THROUGH THE MIDDLE → Large FVG
   - A large blue rectangle with a midline = a significant Fair Value Gap (bigger imbalance)
   - The midline = the 50% equilibrium of the FVG (key magnetic level)
   - These act as major draw-on-liquidity targets
   - Read the top, midline, and bottom price of the box

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

Analyse this trading chart screenshot and extract every visible element using the legend above.

Return ONLY valid JSON, no other text, matching this structure exactly:
{
  "asset": "ticker if visible or null",
  "timeframe": "e.g. 1H or 15M",
  "structure": "HH+HL bullish | LH+LL bearish | mixed — include CHoCH/BOS direction and price level if visible",
  "trend": "bullish|bearish|ranging",
  "fvgs": [{"direction": "bearish", "top": 0.0, "bottom": 0.0}],
  "large_fvgs": [{"direction": "bullish|bearish", "top": 0.0, "midline": 0.0, "bottom": 0.0}],
  "order_blocks": [{"direction": "bullish|bearish", "top": 0.0, "bottom": 0.0}],
  "liquidity": "describe equal highs/lows, BSL/SSL, and any visible session high/low labels (e.g. London H at 215.50, Asia L at 213.80)",
  "killzone": "London|NY|Asia|none — based on visible candle activity, NOT session label boxes",
  "poi_levels": ["195.40", "194.80"]
}"""

MULTI_PROMPT = """You are an expert ICT (Inner Circle Trader) INTRADAY analyst performing multi-timeframe analysis for DAY TRADING.
You are looking for setups that complete within 1–2 trading sessions (20–150 pip moves).
Use the higher timeframes (1D/4H/1H) ONLY for directional bias. All actual entry signals come from the lower timeframes (15M/5M).
Focus on: current session OBs/FVGs, intraday CHoCH/BOS, same-day liquidity (session highs/lows, equal highs/lows).
Do NOT focus on multi-week swing levels or targets more than 150 pips away.
You have been given {n} trading chart screenshots. Treat them in order — Chart 1 is the highest timeframe, last is lowest.

""" + VISUAL_LEGEND + """

For EACH chart, carefully identify every visual element using the legend above and extract:

  asset, timeframe (identify or estimate: 1W/1D/4H/1H/15M/5M/1M),
  structure: string — "HH+HL bullish | LH+LL bearish | mixed" — include CHoCH/BOS direction and level if visible,
  fvgs: list of red boxes — [{{"direction": "bearish", "top": <price>, "bottom": <price>}}],
  large_fvgs: list of large blue boxes with midline — [{{"direction": "bullish|bearish", "top": <price>, "midline": <price>, "bottom": <price>}}],
  order_blocks: list of small blue boxes — [{{"direction": "bullish|bearish", "top": <price>, "bottom": <price>}}],
  liquidity: focus on INTRADAY liquidity — equal highs/lows within 50–100 pips, session high/low labels (e.g. "London H 215.50, Asia L 213.80"), BSL/SSL that could be swept this session,
  trend: bullish|bearish|ranging,
  killzone: London|NY|Asia|none — based on visible candle activity, NOT session label boxes,
  poi_levels: list of key price levels as strings

Then synthesise:
  htf_bias: overall directional bias from higher timeframes
  ltf_bias: short-term bias from lower timeframes
  combined_trend: multi-TF trend description
  confluence_notes: where timeframes agree or conflict, which OBs/FVGs line up across TFs

Return ONLY valid JSON — no extra text:
{{
  "asset": "...",
  "timeframe": "multi-timeframe",
  "htf_bias": "bullish|bearish|neutral",
  "ltf_bias": "bullish|bearish|neutral",
  "combined_trend": "...",
  "confluence_notes": "...",
  "structure": "...",
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
      "fvgs": [],
      "large_fvgs": [],
      "order_blocks": [],
      "liquidity": "...",
      "trend": "...",
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
