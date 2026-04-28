from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class RegistryId:
    type: str
    value: str
    raw_quote: Optional[str] = None
    source_url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LegalTextInput:
    text: str
    source_url: str
    source_type: str


@dataclass
class LegalEvidence:
    id: str
    source_url: str
    source_type: str
    claim_type: str
    raw_quote: str
    country: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    registry_id: Optional[RegistryId] = None
    confidence: float = 0.0
    weight: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.registry_id is not None:
            d["registry_id"] = self.registry_id.to_dict()
        return d


@dataclass
class LegalSignals:
    legal_entity_name: Optional[str] = None
    legal_form: Optional[str] = None
    legal_form_original: Optional[str] = None
    legal_form_expanded: Optional[str] = None
    registry_ids: list[RegistryId] = field(default_factory=list)
    registered_city: Optional[str] = None
    registered_country: Optional[str] = None
    registered_address: Optional[str] = None
    evidence: list[LegalEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "legal_entity_name": self.legal_entity_name,
            "legal_form": self.legal_form,
            "legal_form_original": self.legal_form_original,
            "legal_form_expanded": self.legal_form_expanded,
            "registry_ids": [x.to_dict() for x in self.registry_ids],
            "registered_city": self.registered_city,
            "registered_country": self.registered_country,
            "registered_address": self.registered_address,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": list(self.warnings),
        }


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _city_norm(city: str) -> str:
    c = _norm(city)
    low = c.lower()
    if low in {"łódź", "lodz"}:
        return "Lodz"
    return c


def _country_norm(country: str) -> str:
    c = _norm(country).lower()
    if c in {"poland", "polska", "pl"}:
        return "Poland"
    return _norm(country)


def _new_evidence(
    *,
    source_url: str,
    source_type: str,
    claim_type: str,
    raw_quote: str,
    country: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    registry_id: Optional[RegistryId] = None,
    confidence: float = 0.9,
    weight: float = 0.95,
) -> LegalEvidence:
    return LegalEvidence(
        id=str(uuid.uuid4()),
        source_url=source_url,
        source_type=source_type,
        claim_type=claim_type,
        raw_quote=_norm(raw_quote)[:500],
        country=country,
        city=city,
        address=address,
        registry_id=registry_id,
        confidence=confidence,
        weight=weight,
    )


def _extract_legal_form(text: str) -> Optional[str]:
    low = text.lower()
    if re.search(r"\b(sp\.?\s*z\s*o\.?\s*o\.?|spółka\s+z\s+ograniczoną\s+odpowiedzialnością)\b", low):
        return "sp. z o.o."
    return None


def _extract_entity_name(text: str) -> Optional[str]:
    # Conservative: capture around legal suffix.
    m = re.search(
        r"(?:©\s*\d{4}\s*)?([A-ZŁŚŻŹĆŃÓ][A-Za-z0-9ŁŚŻŹĆŃÓąćęłńóśźż\-.& ]{1,110}?\bsp\.?\s*z\s*o\.?\s*o\.?)",
        text,
        flags=re.I,
    )
    if not m:
        return None
    name = _norm(m.group(1))
    name = re.sub(r"^(©\s*\d{4}\s*)", "", name).strip(" .,-")
    name = re.sub(r"\bsp\.?\s*z\s*o\.?\s*o\.?\b", "sp. z o.o.", name, flags=re.I)
    return name[:120] if name else None


def _registry_ids(text: str, source_url: str) -> list[RegistryId]:
    out: list[RegistryId] = []
    for m in re.finditer(r"\bKRS(?:\s*(?:number|no\.?|nr|:))?\s*[:#]?\s*(\d{10})\b", text, flags=re.I):
        out.append(RegistryId(type="KRS", value=m.group(1), raw_quote=_norm(m.group(0)), source_url=source_url))
    for m in re.finditer(r"\bNIP(?:\s*(?:number|no\.?|nr|:))?\s*[:#]?\s*([0-9\-\s]{10,13})\b", text, flags=re.I):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 10:
            out.append(RegistryId(type="NIP", value=digits, raw_quote=_norm(m.group(0)), source_url=source_url))
    for m in re.finditer(r"\bREGON(?:\s*(?:number|no\.?|nr|:))?\s*[:#]?\s*(\d{9}|\d{14})\b", text, flags=re.I):
        out.append(RegistryId(type="REGON", value=m.group(1), raw_quote=_norm(m.group(0)), source_url=source_url))
    return out


def _registered_in(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    m = re.search(
        r"(?:registered|incorporated)\s+in\s+([A-ZŁŚŻŹĆŃÓ][\w\-.ąćęłńóśźżŁŚŻŹĆŃÓ]+)(?:,\s*([A-ZŁŚŻŹĆŃÓ][\w\-.ąćęłńóśźżŁŚŻŹĆŃÓ ]+))?",
        text,
        flags=re.I,
    )
    if not m:
        return None, None, None
    city = _city_norm(m.group(1))
    country = _country_norm(m.group(2) or "")
    quote = _norm(m.group(0))
    return city, (country or None), quote


def _registered_address(text: str) -> tuple[Optional[str], Optional[str]]:
    m = re.search(
        r"\b(?:registered office|registered address|company address|address|siedziba)\s*[:\-]?\s*"
        r"((?:ul\.|ulica|al\.|aleja)?\s*[^,\n]+,\s*\d{2}-\d{3}\s+[A-ZŁŚŻŹĆŃÓ][A-Za-ząćęłńóśźżŁŚŻŹĆŃÓ\-\s]+)",
        text,
        flags=re.I,
    )
    if not m:
        return None, None
    addr = _norm(m.group(1))
    cm = re.search(r"\d{2}-\d{3}\s+([A-ZŁŚŻŹĆŃÓ][A-Za-ząćęłńóśźżŁŚŻŹĆŃÓ\-\s]+)$", addr)
    city = _city_norm(cm.group(1)) if cm else None
    return addr, city


def extract_legal_signals(texts: list[LegalTextInput]) -> LegalSignals:
    out = LegalSignals()
    seen_registry: set[tuple[str, str]] = set()

    for inp in texts:
        txt = inp.text or ""
        if not txt.strip():
            continue
        low = txt.lower()
        legalish = any(
            k in low
            for k in (
                "krs",
                "nip",
                "regon",
                "registered in",
                "registered office",
                "sp. z o.o",
                "spółka z ograniczoną odpowiedzialnością",
                "limited liability company",
                "incorporated in",
            )
        )
        if not legalish:
            continue

        if out.legal_form is None:
            lf = _extract_legal_form(txt)
            if lf:
                out.legal_form = lf
                out.legal_form_original = lf
                out.legal_form_expanded = "spółka z ograniczoną odpowiedzialnością" if lf == "sp. z o.o." else None
                out.evidence.append(
                    _new_evidence(
                        source_url=inp.source_url,
                        source_type=inp.source_type,
                        claim_type="legal_form",
                        raw_quote=lf,
                        confidence=0.85,
                        weight=0.85,
                    )
                )

        if out.legal_entity_name is None:
            name = _extract_entity_name(txt)
            if name:
                out.legal_entity_name = name
                out.evidence.append(
                    _new_evidence(
                        source_url=inp.source_url,
                        source_type=inp.source_type,
                        claim_type="legal_entity_name",
                        raw_quote=name,
                        confidence=0.9,
                        weight=0.9,
                    )
                )

        reg_ids = _registry_ids(txt, inp.source_url)
        for rid in reg_ids:
            key = (rid.type, rid.value)
            if key in seen_registry:
                continue
            seen_registry.add(key)
            out.registry_ids.append(rid)
            out.evidence.append(
                _new_evidence(
                    source_url=inp.source_url,
                    source_type=inp.source_type,
                    claim_type="legal_registration",
                    raw_quote=rid.raw_quote or f"{rid.type} {rid.value}",
                    country="Poland" if rid.type in {"KRS", "NIP", "REGON"} else None,
                    registry_id=rid,
                    confidence=0.95,
                    weight=0.95,
                )
            )
            if rid.type in {"KRS", "NIP", "REGON"} and out.registered_country is None:
                out.registered_country = "Poland"

        city, country, quote = _registered_in(txt)
        if city and out.registered_city is None:
            out.registered_city = city
        if country and out.registered_country is None:
            out.registered_country = country
        if quote:
            out.evidence.append(
                _new_evidence(
                    source_url=inp.source_url,
                    source_type=inp.source_type,
                    claim_type="legal_registration",
                    raw_quote=quote,
                    city=city,
                    country=country,
                    confidence=0.95,
                    weight=0.95,
                )
            )

        addr, addr_city = _registered_address(txt)
        if addr and out.registered_address is None:
            out.registered_address = addr
        if addr_city and out.registered_city is None:
            out.registered_city = addr_city
        if addr:
            out.evidence.append(
                _new_evidence(
                    source_url=inp.source_url,
                    source_type=inp.source_type,
                    claim_type="registered_address",
                    raw_quote=addr,
                    city=addr_city,
                    address=addr,
                    confidence=0.9,
                    weight=0.9,
                )
            )

    return out

