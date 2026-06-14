import yfinance as yf
from datetime import datetime, timezone


def get_market_data(ticker: str) -> dict:
    """Fetch live OHLCV data, session ranges, and key price levels."""
    stock = yf.Ticker(ticker)

    hist_1m = stock.history(period="1d", interval="1m")
    hist_15m = stock.history(period="5d", interval="15m")
    hist_daily = stock.history(period="5d", interval="1d")

    current_price = float(hist_1m["Close"].iloc[-1]) if not hist_1m.empty else None

    pdh = pdl = pdc = None
    if len(hist_daily) >= 2:
        prev = hist_daily.iloc[-2]
        pdh = float(prev["High"])
        pdl = float(prev["Low"])
        pdc = float(prev["Close"])

    asian_high = asian_low = None
    london_high = london_low = None

    if not hist_1m.empty:
        df = hist_1m.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        now_utc = datetime.now(timezone.utc)
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

        asian_mask = (df.index >= day_start) & (df.index < day_start.replace(hour=8))
        asian_session = df[asian_mask]
        if not asian_session.empty:
            asian_high = float(asian_session["High"].max())
            asian_low = float(asian_session["Low"].min())

        london_mask = (df.index >= day_start.replace(hour=7)) & (df.index < day_start.replace(hour=8))
        london_range = df[london_mask]
        if not london_range.empty:
            london_high = float(london_range["High"].max())
            london_low = float(london_range["Low"].min())

    # Simple intraday trend from last 4 x 15m candles
    trend_direction = "unknown"
    if not hist_15m.empty and len(hist_15m) >= 4:
        recent = hist_15m["Close"].iloc[-4:]
        if recent.iloc[-1] > recent.iloc[0]:
            trend_direction = "bullish"
        elif recent.iloc[-1] < recent.iloc[0]:
            trend_direction = "bearish"
        else:
            trend_direction = "neutral"

    return {
        "ticker": ticker,
        "current_price": current_price,
        "pdh": pdh,
        "pdl": pdl,
        "pdc": pdc,
        "asian_high": asian_high,
        "asian_low": asian_low,
        "london_high": london_high,
        "london_low": london_low,
        "intraday_trend": trend_direction,
        "data_available": not hist_1m.empty,
    }
