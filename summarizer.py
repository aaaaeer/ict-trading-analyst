from __future__ import annotations

import json
import re
import anthropic

# Ordered from most to least impactful for ICT analysis
_KEY_TFS   = ["1D", "4H", "1H", "15M", "5M"]   # must-have for a solid read
_EXTRA_TFS = ["1W", "1M"]                        # nice-to-have, rarely critical


def _detect_missing_timeframes(timeframes: list[str]) -> tuple[list[str], list[str]]:
    """Return (key_missing, extra_missing) timeframes not present in the analysis."""
    present = {tf.upper().strip() for tf in timeframes}
    key_missing   = [tf for tf in _KEY_TFS   if tf not in present]
    extra_missing = [tf for tf in _EXTRA_TFS if tf not in present]
    return key_missing, extra_missing


def generate_summary(
    chart_data: dict,
    market_data: dict,
    news: dict,
    calendar: dict,
    bias: dict,
    missing_sources: list[str] | None = None,
) -> dict:
    """
    Call Claude to produce a plain-English summary of the bias reasoning
    and concrete suggestions for improving the analysis.
    """
    charts = chart_data.get("charts", [])
    timeframes_present = (
        [c.get("timeframe", "unknown") for c in charts]
        if charts
        else [str(chart_data.get("timeframe", "unknown"))]
    )
    key_missing, extra_missing = _detect_missing_timeframes(timeframes_present)
    # Only flag timeframes as missing if the important ones aren't covered
    tf_coverage_ok = len(key_missing) == 0

    context = {
        "asset": market_data.get("ticker", "Unknown"),
        "current_price": market_data.get("current_price"),
        "pdh": market_data.get("pdh"),
        "pdl": market_data.get("pdl"),
        "timeframes_analysed": timeframes_present,
        "key_timeframes_missing": key_missing,
        "tf_coverage_complete": tf_coverage_ok,
        "htf_bias": chart_data.get("htf_bias", ""),
        "ltf_bias": chart_data.get("ltf_bias", ""),
        "structure": chart_data.get("structure", ""),
        "trend": chart_data.get("trend", ""),
        "liquidity": str(chart_data.get("liquidity", "")),
        "fvgs": chart_data.get("fvgs", []),
        "order_blocks": chart_data.get("order_blocks", []),
        "confluence_notes": chart_data.get("confluence_notes", ""),
        "news_sentiment": news.get("overall_sentiment", "neutral"),
        "news_score": news.get("sentiment_score", 0),
        "news_sources": news.get("source_count", 0),
        "high_impact_events": len(calendar.get("events", [])),
        "imminent_event": calendar.get("has_imminent", False),
        "bias_score": bias["score"],
        "direction": bias["direction"],
        "confidence": bias["confidence"],
        "score_breakdown": bias.get("breakdown", {}),
        "reasoning_points": bias.get("reasoning", []),
        "failed_sources": missing_sources or [],
    }

    prompt = f"""You are a concise ICT (Inner Circle Trader) analyst assistant reviewing an automated trading analysis.

Here is the full analysis data:
{json.dumps(context, indent=2)}

Your job is to write two things:

1. SUMMARY (3-5 sentences):
   - Explain in plain English WHY the bias is {bias['direction']} at a score of {bias['score']}/100.
   - Reference the specific ICT factors that mattered most (structure, liquidity draw, OB/FVG, PDH/PDL).
   - Mention if timeframes agree or conflict.
   - Note any risk (imminent news, low confluence, conflicting signals).
   - Be direct. No padding. Write as a professional analyst would in a trading journal.

2. SUGGESTIONS (2-5 bullet points):
   {"- Timeframe coverage is COMPLETE — do NOT suggest adding more timeframe charts. Focus suggestions on other areas." if tf_coverage_ok else f"- Key timeframes missing: {key_missing}. Suggest which one to add FIRST and why (e.g. 'Add 4H to confirm HTF bias before trusting the 15M entry')."}
   - Whether the current confluence is strong or weak, and what would make it stronger.
   - Any data quality issues (failed sources, no news, missing PDH/PDL).
   - Any specific price levels or conditions to watch before entering.
   - Any manual checks the trader should do before acting on this signal.
   Keep each suggestion to one sentence. Be specific, not generic. Do NOT pad with generic advice.

Return ONLY valid JSON — no markdown, no extra text:
{{
  "summary": "3-5 sentence explanation here.",
  "suggestions": [
    "Specific suggestion 1.",
    "Specific suggestion 2.",
    "Specific suggestion 3."
  ]
}}"""

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {
        "summary": text.strip(),
        "suggestions": [],
    }
