from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI


_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "analysis_prompt.md").read_text(encoding="utf-8")


def run_investment_analysis(
    client: OpenAI,
    crm_json: dict,
    model: str = "gpt-4.1",
) -> str:
    action = ((crm_json.get("routing") or {}).get("action") or "").upper()
    if action not in {"PASS_TO_ASSOCIATE", "PASS_TO_PARTNER"}:
        return ""
    r = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": json.dumps(crm_json, ensure_ascii=False)},
        ],
    )
    return (r.choices[0].message.content or "").strip()

