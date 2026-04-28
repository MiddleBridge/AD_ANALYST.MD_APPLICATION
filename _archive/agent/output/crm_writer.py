from __future__ import annotations

import json
from pathlib import Path


def write_crm_json(company_slug: str, crm_json: dict, out_dir: str = "tmp/vc_triage") -> Path:
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    dst = p / f"{company_slug}_crm.json"
    dst.write_text(json.dumps(crm_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst

