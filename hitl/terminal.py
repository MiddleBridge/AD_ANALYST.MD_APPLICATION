"""
HITL terminal — clean 1-pager brief for Example VC Fund partner review.
"""
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from storage.models import Brief, HITLDecision

console = Console()

REC_COLORS = {
    "STRONG_YES": "bright_green",
    "YES": "green",
    "MAYBE": "yellow",
    "NO": "red",
    "STRONG_NO": "bright_red",
}

REJECTION_KINDS = [
    ("wrong_geo",          "Geography / region fit"),
    ("wrong_stage",        "Stage mismatch"),
    ("too_early",          "Too early — come back later"),
    ("weak_traction",      "Traction / validation missing"),
    ("outside_thesis",     "Outside investment thesis"),
    ("unclear_problem",    "Problem unclear or not validated"),
    ("not_venture_scale",  "Not venture-scale"),
    ("generic",            "Other"),
]


def _color(score: int) -> str:
    if score >= 9: return "bright_green"
    if score >= 7: return "green"
    if score >= 5: return "yellow"
    return "red"


def _overall_color(score: float) -> str:
    if score >= 8: return "bright_green"
    if score >= 6: return "green"
    if score >= 4: return "yellow"
    return "red"


def _bar(score: int) -> str:
    c = _color(score)
    return f"[{c}]{'█' * score}{'░' * (10 - score)}[/{c}]"


def display_brief(brief: Brief) -> None:
    console.print()
    console.rule("[bold cyan]EXAMPLE VC FUND — DEAL BRIEF[/bold cyan]")
    console.print()

    rec_color  = REC_COLORS.get(brief.recommendation, "white")
    ovr_color  = _overall_color(brief.overall_score)
    confidence = getattr(brief, "gate2_confidence", "medium")
    conf_icon  = {"high": "●", "medium": "◑", "low": "○"}.get(confidence, "◑")

    # ── Header panel ─────────────────────────────────────────────────────────
    console.print(Panel(
        f"[bold white]{brief.company_name}[/bold white]   "
        f"[dim]{brief.sender_name} <{brief.sender_email}>[/dim]\n"
        f"[dim]Received:[/dim] {brief.date_received}   "
        f"[dim]{brief.geography} · {brief.stage} · {brief.sector}[/dim]\n\n"
        f"[bold]Score:[/bold] [{ovr_color}]{brief.overall_score:.1f}/10[/{ovr_color}]   "
        f"[bold]Verdict:[/bold] [{rec_color}]{brief.recommendation}[/{rec_color}]   "
        f"[dim]confidence: {conf_icon} {confidence}[/dim]",
        border_style=ovr_color,
        expand=False,
    ))

    # ── What they do ──────────────────────────────────────────────────────────
    console.print()
    console.print("[bold]WHAT THEY DO[/bold]")
    console.print(f"  {brief.one_liner}")
    if getattr(brief, "what_they_do", ""):
        console.print(f"  [dim]{brief.what_they_do}[/dim]")

    # ── Key facts grid ────────────────────────────────────────────────────────
    console.print()
    grid = Table.grid(padding=(0, 3))
    grid.add_row("[dim]Founded[/dim]",        brief.founded_year or "unknown")
    grid.add_row("[dim]Ask[/dim]",            brief.fundraising_ask or "not stated")
    grid.add_row("[dim]Use of funds[/dim]",   brief.use_of_funds or "not stated")
    grid.add_row("[dim]Traction[/dim]",       brief.current_traction_summary or "not stated")
    grid.add_row("[dim]Business model[/dim]", brief.business_model_description or "not stated")
    console.print(grid)

    # ── Founders ─────────────────────────────────────────────────────────────
    founders = getattr(brief, "founders", []) or []
    if founders:
        console.print()
        console.print("[bold]FOUNDERS[/bold]")
        for f in founders:
            name = f.get("name", "Unknown")
            bg   = f.get("background", "")
            role = f.get("role", "")
            if name == "NOT_FOUND_IN_DECK":
                console.print("  [red]⚠ No founder info found in deck — ask directly[/red]")
            else:
                label = f"[bold]{name}[/bold]" + (f"  [dim]{role}[/dim]" if role else "")
                console.print(f"  {label}")
                if bg:
                    console.print(f"  [dim]{bg}[/dim]")

    # ── Scorecard with reasoning ──────────────────────────────────────────────
    console.print()
    console.print("[bold]SCORECARD[/bold]")
    console.print()

    scorecard = getattr(brief, "scorecard", {}) or {}
    for dim_name, dim in scorecard.items():
        score     = getattr(dim, "score", 0)
        reasoning = getattr(dim, "reasoning", "")
        c = _color(score)
        console.print(f"  [{c}]{score:2d}/10[/{c}]  {_bar(score)}  [bold]{dim_name}[/bold]")
        if reasoning:
            # Wrap long reasoning at 90 chars
            for chunk in _wrap(reasoning, 88):
                console.print(f"         [dim]{chunk}[/dim]")
        console.print()

    # ── Missing critical data ─────────────────────────────────────────────────
    missing = getattr(brief, "missing_critical_data", []) or []
    if missing:
        console.print("[bold yellow]MISSING — need to verify[/bold yellow]")
        for m in missing:
            console.print(f"  • {m}")
        console.print()

    # ── Questions to ask founder ──────────────────────────────────────────────
    questions = getattr(brief, "should_ask_founder", []) or []
    if questions:
        console.print("[bold cyan]QUESTIONS FOR FOUNDER CALL[/bold cyan]")
        for q in questions:
            console.print(f"  ? {q}")
        console.print()

    # ── Red flags ────────────────────────────────────────────────────────────
    sol_flags  = getattr(brief, "solution_love_flags", []) or []
    exec_flags = getattr(brief, "slow_execution_flags", []) or []
    qual_flags = getattr(brief, "quality_flags", []) or []
    if sol_flags or exec_flags or qual_flags:
        console.print("[bold red]RED FLAGS[/bold red]")
        for f in sol_flags:
            console.print(f"  [red]⚠ Solution-love:[/red] {f}")
        for f in exec_flags:
            console.print(f"  [red]⚠ Slow execution:[/red] {f}")
        for f in qual_flags:
            console.print(f"  [yellow]⚠ Data quality:[/yellow] {f}")
        console.print()

    # ── Strengths & concerns ─────────────────────────────────────────────────
    strengths = getattr(brief, "strengths", []) or []
    concerns  = getattr(brief, "concerns", []) or []
    if strengths:
        console.print("[bold green]TOP STRENGTHS[/bold green]")
        for s in strengths:
            console.print(f"  ✓ {s}")
        console.print()
    if concerns:
        console.print("[bold red]TOP CONCERNS[/bold red]")
        for c in concerns:
            console.print(f"  ✗ {c}")
        console.print()

    # ── Analyst summary ───────────────────────────────────────────────────────
    summary = getattr(brief, "executive_summary", "") or ""
    if summary:
        console.print(Panel(
            summary,
            title="[bold]Analyst Summary[/bold]",
            border_style="dim",
        ))

    # ── Venture scale ─────────────────────────────────────────────────────────
    vsa = getattr(brief, "venture_scale_assessment", "") or ""
    if vsa:
        console.print(Panel(
            vsa,
            title="[bold yellow]Venture Scale — can this return the fund?[/bold yellow]",
            border_style="yellow",
        ))

    # ── Portfolio comparable ───────────────────────────────────────────────────
    comparable = getattr(brief, "comparable", "") or ""
    if comparable:
        console.print(f"[dim]Closest portfolio match:[/dim] {comparable}")

    console.print()


def get_decision(brief: Brief) -> HITLDecision:
    console.rule("[bold yellow]YOUR DECISION[/bold yellow]")
    console.print()
    console.print("  [bold green]A[/bold green] — APPROVE   → schedule call, draft email")
    console.print("  [bold red]R[/bold red] — REJECT    → send polite decline")
    console.print("  [bold yellow]S[/bold yellow] — SKIP      → save for later, no action")
    console.print()

    while True:
        raw = console.input("[bold]Decision [A/R/S]: [/bold]").strip().upper()

        if raw in ("A", "APPROVE"):
            notes = console.input("Notes for your own reference (optional): ").strip()
            console.print(f"[green]✓ APPROVED[/green] — {brief.company_name}")
            return HITLDecision(approved=True, notes=notes)

        elif raw in ("R", "REJECT"):
            console.print()
            console.print("Rejection reason (choose number or type custom):")
            for i, (code, label) in enumerate(REJECTION_KINDS, 1):
                console.print(f"  {i}) {label}")
            console.print()
            raw_kind = console.input("Number or custom text: ").strip()
            try:
                idx = int(raw_kind) - 1
                kind_code, kind_label = REJECTION_KINDS[idx]
            except (ValueError, IndexError):
                kind_code = "generic"
                kind_label = raw_kind or "Generic"
            console.print(f"[red]✗ REJECTED[/red] — {brief.company_name}  [dim]({kind_label})[/dim]")
            return HITLDecision(
                approved=False,
                rejection_reason=kind_label,
                rejection_kind=kind_code,
            )

        elif raw in ("S", "SKIP"):
            console.print(f"[yellow]⏸ SKIPPED[/yellow] — {brief.company_name}")
            return HITLDecision(approved=False, rejection_reason="SKIPPED — no action taken")

        else:
            console.print("[red]Enter A, R, or S.[/red]")


def _wrap(text: str, width: int) -> list[str]:
    """Simple word-wrap that preserves existing content."""
    if len(text) <= width:
        return [text]
    lines, current = [], ""
    for word in text.split():
        if len(current) + len(word) + 1 <= width:
            current = (current + " " + word).lstrip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]
