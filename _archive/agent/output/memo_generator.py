from __future__ import annotations

from pathlib import Path


def write_memo(company_slug: str, memo: str, out_dir: str = "tmp/vc_triage") -> Path:
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    dst = p / f"{company_slug}_memo.md"
    dst.write_text(memo or "", encoding="utf-8")
    return dst

