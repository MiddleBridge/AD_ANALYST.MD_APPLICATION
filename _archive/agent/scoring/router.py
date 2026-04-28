from __future__ import annotations


def route_deal(scores: dict, fund_fit: dict, kill_flags: list[str]) -> str:
    # HARD REJECTS
    if len(kill_flags) >= 2:
        return "REJECT"
    if float(scores.get("overall_weighted", 0)) < 4.0:
        return "REJECT"
    if not bool(fund_fit.get("stage_match", False)):
        return "REJECT"

    # WATCHLIST
    ov = float(scores.get("overall_weighted", 0))
    if 4.0 <= ov < 5.5:
        return "WATCH_6M"
    if float(scores.get("team", 0)) >= 8 and float(scores.get("traction", 0)) < 5:
        return "WATCH_3M"

    # PASS — ASSOCIATE
    if 5.5 <= ov < 7.0:
        return "PASS_TO_ASSOCIATE"
    if float(fund_fit.get("inovo_fit_score", 0)) < 6:
        return "PASS_TO_ASSOCIATE"

    # PASS — PARTNER
    if ov >= 7.0 and float(fund_fit.get("inovo_fit_score", 0)) >= 7:
        return "PASS_TO_PARTNER"
    return "PASS_TO_ASSOCIATE"

