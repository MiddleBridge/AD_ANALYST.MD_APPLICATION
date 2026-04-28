from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class InovoThesisConfig:
    # Fund "thesis knobs" used for deterministic fund-fit checks.
    allowed_stages: list[str]
    allowed_hq_countries: list[str]
    allowed_geo_markets: list[str]
    focus_sectors: list[str]
    ticket_size_eur_min: float
    ticket_size_eur_max: float

    # Simple keyword matchers used in deterministic thesis_match.
    sector_keywords: dict[str, list[str]]


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()] if str(v).strip() else []


def load_thesis_config(path: str | Path) -> InovoThesisConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config.yaml must be a mapping")

    thesis = raw.get("inovo_thesis") or {}
    if not isinstance(thesis, dict):
        thesis = {}

    kw = thesis.get("sector_keywords") or {}
    if not isinstance(kw, dict):
        kw = {}

    sector_keywords: dict[str, list[str]] = {}
    for k, v in kw.items():
        sector_keywords[str(k).strip().lower()] = [s.lower() for s in _as_list(v)]

    return InovoThesisConfig(
        allowed_stages=[s.lower() for s in _as_list(thesis.get("allowed_stages"))],
        allowed_hq_countries=[c.lower() for c in _as_list(thesis.get("allowed_hq_countries"))],
        allowed_geo_markets=[c.lower() for c in _as_list(thesis.get("allowed_geo_markets"))],
        focus_sectors=[s.lower() for s in _as_list(thesis.get("focus_sectors"))],
        ticket_size_eur_min=float(thesis.get("ticket_size_eur_min") or 0),
        ticket_size_eur_max=float(thesis.get("ticket_size_eur_max") or 0),
        sector_keywords=sector_keywords,
    )

