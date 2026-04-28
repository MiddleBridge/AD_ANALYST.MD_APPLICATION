"""Website URL screening: crawl → facts → Gate 1 → 12-dimension scores → VC layer → verdict."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

from openai import OpenAI

from agents.schemas_website import (
    WebsiteFactsOutput,
    WebsiteGate1Output,
    WebsiteInvestmentAssessment,
    WebsiteScoresOutput,
)
from agents.website_enrichment import (
    enrich_from_markdown,
    merge_enrichment_into_facts,
)
from agents.hq_resolver import enabled as _hq_enabled, resolve_hq_country
from agents.website_quality import (
    build_evidence_table,
    deterministic_website_kill_flags,
    facts_dict_from_model,
    filter_kill_flags_against_dimensions,
    merge_kill_flags,
)
from config.llm_cost import (
    OPENAI_MODEL,
    OPENAI_MODEL_LIGHT,
    TOK_GATE1_OUT,
    TOK_WEBSITE_FACTS_OUT,
    TOK_WEBSITE_SCORES_OUT,
    WEBSITE_LLM_MARKDOWN_CHARS,
)
from config.website_prompts import (
    WEBSITE_FACTS_SYSTEM,
    WEBSITE_FACTS_USER,
    WEBSITE_GATE1_SYSTEM,
    WEBSITE_GATE1_USER,
    WEBSITE_SCORE_SYSTEM,
    WEBSITE_SCORE_USER,
)
from config.website_scoring import (
    apply_website_evidence_caps,
    calculate_website_weighted_score,
)
from agents.website_vc_pipeline import build_website_vc_final
from storage.models import Gate1Result
from tools.website_to_markdown import WebsiteMarkdownResult, fetch_website_markdown

# 12-dim website scoring; override with OPENAI_WEBSITE_MODEL if set.
WEBSITE_SCORE_MODEL = os.getenv("OPENAI_WEBSITE_MODEL") or OPENAI_MODEL

EVIDENCE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {"type": "string"},
        "source": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}

WEB_DIM_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "reasoning": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
        "missing_data": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "reasoning", "evidence", "missing_data"],
}

WEBSITE_FACTS_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_website_facts",
        "description": "Structured facts from website markdown only",
        "parameters": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string"},
                "one_liner": {"type": "string"},
                "founded_year": {"type": "string"},
                "founders": {"type": "string"},
                "team": {"type": "string"},
                "target_customer": {"type": "string"},
                "sector": {"type": "string"},
                "geography": {"type": "string"},
                "product_description": {"type": "string"},
                "use_cases": {"type": "string"},
                "pricing_signals": {"type": "string"},
                "customer_proof": {"type": "string"},
                "logos_or_case_studies": {"type": "string"},
                "traction_signals": {"type": "string"},
                "team_signals": {"type": "string"},
                "technical_depth": {"type": "string"},
                "integrations": {"type": "string"},
                "security_compliance_signals": {"type": "string"},
                "hiring_signals": {"type": "string"},
                "blog_content_velocity": {"type": "string"},
                "market_claims": {"type": "string"},
                "inferred_signals": {"type": "string"},
                "unclear_or_missing_data": {"type": "string"},
            },
            "required": ["company_name", "one_liner", "product_description", "unclear_or_missing_data"],
        },
    },
}

WEBSITE_GATE1_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_website_gate1",
        "description": "Website-only mandate check for Inovo.VC",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["PASS", "FAIL_CONFIDENT", "UNCERTAIN_NEED_MORE_CONTEXT"],
                },
                "geography_match": {"type": "boolean"},
                "stage_guess": {"type": "string"},
                "sector_match": {"type": "boolean"},
                "company_name": {"type": "string"},
                "rejection_reason": {"type": "string"},
                "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            },
            "required": [
                "verdict",
                "geography_match",
                "stage_guess",
                "sector_match",
                "confidence",
            ],
        },
    },
}

WEBSITE_SCORES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_website_scores",
        "description": "12 website evidence dimensions 1-10",
        "parameters": {
            "type": "object",
            "properties": {
                "problem_clarity": WEB_DIM_SCHEMA,
                "product_clarity": WEB_DIM_SCHEMA,
                "target_customer_clarity": WEB_DIM_SCHEMA,
                "urgency_and_budget_signal": WEB_DIM_SCHEMA,
                "differentiation": WEB_DIM_SCHEMA,
                "traction_evidence": WEB_DIM_SCHEMA,
                "customer_proof": WEB_DIM_SCHEMA,
                "business_model_clarity": WEB_DIM_SCHEMA,
                "founder_or_team_signal": WEB_DIM_SCHEMA,
                "distribution_signal": WEB_DIM_SCHEMA,
                "market_potential": WEB_DIM_SCHEMA,
                "technical_depth_or_defensibility": WEB_DIM_SCHEMA,
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "missing_critical_data": {"type": "array", "items": {"type": "string"}},
                "should_ask_founder": {"type": "array", "items": {"type": "string"}},
                "suggested_kill_flags": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "problem_clarity",
                "product_clarity",
                "target_customer_clarity",
                "urgency_and_budget_signal",
                "differentiation",
                "traction_evidence",
                "customer_proof",
                "business_model_clarity",
                "founder_or_team_signal",
                "distribution_signal",
                "market_potential",
                "technical_depth_or_defensibility",
                "confidence",
                "missing_critical_data",
                "should_ask_founder",
                "suggested_kill_flags",
            ],
        },
    },
}


def _extract_tool_input(response) -> dict | None:
    try:
        tool_call = response.choices[0].message.tool_calls[0]
        return json.loads(tool_call.function.arguments)
    except (IndexError, AttributeError, TypeError, json.JSONDecodeError):
        return None


_CEE_TLDS = (
    ".pl",
    ".cz",
    ".sk",
    ".hu",
    ".ro",
    ".bg",
    ".hr",
    ".si",
    ".ee",
    ".lv",
    ".lt",
    ".rs",
    ".ua",
    ".md",
)


def _cee_domain_signal(website_url: str) -> bool:
    try:
        host = urlparse(website_url or "").netloc.lower()
    except Exception:
        host = ""
    if not host:
        return False
    return any(host.endswith(t) for t in _CEE_TLDS)


_CEE_DIASPORA_NAME_PATTERNS = (
    # Polish surname endings
    "ski", "cki", "wicz", "wski", "czyk", "czuk", "iak", "ak ", "ek ",
    # Common Polish given names (lowercase substrings — match within "Michał", "Tymon", etc.)
    "michał", "michal", "tomasz", "tymon", "krzysztof", "wojciech", "łukasz", "lukasz",
    "paweł", "pawel", "marcin", "bartosz", "kacper", "jakub", "mateusz", "piotr",
    "grzegorz", "andrzej", "rafał", "rafal", "dominik", "katarzyna", "agnieszka",
    "magdalena", "joanna", "natalia", "aleksandra",
    # Lithuanian / Latvian / Estonian / Czech / Slovak / Romanian / Bulgarian common markers
    "vilnius", "kaunas", "tallinn", "tartu", "riga",
    # Croatian / ex-YU diaspora (common in US-YC startups)
    "ović", "croatia", "croatian", "zagreb",
    "sabljic", "sabljić", "rasic", "rašić", "ostrez", "zvonimir", "senko",
)


def _cee_founder_signal(facts: WebsiteFactsOutput) -> bool:
    """CEE diaspora signal from founder/team names — Polish/CEE surnames + given names."""
    blob = " ".join(
        [
            facts.founders or "",
            facts.team or "",
            facts.team_signals or "",
            facts.inferred_signals or "",
        ]
    ).lower()
    if not blob.strip():
        return False
    return any(p in blob for p in _CEE_DIASPORA_NAME_PATTERNS)


def _cee_text_signal(facts: WebsiteFactsOutput) -> bool:
    blob = " ".join(
        [
            facts.geography or "",
            facts.one_liner or "",
            facts.product_description or "",
            facts.company_name or "",
            facts.target_customer or "",
            facts.founders or "",
            facts.team or "",
            facts.team_signals or "",
            facts.inferred_signals or "",
        ]
    ).lower()
    markers = (
        "poland",
        "polska",
        "warsaw",
        "warszawa",
        "krakow",
        "wrocław",
        "gdańsk",
        "gdansk",
        "czech",
        "prague",
        "praha",
        "budapest",
        "bucharest",
        "bucuresti",
        "cee",
        "central and eastern europe",
        "central europe",
        "slovakia",
        "bratislava",
        "estonia",
        "tallinn",
        "latvia",
        "riga",
        "lithuania",
        "vilnius",
        "zagreb",
        "ljubljana",
        "sofia",
        "croatia",
        "croatian",
        "serbia",
        "serbian",
        "ukraine",
        "ukrainian",
    )
    return any(m in blob for m in markers)


def _infer_sector_from_public_copy(facts: WebsiteFactsOutput) -> None:
    """Broad sector hint when the extractor returns unknown — improves mandate UX without pretending it's verified."""
    raw = (facts.sector or "").strip().lower()
    if raw and raw not in ("unknown", "n/a", "none", "not stated", "missing", ""):
        return
    blob = " ".join(
        [
            facts.one_liner or "",
            facts.product_description or "",
            facts.use_cases or "",
            facts.company_name or "",
        ]
    ).lower()
    if not blob.strip():
        return
    label = ""
    # IDE / dev workflow before generic "AI + UI" (avoid mis-labeling e.g. VS Code agents).
    if any(
        k in blob
        for k in (
            "vs code",
            "vscode",
            "visual studio code",
            "cursor",
            "ide",
            "developer tool",
            "full-stack",
            "full stack",
            "debugging",
            "github",
        )
    ) and any(
        k in blob for k in ("code", "coding", "developer", "engineering", "deploy", "repository", "agent")
    ):
        label = "Developer tools / AI coding"
    elif any(k in blob for k in ("fintech", "payments", "banking", "lending")):
        label = "FinTech"
    elif any(k in blob for k in ("healthcare", "health tech", "clinical", "medical", "hipaa")):
        label = "HealthTech"
    elif any(
        k in blob
        for k in ("devops", "infrastructure", "data infra", "observability")
    ):
        label = "Developer tools / infra"
    elif "marketplace" in blob:
        label = "Marketplace"
    elif any(k in blob for k in ("ai", "llm", "machine learning")):
        if any(k in blob for k in ("design", "ui", "ux", "prototype", "figma", "interface", "mockup")):
            label = "AI / UI & design tools"
        elif any(k in blob for k in ("agent", "workflow", "automation", "copilot")):
            label = "AI agents / workflow automation"
        else:
            label = "AI / ML (software)"
    elif "saas" in blob or "b2b" in blob:
        label = "B2B SaaS"
    if not label:
        return
    facts.sector = label
    note = f"sector_inferred_from_public_copy: {label} (weak)"
    facts.inferred_signals = ((facts.inferred_signals + "\n") if facts.inferred_signals else "") + note


def _maybe_enrich_founder_roots_osint(
    facts: WebsiteFactsOutput,
    root_url: str,
    enrichment_notes: list[str],
) -> None:
    """When the site reads US/SF but omits diaspora, optional Tavily pass fills inferred_signals."""
    from agents.founder_roots_resolver import resolve_founder_roots_cee, roots_osint_enabled

    if not roots_osint_enabled():
        return
    has_founders = bool((facts.founders or "").strip()) and (facts.founders or "").strip().lower() not in ("unknown", "n/a", "none")
    if has_founders and (_cee_founder_signal(facts) or _cee_text_signal(facts) or _cee_domain_signal(root_url)):
        return
    def _founder_names(raw: str) -> list[str]:
        txt = (raw or "").strip()
        if not txt or txt.lower() in ("unknown", "n/a", "none"):
            return []
        names: list[str] = []
        for chunk in txt.split(";"):
            c = chunk.strip()
            if not c:
                continue
            # "Name — role" or "Name - role" -> keep name part
            if "—" in c:
                c = c.split("—", 1)[0].strip()
            elif " - " in c:
                c = c.split(" - ", 1)[0].strip()
            if c:
                names.append(c)
        return names[:6]

    def _token_to_country(token: str) -> str:
        t = (token or "").lower()
        for k, v in (
            ("pol", "Poland"),
            ("lithuan", "Lithuania"),
            ("latv", "Latvia"),
            ("eston", "Estonia"),
            ("czech", "Czech Republic"),
            ("slovak", "Slovakia"),
            ("hungar", "Hungary"),
            ("roman", "Romania"),
            ("bulgar", "Bulgaria"),
            ("croat", "Croatia"),
            ("sloven", "Slovenia"),
            ("serb", "Serbia"),
            ("ukrain", "Ukraine"),
            ("bosnia", "Bosnia and Herzegovina"),
            ("montenegro", "Montenegro"),
            ("macedon", "North Macedonia"),
            ("alban", "Albania"),
            ("moldov", "Moldova"),
            ("kosovo", "Kosovo"),
        ):
            if k in t:
                return v
        return "CEE / diaspora"

    res = resolve_founder_roots_cee(company_name=facts.company_name or "", website_url=root_url)
    if res.founder_names and not has_founders:
        facts.founders = "; ".join([f"{n} — Founder" for n in res.founder_names[:5]])
        if not (facts.team or "").strip():
            facts.team = facts.founders
        enrichment_notes.append(f"founder_roots_osint: filled founders from snippets ({len(res.founder_names)})")

    if res.cee_signal and res.summary:
        facts.inferred_signals = ((facts.inferred_signals + "\n") if facts.inferred_signals else "") + res.summary.strip()
        if res.matched_signal:
            facts.inferred_signals = (
                (facts.inferred_signals + "\n") if facts.inferred_signals else ""
            ) + f"founder_nationality_hint: {res.matched_signal} (osint)"
            fns = _founder_names(facts.founders or facts.team or "")
            if fns:
                country = _token_to_country(res.matched_signal)
                per_founder = "; ".join(f"{nm} -> {country} (osint)" for nm in fns)
                facts.inferred_signals = (
                    (facts.inferred_signals + "\n") if facts.inferred_signals else ""
                ) + f"founder_nationality_hint: {per_founder}"
        enrichment_notes.append(f"founder_roots_osint: CEE/diaspora hint (n_sources={res.n_sources})")
        return

    # No surname-based fallback: keep nationality hints evidence-backed (OSINT/explicit signals only).


def _effective_geography_match(
    g1w: WebsiteGate1Output,
    *,
    facts: WebsiteFactsOutput,
    website_url: str,
) -> bool:
    """LLM sometimes sets geography_match=false on thin sites; .pl / CEE copy is a hard hint for mandate."""
    if g1w.geography_match:
        return True
    if _cee_domain_signal(website_url) or _cee_text_signal(facts) or _cee_founder_signal(facts):
        return True
    return False


def _mandate_blocks_before_scoring(
    g1w: WebsiteGate1Output,
    *,
    facts: WebsiteFactsOutput,
    website_url: str,
) -> tuple[bool, list[str]]:
    """Hard gates before 12-dim + VC.

    UNCERTAIN + geography_match=false was stopping the whole pipeline (0 scores) even when the
    model was hedging. We only hard-cut geo/sector when the gate is confident, unless env forces strict.
    """
    kills: list[str] = []
    if g1w.verdict == "FAIL_CONFIDENT":
        kills.append("gate1_fail_confident")

    conf_hi = (g1w.confidence or "").upper() == "HIGH"
    strict_geo = os.getenv("WEBSITE_MANDATE_STRICT_GEOGRAPHY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    strict_sector = os.getenv("WEBSITE_MANDATE_STRICT_SECTOR", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

    geo_ok = _effective_geography_match(g1w, facts=facts, website_url=website_url)
    geo_hard = not geo_ok and (
        strict_geo or g1w.verdict == "FAIL_CONFIDENT" or conf_hi
    )
    if geo_hard:
        kills.append("mandate_fail_geography")

    # Sector is often ambiguous from marketing pages; do not hard-block on sector when Gate1 is UNCERTAIN
    # unless strict mode is explicitly enabled or the gate is confident.
    sector_hard = strict_sector and (not g1w.sector_match) and (
        g1w.verdict == "FAIL_CONFIDENT" or conf_hi
    )
    if sector_hard:
        kills.append("mandate_fail_sector")

    blocked = bool(kills)
    return blocked, kills


def _website_gate1_to_gate1(w: WebsiteGate1Output, facts: WebsiteFactsOutput) -> Gate1Result:
    sg = (w.stage_guess or "").lower()
    stage_ok = any(x in sg for x in ("pre-seed", "preseed", "seed", "early", "series a")) or w.verdict != "FAIL_CONFIDENT"
    return Gate1Result(
        verdict=w.verdict,
        geography_match=w.geography_match,
        stage_match=stage_ok,
        sector_match=w.sector_match,
        company_name=w.company_name or facts.company_name or "",
        detected_stage=w.stage_guess or "",
        detected_geography=facts.geography or "",
        detected_sector=facts.sector or "",
        rejection_reason=w.rejection_reason or "",
        confidence=w.confidence,
    )


def website_dimension_int_scores(s: WebsiteScoresOutput) -> dict[str, int]:
    return {
        "problem_clarity": int(s.problem_clarity.score),
        "product_clarity": int(s.product_clarity.score),
        "target_customer_clarity": int(s.target_customer_clarity.score),
        "urgency_and_budget_signal": int(s.urgency_and_budget_signal.score),
        "differentiation": int(s.differentiation.score),
        "traction_evidence": int(s.traction_evidence.score),
        "customer_proof": int(s.customer_proof.score),
        "business_model_clarity": int(s.business_model_clarity.score),
        "founder_or_team_signal": int(s.founder_or_team_signal.score),
        "distribution_signal": int(s.distribution_signal.score),
        "market_potential": int(s.market_potential.score),
        "technical_depth_or_defensibility": int(s.technical_depth_or_defensibility.score),
    }


def _derive_strengths_concerns(s: WebsiteScoresOutput) -> tuple[list[str], list[str]]:
    pairs = website_dimension_int_scores(s).items()
    ranked = sorted(pairs, key=lambda x: x[1], reverse=True)
    low = sorted(pairs, key=lambda x: x[1])
    strengths: list[str] = []
    concerns: list[str] = []
    for key, val in ranked[:3]:
        dim = getattr(s, key)
        reason = (dim.reasoning or "").strip().split(".")[0][:160]
        strengths.append(f"{key} ({val}/10): {reason}".strip())
    for key, val in low[:3]:
        dim = getattr(s, key)
        reason = (dim.reasoning or "").strip().split(".")[0][:160]
        concerns.append(f"{key} ({val}/10): {reason}".strip())
    return strengths, concerns


def _next_step(v: str) -> str:
    return {
        "REJECT_AUTO": "Reject — not a fit from public website signals.",
        "NEEDS_DECK": "Request pitch deck and light data room; website alone is insufficient.",
        "NEEDS_FOUNDER_CALL": "Short founder call + request deck; validate ICP, traction, and model.",
        "PASS_TO_HITL": "Route to partner HITL with external diligence summary.",
        "STRONG_SIGNAL": "Fast-track partner review; still confirm private metrics on call.",
    }.get(v, "Review manually.")


_FLOOR_BAND_NOTE = "Floor applied (4/10 PARTIAL): website provides at least minimal evidence; LLM was overly harsh given facts."


def _bump_dimension_floor(scores: WebsiteScoresOutput, name: str, floor: int, note: str) -> bool:
    """Raise a dimension score to `floor` if it is below it. Return True if changed."""
    dim = getattr(scores, name, None)
    if dim is None:
        return False
    if dim.score < floor:
        dim.score = floor  # type: ignore[assignment]
        existing = (dim.reasoning or "").strip()
        dim.reasoning = (note + " " + existing).strip()
        return True
    return False


def _apply_website_scoring_floors(scores: WebsiteScoresOutput, facts: WebsiteFactsOutput) -> None:
    """Prevent a 1/10 score on dimensions where the website actually carries
    evidence, even if the LLM was harsh. Each floor is conservative (4/10).

    Currently gated by env var ``WEBSITE_SCORING_FLOORS`` (default: enabled).
    """
    if os.getenv("WEBSITE_SCORING_FLOORS", "1").lower() not in ("1", "true", "yes"):
        return

    def _has(value: str) -> bool:
        v = (value or "").strip().lower()
        return bool(v) and v not in {"unknown", "n/a", "none", "not stated"}

    if _has(facts.founders) or _has(facts.team):
        _bump_dimension_floor(
            scores,
            "founder_or_team_signal",
            4,
            f"{_FLOOR_BAND_NOTE} Names/roles present on About/Team page.",
        )

    if _has(facts.pricing_signals):
        _bump_dimension_floor(
            scores,
            "business_model_clarity",
            4,
            f"{_FLOOR_BAND_NOTE} Monetization phrasing detected on site.",
        )

    if _has(facts.target_customer) or _has(facts.use_cases):
        _bump_dimension_floor(
            scores,
            "target_customer_clarity",
            4,
            f"{_FLOOR_BAND_NOTE} ICP phrasing detected on site.",
        )

    if _has(facts.integrations) or _has(facts.security_compliance_signals):
        _bump_dimension_floor(
            scores,
            "technical_depth_or_defensibility",
            4,
            f"{_FLOOR_BAND_NOTE} Integrations / compliance evidence detected.",
        )


class WebsiteScreeningAgent:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._telemetry_parts: list[dict[str, Any]] = []
        self._last_enrichment_hints = None
        self._last_enrichment_notes: list[str] = []

    def reset_telemetry(self) -> None:
        self._telemetry_parts = []
        self._last_enrichment_hints = None
        self._last_enrichment_notes = []

    def get_telemetry(self) -> list[dict[str, Any]]:
        return list(self._telemetry_parts)

    def _cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        inp = float(os.getenv("OPENAI_PRICE_INPUT_PER_1M", "5.0"))
        out = float(os.getenv("OPENAI_PRICE_OUTPUT_PER_1M", "15.0"))
        return round((prompt_tokens * inp + completion_tokens * out) / 1_000_000, 6)

    def _chat_tool(
        self,
        *,
        system: str,
        user_content: str,
        tools: list,
        tool_name: str,
        max_tokens: int,
        model: str | None = None,
        temperature: float = 0.2,
        telemetry_name: str = "website_llm",
    ) -> dict | None:
        response = self.client.chat.completions.create(
            model=model or WEBSITE_SCORE_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_name}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        usage = getattr(response, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        self._telemetry_parts.append(
            {
                "name": telemetry_name,
                "input_tokens": pt,
                "output_tokens": ct,
                "total_tokens": pt + ct,
                "cost_usd": self._cost_usd(pt, ct),
            }
        )
        return _extract_tool_input(response)

    def _validate_retry(self, model_cls, raw: dict | None, retry_fn):
        if raw is None:
            raw = retry_fn()
        try:
            return model_cls.model_validate(raw)
        except Exception:
            raw = retry_fn()
            if raw is None:
                raise
            return model_cls.model_validate(raw)

    def extract_facts(self, combined_markdown: str) -> WebsiteFactsOutput:
        md = combined_markdown[:WEBSITE_LLM_MARKDOWN_CHARS]
        if len(combined_markdown) > WEBSITE_LLM_MARKDOWN_CHARS:
            md += "\n\n[WEBSITE MARKDOWN TRUNCATED FOR LLM CONTEXT — raise WEBSITE_LLM_MARKDOWN_CHARS if needed]\n"
        user = WEBSITE_FACTS_USER.format(combined_markdown=md)

        def call():
            return self._chat_tool(
                system=WEBSITE_FACTS_SYSTEM,
                user_content=user,
                tools=[WEBSITE_FACTS_TOOL],
                tool_name="submit_website_facts",
                max_tokens=TOK_WEBSITE_FACTS_OUT,
                model=OPENAI_MODEL_LIGHT,
                telemetry_name="website_facts",
            )

        raw = call()
        return self._validate_retry(WebsiteFactsOutput, raw, retry_fn=call)

    def gate1(self, facts: WebsiteFactsOutput) -> WebsiteGate1Output:
        fj = json.dumps(facts.model_dump(), ensure_ascii=False, indent=2)
        user = WEBSITE_GATE1_USER.format(facts_json=fj)

        def call():
            return self._chat_tool(
                system=WEBSITE_GATE1_SYSTEM,
                user_content=user,
                tools=[WEBSITE_GATE1_TOOL],
                tool_name="submit_website_gate1",
                max_tokens=TOK_GATE1_OUT,
                model=OPENAI_MODEL_LIGHT,
                telemetry_name="website_gate1",
            )

        raw = call()
        return self._validate_retry(WebsiteGate1Output, raw, retry_fn=call)

    def score_dimensions(self, facts: WebsiteFactsOutput) -> WebsiteScoresOutput:
        fj = json.dumps(facts.model_dump(), ensure_ascii=False, indent=2)
        user = WEBSITE_SCORE_USER.format(facts_json=fj)

        def call():
            return self._chat_tool(
                system=WEBSITE_SCORE_SYSTEM,
                user_content=user,
                tools=[WEBSITE_SCORES_TOOL],
                tool_name="submit_website_scores",
                max_tokens=TOK_WEBSITE_SCORES_OUT,
                model=WEBSITE_SCORE_MODEL,
                telemetry_name="website_scores",
            )

        raw = call()
        return self._validate_retry(WebsiteScoresOutput, raw, retry_fn=call)

    def run(
        self,
        website_url: str,
        *,
        md_result: Optional[WebsiteMarkdownResult] = None,
        max_pages: int = 10,
        timeout_seconds: float = 20.0,
    ) -> Tuple[
        WebsiteInvestmentAssessment,
        Gate1Result,
        dict[str, Any],
        Optional[WebsiteScoresOutput],
        WebsiteMarkdownResult,
    ]:
        t0 = time.perf_counter()
        self.reset_telemetry()
        md = md_result or fetch_website_markdown(
            website_url,
            max_pages=max_pages,
            timeout_seconds=timeout_seconds,
        )
        facts = self.extract_facts(md.combined_markdown)
        # Deterministic post-crawl enrichment: backfill founders / geography /
        # business-model / integrations / compliance from raw markdown when
        # the LLM left those facts blank or "unknown". This prevents the
        # downstream scoring layer from punishing companies whose proper-noun
        # signal IS on the website but the LLM happened to ignore it.
        enrichment = enrich_from_markdown(md.combined_markdown)
        facts, enrichment_notes = merge_enrichment_into_facts(facts, enrichment)
        self._last_enrichment_hints = enrichment
        self._last_enrichment_notes = enrichment_notes
        _infer_sector_from_public_copy(facts)
        _maybe_enrich_founder_roots_osint(facts, md.root_url, enrichment_notes)

        # Optional HQ resolver (no LLM): use web-search snippets (e.g. LinkedIn) only when website lacks geo.
        # OFF by default to avoid unexpected paid search usage.
        try:
            if _hq_enabled():
                geo = (facts.geography or "").strip().lower()
                if geo in ("", "unknown", "n/a", "none", "not stated", "missing"):
                    res = resolve_hq_country(
                        domain=md.root_url,
                        company_name=(facts.company_name or ""),
                        max_results=int(os.getenv("HQ_RESOLVER_MAX_RESULTS", "10") or "10"),
                    )
                    if res.status in ("VERIFIED", "LIKELY") and res.hq_country:
                        facts.geography = f"{res.hq_city}, {res.hq_country}".strip(", ")
                        reg = (res.legal_registered_office or {}) if hasattr(res, "legal_registered_office") else {}
                        reg_country = str(reg.get("country") or "").strip()
                        reg_city = str(reg.get("city") or "").strip()
                        if reg_country:
                            reg_val = f"{reg_city}, {reg_country}".strip(", ")
                            facts.inferred_signals = (
                                (facts.inferred_signals + "\n") if facts.inferred_signals else ""
                            ) + f"company_registration_geo_hint: {reg_val} (hq_resolver_legal)"
                        else:
                            facts.inferred_signals = (
                                (facts.inferred_signals + "\n") if facts.inferred_signals else ""
                            ) + f"company_registration_geo_hint: {facts.geography} (hq_resolver_operating)"
                        enrichment_notes.append(f"hq_resolver: {res.summary}")
                    elif res.status in ("VERIFIED", "LIKELY") and res.hq_city and not res.hq_country:
                        # city-only (unmapped) -> keep as weak inference, don't set facts.geography
                        facts.inferred_signals = (
                            (facts.inferred_signals + "\n") if facts.inferred_signals else ""
                        ) + f"hq_city_from_linkedin_snippet: {res.hq_city} (weak)"
                        enrichment_notes.append(f"hq_resolver: {res.summary}")
        except Exception:
            pass
        g1w = self.gate1(facts)

        # CEE-diaspora rescue: if Gate 1 says FAIL_CONFIDENT purely because of geography,
        # but we detect Polish/CEE founder names or CEE TLD/text signals, downgrade to
        # UNCERTAIN so the deck/website is not killed for HQ-only geography mismatch.
        # Inovo invests in CEE diaspora — Polish founders in Switzerland HQ are a fit.
        if g1w.verdict == "FAIL_CONFIDENT":
            reason_lower = (g1w.rejection_reason or "").lower()
            hard_out = any(
                k in reason_lower
                for k in ("not a startup", "agency", "spam", "gambling", "adult", "crypto fluff")
            )
            osint_cee = "cee_founder_roots_osint" in (facts.inferred_signals or "").lower()
            geo_only_reason = any(
                k in reason_lower for k in ("geography", "geo", "cee", "region", "country", "switzerland", "us-based", "outside")
            ) and not any(
                k in reason_lower for k in ("not a startup", "agency", "spam", "series b", "series c", "not raising")
            )
            us_no_cee_narrative = any(
                k in reason_lower
                for k in (
                    "no cee",
                    "without cee",
                    "non-cee",
                    "outside cee",
                    "us-based",
                    "u.s. with no",
                    "united states with no",
                    "no cee link",
                )
            )
            cee_diaspora = (
                _cee_founder_signal(facts)
                or _cee_domain_signal(md.root_url)
                or _cee_text_signal(facts)
            )
            if (
                not hard_out
                and (cee_diaspora or osint_cee)
                and (geo_only_reason or us_no_cee_narrative or osint_cee)
            ):
                g1w.verdict = "UNCERTAIN_NEED_MORE_CONTEXT"
                g1w.geography_match = True
                g1w.sector_match = True
                g1w.rejection_reason = (
                    "[overridden: CEE / diaspora signal (names, site copy, or founder_roots OSINT) — "
                    f"original: {g1w.rejection_reason}]"
                )

        gate1 = _website_gate1_to_gate1(g1w, facts)
        mandate_blocked, mandate_kills = _mandate_blocks_before_scoring(
            g1w,
            facts=facts,
            website_url=md.root_url,
        )

        if mandate_blocked:
            reason = g1w.rejection_reason or "Outside mandate"
            if "mandate_fail_geography" in mandate_kills and g1w.verdict != "FAIL_CONFIDENT":
                reason = "Geography outside fund mandate (no scoring run)."
            elif "mandate_fail_sector" in mandate_kills and g1w.verdict != "FAIL_CONFIDENT":
                reason = "Sector outside thesis (no scoring run)."
            assessment = WebsiteInvestmentAssessment(
                website_score=0.0,
                quality_score=0.0,
                vc_score=0.0,
                raw_website_score=0.0,
                confidence=g1w.confidence.lower(),
                verdict="REJECT_AUTO",
                top_strengths=[],
                top_concerns=[reason],
                missing_critical_data=[facts.unclear_or_missing_data] if facts.unclear_or_missing_data else [],
                founder_questions=[],
                evidence_table=build_evidence_table(facts, []),
                kill_flags=mandate_kills[:12],
                recommended_next_step=_next_step("REJECT_AUTO"),
                company_name=g1w.company_name or facts.company_name,
                website_url=md.root_url,
                gate1_verdict=g1w.verdict,
                why_not_higher=[],
            )
            return assessment, gate1, facts_dict_from_model(facts), None, md
        scores_out = self.score_dimensions(facts)
        # Evidence-based scoring floors — if the website itself proves a
        # signal, the LLM is not allowed to call it 1/10 ("WEAK"). We move
        # the dimension up to 4/10 ("PARTIAL") and annotate the reasoning so
        # the evidence ledger / Notion render stays honest.
        _apply_website_scoring_floors(scores_out, facts)
        dim_map = website_dimension_int_scores(scores_out)
        raw_score = calculate_website_weighted_score(dim_map)
        facts_d = facts_dict_from_model(facts)
        ok_pages = sum(1 for p in md.pages if p.fetch_ok and p.text_length > 50)
        capped, cap_reasons = apply_website_evidence_caps(
            raw_score,
            facts=facts_d,
            extraction_quality_score=md.extraction_quality_score,
            combined_markdown=md.combined_markdown,
            num_pages_fetched_ok=ok_pages,
        )
        det_kills = deterministic_website_kill_flags(
            facts=facts_d,
            dim_scores=dim_map,
            combined_markdown=md.combined_markdown,
        )
        kills = merge_kill_flags(scores_out.suggested_kill_flags or [], det_kills)
        kills = filter_kill_flags_against_dimensions(kills, dim_scores=dim_map, facts=facts_d)
        conf = scores_out.confidence or "medium"
        strengths, concerns = _derive_strengths_concerns(scores_out)
        missing = list(scores_out.missing_critical_data or [])
        if facts.unclear_or_missing_data:
            missing.insert(0, facts.unclear_or_missing_data)
        questions = list(scores_out.should_ask_founder or [])[:8]

        vc_final = build_website_vc_final(
            self.client,
            facts=facts,
            scores=scores_out,
            combined_markdown=md.combined_markdown,
            website_url=md.root_url,
            raw_twelve_dim_score=raw_score,
            capped_twelve_dim_score=capped,
            gate1_verdict=g1w.verdict,
            gate1_fail=False,
            top_strengths=strengths,
            top_concerns=concerns,
            kill_flags_base=kills,
            cap_reasons_twelve=cap_reasons,
            model=WEBSITE_SCORE_MODEL,
        )
        cap_merged = list(vc_final.cap_reasons or [])
        vc_cap_hit = bool(vc_final.vc_cap_reasons)

        assessment = WebsiteInvestmentAssessment(
            website_score=vc_final.vc_score,
            quality_score=vc_final.quality_score,
            vc_score=vc_final.vc_score,
            raw_website_score=vc_final.raw_website_score,
            confidence=conf,
            verdict=vc_final.final_verdict,
            top_strengths=strengths[:3],
            top_concerns=concerns[:3],
            missing_critical_data=missing[:12],
            founder_questions=questions,
            evidence_table=build_evidence_table(facts, missing),
            kill_flags=(vc_final.kill_flags or kills)[:40],
            recommended_next_step=vc_final.recommended_next_step,
            cap_applied=(capped < raw_score - 1e-6) or vc_cap_hit,
            cap_reasons=cap_merged,
            company_name=facts.company_name or g1w.company_name,
            website_url=md.root_url,
            gate1_verdict=g1w.verdict,
            why_not_higher=list(vc_final.why_not_higher or [])[:25],
            vc_analysis=vc_final,
        )
        # Crawl instability safety: if we attempted pages but all failed, do not
        # auto-reject on missing website text alone; escalate to deck request.
        pages_attempted = len(md.pages)
        ok_pages_after_crawl = sum(1 for p in md.pages if p.fetch_ok and (p.markdown or "").strip())
        if pages_attempted > 0 and ok_pages_after_crawl == 0 and g1w.verdict != "FAIL_CONFIDENT":
            assessment.verdict = "NEEDS_DECK"
            assessment.recommended_next_step = _next_step("NEEDS_DECK")
            if "crawl_failed_all_pages" not in assessment.top_concerns:
                assessment.top_concerns = (assessment.top_concerns[:2] + ["crawl_failed_all_pages"])[:3]
        _ = time.perf_counter() - t0
        return assessment, gate1, facts_d, scores_out, md


def website_facts_to_external_dict(facts: dict[str, Any]) -> dict[str, Any]:
    """Shape compatible with Gate 2.5 / hard-cap helpers."""
    return {
        "company_name": facts.get("company_name", ""),
        "what_they_do": facts.get("product_description") or facts.get("one_liner", ""),
        "founded_year": facts.get("founded_year", ""),
        "founders": [{"name": facts.get("founders") or "Team (website)", "background": facts.get("team") or facts.get("team_signals", "") or "unknown"}],
        "geography": facts.get("geography", ""),
        "stage": "",
        "traction": facts.get("traction_signals", ""),
        "fundraising_ask": "",
        "use_of_funds": "",
        "customers": facts.get("customer_proof", ""),
        "pricing": facts.get("pricing_signals", ""),
        "market": facts.get("market_claims", ""),
        "quotes": [],
        "facts": [],
    }
