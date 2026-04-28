from __future__ import annotations

import re
from dataclasses import asdict, dataclass

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


@dataclass
class LegalBlock:
    source_url: str
    selector: str
    text: str
    priority: str
    matched_terms: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


_LEGAL_TERMS = [
    "krs",
    "nip",
    "regon",
    "vat",
    "company number",
    "registration number",
    "registered in",
    "registered office",
    "registered address",
    "company address",
    "limited liability company",
    "sp. z o.o.",
    "sp z o o",
    "spółka z ograniczoną odpowiedzialnością",
    "incorporated in",
    "incorporation",
    "court register",
    "national court register",
    "address",
    "siedziba",
    "zarejestrowana",
    "zarejestrowany",
    "numer krs",
]

_SELECTORS = [
    "footer",
    "[class*=footer]",
    "[id*=footer]",
    "[class*=legal]",
    "[id*=legal]",
    "[class*=imprint]",
    "[id*=imprint]",
    "[class*=contact]",
    "[id*=contact]",
    "[class*=company]",
    "[id*=company]",
    "address",
    "[itemtype*=Organization]",
    "[itemtype*=PostalAddress]",
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _terms(text: str) -> list[str]:
    low = (text or "").lower()
    out: list[str] = []
    for t in _LEGAL_TERMS:
        if t in low:
            out.append(t)
    return out


def extract_legal_blocks_from_html(html: str, source_url: str) -> list[LegalBlock]:
    if not (html or "").strip():
        return []
    blocks: list[LegalBlock] = []
    seen: set[str] = set()

    if BeautifulSoup is None:
        # Minimal fallback: extract footer-ish raw chunks.
        for m in re.finditer(r"<footer[^>]*>(.*?)</footer>", html, flags=re.I | re.S):
            txt = _norm(re.sub(r"<[^>]+>", " ", m.group(1)))
            if len(txt) < 40:
                continue
            mt = _terms(txt)
            if not mt:
                continue
            if txt.lower() in seen:
                continue
            seen.add(txt.lower())
            blocks.append(LegalBlock(source_url, "footer", txt, "HIGH", mt))
        return blocks

    soup = BeautifulSoup(html, "html.parser")
    for sel in _SELECTORS:
        for node in soup.select(sel):
            txt = _norm(node.get_text(" ", strip=True))
            if len(txt) < 40:
                continue
            mt = _terms(txt)
            if not mt:
                continue
            k = txt.lower()
            if k in seen:
                continue
            seen.add(k)
            pr = "HIGH" if ("footer" in sel or "address" in sel or "legal" in sel) else "MEDIUM"
            blocks.append(LegalBlock(source_url=source_url, selector=sel, text=txt, priority=pr, matched_terms=mt))
    return blocks

