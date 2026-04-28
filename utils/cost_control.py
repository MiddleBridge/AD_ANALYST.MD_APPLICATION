"""Pre-flight cost and API-usage caps for screening (env-driven)."""

from __future__ import annotations

import os


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def caps_enabled() -> bool:
    return _bool_env("ENABLE_COST_CAPS", "true")


def max_cost_per_run_usd() -> float:
    return float(os.getenv("MAX_COST_PER_RUN_USD", "1.00"))


def max_cost_per_day_usd() -> float:
    return float(os.getenv("MAX_COST_PER_DAY_USD", "20.00"))


def max_external_checks_per_day() -> int:
    return int(os.getenv("MAX_EXTERNAL_CHECKS_PER_DAY", "10"))


def estimate_gate2_usd() -> float:
    return float(os.getenv("ESTIMATE_GATE2_USD", "0.35"))


def estimate_gate25_usd() -> float:
    return float(os.getenv("ESTIMATE_GATE25_USD", "0.45"))


def estimate_website_pipeline_usd() -> float:
    return float(os.getenv("ESTIMATE_WEBSITE_PIPELINE_USD", "0.55"))


def should_block_stage(
    message_id: str,
    *,
    estimated_extra_usd: float,
) -> tuple[bool, str]:
    """
    Returns (block, reason) if caps are enabled and this stage would exceed limits.
    """
    if not caps_enabled():
        return False, ""

    from storage import database as db

    spend_day = db.get_spend_since_utc_midnight()
    current_run = db.get_deal_cost_usd(message_id)
    run_cap = max_cost_per_run_usd()
    day_cap = max_cost_per_day_usd()

    if db.would_exceed_daily_budget(estimated_extra_usd, day_cap):
        return True, (
            f"DAILY_CAP projected_spend_usd={spend_day:.4f}+est={estimated_extra_usd:.4f} "
            f"exceeds MAX_COST_PER_DAY_USD={day_cap:.2f}"
        )

    if current_run + estimated_extra_usd > run_cap + 1e-9:
        return True, (
            f"RUN_CAP current_run_usd={current_run:.4f}+est={estimated_extra_usd:.4f} "
            f"exceeds MAX_COST_PER_RUN_USD={run_cap:.2f}"
        )

    return False, ""


def should_block_external_budget() -> tuple[bool, str]:
    """Gate 2.5 daily count cap (separate from dollar caps)."""
    if not caps_enabled():
        return False, ""

    from storage import database as db

    n = db.count_gate25_completions_since_utc_midnight()
    lim = max_external_checks_per_day()
    if n >= lim:
        return True, (
            f"EXTERNAL_DAILY_COUNT n_completions_today={n} "
            f">= MAX_EXTERNAL_CHECKS_PER_DAY={lim}"
        )
    return False, ""
