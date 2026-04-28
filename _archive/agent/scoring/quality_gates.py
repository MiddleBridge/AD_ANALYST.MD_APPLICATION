from __future__ import annotations

from typing import Any


def run_quality_gates(crm: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []

    # Gate 1: source coverage
    source_urls = set()
    for lst_key in ("funding",):
        block = crm.get(lst_key) or {}
        u = block.get("source_url")
        if isinstance(u, str) and u:
            source_urls.add(u)
    for ff in crm.get("founders") or []:
        u = ff.get("linkedin")
        if isinstance(u, str) and u.startswith("http"):
            source_urls.add(u)
    for _, v in ((crm.get("meta") or {}).items()):
        if isinstance(v, str) and v.startswith("http"):
            source_urls.add(v)
    if len(source_urls) < 3:
        failures.append("gate1_source_coverage")

    # Gate 2: no unknown in critical fields
    basics = crm.get("basics") or {}
    if str(basics.get("founded", "unknown")).lower() == "unknown":
        failures.append("gate2_founded_unknown")
    if str((crm.get("product") or {}).get("category", "unknown")).lower() == "unknown":
        failures.append("gate2_category_unknown")
    if not (crm.get("founders") or []):
        failures.append("gate2_founders_missing")

    # Gate 3: kill flag justification
    kf = (crm.get("routing") or {}).get("kill_flags") or []
    kf_evidence = (crm.get("routing") or {}).get("kill_flag_evidence") or {}
    for flag in kf:
        ev = kf_evidence.get(flag) or []
        if len(ev) < 2:
            failures.append(f"gate3_kill_flag_insufficient:{flag}")

    # Gate 4: competitive grounding
    competitors = (crm.get("market") or {}).get("named_competitors") or []
    if len(competitors) < 3:
        failures.append("gate4_competitors_lt_3")

    # Gate 5: founder linkedin match
    for founder in crm.get("founders") or []:
        li = str(founder.get("linkedin", "unknown"))
        if li != "unknown" and not li.startswith("http"):
            failures.append("gate5_bad_linkedin_format")

    return len(failures) == 0, failures

