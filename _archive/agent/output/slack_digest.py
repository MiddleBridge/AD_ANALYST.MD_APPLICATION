from __future__ import annotations


def build_daily_digest(rows: list[dict]) -> str:
    top = sorted(rows, key=lambda r: float(((r.get("scoring") or {}).get("overall_weighted") or 0)), reverse=True)[:5]
    lines = ["Top 5 deals today:"]
    for i, r in enumerate(top, 1):
        meta = r.get("meta") or {}
        routing = r.get("routing") or {}
        score = (r.get("scoring") or {}).get("overall_weighted")
        lines.append(f"{i}. {meta.get('company_name','unknown')} | score={score} | action={routing.get('action','unknown')}")
    return "\n".join(lines)

