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

SINGLE_PROMPT = """You are an expert ICT (Inner Circle Trader) technical analyst.
Analyse this trading chart screenshot and extract:
- Asset and timeframe (if visible)
- Current price action structure: HH, HL, LH, LL (market structure)
- Any visible FVGs (Fair Value Gaps) and their direction
- Order blocks (OB): bullish or bearish, and approximate price levels
- Liquidity pools: buy-side or sell-side (equal highs/lows, swing points)
- Current trend on this timeframe
- Any visible killzone (London, NY, Asia)
- Any obvious POI (Point of Interest) price levels
Return your analysis as JSON with keys:
asset, timeframe, structure, fvgs, order_blocks, liquidity, trend, killzone, poi_levels"""

MULTI_PROMPT = """You are an expert ICT (Inner Circle Trader) technical analyst performing multi-timeframe analysis.
You have been given {n} trading chart screenshots. They represent different timeframes of the same (or related) asset.
Treat them in the order provided — Chart 1 is typically the highest timeframe, the last chart the lowest.

For EACH chart extract:
  asset, timeframe (identify or estimate: 1W/1D/4H/1H/15M/5M/1M),
  structure (HH+HL=bullish | LH+LL=bearish | mixed),
  fvgs (list: {{"direction": "bullish|bearish", "level": <price or null>}}),
  order_blocks (list: {{"type": "bullish|bearish", "level": <price or null>}}),
  liquidity ("buy-side above <level>" | "sell-side below <level>" | description),
  trend (bullish|bearish|ranging),
  killzone (London|NY|Asia|none),
  poi_levels (list of notable price levels as strings)

Then synthesise across all charts:
  htf_bias: directional bias from the higher timeframes
  ltf_bias: short-term bias from the lower timeframes
  combined_trend: overall multi-TF trend description
  confluence_notes: where timeframes agree or conflict

Return ONLY valid JSON — no extra text — matching this structure exactly:
{{
  "asset": "...",
  "timeframe": "multi-timeframe",
  "htf_bias": "bullish|bearish|neutral",
  "ltf_bias": "bullish|bearish|neutral",
  "combined_trend": "...",
  "confluence_notes": "...",
  "structure": "<from HTF chart>",
  "fvgs": [],
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
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
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
        max_tokens=2048,
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
