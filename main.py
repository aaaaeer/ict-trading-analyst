import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def _step(progress: Progress, label: str, fn, *args, **kwargs):
    task = progress.add_task(label, total=None)
    try:
        result = fn(*args, **kwargs)
        progress.update(task, description=f"[green]✓ {label}[/green]")
        return result, None
    except Exception as exc:
        progress.update(task, description=f"[red]✗ {label}: {exc}[/red]")
        return None, str(exc)
    finally:
        progress.remove_task(task)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="ICT Trading Analyst — AI-powered multi-timeframe chart analysis"
    )
    parser.add_argument(
        "--chart",
        nargs="+",
        required=True,
        metavar="FILE",
        help="One or more chart screenshots (any image format). Pass HTF first, e.g. --chart 1h.png 15m.png 5m.png",
    )
    parser.add_argument("--asset", required=True, help="Asset ticker, e.g. EURUSD=X, BTC-USD, ES=F")
    parser.add_argument("--no-news", action="store_true", help="Skip news and calendar fetching")
    parser.add_argument(
        "--export",
        nargs="?",
        const="auto",
        metavar="FILE",
        help="Export full analysis as JSON. Omit FILE for auto-named output, or pass a path.",
    )
    args = parser.parse_args()

    # Validate all chart files exist
    missing_files = [f for f in args.chart if not os.path.exists(f)]
    if missing_files:
        for f in missing_files:
            console.print(f"[red]Error: file not found: {f}[/red]")
        sys.exit(1)

    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY not set in .env or environment[/red]")
        sys.exit(1)

    news_api_key = os.getenv("NEWS_API_KEY")

    n_charts = len(args.chart)
    chart_label = (
        f"Analysing {n_charts} charts (multi-timeframe) with Claude vision…"
        if n_charts > 1
        else "Analysing chart with Claude vision…"
    )

    chart_data: dict = {}
    market_data: dict = {"ticker": args.asset}
    news: dict = {
        "headlines": [], "overall_sentiment": "neutral",
        "sentiment_score": 0.0, "bull_count": 0, "bear_count": 0, "source_count": 0,
    }
    calendar: dict = {"events": [], "has_imminent": False}
    missing: list[str] = []

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:

        # 1 — Chart vision (single or multi)
        from chart_vision import analyze_charts
        result, err = _step(p, chart_label, analyze_charts, args.chart)
        if result is not None:
            chart_data = result
        else:
            missing.append(f"Chart vision: {err}")

        # 2 — Market data
        from market_data import get_market_data
        result, err = _step(p, f"Fetching live data for {args.asset}…", get_market_data, args.asset)
        if result is not None:
            market_data = result
        else:
            missing.append(f"Market data: {err}")

        if not args.no_news:
            # 3 — News
            from news_fetcher import fetch_news
            result, err = _step(p, "Fetching news…", fetch_news, args.asset, news_api_key)
            if result is not None:
                news = result
            else:
                missing.append(f"News: {err}")

            # 4 — Calendar
            from calendar_fetcher import fetch_calendar
            result, err = _step(p, "Fetching economic calendar…", fetch_calendar)
            if result is not None:
                calendar = result
            else:
                missing.append(f"Calendar: {err}")

        # 5 — Bias engine
        from ict_engine import generate_bias
        result, err = _step(p, "Computing ICT bias…", generate_bias, chart_data, market_data, news, calendar)
        if result is not None:
            bias = result
        else:
            bias = {
                "score": 0, "direction": "NEUTRAL", "confidence": "LOW",
                "reasoning": [f"Engine error: {err}"], "breakdown": {},
            }

        # 6 — Trade setup (entry / SL / TP)
        from trade_setup import generate_trade_setup
        trade_result, err = _step(
            p, "Calculating entry, SL, and TP levels…",
            generate_trade_setup, chart_data, market_data, bias,
        )
        if trade_result is None:
            trade_result = {"direction": bias.get("direction", "NEUTRAL"), "error": err}

        # 7 — Natural language summary + suggestions
        from summarizer import generate_summary
        summary_result, err = _step(
            p, "Writing analyst summary…",
            generate_summary, chart_data, market_data, news, calendar, bias, missing,
        )
        if summary_result is None:
            summary_result = {
                "summary": f"Summary unavailable: {err}",
                "suggestions": [],
            }

    if missing:
        console.print("\n[yellow]Data sources that failed or were skipped:[/yellow]")
        for m in missing:
            console.print(f"  [yellow]• {m}[/yellow]")

    from output import print_report
    print_report(chart_data, market_data, news, calendar, bias, summary_result, trade_result)

    if args.export is not None:
        if args.export == "auto":
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            asset_slug = args.asset.replace("=", "").replace("-", "").upper()
            out_path = f"ict_{asset_slug}_{ts}.json"
        else:
            out_path = args.export
        from exporter import export_analysis
        written = export_analysis(chart_data, market_data, news, calendar, bias, summary_result, out_path, trade_result)
        console.print(f"[green]✓ Analysis exported →[/green] [bold]{written}[/bold]\n")


if __name__ == "__main__":
    main()
