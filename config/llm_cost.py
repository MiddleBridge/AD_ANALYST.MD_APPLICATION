"""Model picks and context limits to control OpenAI spend."""

from __future__ import annotations

import os

# Heavy steps: deck extract/score/brief, website 12-dim score, external market assessment.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Cheap steps: Gate 1, website facts + website Gate 1, external research query plan.
# Override with OPENAI_MODEL_LIGHT=gpt-4o to force full model everywhere.
OPENAI_MODEL_LIGHT = os.getenv("OPENAI_MODEL_LIGHT", "gpt-4o-mini")

# Website: max chars of crawl markdown sent to the facts LLM (input $ scales ~linearly).
WEBSITE_LLM_MARKDOWN_CHARS = int(os.getenv("WEBSITE_LLM_MARKDOWN_CHARS", "55000"))

# External: cap how many sources and how long each snippet is in the market LLM prompt.
EXTERNAL_LLM_MAX_SOURCES = int(os.getenv("EXTERNAL_LLM_MAX_SOURCES", "32"))
EXTERNAL_SOURCE_SNIPPET_CHARS = int(os.getenv("EXTERNAL_SOURCE_SNIPPET_CHARS", "450"))

# Screener / website: output ceilings (smaller = slightly faster; tool JSON rarely needs 4k).
TOK_GATE1_OUT = int(os.getenv("OPENAI_MAX_TOKENS_GATE1", "384"))
TOK_EXTRACT_OUT = int(os.getenv("OPENAI_MAX_TOKENS_EXTRACT", "3072"))
TOK_SCORECARD_OUT = int(os.getenv("OPENAI_MAX_TOKENS_SCORECARD", "3072"))
TOK_BRIEF_OUT = int(os.getenv("OPENAI_MAX_TOKENS_BRIEF", "1536"))
TOK_WEBSITE_FACTS_OUT = int(os.getenv("OPENAI_MAX_TOKENS_WEBSITE_FACTS", "3072"))
TOK_WEBSITE_SCORES_OUT = int(os.getenv("OPENAI_MAX_TOKENS_WEBSITE_SCORES", "3072"))
# Single batched JSON for website VC layer (replaces many small VC calls).
TOK_WEBSITE_VC_PACK_OUT = int(os.getenv("OPENAI_MAX_TOKENS_WEBSITE_VC_PACK", "4096"))
TOK_EXTERNAL_PLAN_OUT = int(os.getenv("OPENAI_MAX_TOKENS_EXTERNAL_PLAN", "768"))
TOK_EXTERNAL_MARKET_OUT = int(os.getenv("OPENAI_MAX_TOKENS_EXTERNAL_MARKET", "3072"))


def llm_cost_usd_from_tokens(prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost from token counts (same pricing knobs as `agents/screener._cost_usd`)."""
    inp = float(os.getenv("OPENAI_PRICE_INPUT_PER_1M", "5.0"))
    out = float(os.getenv("OPENAI_PRICE_OUTPUT_PER_1M", "15.0"))
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    return round((pt * inp + ct * out) / 1_000_000, 6)
