#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.inovo_decision import (
    Blockers,
    apply_inovo_geo_rule,
    build_inovo_mandate_fit,
    inovo_verdict,
)
from agents.inovo_domain import InovoGeoAssessment


def _stage_decision(stage: str) -> str:
    st = (stage or "").lower()
    if st in ("pre-seed", "seed", "seed-extension"):
        return "PASS"
    if st in ("late-seed", "series-a-ready", "series-a", "unknown"):
        return "UNCERTAIN"
    return "FAIL"


def _sector_decision(sector: str) -> str:
    s = (sector or "").lower()
    if not s or s == "unknown":
        return "UNCERTAIN"
    if any(x in s for x in ("agency", "consulting", "services", "crypto")):
        return "FAIL"
    return "PASS"


def run_eval() -> int:
    data_path = Path(__file__).resolve().parent / "inovo_golden_30.jsonl"
    rows = [json.loads(x) for x in data_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = 0
    bad = []
    for row in rows:
        geo_decision = apply_inovo_geo_rule(
            InovoGeoAssessment(
                status=row["geo_status"],
                strongest_signal=None,
                confidence=0.7,
                decision="UNCERTAIN",
            )
        )
        mandate = build_inovo_mandate_fit(
            geo_decision=geo_decision,
            stage_decision=_stage_decision(row["stage"]),
            sector_decision=_sector_decision(row["sector"]),
            ticket_decision="UNKNOWN",
            software_decision="PASS",
        )
        if row["geo_status"] != "confirmed_cee" and mandate.overall == "PASS":
            mandate.overall = "UNCERTAIN"
        verdict = inovo_verdict(
            mandate_fit=mandate.overall,
            investment_interest=row["interest"],
            confidence=0.7,
            blockers=Blockers(False, []),
        )
        if verdict == row["expected_verdict"]:
            ok += 1
        else:
            bad.append((row["id"], row["expected_verdict"], verdict))
    print(f"Golden eval: {ok}/{len(rows)} passed")
    for cid, expected, got in bad[:20]:
        print(f"- {cid}: expected={expected}, got={got}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(run_eval())

