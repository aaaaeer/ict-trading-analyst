def generate_bias(
    chart_data: dict,
    market_data: dict,
    news: dict,
    calendar: dict,
) -> dict:
    """
    ICT intraday bias engine.

    Scoring model (total 100 pts):
      1. HTF Structure / Trend       25 pts  — HH+HL vs LH+LL on 1D/4H
      2. Draw on Liquidity            25 pts  — where is price being pulled?
      3. Premium / Discount           15 pts  — is price in the right area to trade?
      4. FVG / OB at entry level      15 pts  — is there a POI at the right place?
      5. LTF confirmation             10 pts  — CHoCH/BOS on 15M/5M?
      6. PDH / PDL context             5 pts  — where is price in yesterday's range?
      7. News sentiment                5 pts  — macro direction confirmation
      Events: confidence penalty only (not score)
    """

    score = 0.0
    reasoning: list[str] = []
    breakdown: dict[str, float] = {}

    is_multi_tf = bool(chart_data.get("charts"))
    current_p = market_data.get("current_price")

    # ── 1. HTF Structure (25 pts) ────────────────────────────────────────────
    htf = 0.0

    if is_multi_tf:
        htf_bias = str(chart_data.get("htf_bias", "")).lower()
        ltf_bias = str(chart_data.get("ltf_bias", "")).lower()

        if htf_bias == "bullish":
            htf = 25
        elif htf_bias == "bearish":
            htf = -25

        if htf_bias == ltf_bias and htf_bias in ("bullish", "bearish"):
            bonus = 5 if htf_bias == "bullish" else -5
            htf += bonus
            reasoning.append(f"Structure: HTF + LTF both {htf_bias.upper()} — full alignment")
        elif htf_bias and ltf_bias and htf_bias != ltf_bias and ltf_bias not in ("", "neutral"):
            reasoning.append(f"Structure: HTF {htf_bias.upper()} vs LTF {ltf_bias.upper()} — conflict, wait for LTF to align")
        else:
            reasoning.append(f"Structure: HTF {htf_bias.upper() or 'unclear'}, LTF {ltf_bias.upper() or 'unclear'}")

        notes = chart_data.get("confluence_notes", "")
        if notes:
            reasoning.append(f"Confluence: {notes}")
    else:
        structure = str(chart_data.get("structure", "")).lower()
        trend = str(chart_data.get("trend", "")).lower()

        # Count bullish vs bearish keywords — more reliable than strict AND NOT
        bull_kw = sum(1 for k in ("hh", "hl", "bullish bos", "bullish choch") if k in structure)
        bear_kw = sum(1 for k in ("lh", "ll", "bearish bos", "bearish choch") if k in structure)

        if bull_kw > bear_kw:
            htf = 25
            reasoning.append("Structure: Bullish (HH+HL, bullish BOS/CHoCH)")
        elif bear_kw > bull_kw:
            htf = -25
            reasoning.append("Structure: Bearish (LH+LL, bearish BOS/CHoCH)")
        elif "bullish" in trend:
            htf = 12
            reasoning.append("Structure: Mixed — trend leans bullish")
        elif "bearish" in trend:
            htf = -12
            reasoning.append("Structure: Mixed — trend leans bearish")
        else:
            reasoning.append("Structure: No clear bias from structure/trend")

    score += htf
    breakdown["htf_structure"] = htf

    # ── 2. Draw on Liquidity (25 pts) ────────────────────────────────────────
    # Use the explicit draw_on_liquidity field first, then fall back to liquidity text
    dol_field = str(chart_data.get("draw_on_liquidity", "")).lower()
    liq_field  = str(chart_data.get("liquidity", "")).lower()
    dol_text   = dol_field or liq_field
    liq_score  = 0.0

    _swept_kw = ("swept", "taken", "grabbed", "cleared", "raided", "already ran",
                 "ran through", "ran above", "ran below", "price swept")

    # BSL = buy-side liquidity (equal highs, session highs) — draws price UP
    # SSL = sell-side liquidity (equal lows, session lows)  — draws price DOWN
    bsl_present = any(k in dol_text for k in ("bsl", "buy side", "buy-side", "equal high", "equal highs", "buyside"))
    ssl_present = any(k in dol_text for k in ("ssl", "sell side", "sell-side", "equal low", "equal lows", "sellside"))
    bsl_swept   = bsl_present and any(k in dol_text for k in _swept_kw)
    ssl_swept   = ssl_present and any(k in dol_text for k in _swept_kw)

    if bsl_swept:
        liq_score = -20
        reasoning.append("Draw on Liquidity: BSL was swept → bearish reversal setup (liquidity grab above, now drops)")
    elif ssl_swept:
        liq_score = 20
        reasoning.append("Draw on Liquidity: SSL was swept → bullish reversal setup (liquidity grab below, now pumps)")
    elif bsl_present:
        liq_score = 25
        reasoning.append(f"Draw on Liquidity: BSL / equal highs above → price drawing UP (bullish)")
    elif ssl_present:
        liq_score = -25
        reasoning.append(f"Draw on Liquidity: SSL / equal lows below → price drawing DOWN (bearish)")
    else:
        reasoning.append("Draw on Liquidity: No clear liquidity target identified from chart")

    score += liq_score
    breakdown["draw_on_liquidity"] = liq_score

    # ── 3. Premium / Discount (15 pts) ───────────────────────────────────────
    pd_field = str(chart_data.get("premium_discount", "")).lower()
    # Fallback: use PDH/PDL midpoint if field not available
    if not pd_field and current_p:
        pdh = market_data.get("pdh")
        pdl = market_data.get("pdl")
        if pdh and pdl:
            mid = (pdh + pdl) / 2
            pd_field = "premium" if current_p > mid else "discount"

    pd_score = 0.0
    # ICT rule: buy in discount, sell in premium
    # If bearish draw (SSL): premium = good (price is high, draws to SSL below)
    # If bullish draw (BSL): discount = good (price is low, draws to BSL above)
    if "discount" in pd_field:
        pd_score = 15   # price is cheap — bullish POI area
        reasoning.append("Premium/Discount: Price in DISCOUNT — ideal area for buy setups")
    elif "premium" in pd_field:
        pd_score = -15  # price is expensive — bearish POI area
        reasoning.append("Premium/Discount: Price in PREMIUM — ideal area for sell setups")
    else:
        reasoning.append("Premium/Discount: Price at equilibrium — less clear directional bias")

    score += pd_score
    breakdown["premium_discount"] = pd_score

    # ── 4. FVG / OB at entry level (15 pts) ─────────────────────────────────
    def _score_zones(items, label, weight) -> tuple[float, list[str]]:
        bull, bear, reasons = 0, 0, []
        if not isinstance(items, list):
            s = str(items).lower()
            if "bullish" in s: bull += weight
            if "bearish" in s: bear += weight
            return bull - bear, []
        for item in items:
            if not isinstance(item, dict):
                continue
            d = str(item.get("direction", "")).lower()
            top = item.get("top")
            bot = item.get("bottom")
            if not d or not top or not bot:
                continue
            if "bearish" in d or "supply" in d:
                if current_p and current_p <= top * 1.005:
                    bear += weight
                    reasons.append(f"Bearish {label} {bot:.3f}–{top:.3f} (supply above price)")
            elif "bullish" in d or "demand" in d:
                if current_p and current_p >= bot * 0.995:
                    bull += weight
                    reasons.append(f"Bullish {label} {bot:.3f}–{top:.3f} (demand below price)")
        return bull - bear, reasons

    s1, r1 = _score_zones(chart_data.get("large_fvgs", []),   "large FVG", 15)
    s2, r2 = _score_zones(chart_data.get("fvgs", []),          "FVG",       10)
    s3, r3 = _score_zones(chart_data.get("order_blocks", []),  "OB",        10)
    ob_reasons = r1 + r2 + r3
    ob_score   = max(-15, min(15, s1 + s2 + s3))

    for r in ob_reasons[:3]:
        reasoning.append(f"POI: {r}")
    if not ob_reasons:
        reasoning.append("POI: No price-relevant FVG/OB found near current price")

    score += ob_score
    breakdown["fvg_ob"] = ob_score

    # ── 5. LTF Confirmation (10 pts) ─────────────────────────────────────────
    ltf_score = 0.0
    ltf_confirmed = False

    # Look for CHoCH/BOS confirmation on the lowest timeframe chart
    charts = chart_data.get("charts", [])
    ltf_charts = [c for c in charts if str(c.get("timeframe", "")).upper() in ("5M", "15M", "1M")]
    if not ltf_charts and charts:
        ltf_charts = [charts[-1]]  # use lowest TF available

    for c in ltf_charts:
        struct = str(c.get("structure", "")).lower()
        disp   = str(c.get("displacement", "")).lower()
        if any(k in struct for k in ("choch", "bos", "change of character", "break of structure")):
            if "bullish" in struct or "bullish" in disp:
                ltf_score = 10
                ltf_confirmed = True
                reasoning.append(f"LTF ({c.get('timeframe','?')}): Bullish CHoCH/BOS confirmed — entry signal")
            elif "bearish" in struct or "bearish" in disp:
                ltf_score = -10
                ltf_confirmed = True
                reasoning.append(f"LTF ({c.get('timeframe','?')}): Bearish CHoCH/BOS confirmed — entry signal")

    if not ltf_confirmed:
        displacement = str(chart_data.get("displacement", "")).lower()
        if "bullish" in displacement:
            ltf_score = 5
            reasoning.append("LTF: Bullish displacement visible but no CHoCH yet — wait for confirmation")
        elif "bearish" in displacement:
            ltf_score = -5
            reasoning.append("LTF: Bearish displacement visible but no CHoCH yet — wait for confirmation")
        else:
            reasoning.append("LTF: No CHoCH/BOS or displacement detected — entry not yet confirmed")

    score += ltf_score
    breakdown["ltf_confirmation"] = ltf_score

    # ── 6. PDH / PDL context (5 pts) ─────────────────────────────────────────
    pdh = market_data.get("pdh")
    pdl = market_data.get("pdl")
    pdh_score = 0.0

    if current_p and pdh and pdl:
        mid = (pdh + pdl) / 2
        if current_p > pdh:
            pdh_score = 5
            reasoning.append(f"PDH/PDL: Above PDH {pdh:.3f} — bullish breakout context")
        elif current_p < pdl:
            pdh_score = -5
            reasoning.append(f"PDH/PDL: Below PDL {pdl:.3f} — bearish breakdown context")
        elif current_p >= mid:
            pdh_score = 2
            reasoning.append(f"PDH/PDL: Upper half of range ({pdl:.3f}–{pdh:.3f})")
        else:
            pdh_score = -2
            reasoning.append(f"PDH/PDL: Lower half of range ({pdl:.3f}–{pdh:.3f})")
    else:
        reasoning.append("PDH/PDL: Data unavailable")

    score += pdh_score
    breakdown["pdh_pdl"] = pdh_score

    # ── 7. News (5 pts) ──────────────────────────────────────────────────────
    news_raw   = news.get("sentiment_score", 0.0)
    news_score = round(news_raw * 5, 1)
    reasoning.append(
        f"News: {news.get('overall_sentiment','neutral').capitalize()} "
        f"({news.get('source_count', 0)} sources, score {news_raw:+.2f})"
    )
    score += news_score
    breakdown["news"] = news_score

    # ── Events: confidence penalty ────────────────────────────────────────────
    has_imminent  = calendar.get("has_imminent", False)
    event_count   = len(calendar.get("events", []))
    conf_penalty  = 0

    if has_imminent:
        conf_penalty = 30
        reasoning.append("⚠ Imminent high-impact news event — confidence severely reduced, consider waiting")
    elif event_count > 0:
        conf_penalty = 10
        reasoning.append(f"Calendar: {event_count} high-impact event(s) today — moderate caution")
    else:
        reasoning.append("Calendar: No high-impact events flagged")

    # ── Final ─────────────────────────────────────────────────────────────────
    final_score = int(max(-100, min(100, round(score))))

    if final_score >= 35:
        direction = "BULLISH"
    elif final_score <= -35:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    abs_score = abs(final_score)
    if conf_penalty >= 30:
        confidence = "LOW (imminent event)"
    elif conf_penalty > 0:
        confidence = "MEDIUM (events today)"
    elif abs_score >= 65:
        confidence = "HIGH"
    elif abs_score >= 35:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "score":              final_score,
        "direction":          direction,
        "confidence":         confidence,
        "reasoning":          reasoning,
        "breakdown":          breakdown,
        "confidence_penalty": conf_penalty,
    }
