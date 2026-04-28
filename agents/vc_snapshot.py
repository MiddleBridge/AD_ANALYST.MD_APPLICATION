from __future__ import annotations

import json
from typing import Any, Optional


def _as_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def _pick_first_evidence(dimension: dict[str, Any] | None) -> tuple[str, str]:
    """
    Returns (quote, source) for first evidence item if present.
    """
    if not dimension:
        return ("", "")
    ev = dimension.get("evidence") or []
    if not isinstance(ev, list) or not ev:
        return ("", "")
    item = ev[0] or {}
    quote = _as_text(item.get("quote"))
    source = _as_text(item.get("source"))
    return (quote, source)


def _score_of(dim: dict[str, Any] | None) -> Optional[float]:
    if not dim:
        return None
    s = dim.get("score")
    try:
        if s is None:
            return None
        return float(s)
    except Exception:
        return None


def _join_list(x: Any, *, max_items: int = 3) -> str:
    if not x:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        # Common shapes: {"name": "...", "background": "..."} or {"names": [...]}
        name = _as_text(x.get("name") or x.get("full_name"))
        if name and name.lower() != "unknown":
            return name
        names = x.get("names")
        if isinstance(names, list):
            return _join_list(names, max_items=max_items)
        return ""
    if isinstance(x, list):
        items: list[str] = []
        for i in x:
            if isinstance(i, dict):
                nm = _as_text(i.get("name") or i.get("full_name"))
                if nm and nm.lower() != "unknown":
                    items.append(nm)
                continue
            t = _as_text(i)
            if t and t.lower() != "unknown":
                items.append(t)
        if not items:
            return ""
        return ", ".join(items[:max_items])
    return _as_text(x)


def render_vc_snapshot_card(
    *,
    company_name: str,
    gate1_detected_geography: str = "",
    gate1_detected_sector: str = "",
    gate1_detected_stage: str = "",
    gate2_overall_score: Optional[float] = None,
    gate2_recommendation: str = "",
    gate2_strengths_json: str = "[]",
    gate2_concerns_json: str = "[]",
    gate2_missing_critical_data_json: str = "[]",
    gate2_should_ask_founder_json: str = "[]",
    facts_json: Optional[str] = None,
    dimensions_json: Optional[str] = None,
    max_chars: int = 1800,
) -> str:
    facts: dict[str, Any] = {}
    dims: dict[str, Any] = {}
    try:
        facts = json.loads(facts_json or "{}") or {}
    except Exception:
        facts = {}
    try:
        dims = json.loads(dimensions_json or "{}") or {}
    except Exception:
        dims = {}

    what = _as_text(facts.get("what_they_do") or facts.get("company_one_liner"))
    geo = _as_text(facts.get("geography")) or _as_text(gate1_detected_geography)
    sector = _as_text(gate1_detected_sector)
    stage = _as_text(facts.get("stage")) or _as_text(gate1_detected_stage)
    founded_year = _as_text(facts.get("founded_year"))
    founders = _join_list(facts.get("founders"))
    customers = _join_list(facts.get("customers") or facts.get("customer"))
    pricing = _as_text(facts.get("pricing"))
    fundraising = _as_text(facts.get("fundraising_ask"))

    # Dimensions (best-effort across current schema)
    problem = dims.get("problem") if isinstance(dims.get("problem"), dict) else None
    wedge = dims.get("wedge") if isinstance(dims.get("wedge"), dict) else None
    moat = dims.get("moat_path") if isinstance(dims.get("moat_path"), dict) else None
    traction = dims.get("traction") if isinstance(dims.get("traction"), dict) else None
    biz = dims.get("business_model") if isinstance(dims.get("business_model"), dict) else None
    team = dims.get("founder_market_fit") if isinstance(dims.get("founder_market_fit"), dict) else None
    market = dims.get("market") if isinstance(dims.get("market"), dict) else None
    timing = dims.get("timing") if isinstance(dims.get("timing"), dict) else None

    wedge_quote, wedge_src = _pick_first_evidence(wedge)
    problem_quote, problem_src = _pick_first_evidence(problem)
    moat_quote, moat_src = _pick_first_evidence(moat)

    strengths = []
    concerns = []
    missing = []
    questions = []
    for raw, out in [
        (gate2_strengths_json, strengths),
        (gate2_concerns_json, concerns),
        (gate2_missing_critical_data_json, missing),
        (gate2_should_ask_founder_json, questions),
    ]:
        try:
            parsed = json.loads(raw or "[]")
            if isinstance(parsed, list):
                out.extend([_as_text(x) for x in parsed if _as_text(x)])
        except Exception:
            pass

    # Build a compact, VC-friendly card (one paragraph with newlines)
    lines: list[str] = []
    lines.append("VC Snapshot (auto)")
    basics_bits = [b for b in [geo, sector, stage] if b]
    basics = " | ".join(basics_bits) if basics_bits else "unknown"
    fy = founded_year if founded_year and founded_year.lower() != "unknown" else "unknown"
    fnd = founders if founders and founders.lower() != "unknown" else "unknown"
    score_s = f"{gate2_overall_score:.2f}/10" if isinstance(gate2_overall_score, (int, float)) else "n/a"
    rec = _as_text(gate2_recommendation) or "n/a"
    lines.append(f"Company: {company_name}  •  {basics}  •  Founded: {fy}  •  Founders: {fnd}")
    lines.append(f"Score: {score_s}  •  Recommendation: {rec}")

    if what:
        lines.append(f"What: {what}")
    if customers:
        lines.append(f"Customer/ICP: {customers}")
    if fundraising:
        lines.append(f"Raise/Use of funds: {fundraising}")
    if pricing:
        lines.append(f"Pricing/Model: {pricing}")

    # Quick evidence anchors (if present)
    anchors: list[str] = []
    if problem_quote:
        anchors.append(f'Problem: "{problem_quote}" ({problem_src})'.strip())
    if wedge_quote:
        anchors.append(f'USP/Wedge: "{wedge_quote}" ({wedge_src})'.strip())
    if moat_quote:
        anchors.append(f'Moat hint: "{moat_quote}" ({moat_src})'.strip())
    if anchors:
        lines.append("Evidence:")
        for a in anchors[:3]:
            lines.append(f"- {a}")

    # Scores (compact)
    score_bits = []
    for name, dim in [
        ("problem", problem),
        ("wedge", wedge),
        ("moat", moat),
        ("market", market),
        ("traction", traction),
        ("biz_model", biz),
        ("team", team),
        ("timing", timing),
    ]:
        s = _score_of(dim)
        if s is not None:
            score_bits.append(f"{name} {int(s)}/10")
    if score_bits:
        lines.append("Subscores: " + " • ".join(score_bits[:8]))

    if strengths:
        lines.append("Strengths: " + " | ".join(strengths[:3]))
    if concerns:
        lines.append("Concerns: " + " | ".join(concerns[:3]))

    # Missing & questions (signal VC wants)
    if missing:
        lines.append("Missing (blocking): " + " | ".join(missing[:4]))
    if questions:
        lines.append("Questions to ask: " + " | ".join(questions[:3]))

    text = "\n".join([l for l in lines if _as_text(l)])
    if max_chars and len(text) > max_chars:
        text = text[: max(0, max_chars - 1)].rstrip() + "…"
    return text

