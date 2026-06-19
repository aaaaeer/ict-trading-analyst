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

        bullish_structure = any(k in structure for k in ("hh", "hl", "bullish", "choch bullish", "bos bullish")) \
            and not any(k in structure for k in ("lh", "ll", "bearish"))
        bearish_structure = any(k in structure for k in ("lh", "ll", "bearish", "choch bearish", "bos bearish")) \
            and not any(k in structure for k in ("hh", "hl", "bullish"))

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

    # Swept keywords: BSL/SSL that has already been taken is a reversal signal, not a draw
    _swept = ("swept", "taken", "grabbed", "cleared", "raided", "already ran", "swept above",
              "grabbed above", "price swept", "ran above", "ran through")
    bsl_present = "buy" in liq or "bsl" in liq
    ssl_present = "sell" in liq or "ssl" in liq
    bsl_swept = bsl_present and any(k in liq for k in _swept)
    ssl_swept = ssl_present and any(k in liq for k in _swept)

    if bsl_swept:
        liq_score = -20
        reasoning.append("Liquidity: BSL already swept → bearish reversal signal (liquidity grab complete)")
    elif bsl_present:
        liq_score = 25
        reasoning.append("Liquidity: Buy-side liquidity above price → bullish draw")
    elif ssl_swept:
        liq_score = 20
        reasoning.append("Liquidity: SSL already swept → bullish reversal signal (liquidity grab complete)")
    elif ssl_present:
        liq_score = -25
        reasoning.append("Liquidity: Sell-side liquidity below price → bearish draw")
    else:
        reasoning.append("Liquidity: No clear liquidity draw identified")

    score += liq_score
    breakdown["liquidity"] = liq_score

    # ── Step 3: FVG + Order Block Confluence (20%) ───────────────────────────
    # Check actual price levels: only score zones price is AT or APPROACHING
    # Bearish zones above price = resistance; bullish zones below price = support
    current_p = market_data.get("current_price")

    def _score_zones(items, label, weight) -> tuple[float, list[str]]:
        """Return (net_score, reasons) for a list of FVG/OB dicts."""
        bull = 0
        bear = 0
        reasons = []
        if not isinstance(items, list):
            # Fallback: string check
            s = str(items).lower()
            if "bearish" in s:
                bear += weight
            if "bullish" in s:
                bull += weight
            return bull - bear, []
        for item in items:
            if not isinstance(item, dict):
                continue
            d = str(item.get("direction", "")).lower()
            top = item.get("top")
            bot = item.get("bottom")
            if not d:
                continue
            if d == "bearish":
                # Bearish zone matters if price is below or inside it (approaching resistance)
                if top and current_p and current_p <= top * 1.005:
                    bear += weight
                    reasons.append(f"Bearish {label} {bot}–{top} → price approaching/inside resistance")
            elif d == "bullish":
                # Bullish zone matters if price is above or inside it (at demand)
                if bot and current_p and current_p >= bot * 0.995:
                    bull += weight
                    reasons.append(f"Bullish {label} {bot}–{top} → price at/above demand zone")
        return bull - bear, reasons

    ob_score = 0.0
    ob_reasons: list[str] = []

    s1, r1 = _score_zones(chart_data.get("large_fvgs", []), "large FVG", 20)
    s2, r2 = _score_zones(chart_data.get("fvgs", []),       "FVG",       15)
    s3, r3 = _score_zones(chart_data.get("order_blocks", []), "OB",       10)
    ob_reasons = r1 + r2 + r3

    ob_score = max(-20, min(20, s1 + s2 + s3))

    if ob_reasons:
        for r in ob_reasons[:3]:  # cap to 3 lines
            reasoning.append(f"POI: {r}")
    elif ob_score > 0:
        reasoning.append("POI: Bullish FVG/OB confluence detected")
    elif ob_score < 0:
        reasoning.append("POI: Bearish FVG/OB confluence detected")
    else:
        reasoning.append("POI: No price-relevant FVG/OB signals at current level")

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
