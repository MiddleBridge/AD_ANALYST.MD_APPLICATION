"""Deterministic website kill flags and evidence table helpers."""

from __future__ import annotations

import re
from typing import Any

from agents.schemas_website import EvidenceTableRow, WebsiteFactsOutput

_IMP_MARKET = re.compile(
    r"\b(trillion|billion[s]?\s+market|tams?\s*[$€]?\s*[0-9]{2,}|every company|everyone will)\b",
    re.I,
)
_AGENCY = re.compile(
    r"\b(we build apps for you|hire us|digital agency|outsourc|body\s*shop|staff aug)\b",
    re.I,
)
_PLATFORM = re.compile(
    r"\b(end[- ]to[- ]end platform for everything|one platform to rule|unified platform for all)\b",
    re.I,
)
_USER_SCALE = re.compile(
    r"(\d{1,3}([.,]\d+)?\s*%|\d+\s*[kmb]\+?\s+(students|users|learners|customers)|\d{3,}\+?\s*(students|users|learners)?|"
    r"(600|700|800|900|\d{3,})\s*[kK]\+?\s*(students|users|learners)?|pass\s*rate|completion\s*rate)",
    re.I,
)


def facts_dict_from_model(f: WebsiteFactsOutput) -> dict[str, Any]:
    return f.model_dump()


def merge_kill_flags(llm_suggested: list[str], deterministic: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in deterministic + llm_suggested:
        k = (x or "").strip().lower().replace(" ", "_")
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def filter_kill_flags_against_dimensions(
    flags: list[str],
    *,
    dim_scores: dict[str, int],
    facts: dict[str, Any],
) -> list[str]:
    """
    Drop kill flags that contradict structured dimension scores or extracted facts
    (e.g. LLM suggests no_customer_evidence while traction_evidence is strong).
    """
    out: list[str] = []
    for f in flags:
        code = (f or "").strip().lower().replace(" ", "_")
        if not code:
            continue
        tc = dim_scores.get("target_customer_clarity", 0)
        pc = dim_scores.get("product_clarity", 0)
        prc = dim_scores.get("problem_clarity", 0)
        tr = dim_scores.get("traction_evidence", 0)
        cpr = dim_scores.get("customer_proof", 0)
        bmc = dim_scores.get("business_model_clarity", 0)
        diff = dim_scores.get("differentiation", 0)
        tech = dim_scores.get("technical_depth_or_defensibility", 0)

        if code == "no_clear_icp" and (tc >= 7 or _has_icp(facts)):
            continue
        if code == "no_product_specificity" and max(pc, prc) >= 7:
            continue
        if code == "no_customer_evidence" and (tr >= 7 or cpr >= 6 or _has_user_scale_signal(facts)):
            continue
        if code == "no_business_model_signal" and (bmc >= 6 or _pricing_hint_anywhere(facts)):
            continue
        if code == "no_right_to_win" and (tr >= 7 or (diff + tech) >= 10 or max(diff, tech) >= 6):
            continue
        if code == "vague_ai_wrapper" and diff >= 6:
            continue
        # If our deterministic enrichment found founder/team names on the
        # site, do not let the LLM keep a "no_founder_or_team_signal" kill.
        if code == "no_founder_or_team_signal" and (
            _non_empty(facts.get("founders")) or _non_empty(facts.get("team"))
        ):
            continue
        out.append(f)
    return out


def _has_user_scale_signal(facts: dict[str, Any]) -> bool:
    blob = (
        str(facts.get("traction_signals", ""))
        + " "
        + str(facts.get("customer_proof", ""))
        + " "
        + str(facts.get("market_claims", ""))
    )
    return bool(_USER_SCALE.search(blob))


def _pricing_hint_anywhere(facts: dict[str, Any]) -> bool:
    for k in ("pricing_signals", "product_description", "one_liner", "use_cases"):
        s = str(facts.get(k, "")).lower()
        if not s.strip():
            continue
        if re.search(
            r"\b(free|freemium|premium|pricing|tier|plan|€|\$|pln|usd|/month|per seat|subscription)\b",
            s,
        ):
            return True
    return False


def deterministic_website_kill_flags(
    *,
    facts: dict[str, Any],
    dim_scores: dict[str, int],
    combined_markdown: str,
) -> list[str]:
    flags: list[str] = []
    blob = " ".join(
        str(facts.get(k, ""))
        for k in (
            "product_description",
            "one_liner",
            "market_claims",
            "use_cases",
            "inferred_signals",
        )
    ).lower()
    md = (combined_markdown or "").lower()

    if not _has_icp(facts) and dim_scores.get("target_customer_clarity", 0) <= 5:
        flags.append("no_clear_icp")

    if (
        not _non_empty(facts.get("product_description"))
        and dim_scores.get("product_clarity", 0) <= 5
        and dim_scores.get("problem_clarity", 0) <= 6
    ):
        flags.append("no_product_specificity")

    named_customers = _non_empty(facts.get("customer_proof")) or _non_empty(facts.get("logos_or_case_studies"))
    if not named_customers:
        if (
            dim_scores.get("traction_evidence", 0) < 7
            and dim_scores.get("customer_proof", 0) < 6
            and not _has_user_scale_signal(facts)
        ):
            flags.append("no_customer_evidence")

    if (
        not _non_empty(facts.get("pricing_signals"))
        and dim_scores.get("business_model_clarity", 0) <= 5
        and not _pricing_hint_anywhere(facts)
    ):
        flags.append("no_business_model_signal")

    if _AGENCY.search(blob) or _AGENCY.search(md):
        flags.append("commodity_services_agency")

    if _IMP_MARKET.search(blob) or _IMP_MARKET.search(str(facts.get("market_claims", ""))):
        flags.append("impossible_market_claims")

    if _PLATFORM.search(blob):
        flags.append("overbroad_platform_claim")

    if (
        re.search(r"\b(ai|llm|gpt)\b", blob)
        and dim_scores.get("differentiation", 10) <= 5
        and dim_scores.get("technical_depth_or_defensibility", 10) <= 5
    ):
        flags.append("vague_ai_wrapper")

    if (
        dim_scores.get("differentiation", 10) <= 4
        and dim_scores.get("technical_depth_or_defensibility", 10) <= 4
        and dim_scores.get("traction_evidence", 10) < 7
    ):
        flags.append("no_right_to_win")

    logos = str(facts.get("logos_or_case_studies", "")).lower()
    if "logo wall" in md and not _non_empty(facts.get("customer_proof")):
        flags.append("fake_social_proof_or_unverifiable_logos")

    sec = str(facts.get("security_compliance_signals", "")).lower()
    if ("hipaa" in blob or "soc2" in blob or "gdpr" in blob) and not sec and "fintech" in blob:
        flags.append("regulatory_risk_unaddressed")

    return flags


def _non_empty(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    if not s or s.lower() in ("unknown", "n/a", "none", "not found", "missing"):
        return False
    return True


def _has_icp(facts: dict[str, Any]) -> bool:
    return _non_empty(facts.get("target_customer"))


def build_evidence_table(facts: WebsiteFactsOutput, missing: list[str]) -> list[EvidenceTableRow]:
    rows: list[EvidenceTableRow] = []
    mapping = [
        ("Company", facts.company_name, "fact_on_site"),
        ("One-liner", facts.one_liner, "fact_on_site"),
        ("ICP", facts.target_customer, "fact_on_site"),
        ("Product", facts.product_description, "fact_on_site"),
        ("Pricing signals", facts.pricing_signals, "fact_on_site"),
        ("Customer proof", facts.customer_proof, "fact_on_site"),
        ("Logos / cases", facts.logos_or_case_studies, "fact_on_site"),
        ("Traction (claimed)", facts.traction_signals, "fact_on_site"),
        ("Team", facts.team_signals, "fact_on_site"),
        ("Inferred (weak)", facts.inferred_signals, "inferred"),
    ]
    for aspect, text, kind in mapping:
        if _non_empty(text):
            rows.append(EvidenceTableRow(aspect=aspect, finding=str(text)[:500], kind=kind))  # type: ignore[arg-type]
    for m in missing[:12]:
        rows.append(
            EvidenceTableRow(
                aspect="Missing",
                finding=m,
                kind="missing",
            )
        )
    return rows
