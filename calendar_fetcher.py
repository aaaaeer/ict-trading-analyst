import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

FOREX_FACTORY_RSS = "https://www.forexfactory.com/ff_calendar_thisweek.xml"

MAJOR_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}


def _extract_currency(title: str) -> str:
    for curr in MAJOR_CURRENCIES:
        if curr in title:
            return curr
    return "Unknown"


def _infer_impact(title: str, description: str) -> str:
    combined = f"{title} {description}".lower()
    high_terms = ["nfp", "cpi", "gdp", "fomc", "rate decision", "interest rate",
                  "non-farm", "inflation", "unemployment", "fed ", "ecb ", "boe "]
    if any(t in combined for t in high_terms):
        return "high"
    return "medium"


def fetch_calendar(target_currencies: list[str] | None = None) -> dict:
    """Fetch this week's economic calendar and filter for high-impact events."""
    events: list[dict] = []
    errors: list[str] = []

    now_utc = datetime.now(timezone.utc)
    two_hours_later = now_utc + timedelta(hours=2)

    try:
        feed = feedparser.parse(FOREX_FACTORY_RSS)
        for entry in feed.entries:
            title = entry.get("title", "")
            description = entry.get("description", "")
            published = entry.get("published", "")

            currency = _extract_currency(title)
            if target_currencies and currency not in target_currencies:
                continue

            impact = _infer_impact(title, description)
            if impact != "high":
                continue

            # Parse event time
            event_time = None
            imminent = False
            try:
                event_time = parsedate_to_datetime(published)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                imminent = now_utc <= event_time <= two_hours_later
            except Exception:
                pass

            events.append({
                "time": event_time.isoformat() if event_time else published,
                "currency": currency,
                "event": title,
                "impact": impact,
                "imminent": imminent,
                "source": "ForexFactory",
            })
    except Exception as e:
        errors.append(f"Forex Factory RSS: {e}")

    imminent_count = sum(1 for ev in events if ev["imminent"])

    return {
        "events": events,
        "imminent_count": imminent_count,
        "has_imminent": imminent_count > 0,
        "errors": errors,
    }
