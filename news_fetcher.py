import requests
import feedparser

BULLISH_KEYWORDS = [
    "rally", "surge", "rise", "gain", "up", "bull", "growth", "positive",
    "strong", "beat", "exceed", "record high", "optimism", "recovery",
    "hawkish", "buy", "upside", "outperform",
]
BEARISH_KEYWORDS = [
    "fall", "drop", "decline", "down", "bear", "loss", "negative", "weak",
    "miss", "below", "record low", "pessimism", "dovish", "recession",
    "crisis", "risk", "concern", "sell", "downside", "slowdown",
]

ASSET_TERMS: dict[str, list[str]] = {
    "EURUSD=X": ["EUR/USD", "Euro", "ECB"],
    "GBPUSD=X": ["GBP/USD", "Pound", "Bank of England"],
    "USDJPY=X": ["USD/JPY", "Yen", "Bank of Japan"],
    "GBPJPY=X": ["GBP/JPY", "Pound Yen", "Bank of England", "Bank of Japan"],
    "EURJPY=X": ["EUR/JPY", "Euro Yen", "ECB", "Bank of Japan"],
    "AUDUSD=X": ["AUD/USD", "Australian Dollar", "RBA"],
    "USDCAD=X": ["USD/CAD", "Canadian Dollar", "Bank of Canada"],
    "USDCHF=X": ["USD/CHF", "Swiss Franc", "SNB"],
    "BTC-USD":  ["Bitcoin", "BTC", "crypto"],
    "ETH-USD":  ["Ethereum", "ETH", "crypto"],
    "ES=F":     ["S&P 500", "SPX", "equities"],
    "NQ=F":     ["Nasdaq", "NQ", "tech stocks"],
    "GC=F":     ["Gold", "XAU"],
    "CL=F":     ["Oil", "WTI", "crude"],
}

RSS_FEEDS = [
    "https://www.fxstreet.com/rss/news",
    "https://www.dailyfx.com/feeds/all",
    "https://feeds.reuters.com/reuters/businessNews",
]


def _search_terms(asset: str) -> list[str]:
    if asset in ASSET_TERMS:
        return ASSET_TERMS[asset]
    base = asset.replace("=X", "").replace("-USD", "").replace("=F", "")
    return [base]


def _classify(text: str) -> str:
    text_lower = text.lower()
    bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def fetch_news(asset: str, api_key: str | None = None) -> dict:
    headlines: list[dict] = []

    if api_key:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": _search_terms(asset)[0],
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "language": "en",
                    "apiKey": api_key,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                for article in resp.json().get("articles", [])[:10]:
                    title = article.get("title", "")
                    headlines.append({
                        "title": title,
                        "source": article.get("source", {}).get("name", "Unknown"),
                        "sentiment": _classify(title),
                        "url": article.get("url", ""),
                    })
        except Exception as e:
            print(f"[news] NewsAPI error: {e}")

    if not headlines:
        terms = _search_terms(asset)
        for feed_url in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                if not feed.entries:
                    continue
                for entry in feed.entries[:40]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    combined = f"{title} {summary}"
                    # Match any search term (case-insensitive)
                    if any(t.lower() in combined.lower() for t in terms):
                        headlines.append({
                            "title": title,
                            "source": feed.feed.get("title", "RSS"),
                            "sentiment": _classify(combined),
                            "url": entry.get("link", ""),
                        })
                    if len(headlines) >= 10:
                        break
                if headlines:
                    break  # Stop after first feed that returns results
            except Exception as e:
                print(f"[news] RSS error ({feed_url}): {e}")


    sentiments = [h["sentiment"] for h in headlines]
    bull_count = sentiments.count("bullish")
    bear_count = sentiments.count("bearish")
    total = max(len(sentiments), 1)

    if bull_count > bear_count:
        overall = "bullish"
        score = bull_count / total
    elif bear_count > bull_count:
        overall = "bearish"
        score = -bear_count / total
    else:
        overall = "neutral"
        score = 0.0

    return {
        "headlines": headlines,
        "overall_sentiment": overall,
        "sentiment_score": round(score, 2),
        "bull_count": bull_count,
        "bear_count": bear_count,
        "source_count": len(headlines),
    }
