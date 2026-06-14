def generate_bias(
    chart_data: dict,
    market_data: dict,
    news: dict,
    calendar: dict,
) -> dict:
    """Apply ICT concepts to produce a directional bias score (-100 to +100)."""

    score = 0.0
    reasoning: list[str] = []
    breakdown: dict[str, float] = {}

    is_multi_tf = bool(chart_data.get("charts"))

    # ── Step 1: Higher Timeframe Bias (30%) ──────────────────────────────────
    # For multi-TF: use htf_bias directly; for single: derive from structure/trend
    htf = 0.0

    if is_multi_tf:
        htf_bias = str(chart_data.get("htf_bias", "")).lower()
        ltf_bias = str(chart_data.get("ltf_bias", "")).lower()

        if htf_bias == "bullish":
            htf = 30
        elif htf_bias == "bearish":
            htf = -30

        # LTF alignment bonus / penalty (5 pts extra weight)
        if htf_bias == ltf_bias and htf_bias in ("bullish", "bearish"):
            htf += 5 if htf_bias == "bullish" else -5
            reasoning.append(
                f"Multi-TF: HTF and LTF both {htf_bias.upper()} — strong confluence"
            )
        elif htf_bias and ltf_bias and htf_bias != ltf_bias and ltf_bias != "neutral":
            reasoning.append(
                f"Multi-TF: HTF {htf_bias.upper()} vs LTF {ltf_bias.upper()} — conflicting timeframes, caution"
            )
        else:
            reasoning.append(f"Multi-TF: HTF bias = {htf_bias.upper()}, LTF bias = {ltf_bias.upper()}")

        notes = chart_data.get("confluence_notes", "")
        if notes:
            reasoning.append(f"Confluence: {notes}")
    else:
        structure = str(chart_data.get("structure", "")).lower()
        trend = str(chart_data.get("trend", "")).lower()

        bullish_structure = any(k in structure for k in ("hh", "hl")) and not any(k in structure for k in ("lh", "ll"))
        bearish_structure = any(k in structure for k in ("lh", "ll")) and not any(k in structure for k in ("hh", "hl"))

        if bullish_structure or ("bullish" in structure and "bearish" not in structure):
            htf = 30
            reasoning.append("Structure: Bullish (HH + HL pattern)")
        elif bearish_structure or ("bearish" in structure and "bullish" not in structure):
            htf = -30
            reasoning.append("Structure: Bearish (LH + LL pattern)")
        elif "bullish" in trend:
            htf = 15
            reasoning.append("Structure: Mixed — trend bias bullish")
        elif "bearish" in trend:
            htf = -15
            reasoning.append("Structure: Mixed — trend bias bearish")
        else:
            reasoning.append("Structure: Unclear — no strong directional bias")

    score += htf
    breakdown["htf_structure"] = htf

    # ── Step 2: Liquidity (25%) ──────────────────────────────────────────────
    liq = str(chart_data.get("liquidity", "")).lower()
    liq_score = 0.0

    if "buy" in liq or "bsl" in liq:
        liq_score = 25
        reasoning.append("Liquidity: Buy-side liquidity above price → bullish draw")
    elif "sell" in liq or "ssl" in liq:
        liq_score = -25
        reasoning.append("Liquidity: Sell-side liquidity below price → bearish draw")
    else:
        reasoning.append("Liquidity: No clear liquidity draw identified")

    score += liq_score
    breakdown["liquidity"] = liq_score

    # ── Step 3: FVG + Order Block Confluence (20%) ───────────────────────────
    fvg_str = str(chart_data.get("fvgs", "")).lower()
    ob_str = str(chart_data.get("order_blocks", "")).lower()
    ob_score = 0.0

    if "bullish" in fvg_str or "bullish" in ob_str:
        ob_score += 20
        reasoning.append("POI: Bullish FVG/OB → confluence with upside")
    if "bearish" in fvg_str or "bearish" in ob_str:
        ob_score -= 20
        reasoning.append("POI: Bearish FVG/OB → confluence with downside")
    if ob_score == 0:
        reasoning.append("POI: No FVG/OB signals detected")

    score += ob_score
    breakdown["fvg_ob"] = ob_score

    # ── Step 4: PDH / PDL (10%) ──────────────────────────────────────────────
    current = market_data.get("current_price")
    pdh = market_data.get("pdh")
    pdl = market_data.get("pdl")
    pdh_score = 0.0

    if current and pdh and pdl:
        if current > pdh:
            pdh_score = 10
            reasoning.append(f"PDH/PDL: Price ({current:.5f}) above PDH ({pdh:.5f}) → bullish momentum")
        elif current < pdl:
            pdh_score = -10
            reasoning.append(f"PDH/PDL: Price ({current:.5f}) below PDL ({pdl:.5f}) → bearish continuation")
        else:
            mid = (pdh + pdl) / 2
            if current >= mid:
                pdh_score = 5
                reasoning.append(f"PDH/PDL: Price in upper half of range ({pdh:.5f}–{pdl:.5f})")
            else:
                pdh_score = -5
                reasoning.append(f"PDH/PDL: Price in lower half of range ({pdh:.5f}–{pdl:.5f})")
    else:
        reasoning.append("PDH/PDL: Price data unavailable")

    score += pdh_score
    breakdown["pdh_pdl"] = pdh_score

    # ── Step 5: News Sentiment (10%) ─────────────────────────────────────────
    news_sentiment = news.get("overall_sentiment", "neutral")
    news_raw = news.get("sentiment_score", 0.0)
    news_score = round(news_raw * 10, 1)

    reasoning.append(
        f"News: {news_sentiment.capitalize()} sentiment "
        f"({news.get('source_count', 0)} sources, score {news_raw:+.2f})"
    )
    score += news_score
    breakdown["news"] = news_score

    # ── Step 6: High-Impact Events (risk flag) ────────────────────────────────
    has_imminent = calendar.get("has_imminent", False)
    event_count = len(calendar.get("events", []))
    confidence_penalty = 0

    if has_imminent:
        confidence_penalty = 30
        reasoning.append("⚠ Imminent high-impact event → confidence significantly reduced")
    elif event_count > 0:
        confidence_penalty = 10
        reasoning.append(f"Calendar: {event_count} high-impact event(s) today → moderate caution")
    else:
        reasoning.append("Calendar: No high-impact events flagged")

    # ── Final score ───────────────────────────────────────────────────────────
    final_score = int(max(-100, min(100, round(score))))

    if final_score >= 40:
        direction = "BULLISH"
    elif final_score <= -40:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    abs_score = abs(final_score)
    if confidence_penalty >= 30:
        confidence = "LOW (imminent event)"
    elif confidence_penalty > 0:
        confidence = "MEDIUM (events today)"
    elif abs_score >= 70:
        confidence = "HIGH"
    elif abs_score >= 40:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "score": final_score,
        "direction": direction,
        "confidence": confidence,
        "reasoning": reasoning,
        "breakdown": breakdown,
        "confidence_penalty": confidence_penalty,
    }
