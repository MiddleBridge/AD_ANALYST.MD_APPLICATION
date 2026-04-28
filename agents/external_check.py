"""Gate 2.5 — external research + external market assessment."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from agents.external_research import TavilyResearchProvider, get_research_provider
from agents.final_scoring import (
    ScreeningMode,
    apply_hard_cap_rules,
    cap_external_when_provider_down,
    compute_external_weighted_score,
    compute_risk_penalty,
    merge_kill_flags,
)
from agents.schemas_gate25 import (
    ExternalMarketCheckResult,
    ExternalMarketLLMAssessment,
    ExternalSource,
    KillFlag,
    ResearchQueryItem,
    ResearchQueryPlan,
)
from config.llm_cost import (
    EXTERNAL_LLM_MAX_SOURCES,
    EXTERNAL_SOURCE_SNIPPET_CHARS,
    OPENAI_MODEL,
    OPENAI_MODEL_LIGHT,
    TOK_EXTERNAL_MARKET_OUT,
    TOK_EXTERNAL_PLAN_OUT,
    llm_cost_usd_from_tokens,
)
from storage.models import Gate1Result, Gate2Result

# Gate 2.5 market step; plan step uses OPENAI_MODEL_LIGHT.
EXTERNAL_MARKET_MODEL = os.getenv("OPENAI_EXTERNAL_MODEL") or OPENAI_MODEL

_ROOT = Path(__file__).resolve().parent.parent
_QUERIES_PROMPT = (_ROOT / "config/prompts/external_research_queries.md").read_text(encoding="utf-8")
_MARKET_PROMPT = (_ROOT / "config/prompts/external_market_check.md").read_text(encoding="utf-8")

RESEARCH_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_research_queries",
        "description": "Ordered web research queries for external diligence",
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "purpose": {"type": "string"},
                            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        "required": ["query", "purpose", "priority"],
                    },
                }
            },
            "required": ["queries"],
        },
    },
}

_DIM = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "reasoning": {"type": "string"},
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "source_indices": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["score", "reasoning", "missing_data"],
}

KILL_IN_LLM = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "severity": {"type": "string", "enum": ["warning", "major"]},
        "description": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["code", "severity", "description"],
}

EXTERNAL_ASSESSMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_external_market_assessment",
        "description": "External market diligence scores and narrative (no invented metrics)",
        "parameters": {
            "type": "object",
            "properties": {
                "market_saturation": _DIM,
                "competitive_position": _DIM,
                "incumbent_risk": _DIM,
                "distribution_feasibility": _DIM,
                "cac_viability": _DIM,
                "switching_trigger": _DIM,
                "trend_validity": _DIM,
                "regulatory_platform_risk": _DIM,
                "right_to_win": _DIM,
                "external_confidence": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "market_summary": {"type": "string"},
                "competition_summary": {"type": "string"},
                "right_to_win_summary": {"type": "string"},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "suggested_kill_flags": {"type": "array", "items": KILL_IN_LLM},
            },
            "required": [
                "market_saturation",
                "competitive_position",
                "incumbent_risk",
                "distribution_feasibility",
                "cac_viability",
                "switching_trigger",
                "trend_validity",
                "regulatory_platform_risk",
                "right_to_win",
                "external_confidence",
                "market_summary",
                "competition_summary",
                "right_to_win_summary",
                "open_questions",
                "suggested_kill_flags",
            ],
        },
    },
}


def _extract_tool_args(response) -> dict | None:
    try:
        tc = response.choices[0].message.tool_calls[0]
        return json.loads(tc.function.arguments)
    except (AttributeError, IndexError, TypeError, json.JSONDecodeError):
        return None


def _telemetry(t0: float, response) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    return {
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "input_tokens": pt,
        "output_tokens": ct,
    }


def _validate_retry(model_cls, raw: dict | None, retry_fn):
    if raw is None:
        raw = retry_fn()
    try:
        return model_cls.model_validate(raw)
    except Exception:
        raw = retry_fn()
        if raw is None:
            raise
        return model_cls.model_validate(raw)


def _research_bundle_json_for_llm(sources: list[ExternalSource]) -> str:
    """Cap source count and snippet length — market prompt input is a major cost driver."""
    capped = sources[:EXTERNAL_LLM_MAX_SOURCES]
    snip = EXTERNAL_SOURCE_SNIPPET_CHARS
    rows: list[dict[str, Any]] = []
    for i, s in enumerate(capped):
        d = s.model_dump(exclude_none=True)
        text = d.get("snippet")
        if isinstance(text, str) and len(text) > snip:
            d["snippet"] = text[:snip] + "…"
        rows.append({"index": i, **d})
    return json.dumps(rows, ensure_ascii=False, indent=2)


def run_gate25_external_check(
    *,
    facts_dict: dict[str, Any],
    dimensions_json: str,
    gate1: Gate1Result,
    gate2: Gate2Result,
    screening_mode: ScreeningMode = "deck",
) -> tuple[ExternalMarketCheckResult, dict[str, Any]]:
    gate25_started_iso = datetime.utcnow().isoformat()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    provider, provider_live = get_research_provider()
    max_queries = int(os.getenv("EXTERNAL_RESEARCH_MAX_QUERIES", "8"))
    per_q = int(os.getenv("EXTERNAL_RESEARCH_RESULTS_PER_QUERY", "5"))

    facts_json = json.dumps(facts_dict, ensure_ascii=False, indent=2)
    ctx = (
        f"Company / facts JSON:\n{facts_json}\n\n"
        f"Gate1 sector: {gate1.detected_sector}, geo: {gate1.detected_geography}\n"
        f"Gate2 one-liner: {gate2.company_one_liner}\n"
    )

    def call_plan():
        t0 = time.perf_counter()
        r = client.chat.completions.create(
            model=OPENAI_MODEL_LIGHT,
            temperature=0.2,
            max_tokens=TOK_EXTERNAL_PLAN_OUT,
            tools=[RESEARCH_PLAN_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_research_queries"}},
            messages=[
                {"role": "system", "content": _QUERIES_PROMPT},
                {"role": "user", "content": ctx},
            ],
        )
        tel = _telemetry(t0, r)
        return _extract_tool_args(r), tel

    raw_plan, tel_plan = call_plan()
    plan = _validate_retry(ResearchQueryPlan, raw_plan, lambda: call_plan()[0] or {})

    if not plan.queries:
        nm = str(facts_dict.get("company_name") or gate2.company_name or "startup")
        plan = ResearchQueryPlan(
            queries=[
                ResearchQueryItem(
                    query=f"{nm} competitors funding landscape",
                    purpose="competition",
                    priority=1,
                )
            ]
        )

    queries = sorted(
        plan.queries,
        key=lambda q: (q.priority, -len(q.query)),
    )[:max_queries]
    search_calls = len(queries)

    all_sources: list[ExternalSource] = []
    seen: set[str] = set()
    for item in queries:
        for src in provider.search(item.query, per_q):
            u = src.url or ""
            if u and u in seen:
                continue
            if u:
                seen.add(u)
            all_sources.append(src)

    if not provider_live:
        prov_warn = (
            "External research provider unavailable; external check based only on deck facts "
            "(no live web results). external_score capped at 6.0."
        )
        force_low_external = True
    elif not all_sources:
        prov_warn = "Live research returned no indexed sources for this run; external_score capped at 6.0."
        force_low_external = True
    else:
        prov_warn = None
        force_low_external = False

    research_block = _research_bundle_json_for_llm(all_sources)

    mode_preamble = ""
    if screening_mode == "website":
        mode_preamble = (
            "## Screening mode: WEBSITE\n"
            "Facts were extracted from public marketing pages (crawl), not a confidential deck. "
            "Treat claims as unverified; prioritize public sources (news, LinkedIn, reviews, GitHub/docs, "
            "competitors). External validation should weigh heavily.\n\n"
        )

    user_market = f"""{mode_preamble}{_MARKET_PROMPT}

## Extracted facts JSON
{facts_json}

## Internal scorecard (dimensions JSON — deck or website-evidence dimensions)
{dimensions_json}

## Internal overall score (deck-implied or website-implied)
{gate2.overall_score}

## Research bundle (source index = index field; use source_indices in dimensions to cite; empty array if none)
{research_block}

## Provider status
provider_live: {provider_live}
provider_warning: {prov_warn or "none"}
"""

    def call_market():
        t0 = time.perf_counter()
        r = client.chat.completions.create(
            model=EXTERNAL_MARKET_MODEL,
            temperature=0.15,
            max_tokens=TOK_EXTERNAL_MARKET_OUT,
            tools=[EXTERNAL_ASSESSMENT_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_external_market_assessment"}},
            messages=[
                {"role": "system", "content": "You are a rigorous VC external market analyst. Follow the user instructions exactly."},
                {"role": "user", "content": user_market},
            ],
        )
        tel = _telemetry(t0, r)
        return _extract_tool_args(r), tel

    raw_m, tel_m = call_market()
    llm = _validate_retry(ExternalMarketLLMAssessment, raw_m, lambda: call_market()[0] or {})

    ext_scores = {
        "market_saturation_score": llm.market_saturation.score,
        "competitive_position_score": llm.competitive_position.score,
        "incumbent_risk_score": llm.incumbent_risk.score,
        "distribution_feasibility_score": llm.distribution_feasibility.score,
        "cac_viability_score": llm.cac_viability.score,
        "switching_trigger_score": llm.switching_trigger.score,
        "trend_validity_score": llm.trend_validity.score,
        "regulatory_platform_risk_score": llm.regulatory_platform_risk.score,
        "right_to_win_score": llm.right_to_win.score,
    }
    ext_raw = compute_external_weighted_score(
        {k: ext_scores[k] for k in ext_scores}
    )

    if force_low_external:
        ext_score = cap_external_when_provider_down(ext_raw)
        conf = "low"
    else:
        ext_score = ext_raw
        conf = llm.external_confidence

    llm_flags = []
    for kf in llm.suggested_kill_flags or []:
        if kf.severity == "fatal":
            kf = KillFlag(
                code=kf.code,
                severity="major",
                description=kf.description,
                evidence=kf.evidence,
            )
        llm_flags.append(kf)

    hard_cap, det_flags = apply_hard_cap_rules(
        gate2=gate2,
        facts=facts_dict,
        gate1=gate1,
        ext=ext_scores,
    )
    merged_flags = merge_kill_flags(det_flags, llm_flags)
    risk_penalty = compute_risk_penalty(merged_flags)

    cat = gate1.detected_sector or None
    geo = facts_dict.get("geography") or gate1.detected_geography or None
    tc = facts_dict.get("customers") or facts_dict.get("what_they_do")

    result = ExternalMarketCheckResult(
        company_name=str(facts_dict.get("company_name") or gate2.company_name or ""),
        category=cat,
        geography=geo,
        target_customer=str(tc)[:500] if tc else None,
        market_saturation_score=ext_scores["market_saturation_score"],
        competitive_position_score=ext_scores["competitive_position_score"],
        incumbent_risk_score=ext_scores["incumbent_risk_score"],
        distribution_feasibility_score=ext_scores["distribution_feasibility_score"],
        cac_viability_score=ext_scores["cac_viability_score"],
        switching_trigger_score=ext_scores["switching_trigger_score"],
        trend_validity_score=ext_scores["trend_validity_score"],
        regulatory_platform_risk_score=ext_scores["regulatory_platform_risk_score"],
        right_to_win_score=ext_scores["right_to_win_score"],
        external_score=ext_score,
        external_confidence=conf,
        kill_flags=merged_flags,
        risk_penalty=risk_penalty,
        hard_cap=hard_cap,
        market_summary=llm.market_summary,
        competition_summary=llm.competition_summary,
        right_to_win_summary=llm.right_to_win_summary,
        open_questions=list(llm.open_questions or []),
        sources=all_sources,
        provider_unavailable_warning=prov_warn,
    )

    pt_in = int(tel_plan.get("input_tokens", 0) or 0) + int(tel_m.get("input_tokens", 0) or 0)
    pt_out = int(tel_plan.get("output_tokens", 0) or 0) + int(tel_m.get("output_tokens", 0) or 0)
    plan_cost = llm_cost_usd_from_tokens(
        int(tel_plan.get("input_tokens", 0) or 0),
        int(tel_plan.get("output_tokens", 0) or 0),
    )
    market_cost = llm_cost_usd_from_tokens(
        int(tel_m.get("input_tokens", 0) or 0),
        int(tel_m.get("output_tokens", 0) or 0),
    )
    combined_cost = round(plan_cost + market_cost, 6)
    is_tavily = isinstance(provider, TavilyResearchProvider)

    tel_out = {
        "started_at": gate25_started_iso,
        "finished_at": datetime.utcnow().isoformat(),
        "plan": tel_plan,
        "market": tel_m,
        "latency_ms": int(tel_plan.get("latency_ms", 0) or 0)
        + int(tel_m.get("latency_ms", 0) or 0),
        "input_tokens": pt_in,
        "output_tokens": pt_out,
        "cost_usd": combined_cost,
        "search_calls": search_calls,
        "tavily_credits": search_calls if is_tavily else 0,
    }
    return result, tel_out
