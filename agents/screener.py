from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from openai import OpenAI

from agents.quality_checks import run_post_llm_quality_checks
from agents.schemas import (
    Gate1AssessmentParsed,
    Gate2BriefOutput,
    Gate2ExtractOutput,
    Gate2ScoreOutput,
)
from config.llm_cost import (
    OPENAI_MODEL,
    OPENAI_MODEL_LIGHT,
    TOK_BRIEF_OUT,
    TOK_EXTRACT_OUT,
    TOK_GATE1_OUT,
    TOK_SCORECARD_OUT,
)
from config.prompts import (
    GATE1_SYSTEM,
    GATE1_USER,
    GATE2A_SYSTEM,
    GATE2A_USER,
    GATE2B_SYSTEM,
    GATE2B_USER,
    GATE2C_SYSTEM,
    GATE2C_USER,
)
from agents.deck_rubric_caps import apply_deck_rubric_caps
from agents.research_playbook import playbook_json_for_prompt
from config.scoring import apply_outlier_adjustment, calculate_overall_score
from storage.models import Brief, EmailData, Gate1Result, Gate2Result, ScoredDimension

GATE2_THRESHOLD = float(os.getenv("GATE2_PASS_THRESHOLD", "6.0"))
MAX_PDF_MB = float(os.getenv("MAX_PDF_MB", "20"))

EVIDENCE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {"type": "string"},
        "source": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}

DIMENSION_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "reasoning": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "dimension_confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence_used": {"type": "array", "items": {"type": "string"}},
        "queries_run": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete search phrases or deck sections you relied on for this dimension",
        },
        "comparisons_made": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Benchmarks, comps, category norms compared to",
        },
        "why_not_higher": {"type": "string"},
        "why_not_lower": {"type": "string"},
        "evidence_ledger_item_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ids of evidence_ledger items this dimension cites",
        },
    },
    "required": [
        "score",
        "reasoning",
        "evidence",
        "missing_data",
        "dimension_confidence",
        "evidence_used",
        "queries_run",
        "comparisons_made",
        "why_not_higher",
        "why_not_lower",
        "evidence_ledger_item_ids",
    ],
}

LEDGER_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "source_type": {
            "type": "string",
            "enum": ["deck", "website", "external_search", "llm_inference"],
        },
        "claim": {"type": "string"},
        "source_url": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "used_for_dimensions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["source_type", "claim", "confidence", "used_for_dimensions"],
}

FIT_ASSESSMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_fit_assessment",
        "description": "Record Gate 1 classification for this inbound deal",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["PASS", "FAIL_CONFIDENT", "UNCERTAIN_READ_DECK"],
                },
                "geography_match": {"type": "boolean"},
                "stage_match": {"type": "boolean"},
                "sector_match": {"type": "boolean"},
                "company_name": {"type": "string"},
                "company_one_liner": {"type": "string"},
                "detected_stage": {"type": "string"},
                "detected_geography": {"type": "string"},
                "detected_sector": {"type": "string"},
                "rejection_reason": {"type": "string"},
                "flags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            },
            "required": [
                "verdict",
                "geography_match",
                "stage_match",
                "sector_match",
                "confidence",
            ],
        },
    },
}

EXTRACT_FACTS_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_extracted_facts",
        "description": "Structured facts extracted from pitch material",
        "parameters": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string"},
                "company_one_liner": {"type": "string"},
                "what_they_do": {"type": "string"},
                "founded_year": {"type": "string"},
                "founders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "background": {"type": "string"},
                        },
                        "required": ["name", "background"],
                    },
                },
                "geography": {"type": "string"},
                "stage": {"type": "string"},
                "traction": {"type": "string"},
                "fundraising_ask": {"type": "string"},
                "use_of_funds": {"type": "string"},
                "customers": {"type": "string"},
                "pricing": {"type": "string"},
                "market": {"type": "string"},
                "quotes": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                            "evidence": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
                        },
                        "required": ["key", "value", "evidence"],
                    },
                },
            },
            "required": ["what_they_do", "founders"],
        },
    },
}

DIMENSION_SCORES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_dimension_scores",
        "description": "VC dimension scores with evidence — no overall score",
        "parameters": {
            "type": "object",
            "properties": {
                "timing": DIMENSION_SCHEMA,
                "problem": DIMENSION_SCHEMA,
                "wedge": DIMENSION_SCHEMA,
                "founder_market_fit": DIMENSION_SCHEMA,
                "product_love": DIMENSION_SCHEMA,
                "execution_speed": DIMENSION_SCHEMA,
                "market": DIMENSION_SCHEMA,
                "moat_path": DIMENSION_SCHEMA,
                "traction": DIMENSION_SCHEMA,
                "business_model": DIMENSION_SCHEMA,
                "distribution": DIMENSION_SCHEMA,
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "missing_critical_data": {"type": "array", "items": {"type": "string"}},
                "should_ask_founder": {"type": "array", "items": {"type": "string"}},
                "solution_love_flags": {"type": "array", "items": {"type": "string"}},
                "slow_execution_flags": {"type": "array", "items": {"type": "string"}},
                "evidence_ledger": {"type": "array", "items": LEDGER_ITEM_SCHEMA},
            },
            "required": [
                "timing",
                "problem",
                "wedge",
                "founder_market_fit",
                "product_love",
                "execution_speed",
                "market",
                "moat_path",
                "traction",
                "business_model",
                "distribution",
                "confidence",
                "missing_critical_data",
                "should_ask_founder",
                "solution_love_flags",
                "slow_execution_flags",
                "evidence_ledger",
            ],
        },
    },
}

BRIEF_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_brief",
        "description": "Partner brief — no rescoring",
        "parameters": {
            "type": "object",
            "properties": {
                "executive_summary": {"type": "string"},
                "venture_scale_assessment": {"type": "string"},
                "top_strengths": {"type": "array", "items": {"type": "string"}},
                "top_concerns": {"type": "array", "items": {"type": "string"}},
                "comparable_portfolio_company": {"type": "string"},
                "recommendation": {
                    "type": "string",
                    "enum": ["STRONG_YES", "YES", "MAYBE", "NO", "STRONG_NO"],
                },
                "recommendation_rationale": {"type": "string"},
            },
            "required": [
                "executive_summary",
                "venture_scale_assessment",
                "recommendation",
                "recommendation_rationale",
            ],
        },
    },
}


def _cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    inp = float(os.getenv("OPENAI_PRICE_INPUT_PER_1M", "5.0"))
    out = float(os.getenv("OPENAI_PRICE_OUTPUT_PER_1M", "15.0"))
    return round((prompt_tokens * inp + completion_tokens * out) / 1_000_000, 6)


class ScreeningAgent:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _check_pdf_size(self, pdf_bytes: bytes) -> None:
        mb = len(pdf_bytes) / (1024 * 1024)
        if mb > MAX_PDF_MB:
            raise ValueError(f"PDF too large: {mb:.1f}MB (limit: {MAX_PDF_MB}MB)")

    def _extract_tool_input(self, response) -> dict | None:
        try:
            tool_call = response.choices[0].message.tool_calls[0]
            return json.loads(tool_call.function.arguments)
        except (IndexError, AttributeError, TypeError, json.JSONDecodeError):
            return None

    def _telemetry_from_response(self, t0: float, response) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        return {
            "started_at": None,
            "finished_at": None,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "input_tokens": pt,
            "output_tokens": ct,
            "cost_usd": _cost_usd(pt, ct),
        }

    def _merge_telemetry(self, *parts: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "started_at": None,
            "finished_at": None,
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        }
        for p in parts:
            if not p:
                continue
            out["latency_ms"] += int(p.get("latency_ms") or 0)
            out["input_tokens"] += int(p.get("input_tokens") or 0)
            out["output_tokens"] += int(p.get("output_tokens") or 0)
            out["cost_usd"] += float(p.get("cost_usd") or 0)
        out["cost_usd"] = round(out["cost_usd"], 6)
        return out

    def _chat_tool(
        self,
        *,
        system: str,
        user_content: str | list,
        tools: list,
        tool_name: str,
        max_tokens: int,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> tuple[dict | None, dict[str, Any]]:
        t0 = time.perf_counter()
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        response = self.client.chat.completions.create(
            model=model or OPENAI_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_name}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        tel = self._telemetry_from_response(t0, response)
        tel["started_at"] = started
        tel["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self._extract_tool_input(response), tel

    def _validate_with_retry(
        self,
        model_cls,
        raw: dict | None,
        *,
        retry_fn,
    ):
        if raw is None:
            raw2, _ = retry_fn()
            raw = raw2
        try:
            return model_cls.model_validate(raw)
        except Exception:
            raw2, _ = retry_fn()
            if raw2 is None:
                raise
            return model_cls.model_validate(raw2)

    def gate1_fit_check(self, email: EmailData) -> tuple[Gate1Result, dict[str, Any]]:
        attachment_info = (
            f"ATTACHMENT: {email.pdf_filename} (PDF pitch deck attached)"
            if email.has_pdf
            else "No PDF attachment detected."
        )
        user_msg = GATE1_USER.format(
            sender_name=email.sender_name,
            sender_email=email.sender_email,
            subject=email.subject,
            date=email.date,
            body=email.body,
            attachment_info=attachment_info,
        )

        def call():
            return self._chat_tool(
                system=GATE1_SYSTEM,
                user_content=user_msg,
                tools=[FIT_ASSESSMENT_TOOL],
                tool_name="submit_fit_assessment",
                max_tokens=TOK_GATE1_OUT,
                model=OPENAI_MODEL_LIGHT,
            )

        raw, tel = call()
        parsed = self._validate_with_retry(Gate1AssessmentParsed, raw, retry_fn=call)

        return Gate1Result(
            verdict=parsed.verdict,
            geography_match=parsed.geography_match,
            stage_match=parsed.stage_match,
            sector_match=parsed.sector_match,
            company_name=parsed.company_name or "",
            company_one_liner=parsed.company_one_liner or "",
            detected_stage=parsed.detected_stage or "",
            detected_geography=parsed.detected_geography or "",
            detected_sector=parsed.detected_sector or "",
            rejection_reason=parsed.rejection_reason or "",
            flags=list(parsed.flags or []),
            confidence=parsed.confidence,
        ), tel

    def run_gate2_pipeline(
        self,
        email: EmailData,
        gate1: Gate1Result,
        *,
        deck_markdown: Optional[str],
        analysis_mode: str,
        screening_depth: str = "INITIAL",
    ) -> tuple[Gate2Result, dict[str, Any], str, str]:
        if deck_markdown:
            deck_block = deck_markdown
        else:
            deck_block = (
                "[No pitch deck text available — extract facts from the email body only.]"
            )

        user_a = GATE2A_USER.format(
            sender_name=email.sender_name,
            sender_email=email.sender_email,
            subject=email.subject,
            date=email.date,
            email_body=email.body,
            deck_block=deck_block,
        )

        def call_a():
            return self._chat_tool(
                system=GATE2A_SYSTEM,
                user_content=user_a,
                tools=[EXTRACT_FACTS_TOOL],
                tool_name="submit_extracted_facts",
                max_tokens=TOK_EXTRACT_OUT,
            )

        raw_a, tel_a = call_a()
        extract = self._validate_with_retry(Gate2ExtractOutput, raw_a, retry_fn=call_a)
        facts_dict = extract.model_dump()
        facts_json = json.dumps(facts_dict, ensure_ascii=False, indent=2)

        user_b = GATE2B_USER.format(
            facts_json=facts_json,
            research_playbook_json=playbook_json_for_prompt(
                str(extract.company_name or gate1.company_name or "startup"),
                str(gate1.detected_sector or extract.market or "SaaS")[:120],
                None,
            ),
        )

        def call_b():
            return self._chat_tool(
                system=GATE2B_SYSTEM,
                user_content=user_b,
                tools=[DIMENSION_SCORES_TOOL],
                tool_name="submit_dimension_scores",
                max_tokens=TOK_SCORECARD_OUT,
            )

        raw_b, tel_b = call_b()
        scores_parsed = self._validate_with_retry(Gate2ScoreOutput, raw_b, retry_fn=call_b)

        scores_capped, rubric_notes = apply_deck_rubric_caps(scores_parsed, facts_dict)

        dim_scores = {
            "timing": int(scores_capped.timing.score),
            "problem": int(scores_capped.problem.score),
            "wedge": int(scores_capped.wedge.score),
            "founder_market_fit": int(scores_capped.founder_market_fit.score),
            "product_love": int(scores_capped.product_love.score),
            "execution_speed": int(scores_capped.execution_speed.score),
            "market": int(scores_capped.market.score),
            "moat_path": int(scores_capped.moat_path.score),
            "traction": int(scores_capped.traction.score),
            "business_model": int(scores_capped.business_model.score),
            "distribution": int(scores_capped.distribution.score),
        }
        overall_raw = calculate_overall_score(dim_scores)
        overall, outlier_note = apply_outlier_adjustment(overall_raw, dim_scores)
        passes = overall >= GATE2_THRESHOLD

        scoring_audit: list[str] = list(rubric_notes)
        if outlier_note:
            scoring_audit.append(outlier_note)
        if abs(overall - overall_raw) > 1e-6:
            scoring_audit.append(f"internal_weighted_raw={overall_raw:.2f}_after_outlier_gate={overall:.2f}")
        scoring_audit.append(f"evidence_ledger_items={len(scores_capped.evidence_ledger)}")

        dims_dump = scores_capped.model_dump()
        dimensions_json = json.dumps(dims_dump, ensure_ascii=False, indent=2)

        tel_c: dict[str, Any] = {
            "started_at": None,
            "finished_at": None,
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        }
        if str(screening_depth or "INITIAL").upper() == "INITIAL":
            # Token-cheap mode: skip extra brief LLM call in initial screen.
            rec = "MAYBE" if overall >= 5.0 else "NO"
            brief_parsed = Gate2BriefOutput(
                executive_summary=(
                    f"Initial screen based on email/deck only. Overall deck evidence score: {overall:.2f}/10."
                )[:320],
                venture_scale_assessment="Initial stage only; deeper market/founder verification deferred.",
                top_strengths=list(scores_capped.problem.evidence_used or [])[:3],
                top_concerns=list(scores_capped.missing_critical_data or [])[:3],
                comparable_portfolio_company="",
                recommendation=rec,  # type: ignore[arg-type]
                recommendation_rationale="Initial mode avoids deep memo and external research by design.",
            )
        else:
            user_c = GATE2C_USER.format(
                facts_json=facts_json,
                dimensions_json=dimensions_json,
                overall_score=overall,
            )

            def call_c():
                return self._chat_tool(
                    system=GATE2C_SYSTEM,
                    user_content=user_c,
                    tools=[BRIEF_TOOL],
                    tool_name="submit_brief",
                    max_tokens=TOK_BRIEF_OUT,
                )

            raw_c, tel_c = call_c()
            brief_parsed = self._validate_with_retry(Gate2BriefOutput, raw_c, retry_fn=call_c)

        def sd(d) -> ScoredDimension:
            ev = [e.model_dump() for e in d.evidence]
            return ScoredDimension(
                score=int(d.score),
                reasoning=d.reasoning,
                evidence=ev,
                missing_data=list(d.missing_data or []),
                dim_confidence=str(getattr(d, "dimension_confidence", None) or "medium"),
                evidence_used=list(d.evidence_used or []),
                queries_run=list(d.queries_run or []),
                comparisons_made=list(d.comparisons_made or []),
                why_not_higher=str(getattr(d, "why_not_higher", None) or ""),
                why_not_lower=str(getattr(d, "why_not_lower", None) or ""),
                evidence_ledger_item_ids=list(getattr(d, "evidence_ledger_item_ids", None) or []),
            )

        traction_summary = extract.traction or facts_dict.get("traction") or ""

        quality_flags = run_post_llm_quality_checks(
            facts=facts_dict,
            scores=dim_scores,
            traction_summary=str(traction_summary),
        )
        gate2 = Gate2Result(
            passes=passes,
            overall_score=overall,
            recommendation=brief_parsed.recommendation,
            company_name=extract.company_name or gate1.company_name,
            company_one_liner=extract.company_one_liner or gate1.company_one_liner,
            what_they_do=extract.what_they_do or "",
            founded_year=extract.founded_year or "",
            founders=list(extract.founders or []),
            business_model_description=extract.pricing or "",
            fundraising_ask=extract.fundraising_ask or "",
            use_of_funds=extract.use_of_funds or "",
            current_traction_summary=extract.traction or "",
            timing=sd(scores_capped.timing),
            problem=sd(scores_capped.problem),
            wedge=sd(scores_capped.wedge),
            founder_market_fit=sd(scores_capped.founder_market_fit),
            product_love=sd(scores_capped.product_love),
            execution_speed=sd(scores_capped.execution_speed),
            market=sd(scores_capped.market),
            moat_path=sd(scores_capped.moat_path),
            traction=sd(scores_capped.traction),
            business_model=sd(scores_capped.business_model),
            distribution=sd(scores_capped.distribution),
            solution_love_flags=list(scores_capped.solution_love_flags or []),
            slow_execution_flags=list(scores_capped.slow_execution_flags or []),
            executive_summary=brief_parsed.executive_summary,
            venture_scale_assessment=brief_parsed.venture_scale_assessment,
            top_strengths=list(brief_parsed.top_strengths or [])[:3],
            top_concerns=list(brief_parsed.top_concerns or [])[:3],
            comparable_portfolio_company=brief_parsed.comparable_portfolio_company or "",
            recommendation_rationale=brief_parsed.recommendation_rationale,
            gate2_confidence=scores_capped.confidence,
            missing_critical_data=list(scores_capped.missing_critical_data or []),
            should_ask_founder=list(scores_capped.should_ask_founder or []),
            quality_flags=quality_flags,
            scoring_audit=scoring_audit,
            evidence_ledger=[li.model_dump() for li in (scores_capped.evidence_ledger or [])],
            screening_depth=str(screening_depth or "INITIAL").upper(),
        )

        tel_g2 = self._merge_telemetry(tel_a, tel_b, tel_c)
        tel_g2["started_at"] = tel_a.get("started_at")
        tel_g2["finished_at"] = tel_c.get("finished_at")
        return gate2, tel_g2, facts_json, dimensions_json

    def build_brief(
        self,
        email: EmailData,
        gate1: Gate1Result,
        gate2: Gate2Result,
        *,
        external_market: Any = None,
        final_investment_decision: Any = None,
    ) -> Brief:
        internal = gate2.overall_score
        ext_on = final_investment_decision is not None and external_market is not None
        display_overall = (
            float(final_investment_decision.final_score)
            if ext_on and final_investment_decision is not None
            else internal
        )
        reco = (
            str(final_investment_decision.recommendation)
            if ext_on and final_investment_decision is not None
            else gate2.recommendation
        )
        _dims_for_audit = [
            gate2.timing,
            gate2.problem,
            gate2.wedge,
            gate2.founder_market_fit,
            gate2.product_love,
            gate2.execution_speed,
            gate2.market,
            gate2.moat_path,
            gate2.traction,
            gate2.business_model,
            gate2.distribution,
        ]
        queries_flat: list[str] = []
        for d in _dims_for_audit:
            for q in d.queries_run or []:
                if q and q not in queries_flat:
                    queries_flat.append(q)
        how_scores_formed: list[str] = [
            "HOW SCORES WERE FORMED — methodological trace (internal deck score)",
            "Each scorecard row carries: dim confidence, evidence_used, queries_run, comparisons_made, why_not_higher / why_not_lower, deck quotes, and links to evidence_ledger_item_ids where populated.",
            f"Distinct queries_run across dimensions: {len(queries_flat)} (see rows below for full lists).",
        ]
        for q in queries_flat[:20]:
            how_scores_formed.append(f"  query: {q}")
        for line in getattr(gate2, "scoring_audit", None) or []:
            how_scores_formed.append(f"  caps / gates: {line}")
        ledger = getattr(gate2, "evidence_ledger", None) or []
        how_scores_formed.append(f"  evidence_ledger count: {len(ledger)}")
        for li in ledger[:12]:
            st = li.get("source_type", "?")
            cl = (li.get("claim") or "")[:160]
            how_scores_formed.append(f"    [{st}] {cl}")
        how_scores_formed.append(
            "  assumptions: sparse facts → lower dimension_confidence and deterministic rubric caps in code (market / competition heuristics)."
        )
        if gate2.missing_critical_data:
            how_scores_formed.append(
                "  missing data (global): " + "; ".join(gate2.missing_critical_data[:10])
            )
        return Brief(
            company_name=gate2.company_name or gate1.company_name or email.sender_name,
            sender_name=email.sender_name,
            sender_email=email.sender_email,
            date_received=email.date,
            overall_score=display_overall,
            recommendation=reco,
            internal_deck_score=internal,
            final_composite_score=(
                float(final_investment_decision.final_score)
                if ext_on and final_investment_decision is not None
                else None
            ),
            external_check_enabled=ext_on,
            one_liner=gate2.company_one_liner or gate1.company_one_liner,
            what_they_do=gate2.what_they_do,
            founded_year=gate2.founded_year,
            founders=gate2.founders,
            business_model_description=gate2.business_model_description,
            fundraising_ask=gate2.fundraising_ask,
            use_of_funds=gate2.use_of_funds,
            current_traction_summary=gate2.current_traction_summary,
            geography=gate1.detected_geography,
            stage=gate1.detected_stage,
            sector=gate1.detected_sector,
            scorecard={
                "Timing / Why Now": gate2.timing,
                "Problem": gate2.problem,
                "Wedge": gate2.wedge,
                "Founder-Market Fit": gate2.founder_market_fit,
                "Product Love": gate2.product_love,
                "Execution Speed": gate2.execution_speed,
                "Market": gate2.market,
                "Moat Path": gate2.moat_path,
                "Traction": gate2.traction,
                "Business Model": gate2.business_model,
                "Distribution": gate2.distribution,
            },
            solution_love_flags=gate2.solution_love_flags,
            slow_execution_flags=gate2.slow_execution_flags,
            strengths=gate2.top_strengths,
            concerns=gate2.top_concerns,
            executive_summary=gate2.executive_summary,
            venture_scale_assessment=gate2.venture_scale_assessment,
            comparable=gate2.comparable_portfolio_company,
            email_body_preview=email.body[:500],
            gate2_confidence=gate2.gate2_confidence,
            missing_critical_data=gate2.missing_critical_data,
            should_ask_founder=gate2.should_ask_founder,
            quality_flags=gate2.quality_flags,
            external_market=external_market,
            final_investment_decision=final_investment_decision,
            how_scores_formed=how_scores_formed,
        )
