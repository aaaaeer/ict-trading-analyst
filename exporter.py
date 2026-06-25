import json
from datetime import datetime, timezone
from pathlib import Path


def export_analysis(
    chart_data: dict,
    market_data: dict,
    news: dict,
    calendar: dict,
    bias: dict,
    summary: dict,
    output_path: str,
    trade: dict | None = None,
) -> str:
    """Serialize the full analysis to a JSON file. Returns the path written."""

    charts = chart_data.get("charts", [])
    timeframes = (
        [c.get("timeframe", "unknown") for c in charts]
        if charts
        else [str(chart_data.get("timeframe", "unknown"))]
    )

    payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "asset": market_data.get("ticker", chart_data.get("asset", "unknown")),
            "charts_analysed": len(charts) if charts else 1,
            "timeframes": timeframes,
        },
        "verdict": {
            "direction": bias.get("direction"),
            "score": bias.get("score"),
            "confidence": bias.get("confidence"),
        },
        "market": {
            "current_price": market_data.get("current_price"),
            "pdh": market_data.get("pdh"),
            "pdl": market_data.get("pdl"),
            "pdc": market_data.get("pdc"),
            "asian_high": market_data.get("asian_high"),
            "asian_low": market_data.get("asian_low"),
            "london_high": market_data.get("london_high"),
            "london_low": market_data.get("london_low"),
            "intraday_trend": market_data.get("intraday_trend"),
        },
        "chart_analysis": {
            "timeframe": chart_data.get("timeframe"),
            "htf_bias": chart_data.get("htf_bias"),
            "ltf_bias": chart_data.get("ltf_bias"),
            "structure": chart_data.get("structure"),
            "trend": chart_data.get("trend"),
            "premium_discount": chart_data.get("premium_discount"),
            "draw_on_liquidity": chart_data.get("draw_on_liquidity"),
            "displacement": chart_data.get("displacement"),
            "liquidity": chart_data.get("liquidity"),
            "fvgs": chart_data.get("fvgs", []),
            "large_fvgs": chart_data.get("large_fvgs", []),
            "order_blocks": chart_data.get("order_blocks", []),
            "killzone": chart_data.get("killzone"),
            "poi_levels": chart_data.get("poi_levels", []),
            "confluence_notes": chart_data.get("confluence_notes"),
            "charts": charts,
        },
        "news": {
            "overall_sentiment": news.get("overall_sentiment"),
            "sentiment_score": news.get("sentiment_score"),
            "bull_count": news.get("bull_count"),
            "bear_count": news.get("bear_count"),
            "source_count": news.get("source_count"),
            "headlines": news.get("headlines", []),
        },
        "calendar": {
            "events": calendar.get("events", []),
            "has_imminent": calendar.get("has_imminent"),
            "imminent_count": calendar.get("imminent_count", 0),
        },
        "scoring": {
            "total_score": bias.get("score"),
            "breakdown": bias.get("breakdown", {}),
            "reasoning": bias.get("reasoning", []),
        },
        "summary": {
            "text": summary.get("summary", ""),
            "suggestions": summary.get("suggestions", []),
        },
        "trade_setup": trade or {},
    }

    path = Path(output_path)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path.resolve())
