from storage.database import get_pipeline_summary


def _name(d: dict) -> str:
    return d.get("company_name") or d.get("sender_name") or "Unknown"


def _is_consider(d: dict) -> bool:
    st = (d.get("status") or "").upper()
    score = float(d.get("gate2_overall_score") or 0)
    rec = (d.get("gate2_recommendation") or "").lower()
    if st in ("APPROVED", "APPROVED_DRAFT_CREATED", "GATE2_INTERNAL_PASS", "WAITING_HITL"):
        return True
    if score >= 6.5:
        return True
    if any(k in rec for k in ("partner", "hitl", "founder call", "needs founder call")):
        return True
    return False


def _quick_reason(d: dict) -> str:
    if (d.get("gate1_verdict") or "") == "FAIL_CONFIDENT":
        return (d.get("gate1_rejection_reason") or "Gate 1 fail").strip()[:120]
    rec = (d.get("gate2_recommendation") or "").strip()
    if rec:
        return rec[:120]
    return (d.get("status") or "No status").strip()[:120]


def generate_weekly_report(days: int = 7) -> str:
    deals = get_pipeline_summary(days=days)

    if not deals:
        return f"No deals processed in the last {days} days."

    total = len(deals)
    rejected_g1 = sum(
        1 for d in deals
        if d["status"] in ("REJECTED_GATE1", "GATE1_FAILED")
    )
    rejected_g2 = sum(1 for d in deals if d["status"] == "REJECTED_GATE2")
    rejected_hitl = sum(1 for d in deals if d["status"] == "REJECTED_HITL")
    skipped = sum(1 for d in deals if d["status"] == "SKIPPED")
    approved_count = sum(
        1 for d in deals
        if d["status"] in ("APPROVED", "APPROVED_DRAFT_CREATED")
    )
    errors = sum(1 for d in deals if d["status"] == "ERROR")
    pdf_fail = sum(
        1 for d in deals
        if d["status"] in ("PDF_DOWNLOAD_FAILED", "PDF_EXTRACTION_FAILED")
    )

    top_deals = sorted(
        [d for d in deals if d.get("gate2_overall_score")],
        key=lambda x: x["gate2_overall_score"],
        reverse=True,
    )[:5]
    consider = [d for d in deals if _is_consider(d)]
    not_fit = [d for d in deals if d not in consider]

    sectors = {}
    for d in deals:
        s = d.get("gate1_detected_sector", "Unknown")
        sectors[s] = sectors.get(s, 0) + 1

    geographies = {}
    for d in deals:
        g = d.get("gate1_detected_geography", "Unknown")
        geographies[g] = geographies.get(g, 0) + 1

    total_cost = 0.0
    g1_lat = []
    g2_lat = []
    n_g1 = 0
    n_g2 = 0
    for d in deals:
        total_cost += float(d.get("gate1_cost_usd") or 0)
        total_cost += float(d.get("gate2_cost_usd") or 0)
        total_cost += float(d.get("gate25_cost_usd") or 0)
        if d.get("gate1_latency_ms"):
            g1_lat.append(int(d["gate1_latency_ms"]))
            n_g1 += 1
        if d.get("gate2_latency_ms"):
            g2_lat.append(int(d["gate2_latency_ms"]))
            n_g2 += 1

    report_lines = [
        f"EXAMPLE VC FUND — WEEKLY PIPELINE REPORT (last {days} days)",
        "=" * 60,
        "",
        "FUNNEL SUMMARY",
        f"  Total inbound:      {total}",
        f"  Rejected at Gate 1: {rejected_g1}  ({_pct(rejected_g1, total)}%)",
        f"  Rejected at Gate 2: {rejected_g2}  ({_pct(rejected_g2, total)}%)",
        f"  Rejected at review: {rejected_hitl}  ({_pct(rejected_hitl, total)}%)",
        f"  Skipped (HITL):     {skipped}  ({_pct(skipped, total)}%)",
        f"  Approved:           {approved_count}  ({_pct(approved_count, total)}%)",
        f"  PDF / extract fail: {pdf_fail}  ({_pct(pdf_fail, total)}%)",
        f"  Errors:             {errors}  ({_pct(errors, total)}%)",
        "",
        "TOP DEALS THIS WEEK",
    ]
    for d in top_deals:
        score = d.get("gate2_overall_score", 0)
        name = d.get("company_name") or d.get("sender_name", "Unknown")
        rec = d.get("gate2_recommendation", "")
        report_lines.append(f"  {score:.1f}/10  {name}  [{rec}]")

    report_lines += [
        "",
        "PARETO SHORTLIST (80/20)",
        f"  Consider now: {len(consider)}",
        f"  Not fit now:  {len(not_fit)}",
        "",
        "  Consider now (max 10):",
    ]
    for d in consider[:10]:
        score = float(d.get("gate2_overall_score") or 0)
        report_lines.append(f"    - {_name(d)}  ({score:.1f}/10)  — {_quick_reason(d)}")
    if not consider:
        report_lines.append("    - none")

    report_lines += ["", "  Not fit now (max 10):"]
    for d in not_fit[:10]:
        score = float(d.get("gate2_overall_score") or 0)
        report_lines.append(f"    - {_name(d)}  ({score:.1f}/10)  — {_quick_reason(d)}")
    if not not_fit:
        report_lines.append("    - none")

    report_lines += [
        "",
        "SECTORS",
    ]
    for s, count in sorted(sectors.items(), key=lambda x: -x[1]):
        report_lines.append(f"  {count:3d}x  {s}")

    report_lines += [
        "",
        "GEOGRAPHIES",
    ]
    for g, count in sorted(geographies.items(), key=lambda x: -x[1]):
        report_lines.append(f"  {count:3d}x  {g}")

    if n_g1 or n_g2 or total_cost > 0:
        report_lines += [
            "",
            "TELEMETRY (deals with logged latency)",
            f"  Total est. API cost:  ${total_cost:.2f}",
        ]
        if g1_lat:
            report_lines.append(
                f"  Avg Gate 1 latency:   {sum(g1_lat) / len(g1_lat) / 1000:.1f}s  (n={len(g1_lat)})"
            )
        if g2_lat:
            report_lines.append(
                f"  Avg Gate 2 latency:   {sum(g2_lat) / len(g2_lat) / 1000:.1f}s  (n={len(g2_lat)})"
            )
        decks_with_g2_cost = sum(1 for d in deals if float(d.get("gate2_cost_usd") or 0) > 0)
        if decks_with_g2_cost:
            report_lines.append(
                f"  Avg cost / Gate2 run: ${total_cost / max(decks_with_g2_cost, 1):.2f} (rough)"
            )

    report_lines += ["", "=" * 60]
    return "\n".join(report_lines)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0"
    return f"{100 * n // total}"
