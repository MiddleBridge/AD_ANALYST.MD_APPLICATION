"""Single batched LLM call for website VC sub-scores (saves input tokens vs N separate calls)."""

from __future__ import annotations

from openai import OpenAI

from agents.distribution_engine import assemble_distribution_from_pack
from agents.outlier_filter import OutlierLLM, compile_outlier_filter_output
from agents.retention_model import RetentionLLM, compile_retention_model_output
from agents.right_to_win import RTWLLM, compile_right_to_win_output
from agents.schemas_website import WebsiteFactsOutput, WebsiteScoresOutput
from agents.schemas_website_vc import (
    CompetitiveIntelligenceOutput,
    DistributionEngineOutput,
    OutlierFilterOutput,
    RetentionModelOutput,
    RightToWinOutput,
    TrendAnalysisOutput,
    WebsiteVCPackLLM,
)
from agents.trend_analysis import assemble_trend_from_vcpack
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL, TOK_WEBSITE_VC_PACK_OUT

from agents.competitive_intelligence import CategoryOsintBundle

_PACK_SYSTEM = """You are a senior VC analyst at Example VC Fund (CEE-focused early-stage fund). You score a startup using website-only evidence + competitor OSINT snippets.

══════════════════════════════════════════════════════════════════════
FUND MENTAL MODEL — apply BEFORE you assign any number
══════════════════════════════════════════════════════════════════════
Fund invests in CEE/CEE-diaspora founders at pre-seed/seed in:
Dev Tools · AI/ML · AI agents · automation · workflow · data infra ·
HealthTech · SaaS marketplaces · B2B SaaS · FinTech · vertical SaaS · cybersecurity.

Portfolio benchmarks (calibration anchors — your scoring must be CONSISTENT with these):
  Pathway, Booksy, Spacelift, Pythagora, Splx.ai, Infermedica, Sintra.ai,
  Gralio (process intelligence → automation, AI agents).

DO NOT penalize patterns that ARE category norms for B2B/enterprise:
  - "contact sales" / no public pricing → NORM for enterprise (NOT distribution kill).
  - sales_led + partnerships → A REAL distribution strategy (not "missing channels").
  - small case-study count on website → NORM for early stage (NOT "no proof").
  - marketing language with "AI-powered" → score the WEDGE, not the buzzwords.
  - founders without long bios → score on signal you HAVE (names, roles, prior co's),
    not on absence. Polish/CEE diaspora names + technical roles = positive signal.

POSITIVE signals to look for hard:
  - Quantified customer outcomes ("70-85% automation", "82% emails automated") → STRONG proof.
  - Named enterprise logos (Sylvan Inc., GT Golf, etc.) → STRONG proof for early stage.
  - Specific integrations (n8n, Zapier, Make, Salesforce) → real product surface.
  - SOC 2 / compliance signals → enterprise-readiness.
  - CEE-diaspora founder names → mandate fit.
  - Hot category signals (AI agents, process intelligence, infra) → timing 8-9, not 5.

══════════════════════════════════════════════════════════════════════
HARD RULE — NO BARE NUMBERS
══════════════════════════════════════════════════════════════════════
Every numeric score MUST be paired with its `*_reasoning` field that:
  1. Cites the specific FACT (quote or paraphrase) from the digest that drives it.
  2. Names the comparison ("vs. UiPath", "vs. table-stakes RPA", "vs. portfolio Spacelift").
  3. Explains the band ("8 because hot category + named enterprise logos; not 9 because no MRR proof").
A score without reasoning is INVALID. Never write "5.0" without telling me WHY 5 not 6 not 4.

══════════════════════════════════════════════════════════════════════
OUTPUT — ONE JSON object, top-level keys ONLY:
══════════════════════════════════════════════════════════════════════
{
  "feature_parity": {
    "feature_parity_score": 0-10 (HIGH = clone / table stakes — BAD),
    "feature_parity_reasoning": "score=X because <competitor name> does <thing> — this co's <unique angle> is <degree of differentiation>",
    "has_clear_unique_angle": bool,
    "unique_angle": string,
    "is_unique_or_table_stakes": string,
    "strongest_competitor": string,
    "why_competitor_may_win": string
  },
  "trend": {
    "trend_direction": "up|flat|down|unclear",
    "trend_velocity": "fast|medium|slow|unclear",
    "demand_drivers": [string, ...],
    "headwinds": [string, ...],
    "funding_activity": "hot|active|cold|unclear",
    "timing_score": 1-10,
    "timing_reasoning": "score=X because <specific inflection / catalyst> — e.g. 'AI agents tooling 2024-25 wave, GPT-4 unlocked process automation that was infeasible 2 years ago'"
  },
  "distribution": {
    "primary_channels": [string from {SEO,paid_ads,app_store_search,product_led_growth,viral,sales_led,partnerships,community,marketplace,unknown}, ...],
    "dist_evidence": [string, ...],
    "dist_missing": [string, ...],
    "distribution_score": 1-10,
    "distribution_score_reasoning": "score=X because primary channel is <X>, evidence is <Y>; for enterprise B2B sales_led + partnerships is a valid 6-7 baseline (not a kill)",
    "distribution_risk_score": 0-10,
    "distribution_risk_reasoning": "risk=X because <CAC pressure / channel concentration / sales cycle reasoning>",
    "likely_cac_pressure": "low|medium|high|unclear"
  },
  "retention": {
    "use_case_type": "daily_workflow|weekly_workflow|occasional_need|one_time_event|career_lifecycle|compliance_recurring|unclear",
    "natural_churn_risk": "low|medium|high|unclear",
    "expected_lifetime_months": number,
    "expansion_paths": [string, ...],
    "has_expansion_path": bool,
    "evidence_strength": "weak|medium|strong",
    "retention_structural_score": 1-10,
    "retention_reasoning": "score=X because use-case is <type>, expansion path is <yes/no via Y>; e.g. 'process diagnostic is occasional, BUT automation blueprint creates recurring SaaS surface — 6 not 4'"
  },
  "right_to_win": {
    "advantage_types": [{"type":"","claim":"","evidence":"","evidence_strength":"weak|medium|strong"}, ...],
    "strongest_advantage": string,
    "right_to_win_score": 1-10,
    "right_to_win_reasoning": "score=X because <SPECIFIC moat>: founder-market-fit (Polish AI/automation founders), proprietary data (process telemetry from N companies), IP, network effect, switching cost, regulatory. Tie EACH score point to a moat type.",
    "evidence_strength": "none|weak|medium|strong",
    "rtw_missing": [string, ...]
  },
  "outlier": {
    "market_size_outlier": 0-10,
    "market_size_reasoning": "score=X — TAM/buyer reasoning: 'enterprise process intelligence + automation buyer count = millions globally; ACV envelope $50k-500k; market size deserves 8'",
    "category_leadership_potential": 0-10,
    "category_leadership_reasoning": "score=X — credible #1 path? 'category is forming (process intelligence × AI agents); incumbents (Celonis) are slow on AI-native — 7'",
    "scalability": 0-10,
    "scalability_reasoning": "score=X — software vs service mix, deployment cost, automation level",
    "margin_profile": 0-10,
    "margin_profile_reasoning": "score=X — gross margin proxy from pricing model; SaaS=8-9, services-heavy=4-5",
    "distribution_asymmetry": 0-10,
    "distribution_asymmetry_reasoning": "score=X — unfair channel? founder network in CEE enterprise, viral loop, marketplace position",
    "defensibility": 0-10,
    "defensibility_reasoning": "score=X — moat type and durability; for AI co's data flywheel + workflow embedding > model wrapper",
    "fund_return_potential": 0-10,
    "fund_return_reasoning": "score=X — credible path to 50-100x from seed entry? Outcome size × probability. Be honest: most early co's deserve 5-7, only obvious outliers >=8.",
    "reasoning": "<1-paragraph rolled-up: thesis fit, biggest unknowns, why this overall outlier band>",
    "must_validate_next": [string, ...]
  }
}

When evidence is thin: do NOT default to 5.0 with no explanation. Either (a) score lower with explicit "X because website does not show <specific signal>", or (b) score conservatively-mid with explicit "X — <category-norm reasoning>; deck/founder call needed to move higher".
Use ONLY the digest + competitor snippets + score summary. Do not invent revenue, MRR, ARR."""


def _scores_one_liner(scores: WebsiteScoresOutput) -> str:
    return (
        f"traction={scores.traction_evidence.score}, market={scores.market_potential.score}, "
        f"urgency={scores.urgency_and_budget_signal.score}, differentiation={scores.differentiation.score}, "
        f"technical={scores.technical_depth_or_defensibility.score}, distribution_dim={scores.distribution_signal.score}"
    )


def run_vc_pack_llm(
    client: OpenAI,
    *,
    digest: str,
    bundle: CategoryOsintBundle,
    scores: WebsiteScoresOutput,
    market_saturation: float,
    category: str,
    model: str = OPENAI_MODEL,
) -> WebsiteVCPackLLM:
    user = "\n\n".join(
        [
            f"CATEGORY: {category}",
            f"MARKET_SATURATION_HEURISTIC (0-10, high=crowded): {market_saturation}",
            f"12_DIM_SUMMARY: {_scores_one_liner(scores)}",
            "FACT_DIGEST:",
            digest,
            "COMPETITOR_SEARCH_SNIPPETS_JSON:",
            bundle.comp_blob[:14000],
        ]
    )
    return json_llm(
        client,
        system=_PACK_SYSTEM,
        user=user,
        model=model,
        max_tokens=TOK_WEBSITE_VC_PACK_OUT,
        response_model=WebsiteVCPackLLM,
    )


def assemble_vc_sub_outputs(
    pack: WebsiteVCPackLLM,
    *,
    digest: str,
    facts: WebsiteFactsOutput,
    scores: WebsiteScoresOutput,
    ci: CompetitiveIntelligenceOutput,
) -> tuple[TrendAnalysisOutput, DistributionEngineOutput, RetentionModelOutput, RightToWinOutput, OutlierFilterOutput]:
    trend = assemble_trend_from_vcpack(
        pack.trend,
        digest_text=digest,
        market_saturation=float(ci.market_saturation_score),
    )
    dist = assemble_distribution_from_pack(
        primary_channels=pack.distribution.primary_channels,
        dist_evidence=pack.distribution.dist_evidence,
        dist_missing=pack.distribution.dist_missing,
        distribution_score=float(pack.distribution.distribution_score),
        distribution_risk_score=float(pack.distribution.distribution_risk_score),
        likely_cac_pressure=pack.distribution.likely_cac_pressure,
        facts=facts,
        competitive=ci,
        distribution_score_reasoning=pack.distribution.distribution_score_reasoning or "",
        distribution_risk_reasoning=pack.distribution.distribution_risk_reasoning or "",
    )
    r_llm = RetentionLLM(
        use_case_type=pack.retention.use_case_type,
        natural_churn_risk=pack.retention.natural_churn_risk,
        expected_lifetime_months=float(pack.retention.expected_lifetime_months),
        expansion_paths=pack.retention.expansion_paths or [],
        has_expansion_path=bool(pack.retention.has_expansion_path),
        evidence_strength=pack.retention.evidence_strength
        if pack.retention.evidence_strength in ("weak", "medium", "strong")
        else "weak",
        retention_reasoning=pack.retention.retention_reasoning or "",
    )
    ret = compile_retention_model_output(r_llm, facts=facts)

    rtw_llm = RTWLLM(
        advantage_types=pack.right_to_win.advantage_types or [],
        strongest_advantage=pack.right_to_win.strongest_advantage or "",
        evidence_strength=pack.right_to_win.evidence_strength
        if pack.right_to_win.evidence_strength in ("none", "weak", "medium", "strong")
        else "weak",
        right_to_win_reasoning=pack.right_to_win.right_to_win_reasoning or "",
    )
    rtw = compile_right_to_win_output(rtw_llm, facts=facts, competitive=ci)
    if pack.right_to_win.rtw_missing:
        rtw = rtw.model_copy(update={"missing_data": list(pack.right_to_win.rtw_missing)})

    # Pass-through reasoning fields explicitly so they survive into compile_outlier_filter_output.
    _outlier_dump = pack.outlier.model_dump()
    o_llm = OutlierLLM.model_validate(_outlier_dump)
    out = compile_outlier_filter_output(o_llm)

    return trend, dist, ret, rtw, out
