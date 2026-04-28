from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent.config import InovoThesisConfig
from agent.models import EnrichmentBundle, RunContext


_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "extraction_prompt.md").read_text(encoding="utf-8")


def _tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_crm_json",
            "description": "Return strict CRM schema JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meta": {"type": "object"},
                    "basics": {"type": "object"},
                    "founders": {"type": "array", "items": {"type": "object"}},
                    "funding": {"type": "object"},
                    "product": {"type": "object"},
                    "traction": {"type": "object"},
                    "market": {"type": "object"},
                    "scoring": {"type": "object"},
                    "fund_fit": {"type": "object"},
                    "routing": {"type": "object"},
                    "follow_ups": {"type": "object"},
                },
                "required": ["meta", "basics", "founders", "funding", "product", "traction", "market", "scoring", "fund_fit", "routing", "follow_ups"],
            },
        },
    }


def run_structured_extraction(
    client: OpenAI,
    bundle: EnrichmentBundle,
    thesis: InovoThesisConfig,
    run_ctx: RunContext,
    model: str = "gpt-4.1",
) -> dict[str, Any]:
    payload = {
        "meta": {
            "company_name": bundle.company_name,
            "url": bundle.url,
            "screened_at": run_ctx.screened_at,
            "screening_depth": run_ctx.depth,
        },
        "enrichment": {
            "crawled_pages": bundle.crawled_pages,
            "search_results": [s.__dict__ for s in bundle.search_results],
            "funding_sources": [s.__dict__ for s in bundle.funding_sources],
            "founder_sources": [s.__dict__ for s in bundle.founder_sources],
            "competitors": bundle.competitors,
        },
        "thesis": thesis.__dict__,
    }
    r = client.chat.completions.create(
        model=model,
        temperature=0.1,
        tools=[_tool_schema()],
        tool_choice={"type": "function", "function": {"name": "submit_crm_json"}},
        messages=[
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    tool = r.choices[0].message.tool_calls[0]
    return json.loads(tool.function.arguments)

