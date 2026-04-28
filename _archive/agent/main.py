from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
from pathlib import Path
from typing import Any

from openai import OpenAI
from dotenv import load_dotenv

from agent.config import load_thesis_config
from agent.enrichment import lookup_competitors, lookup_founders, scrape_core_pages, search_funding
from agent.extraction import run_investment_analysis, run_structured_extraction
from agent.models import EnrichmentBundle, RunContext, SourceItem
from agent.output import build_daily_digest, write_crm_json, write_memo
from agent.scoring import apply_fund_fit, compute_weighted, route_deal, run_quality_gates
from agents.external_research import get_research_provider

load_dotenv()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"


def _search_general(company_name: str) -> list[SourceItem]:
    provider, _ = get_research_provider()
    out: list[SourceItem] = []
    seen = set()
    for q in [f"{company_name} funding", f"{company_name} founders", f"{company_name} review"]:
        for s in provider.search(q, max_results=5):
            if s.url and s.url not in seen:
                seen.add(s.url)
                out.append(SourceItem(url=s.url, title=s.title or "", snippet=s.snippet or "", source_type="search"))
    return out


def run_enrichment(company_name: str, url: str) -> EnrichmentBundle:
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_pages = ex.submit(scrape_core_pages, url)
        f_funding = ex.submit(search_funding, company_name)
        f_founders = ex.submit(lookup_founders, company_name)
        f_comp = ex.submit(lookup_competitors, company_name)
        pages = f_pages.result()
        funding = f_funding.result()
        founders = f_founders.result()
        competitors = f_comp.result()
    return EnrichmentBundle(
        company_name=company_name,
        url=url,
        crawled_pages=pages,
        search_results=_search_general(company_name),
        founder_sources=founders,
        funding_sources=funding,
        competitors=competitors,
        metadata={"enriched_at": datetime.utcnow().isoformat() + "Z"},
    )


def _normalize_crm_schema(raw: dict[str, Any], company_name: str, url: str, run_ctx: RunContext) -> dict[str, Any]:
    """Force output to the target CRM schema from the brief."""
    r = raw or {}
    def obj(v: Any) -> dict[str, Any]:
        return v if isinstance(v, dict) else {}

    funding_obj = obj(r.get("funding"))
    product_obj = obj(r.get("product"))
    traction_obj = obj(r.get("traction"))
    market_obj = obj(r.get("market"))
    scoring_obj = obj(r.get("scoring"))
    fit_obj = obj(r.get("fund_fit"))
    routing_obj = obj(r.get("routing"))
    follow_obj = obj(r.get("follow_ups"))
    founders_raw = r.get("founders") if isinstance(r.get("founders"), list) else []
    founders = []
    for f in founders_raw[:5]:
        if not isinstance(f, dict):
            continue
        founders.append(
            {
                "name": f.get("name", "unknown"),
                "role": f.get("role", "unknown"),
                "background_one_liner": f.get("background_one_liner") or f.get("background") or "unknown",
                "linkedin": f.get("linkedin", "unknown"),
                "previous_exits": f.get("previous_exits", "unknown"),
            }
        )
    if not founders:
        founders = [{"name": "unknown", "role": "unknown", "background_one_liner": "unknown", "linkedin": "unknown", "previous_exits": "unknown"}]

    competitors = r.get("competitors") or ((r.get("market") or {}).get("named_competitors")) or []
    if not isinstance(competitors, list):
        competitors = []
    comp3 = [str(x) for x in competitors if str(x).strip()][:3]

    product_dist = r.get("distribution_channels") or ((r.get("product") or {}).get("distribution_channels")) or []
    if not isinstance(product_dist, list):
        product_dist = []

    crm = {
        "meta": {
            "company_name": company_name,
            "url": url,
            "screened_at": run_ctx.screened_at,
            "screening_depth": run_ctx.depth,
        },
        "basics": {
            "one_liner": r.get("one_liner", "unknown"),
            "founded": r.get("founded") or "unknown",
            "hq_city": r.get("hq_city") or "unknown",
            "hq_country": r.get("hq_country") or "unknown",
            "geo_markets": r.get("geo_markets") or [],
            "team_size_estimate": r.get("team_size_estimate") or r.get("team_size") or "unknown",
            "stage": r.get("stage") or "unknown",
        },
        "founders": founders,
        "funding": {
            "total_raised_usd": funding_obj.get("total_raised_usd") or "unknown",
            "last_round": funding_obj.get("last_round") or "unknown",
            "last_round_date": funding_obj.get("last_round_date") or "unknown",
            "lead_investor": funding_obj.get("lead_investor") or "unknown",
            "notable_investors": funding_obj.get("notable_investors") or [],
            "source_url": funding_obj.get("source_url") or "unknown",
        },
        "product": {
            "category": product_obj.get("category") or r.get("category") or "unknown",
            "core_value_prop": product_obj.get("core_value_prop") or r.get("one_liner") or "unknown",
            "distribution_channels": product_dist,
            "pricing_model": product_obj.get("pricing_model") or r.get("pricing_model") or "unknown",
            "pricing_visible": product_obj.get("pricing_visible") or r.get("pricing_details") or "not disclosed",
        },
        "traction": {
            "claimed_logos": traction_obj.get("claimed_logos") or [],
            "claimed_metrics": traction_obj.get("claimed_metrics") or [],
            "verified": traction_obj.get("verified") if isinstance(traction_obj.get("verified"), bool) else False,
            "enterprise_readiness": traction_obj.get("enterprise_readiness") or [],
        },
        "market": {
            "category_saturation": market_obj.get("category_saturation") or 5,
            "named_competitors": comp3,
            "platform_risk": market_obj.get("platform_risk") or "none identified",
            "timing_score": market_obj.get("timing_score") or 5,
            "timing_rationale": market_obj.get("timing_rationale") or "unknown",
        },
        "scoring": {
            "team": scoring_obj.get("team") or 5,
            "market": scoring_obj.get("market") or 5,
            "product_wedge": scoring_obj.get("product_wedge") or 5,
            "traction": scoring_obj.get("traction") or 5,
            "defensibility": scoring_obj.get("defensibility") or 5,
            "timing": scoring_obj.get("timing") or 5,
            "capital_efficiency": scoring_obj.get("capital_efficiency") or 5,
            "overall_weighted": scoring_obj.get("overall_weighted") or 0,
        },
        "fund_fit": {
            "inovo_fit_score": fit_obj.get("inovo_fit_score") or 1,
            "inovo_fit_rationale": fit_obj.get("inovo_fit_rationale") or "unknown",
            "stage_match": fit_obj.get("stage_match") or False,
            "geo_match": fit_obj.get("geo_match") or False,
            "thesis_match": fit_obj.get("thesis_match") or False,
        },
        "routing": {
            "action": routing_obj.get("action") or "WATCH_6M",
            "rationale": routing_obj.get("rationale") or "unknown",
            "kill_flags": routing_obj.get("kill_flags") or [],
            "kill_flag_evidence": routing_obj.get("kill_flag_evidence") or {},
        },
        "follow_ups": {
            "online_research_gaps": follow_obj.get("online_research_gaps") or [],
            "questions_for_founders": follow_obj.get("questions_for_founders") or (r.get("questions_for_founders") or []),
        },
    }
    return crm


def _postprocess_crm(crm: dict[str, Any], thesis) -> dict[str, Any]:
    crm = apply_fund_fit(crm, thesis)
    scoring = crm.get("scoring")
    if not isinstance(scoring, dict):
        scoring = {}
        crm["scoring"] = scoring
    scoring["overall_weighted"] = compute_weighted(scoring)
    routing = crm.get("routing")
    if not isinstance(routing, dict):
        routing = {}
        crm["routing"] = routing
    routing["action"] = route_deal(scoring, crm.get("fund_fit") or {}, routing.get("kill_flags") or [])
    return crm


def run_pipeline(company_name: str, url: str, config_path: str = "agent/config.yaml") -> dict[str, Any]:
    thesis = load_thesis_config(config_path)
    run_ctx = RunContext(depth="INITIAL")
    client = OpenAI()
    bundle = run_enrichment(company_name, url)

    crm = {}
    failures: list[str] = []
    for i in range(3):  # initial + max 2 retries
        run_ctx.retry_count = i
        raw = run_structured_extraction(client, bundle, thesis, run_ctx)
        crm = _normalize_crm_schema(raw, company_name, url, run_ctx)
        crm = _postprocess_crm(crm, thesis)
        ok, failures = run_quality_gates(crm)
        if ok:
            break
        # re-enrichment with incremental broader search
        bundle.search_results.extend(_search_general(company_name))
    crm.setdefault("meta", {})
    crm["meta"]["quality_gate_failures"] = failures

    memo = run_investment_analysis(client, crm)
    slug = _slug(company_name)
    crm_path = write_crm_json(slug, crm)
    memo_path = write_memo(slug, memo)
    digest = build_daily_digest([crm])
    digest_path = Path("tmp/vc_triage") / f"{slug}_digest.txt"
    digest_path.write_text(digest, encoding="utf-8")
    return {
        "crm_path": str(crm_path),
        "memo_path": str(memo_path),
        "digest_path": str(digest_path),
        "action": ((crm.get("routing") or {}).get("action") or "unknown"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="VC triage pipeline (new architecture)")
    parser.add_argument("--company", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--config", default="agent/config.yaml")
    args = parser.parse_args()

    out = run_pipeline(args.company, args.url, args.config)
    print(f"CRM: {out['crm_path']}")
    print(f"Memo: {out['memo_path']}")
    print(f"Digest: {out['digest_path']}")
    print(f"Action: {out['action']}")


if __name__ == "__main__":
    main()

