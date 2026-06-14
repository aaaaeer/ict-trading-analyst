import json
import re
import anthropic


def generate_trade_setup(
    chart_data: dict,
    market_data: dict,
    bias: dict,
) -> dict:
    """
    Use Claude to generate ICT-based entry, SL, and TP price levels.
    Returns a dict with all levels and explanatory notes.
    """
    direction = bias.get("direction", "NEUTRAL")

    if direction == "NEUTRAL":
        return {
            "direction": "NEUTRAL",
            "entry": None,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "rr1": None,
            "rr2": None,
            "notes": "No trade setup — bias is neutral. Wait for a clearer directional signal.",
        }

    context = {
        "asset": market_data.get("ticker"),
        "direction": direction,
        "bias_score": bias.get("score"),
        "current_price": market_data.get("current_price"),
        "pdh": market_data.get("pdh"),
        "pdl": market_data.get("pdl"),
        "pdc": market_data.get("pdc"),
        "asian_high": market_data.get("asian_high"),
        "asian_low": market_data.get("asian_low"),
        "london_high": market_data.get("london_high"),
        "london_low": market_data.get("london_low"),
        "structure": chart_data.get("structure"),
        "trend": chart_data.get("trend"),
        "fvgs": chart_data.get("fvgs", []),
        "order_blocks": chart_data.get("order_blocks", []),
        "liquidity": chart_data.get("liquidity"),
        "poi_levels": chart_data.get("poi_levels", []),
        "htf_bias": chart_data.get("htf_bias"),
        "ltf_bias": chart_data.get("ltf_bias"),
        "confluence_notes": chart_data.get("confluence_notes", ""),
        "per_chart": chart_data.get("charts", []),
    }

    prompt = f"""You are a precise ICT (Inner Circle Trader) trade planner. Generate a complete trade setup from this analysis.

Analysis data:
{json.dumps(context, indent=2)}

Direction: {direction}
Current price: {market_data.get("current_price")}

Generate the following — all values must be specific numbers, not ranges:

ENTRY
- Optimal entry price based on the nearest ICT POI (bullish/bearish OB or FVG)
- If price is already inside the POI, use current price as entry

STOP LOSS
- Place beyond the structural invalidation point
- Bullish: below the OB lower edge, or below the nearest swing low
- Bearish: above the OB upper edge, or above the nearest swing high
- Must give the trade room to breathe — not too tight

TAKE PROFIT LEVELS (3 targets)
- TP1: 1:1 to 1:5 RR — nearest liquidity or short-term imbalance fill
- TP2: Key level — PDH/PDL, Asian high/low, major swing or higher-timeframe liquidity pool

NOTES
- entry_notes: 1 sentence on why this entry (which OB/FVG/POI)
- sl_notes: 1 sentence on why this SL (what it's protecting against)
- tp_notes: 1 sentence on the TP logic (what liquidity is being targeted)
- invalidation: the exact price at which this thesis is wrong

Rules:
- Bullish: entry <= current_price, SL < entry, TP1/2/3 > entry (ascending)
- Bearish: entry >= current_price, SL > entry, TP1/2/3 < entry (descending)
- Calculate RR as round((entry - tp) / (entry - sl), 2) for bullish, reversed for bearish
- For forex pairs price decimal places should match the pair (JPY pairs: 3 dp, others: 5 dp)

Return ONLY valid JSON, no other text:
{{
  "entry": <number>,
  "sl": <number>,
  "tp1": <number>,
  "tp2": <number>,
  "rr1": <number>,
  "rr2": <number>,
  "invalidation": "<price level as string>",
  "entry_notes": "<1 sentence>",
  "sl_notes": "<1 sentence>",
  "tp_notes": "<1 sentence>"
}}"""

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            data["direction"] = direction
            return data
        except json.JSONDecodeError:
            pass

    return {
        "direction": direction,
        "entry": market_data.get("current_price"),
        "sl": None,
        "tp1": None,
        "tp2": None,
        "rr1": None,
        "rr2": None,
        "error": "Could not parse trade setup from Claude response",
    }
