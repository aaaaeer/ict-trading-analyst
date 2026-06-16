from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()


def _fmt(val: float | None, decimals: int = 5) -> str:
    return f"{val:.{decimals}f}" if val is not None else "N/A"


def _pip_diff(a: float | None, b: float | None, asset: str = "") -> str:
    if a is None or b is None:
        return ""
    diff = abs(a - b)
    # JPY pairs: 1 pip = 0.01; others: 1 pip = 0.0001
    pip_size = 0.01 if "JPY" in asset.upper() else 0.0001
    pips = diff / pip_size
    return f"  ({pips:.1f} pips)"


def print_trade_setup(trade: dict, asset: str = "") -> None:
    direction = trade.get("direction", "NEUTRAL")
    if direction == "NEUTRAL":
        console.print(Panel(
            f"\n  {trade.get('notes', 'No trade — neutral bias.')}\n",
            title="[bold]TRADE SETUP[/bold]",
            border_style="yellow",
        ))
        return

    dir_color = "green" if direction == "BULLISH" else "red"
    entry = trade.get("entry")
    sl    = trade.get("sl")
    tp1   = trade.get("tp1")
    tp2   = trade.get("tp2")
    rr1   = trade.get("rr1")
    rr2   = trade.get("rr2")

    # Decimal places: JPY pairs use 3, others use 5
    dp = 3 if "JPY" in asset.upper() else 5

    def p(v):
        return f"{v:.{dp}f}" if v is not None else "N/A"

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Label", style="cyan", no_wrap=True)
    t.add_column("Price", style="bold white")
    t.add_column("Detail", style="dim")

    t.add_row(
        "Direction",
        f"[{dir_color}]{'🟢' if direction=='BULLISH' else '🔴'} {direction}[/{dir_color}]",
        "",
    )
    t.add_row("Entry", p(entry), "")
    t.add_row(
        "[red]Stop Loss[/red]",
        f"[red]{p(sl)}[/red]",
        f"[dim]{_pip_diff(entry, sl, asset)}[/dim]",
    )
    t.add_row(
        "[green]TP 1[/green]",
        f"[green]{p(tp1)}[/green]",
        f"[dim]RR {rr1:.1f}:1{_pip_diff(entry, tp1, asset)}[/dim]" if rr1 else "",
    )
    t.add_row(
        "[green]TP 2[/green]",
        f"[green]{p(tp2)}[/green]",
        f"[dim]RR {rr2:.1f}:1{_pip_diff(entry, tp2, asset)}[/dim]" if rr2 else "",
    )
    t.add_row("Invalidation", str(trade.get("invalidation", "N/A")), "")

    notes_lines = []
    for label, key in [("Entry", "entry_notes"), ("SL", "sl_notes"), ("Targets", "tp_notes")]:
        val = trade.get(key, "")
        if val:
            notes_lines.append(f"  [cyan]{label}:[/cyan] {val}")

    body = "\n".join(notes_lines)

    console.print(Panel(t, title=f"[bold]TRADE SETUP — ICT[/bold]", border_style=dir_color))
    if body:
        console.print(Panel(body, title="[bold]SETUP NOTES[/bold]", border_style="dim " + dir_color))
    console.print(
        "[bold red]  ⚠  These levels are for informational purposes only. Apply your own risk management.[/bold red]\n"
    )


def print_report(
    chart_data: dict,
    market_data: dict,
    news: dict,
    calendar: dict,
    bias: dict,
    summary: dict | None = None,
    trade: dict | None = None,
) -> None:
    score = bias["score"]
    direction = bias["direction"]
    confidence = bias["confidence"]

    dir_color = {"BULLISH": "green", "BEARISH": "red"}.get(direction, "yellow")
    dir_symbol = {"BULLISH": "🟢 BULLISH", "BEARISH": "🔴 BEARISH"}.get(direction, "🟡 NEUTRAL / NO TRADE")

    console.print()
    console.rule("[bold cyan]ICT TRADING ANALYST REPORT[/bold cyan]")
    console.print()

    # ── Market Info ──────────────────────────────────────────────────────────
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Key", style="cyan", no_wrap=True)
    t.add_column("Value", style="white")

    ticker = market_data.get("ticker") or chart_data.get("asset", "N/A")
    tf_label = str(chart_data.get("timeframe", "N/A"))
    t.add_row("Asset", str(ticker))
    t.add_row("Timeframe", tf_label)
    t.add_row("Current Price", _fmt(market_data.get("current_price")))
    t.add_row(
        "PDH / PDL / PDC",
        f"{_fmt(market_data.get('pdh'))} / {_fmt(market_data.get('pdl'))} / {_fmt(market_data.get('pdc'))}",
    )
    t.add_row(
        "Asian Range",
        f"{_fmt(market_data.get('asian_high'))} – {_fmt(market_data.get('asian_low'))}",
    )
    t.add_row(
        "London Range",
        f"{_fmt(market_data.get('london_high'))} – {_fmt(market_data.get('london_low'))}",
    )
    t.add_row("Intraday Trend", str(market_data.get("intraday_trend", "N/A")).capitalize())

    console.print(Panel(t, title="[bold]MARKET INFO[/bold]", border_style="blue"))

    # ── Multi-Timeframe Breakdown (shown only when multiple charts provided) ─
    charts = chart_data.get("charts", [])
    if charts:
        mt = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
        mt.add_column("TF", style="cyan bold", no_wrap=True)
        mt.add_column("Structure", style="white")
        mt.add_column("Trend", style="white")
        mt.add_column("Liquidity", style="white")
        mt.add_column("FVG / OB", style="white")

        for ch in charts:
            tf = str(ch.get("timeframe", "?"))
            structure = str(ch.get("structure", ""))
            trend = str(ch.get("trend", ""))
            liq = str(ch.get("liquidity", ""))
            fvgs = ch.get("fvgs", [])
            obs = ch.get("order_blocks", [])

            trend_color = "green" if "bullish" in trend.lower() else "red" if "bearish" in trend.lower() else "yellow"
            struct_color = "green" if any(k in structure.lower() for k in ("hh", "hl", "bullish")) \
                else "red" if any(k in structure.lower() for k in ("lh", "ll", "bearish")) else "white"

            fvg_ob_parts = []
            fvg_str = str(fvgs).lower()
            ob_str = str(obs).lower()
            if "bullish" in fvg_str:
                fvg_ob_parts.append("[green]Bull FVG[/green]")
            if "bearish" in fvg_str:
                fvg_ob_parts.append("[red]Bear FVG[/red]")
            if "bullish" in ob_str:
                fvg_ob_parts.append("[green]Bull OB[/green]")
            if "bearish" in ob_str:
                fvg_ob_parts.append("[red]Bear OB[/red]")
            fvg_ob_str = " / ".join(fvg_ob_parts) if fvg_ob_parts else "None"

            mt.add_row(
                tf,
                f"[{struct_color}]{structure[:30]}[/{struct_color}]",
                f"[{trend_color}]{trend.capitalize()}[/{trend_color}]",
                liq[:35] or "N/A",
                fvg_ob_str,
            )

        htf_bias = str(chart_data.get("htf_bias", "")).upper()
        ltf_bias = str(chart_data.get("ltf_bias", "")).upper()
        htf_color = {"BULLISH": "green", "BEARISH": "red"}.get(htf_bias, "yellow")
        ltf_color = {"BULLISH": "green", "BEARISH": "red"}.get(ltf_bias, "yellow")

        header = (
            f"HTF Bias: [{htf_color}]{htf_bias}[/{htf_color}]  |  "
            f"LTF Bias: [{ltf_color}]{ltf_bias}[/{ltf_color}]"
        )
        console.print(Panel(mt, title=f"[bold]MULTI-TIMEFRAME BREAKDOWN[/bold]  {header}", border_style="magenta"))

        notes = chart_data.get("confluence_notes", "")
        if notes:
            console.print(Panel(f"  {notes}", title="[bold]CONFLUENCE NOTES[/bold]", border_style="dim magenta"))

    # ── Chart Analysis (primary / HTF chart summary) ─────────────────────────
    c = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    c.add_column("Key", style="cyan", no_wrap=True)
    c.add_column("Value", style="white")

    c.add_row("Structure", str(chart_data.get("structure", "N/A")))
    c.add_row("Trend", str(chart_data.get("trend", "N/A")))
    c.add_row("FVGs", str(chart_data.get("fvgs", "None")) or "None")
    c.add_row("Order Blocks", str(chart_data.get("order_blocks", "None")) or "None")
    c.add_row("Liquidity", str(chart_data.get("liquidity", "N/A")))
    c.add_row("POI Levels", str(chart_data.get("poi_levels", "None")) or "None")
    c.add_row("Killzone", str(chart_data.get("killzone", "N/A")))

    title = "[bold]CHART ANALYSIS[/bold]" if not charts else "[bold]CHART ANALYSIS (HTF summary)[/bold]"
    console.print(Panel(c, title=title, border_style="blue"))

    # ── News & Calendar ──────────────────────────────────────────────────────
    n = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    n.add_column("Key", style="cyan", no_wrap=True)
    n.add_column("Value", style="white")

    sent = news.get("overall_sentiment", "neutral")
    sent_color = {"bullish": "green", "bearish": "red"}.get(sent, "yellow")
    n.add_row(
        "News Sentiment",
        f"[{sent_color}]{sent.upper()}[/{sent_color}] ({news.get('source_count', 0)} sources, "
        f"bull: {news.get('bull_count', 0)} | bear: {news.get('bear_count', 0)})",
    )

    for i, h in enumerate(news.get("headlines", [])[:3], 1):
        h_color = {"bullish": "green", "bearish": "red"}.get(h["sentiment"], "dim")
        n.add_row(f"  Headline {i}", f"[{h_color}]{h['title'][:65]}[/{h_color}]")

    events = calendar.get("events", [])
    if events:
        for ev in events[:4]:
            flag = " [bold red]⚠ IMMINENT[/bold red]" if ev.get("imminent") else ""
            n.add_row(
                "[bold yellow]EVENT[/bold yellow]",
                f"{ev.get('currency', '')} | {ev.get('event', '')[:55]}{flag}",
            )
    else:
        n.add_row("Calendar", "No high-impact events found")

    console.print(Panel(n, title="[bold]NEWS & CALENDAR[/bold]", border_style="blue"))

    # ── Score Breakdown ──────────────────────────────────────────────────────
    bd = bias.get("breakdown", {})
    bt = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    bt.add_column("Factor", style="cyan")
    bt.add_column("Score", justify="right")
    bt.add_column("Weight", justify="right", style="dim")

    for name, key, weight in [
        ("HTF Structure",     "htf_structure", "30%"),
        ("Liquidity",         "liquidity",     "25%"),
        ("FVG / Order Block", "fvg_ob",        "20%"),
        ("PDH / PDL",         "pdh_pdl",       "10%"),
        ("News Sentiment",    "news",          "10%"),
    ]:
        val = bd.get(key, 0)
        color = "green" if val > 0 else "red" if val < 0 else "white"
        bt.add_row(name, f"[{color}]{val:+.1f}[/{color}]", weight)

    console.print(Panel(bt, title="[bold]SCORE BREAKDOWN[/bold]", border_style="blue"))

    # ── Reasoning ────────────────────────────────────────────────────────────
    reasoning_body = "\n".join(f"  • {r}" for r in bias.get("reasoning", []))
    console.print(Panel(reasoning_body, title="[bold]REASONING[/bold]", border_style="dim"))

    # ── Final Verdict ────────────────────────────────────────────────────────
    vt = Text()
    vt.append(f"\n  BIAS SCORE:  {score:+d} / 100\n", style="bold white")
    vt.append( "  DIRECTION:   ", style="bold white")
    vt.append(f"{dir_symbol}\n", style=f"bold {dir_color}")
    vt.append(f"  CONFIDENCE:  {confidence}\n", style="bold white")

    if calendar.get("has_imminent"):
        vt.append("\n  ⚠  HIGH-IMPACT EVENT IMMINENT — TRADE WITH EXTREME CAUTION\n", style="bold red")

    console.print(Panel(vt, title="[bold]FINAL VERDICT[/bold]", border_style=dir_color))

    # ── Trade Setup ──────────────────────────────────────────────────────────
    if trade:
        asset = market_data.get("ticker", "")
        print_trade_setup(trade, asset)

    # ── Analyst Summary ──────────────────────────────────────────────────────
    if summary:
        summary_text = summary.get("summary", "")
        if summary_text:
            console.print(Panel(
                f"\n  {summary_text}\n",
                title="[bold]ANALYST SUMMARY[/bold]",
                border_style="cyan",
            ))

        suggestions = summary.get("suggestions", [])
        if suggestions:
            body = "\n".join(f"  [{i}] {s}" for i, s in enumerate(suggestions, 1))
            console.print(Panel(
                body,
                title="[bold]TO IMPROVE THIS ANALYSIS[/bold]",
                border_style="yellow",
            ))

    console.print()
    console.print(
        "[dim]Disclaimer: This is informational analysis only. Never trade automatically on this output.[/dim]"
    )
    console.print()
