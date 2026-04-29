"""Minimal Notion sync for pipeline visibility (80/20, no overengineering)."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from storage.database import get_deal_for_notion, get_deals_for_notion

NOTION_VERSION = "2022-06-28"


@dataclass
class SyncStats:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _normalize_database_id(raw: str) -> str:
    """
    Accept plain 32-char id, hyphenated uuid, or full Notion URL and return
    canonical hyphenated UUID-like id.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.split("?", 1)[0].strip().rstrip("/")
    # Match either 32-hex compact id or 36-char hyphenated UUID in the string.
    m = re.search(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", s)
    if not m:
        return s
    token = m.group(1).replace("-", "")
    if len(token) != 32:
        return s
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


def _status_select(row: dict[str, Any]) -> str:
    if bool(row.get("test_case")):
        return "TEST_CASE"
    fa = str(row.get("final_action") or "").upper()
    st = (row.get("status") or "").upper()
    if fa in ("STOP",):
        return "Reject"
    if fa in ("ASK_FOR_MORE_INFO", "RUN_ENRICHED_SCREEN"):
        return "Review"
    if fa in ("PASS_TO_PARTNER",):
        return "Pass"
    if st.startswith("REJECTED") or st in ("GATE1_FAILED", "GATE2_FAILED"):
        return "Reject"
    if st in ("WAITING_HITL", "GATE1_RUNNING", "GATE2_RUNNING", "GATE25_RUNNING", "NEW"):
        return "Review"
    if st in ("APPROVED", "APPROVED_DRAFT_CREATED", "GATE2_INTERNAL_PASS"):
        return "Pass"
    return "Review"


def _source_label(row: dict[str, Any]) -> str:
    msg_id = str(row.get("message_id") or "").lower()
    if msg_id.startswith("test_"):
        return "test"
    return "email"


def _gmail_message_url(message_id: str) -> str:
    mid = (message_id or "").strip()
    if not mid:
        return ""
    return f"https://mail.google.com/mail/u/0/#inbox/{mid}"


def _linkedin_search_url(name: str, company: str = "") -> str:
    q = urllib.parse.quote_plus(f"{name} {company}".strip())
    return f"https://www.linkedin.com/search/results/people/?keywords={q}"


def _parse_cee_osint(inferred_blob: str) -> list[tuple[str, str]]:
    """Return (country_token, source_url) pairs from inferred_signals.
    Skips example.vc - that domain is only valid for Fund's own portfolio.
    If the same token appears multiple times, keeps the entry with the best URL.
    """
    _SKIP = ("example.vc", "example.vc")
    # token -> best url found so far (empty string = no valid url yet)
    best: dict[str, str] = {}
    order: list[str] = []
    for line in (inferred_blob or "").splitlines():
        line = line.strip()
        if "cee_founder_roots_osint" not in line.lower():
            continue
        token_m = re.search(r'["""]([^"""]+)["""]', line)
        token = token_m.group(1).strip().lower() if token_m else ""
        if not token:
            continue
        url_m = re.search(r'\[?(https?://[^\]\s]+)\]?', line)
        url = url_m.group(1).strip() if url_m else ""
        if url and any(d in url.lower() for d in _SKIP):
            url = ""
        if token not in best:
            best[token] = url
            order.append(token)
        elif not best[token] and url:
            # upgrade: previous entry had no valid url, this one does
            best[token] = url
    return [(t, best[t]) for t in order]


def _profile_fields(row: dict[str, Any]) -> tuple[str, str, str]:
    """
    Returns (founded_year, founders_summary, product_one_liner)
    from persisted Gate2 facts where available.
    """
    founded_year = ""
    founders_summary = ""
    one_liner = ""

    raw = row.get("gate2_facts_json")
    if raw:
        try:
            d = json.loads(raw)
            if not founded_year:
                founded_year = str(d.get("founded_year") or "").strip()
            founders = d.get("founders") or d.get("team") or []
            if isinstance(founders, str):
                raw_f = founders.strip()
                # If the string looks like scraped Crunchbase UI labels
                # (multiple "; — Founder" entries), extract only real names.
                if " — Founder" in raw_f and ";" in raw_f:
                    _noise = {"crunchbase", "legal name", "operating status",
                              "company type", "funding.", "profile"}
                    candidates = []
                    for chunk in raw_f.split(";"):
                        chunk = chunk.strip()
                        if chunk.lower().endswith("— founder"):
                            name_part = chunk[: chunk.lower().rfind("— founder")].strip()
                            # strip leading "Founders " prefix if present
                            if name_part.lower().startswith("founders "):
                                name_part = name_part[9:].strip()
                            # skip if it contains noise words
                            if name_part and not any(n in name_part.lower() for n in _noise):
                                candidates.append(name_part)
                    founders_summary = ", ".join(candidates) if candidates else raw_f
                else:
                    founders_summary = raw_f
            elif isinstance(founders, list) and founders:
                names: list[str] = []
                for f in founders[:4]:
                    if isinstance(f, dict):
                        n = str(f.get("name") or "").strip()
                        if n:
                            names.append(n)
                    elif isinstance(f, str):
                        s = f.strip()
                        if s:
                            names.append(s)
                founders_summary = ", ".join(names[:4])
            if not founders_summary:
                founders_summary = str(
                    d.get("team_signals")
                    or d.get("team")
                    or ""
                ).strip()
            # Prefer product description from extracted deck facts (what business actually does),
            # and only then fallback to one-liner fields.
            if not one_liner:
                one_liner = str(
                    d.get("what_they_do")
                    or d.get("product_description")
                    or d.get("company_one_liner")
                    or d.get("one_liner")
                    or row.get("company_one_liner")
                    or ""
                ).strip()
        except Exception:
            pass
    if not one_liner:
        one_liner = str(row.get("company_one_liner") or "").strip()
    return founded_year, founders_summary, one_liner[:300]


def _title(row: dict[str, Any], *, score_first: bool = False) -> str:
    base = (row.get("company_name") or row.get("sender_name") or "Unknown").strip()[:150]
    return base


def _to_notion_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        except Exception:
            return None
    return dt.strftime("%Y-%m-%d")


_SCHEMA_DIM_KEYS = (
    "timing",
    "problem",
    "wedge",
    "founder_market_fit",
    "product_love",
    "execution_speed",
    "market",
    "moat_path",
    "traction",
    "business_model",
    "distribution",
)

_DIM_LABEL = {
    "timing": "Timing",
    "problem": "Problem / pain",
    "wedge": "Wedge / differentiation",
    "founder_market_fit": "Team / founder–market fit",
    "product_love": "Product",
    "execution_speed": "Execution speed",
    "market": "Market",
    "moat_path": "Moat / defensibility",
    "traction": "Traction",
    "business_model": "Business model",
    "distribution": "Distribution",
}


def _clip(text: str, max_len: int = 420) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - 1)].rstrip() + "…"


def _with_source(line: str, source: str) -> str:
    s = (line or "").rstrip()
    if not s:
        return s
    if "(source:" in s.lower():
        return s
    return f"{s} (source: {source})"


def _annotate_lines_with_source(text: str, default_source: str) -> str:
    """Append `(source: ...)` to value lines in Notion narrative blocks."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        ln = raw.rstrip()
        st = ln.strip()
        if not st:
            out.append(ln)
            continue
        # Section headers and structural lines should stay clean.
        if re.match(r"^\d+\)", st) or st.endswith(":"):
            out.append(ln)
            continue

        src = default_source
        l = st.lower()
        if "external" in l or "osint" in l or "tavily" in l:
            src = "tavily"
        elif "fact_on_site" in l or "on-site" in l or "website:" in l:
            src = "website_crawl"
        elif "final action" in l or "fund fit" in l or "gate 1" in l or "score" in l:
            src = "database+rules"
        elif "executive summary" in l or "rationale" in l or "why not higher" in l:
            src = "llm"
        out.append(_with_source(ln, src))
    return "\n".join(out)


def _facts_dict_from_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("gate2_facts_json")
    if not raw:
        return {}
    try:
        d = json.loads(str(raw))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _json_mixed_list(row: dict[str, Any], key: str) -> list[Any]:
    raw = row.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        arr = json.loads(str(raw))
        return arr if isinstance(arr, list) else []
    except Exception:
        s = str(raw).strip()
        return [s] if s else []


def _format_mixed_line(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, dict):
        q = str(item.get("question") or "").strip()
        w = str(item.get("why_it_matters") or "").strip()
        if q and w:
            return _clip(f"{q} — {w}", 700)
        if q:
            return _clip(q, 700)
        name = str(item.get("name") or item.get("title") or item.get("topic") or "").strip()
        desc = str(
            item.get("description")
            or item.get("detail")
            or item.get("rationale")
            or item.get("reasoning")
            or item.get("evidence")
            or ""
        ).strip()
        score = item.get("score")
        head_parts: list[str] = []
        if name:
            head_parts.append(name)
        if score is not None:
            try:
                head_parts.append(f"({float(score):g}/10)")
            except (TypeError, ValueError):
                pass
        head = " ".join(head_parts).strip()
        if head and desc:
            return _clip(f"{head}: {desc}", 700)
        if head or desc:
            return _clip(head or desc, 700)
        try:
            return _clip(json.dumps(item, ensure_ascii=False), 400)
        except Exception:
            return ""
    return _clip(str(item).strip(), 700)


def _lines_from_items(items: list[Any]) -> list[str]:
    out: list[str] = []
    for it in items:
        line = _format_mixed_line(it)
        if line:
            out.append(line)
    return out


def _dim_high_low_lines(
    dims: dict[str, Any],
    *,
    high_threshold: int = 7,
    low_threshold: int = 4,
) -> tuple[list[str], list[str]]:
    highs: list[str] = []
    lows: list[str] = []
    if not isinstance(dims, dict):
        return highs, lows
    for key in _SCHEMA_DIM_KEYS:
        d = dims.get(key)
        if not isinstance(d, dict):
            continue
        try:
            sc = int(d.get("score"))
        except (TypeError, ValueError):
            continue
        reasoning = _clip(str(d.get("reasoning") or "").strip(), 380)
        label = _DIM_LABEL.get(key, key)
        if sc >= high_threshold and reasoning:
            highs.append(f"• {label} ({sc}/10): {reasoning}")
        if sc <= low_threshold and reasoning:
            lows.append(f"• {label} ({sc}/10): {reasoning}")
    return highs, lows


def _compact_dim_scores(dims: dict[str, Any]) -> str:
    parts: list[str] = []
    if not isinstance(dims, dict):
        return ""
    for key in _SCHEMA_DIM_KEYS:
        d = dims.get(key)
        if not isinstance(d, dict):
            continue
        try:
            sc = int(d.get("score"))
        except (TypeError, ValueError):
            continue
        parts.append(f"{_DIM_LABEL.get(key, key)} {sc}/10")
    return " · ".join(parts)


def _build_deal_summary_blocks(row: dict[str, Any]) -> list[dict[str, Any]]:
    founded_year, founders_summary, one_liner = _profile_fields(row)
    facts_obj = _facts_dict_from_row(row)
    sender = f"{str(row.get('sender_name') or '').strip()} <{str(row.get('sender_email') or '').strip()}>".strip(" <>")
    received_iso = _to_notion_date(row.get("created_at")) or ""
    source_url = str(row.get("source_url") or "").strip()
    link_label = "Website" if source_url else "Gmail"
    primary_link = source_url or _gmail_message_url(str(row.get("message_id") or ""))
    fund_fit = str(row.get("fund_fit_decision") or "")
    deck_ev = str(row.get("deck_evidence_decision") or "")
    generic = str(row.get("generic_vc_interest") or "")
    final_action = str(row.get("final_action") or "")
    auth_risk = str(row.get("auth_risk") or "")
    depth = str(row.get("screening_depth") or "")
    rationale = str(
        row.get("gate2_recommendation_rationale")
        or row.get("gate1_rejection_reason")
        or row.get("gate2_summary")
        or ""
    ).strip()
    verdict = str(row.get("gate2_recommendation") or row.get("final_action") or "").strip()
    fund_score = row.get("fund_fit_score")
    deck_score = row.get("deck_evidence_score")
    gate1_verdict = str(row.get("gate1_verdict") or "").strip()
    external_score = row.get("external_opportunity_score")
    na = "—"
    is_website = bool(source_url)
    if is_website and (not one_liner):
        one_liner = str(
            facts_obj.get("product_description")
            or facts_obj.get("one_liner")
            or ""
        ).strip()
    if is_website and (not one_liner):
        try:
            strengths_arr = _json_mixed_list(row, "gate2_strengths")
            if strengths_arr:
                first = _format_mixed_line(strengths_arr[0])
                if first:
                    one_liner = first
        except Exception:
            pass
    result_obj: dict[str, Any] = {}
    try:
        result_obj = json.loads(str(row.get("gate2_dimensions_json") or "{}"))
        if not isinstance(result_obj, dict):
            result_obj = {}
    except Exception:
        result_obj = {}
    website_scores_obj = result_obj.get("website_scores") if isinstance(result_obj, dict) else None
    dim_highs, dim_lows = _dim_high_low_lines(result_obj)

    def _merge_str_lists(primary: list[str], fallback: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for bucket in (primary, fallback):
            for x in bucket:
                k = x.strip().lower()
                if k and k not in seen:
                    seen.add(k)
                    out.append(x)
        return out

    def _band_label(sc: int) -> str:
        if sc >= 10:
            return "10 (OUTLIER)"
        if sc >= 7:
            return "7–9 (STRONG)"
        if sc >= 4:
            return "4–6 (PARTIAL)"
        return "1–3 (WEAK)"

    def _first_sentence(s: str) -> str:
        t = (s or "").strip()
        if not t:
            return ""
        # Keep the first sentence-ish chunk (avoid huge walls of text in Notion).
        m = re.split(r"(?<=[.!?])\s+", t)
        return (m[0] if m else t)[:260].strip()

    strengths_txt = ""
    if is_website and isinstance(website_scores_obj, dict) and website_scores_obj:
        # Website runs: show score + band + first-sentence rationale, plus one quote if present.
        dim_items: list[tuple[str, int, dict[str, Any]]] = []
        for k, v in website_scores_obj.items():
            if not isinstance(v, dict) or "score" not in v:
                continue
            try:
                dim_items.append((k, int(v.get("score") or 0), v))
            except Exception:
                continue
        dim_items.sort(key=lambda x: x[1], reverse=True)
        top = dim_items[:3]
        lines: list[str] = []
        for k, sc, obj in top:
            rsn = _first_sentence(str(obj.get("reasoning") or ""))
            band = _band_label(sc)
            ev0 = ""
            ev = obj.get("evidence") or []
            if isinstance(ev, list) and ev:
                e0 = ev[0] if isinstance(ev[0], dict) else {}
                q = str(e0.get("quote") or "").strip()
                src = str(e0.get("source") or "").strip()
                if q:
                    ev0 = f' Quote: "{_clip(q, 280)}"' + (f" ({_clip(src, 120)})" if src else "")
            label = k.replace("_", " ")
            lines.append(f"{label} — {sc}/10 [{band}] — {_clip(rsn, 260)}{ev0}".strip())
        strengths_txt = "\n".join(f"• {x}" for x in lines if x)
    else:
        strength_items: list[Any] = []
        if _json_mixed_list(row, "gate2_strengths"):
            strength_items = _json_mixed_list(row, "gate2_strengths")
        elif result_obj.get("top_strengths"):
            strength_items = list(result_obj.get("top_strengths") or [])
        elif result_obj.get("strengths"):
            strength_items = list(result_obj.get("strengths") or [])
        elif (result_obj.get("vc_pack") or {}).get("top_strengths"):
            strength_items = list((result_obj.get("vc_pack") or {}).get("top_strengths") or [])
        elif (result_obj.get("website_vc_pack") or {}).get("strengths"):
            strength_items = list((result_obj.get("website_vc_pack") or {}).get("strengths") or [])
        strength_lines = _lines_from_items(strength_items)
        if not strength_lines and dim_highs:
            strength_lines = list(dim_highs)
        strengths_txt = "\n".join(f"• {s}" for s in strength_lines) if strength_lines else ""

    risks_txt = ""
    if is_website and isinstance(website_scores_obj, dict) and website_scores_obj:
        dim_items2: list[tuple[str, int, dict[str, Any]]] = []
        for k, v in website_scores_obj.items():
            if not isinstance(v, dict) or "score" not in v:
                continue
            try:
                dim_items2.append((k, int(v.get("score") or 0), v))
            except Exception:
                continue
        dim_items2.sort(key=lambda x: x[1])
        low = dim_items2[:3]
        lines2: list[str] = []
        for k, sc, obj in low:
            rsn = _first_sentence(str(obj.get("reasoning") or ""))
            band = _band_label(sc)
            miss0 = ""
            md = obj.get("missing_data") or []
            if isinstance(md, list) and md:
                miss0 = f" Missing: {_clip(str(md[0]), 180)}"
            label = k.replace("_", " ")
            lines2.append(f"{label} — {sc}/10 [{band}] — {_clip(rsn, 260)}{miss0}".strip())
        risks_txt = "\n".join(f"• {x}" for x in lines2 if x)
    else:
        risk_items: list[Any] = []
        if _json_mixed_list(row, "gate2_concerns"):
            risk_items = _json_mixed_list(row, "gate2_concerns")
        elif result_obj.get("top_risks"):
            risk_items = list(result_obj.get("top_risks") or [])
        elif result_obj.get("top_concerns"):
            risk_items = list(result_obj.get("top_concerns") or [])
        risk_lines = _lines_from_items(risk_items)
        if not risk_lines and dim_lows:
            risk_lines = list(dim_lows)
        risks_txt = "\n".join(f"• {s}" for s in risk_lines) if risk_lines else ""

    _mc_top = result_obj.get("missing_critical_data")
    missing_fb = (
        [str(x).strip() for x in _mc_top if str(x).strip()]
        if isinstance(_mc_top, list)
        else []
    )
    missing_src = _merge_str_lists(
        [str(x).strip() for x in _json_mixed_list(row, "gate2_missing_critical_data") if str(x).strip()],
        missing_fb,
    )
    kill_flags = (
        [str(x).strip() for x in _json_mixed_list(row, "gate2_quality_flags") if str(x).strip()]
        or [str(x).strip() for x in (result_obj.get("kill_flags") or []) if str(x).strip()]
        or [str(x).strip() for x in (result_obj.get("red_flags") or []) if str(x).strip()]
    )
    follow_parts = _lines_from_items(_json_mixed_list(row, "gate2_should_ask_founder"))
    follow_strs = follow_parts
    if not follow_strs:
        follow_strs = [
            str(x).strip()
            for x in (result_obj.get("follow_up_questions") or result_obj.get("follow_ups") or [])
            if str(x).strip()
        ]
    followups = follow_strs
    if not followups:
        mvn = result_obj.get("must_validate_next") or []
        if isinstance(mvn, list):
            for item in mvn[:8]:
                if isinstance(item, dict):
                    q = str(item.get("question") or "").strip()
                    w = str(item.get("why_it_matters") or "").strip()
                    if q:
                        followups.append(f"{q} — {w}" if w else q)
    ask_merge = result_obj.get("should_ask_founder") if isinstance(result_obj.get("should_ask_founder"), list) else []
    if ask_merge:
        followups = _merge_str_lists(followups, [str(x).strip() for x in ask_merge if str(x).strip()])

    missing = missing_src
    # Normalize missing: split multiline bullets + dedupe
    missing_norm: list[str] = []
    seen: set[str] = set()
    for item in (missing or []):
        for ln in str(item).splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.startswith("-"):
                s = s.lstrip("-").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            missing_norm.append(s)
    # Prefer detailed entries and drop shorter duplicates contained in longer lines.
    filtered_missing: list[str] = []
    for m in missing_norm:
        ml = m.lower()
        if any((ml != x.lower()) and (ml in x.lower()) and (len(x) > len(m)) for x in missing_norm):
            continue
        filtered_missing.append(m)
    missing_txt = "\n".join([f"• {m}" for m in filtered_missing[:16]]) if filtered_missing else ""
    kill_txt = ", ".join(kill_flags) if kill_flags else ""
    follow_txt = "\n".join([f"• {q}" for q in followups[:12]]) if followups else ""

    # Decision snapshot
    score_val = row.get("deck_evidence_score")
    if score_val is None:
        score_val = row.get("gate2_overall_score")
    score_str = f"{float(score_val):.2f}" if score_val is not None else "n/a"
    stage = str(row.get("gate1_detected_stage") or facts_obj.get("stage") or "").strip()
    geo = str(row.get("gate1_detected_geography") or facts_obj.get("geography") or "").strip()
    sector_snap = str(
        row.get("gate1_detected_sector") or facts_obj.get("sector") or facts_obj.get("market") or ""
    ).strip()
    inferred_blob = str(facts_obj.get("inferred_signals") or "")

    def _extract_inferred_value(prefix: str) -> str:
        for ln in inferred_blob.splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.lower().startswith(prefix.lower()):
                return s.split(":", 1)[1].strip() if ":" in s else s
        return ""

    founder_nationality = _extract_inferred_value("founder_nationality_hint")
    registration_geo = _extract_inferred_value("company_registration_geo_hint")
    if not geo:
        inferred = ""
        if source_url.lower().endswith(".pl") or ".pl/" in source_url.lower():
            inferred = "Poland"
        else:
            try:
                lang = str(facts_obj.get("language") or "").lower()
                founders_blob = (founders_summary or "").lower()
                polish_markers = (
                    "ą", "ć", "ę", "ł", "ń", "ó", "ś", "ź", "ż", "sz", "cz",
                    "wicz", "icz", "ski", "cki", "dzki", "owski", "ewski",
                    "misztal", "zimoch", "kowalski", "nowak", "wiśniewski",
                )
                if "polish" in lang or "polski" in lang:
                    inferred = "Poland"
                elif any(m in founders_blob for m in polish_markers):
                    inferred = "Poland"
            except Exception:
                inferred = ""
        geo = f"{inferred} (inferred)" if inferred else ""
    why_blocked = ""
    if str(final_action or "").upper() not in ("PASS_TO_PARTNER", "PASS"):
        # Avoid misleading "hard reject" language when we're simply asking for more info
        # (common for website-only runs with thin geo/stage evidence).
        soft_hold = str(final_action or "").upper() in ("ASK_FOR_MORE_INFO", "RUN_ENRICHED_SCREEN")
        website_needs_deck = is_website and str(verdict or "").upper() in ("NEEDS_DECK", "NEEDS_FOUNDER_CALL")
        if soft_hold and website_needs_deck:
            why_blocked = "Website-only evidence insufficient — request deck / validate geo & stage."
        else:
            why_blocked = (
                str(row.get("gate1_rejection_reason") or "").strip()
                or (kill_flags[0] if kill_flags else "")
                or (f"Fund Fit: {fund_fit}" if fund_fit and fund_fit != "PASS" else "")
                or (f"Gate 1: UNCERTAIN — geography/stage not confirmed" if "UNCERTAIN" in str(fund_fit or "") else "")
                or "—"
            )
    if not why_blocked:
        why_blocked = "—"

    internal_score_label = "Website VC score" if is_website else "Deck evidence / internal score"
    snap_lines = [
        "1) Scores",
        f"{internal_score_label}: {score_str}",
        f"Gate 2 overall (pipeline): {'%.2f' % float(row.get('gate2_overall_score')) if row.get('gate2_overall_score') is not None else 'n/a'}",
        f"External opportunity score: {'%.2f' % float(external_score) if external_score is not None else 'n/a'}",
        f"Fund Fit score: {'%.2f' % float(fund_score) if fund_score is not None else 'n/a'}",
        "",
        "2) Mandate / routing",
        f"Final Action: {final_action or na}",
        f"Fund Fit decision: {fund_fit or na}",
        f"Gate 1 verdict: {gate1_verdict or na}",
        f"Verdict / recommendation: {verdict or na}",
    ]
    if sector_snap:
        snap_lines.append(f"Sector: {sector_snap}")
    snap_lines.append(f"Stage: {stage or na}")
    snap_lines.append(f"Geography: {geo or na}")
    snap_lines.append(f"Company registration geography: {registration_geo or na}")
    snap_lines.append(f"Founders nationalities: {founder_nationality or na}")
    if is_website:
        snap_lines.append(
            "Mode: Website-only (INITIAL) — unknowns are missing evidence, not a soft reject."
        )
    snap_lines.extend(["", "3) Blockers / uncertainty", f"Why blocked (if not pass): {why_blocked}"])
    snapshot_txt = _annotate_lines_with_source("\n".join(snap_lines), "database+rules")

    # Market context + VC narrative (dims / flags / why-not-higher)
    sat = "n/a"
    timing_sig = "n/a"
    comps = "n/a"
    try:
        sat_raw = (
            result_obj.get("market_saturation")
            or (result_obj.get("vc_scores") or {}).get("saturation_score")
        )
        timing_raw = (
            result_obj.get("timing_score")
            or (result_obj.get("vc_scores") or {}).get("timing_score")
        )
        comp_raw = result_obj.get("competition_density")
        if sat_raw is not None and str(sat_raw).strip():
            sat = f"{float(sat_raw):.1f}/10"
        if timing_raw is not None and str(timing_raw).strip():
            timing_sig = f"{float(timing_raw):.1f}/10"
        if comp_raw:
            comps = str(comp_raw)

        wnh = result_obj.get("why_not_higher") or []
        blob = " | ".join([str(x) for x in wnh if x]) if isinstance(wnh, list) else str(wnh)
        if sat == "n/a":
            m = re.search(r"saturation\s*[:=]?\s*(\d+(?:\.\d+)?)", blob, re.I)
            if m:
                sat = f"{float(m.group(1)):.1f}/10"
        if timing_sig == "n/a":
            m2 = re.search(r"timing[_\s-]*score\s*[:=]?\s*(\d+(?:\.\d+)?)", blob, re.I)
            if m2:
                timing_sig = f"{float(m2.group(1)):.1f}/10"
        fo = facts_obj
        if sat == "n/a":
            sat_raw2 = fo.get("market_saturation") or fo.get("saturation_score")
            if sat_raw2 is not None and str(sat_raw2).strip():
                sat = f"{float(sat_raw2):.1f}/10"
        if timing_sig == "n/a":
            timing_raw2 = fo.get("timing_score")
            if timing_raw2 is not None and str(timing_raw2).strip():
                timing_sig = f"{float(timing_raw2):.1f}/10"
        wnh_blob = str(fo.get("why_not_higher") or "")
        if sat == "n/a":
            m3 = re.search(r"saturation\s*[:=heuristic]*\s*(\d+(?:\.\d+)?)", wnh_blob, re.I)
            if m3:
                sat = f"{float(m3.group(1)):.1f}/10"
        if timing_sig == "n/a":
            m4 = re.search(r"timing[_\s]*score\s*[=:]*\s*(\d+(?:\.\d+)?)", wnh_blob, re.I)
            if m4:
                timing_sig = f"{float(m4.group(1)):.1f}/10"
        if comps == "n/a":
            comps = (
                "crowded (heuristic)"
                if sat != "n/a" and float(sat.split("/")[0]) < 4.0
                else "n/a"
            )
    except Exception:
        pass

    market_extra: list[str] = []
    wnh_list = result_obj.get("why_not_higher") or []
    if isinstance(wnh_list, list):
        for x in wnh_list[:10]:
            t = str(x).strip()
            if t:
                market_extra.append(f"• {t}")
    for dim_key in ("market", "timing"):
        dm = result_obj.get(dim_key)
        if isinstance(dm, dict):
            wnh_dim = str(dm.get("why_not_higher") or "").strip()
            if len(wnh_dim) > 15:
                market_extra.append(f"• {_DIM_LABEL.get(dim_key, dim_key)} — {_clip(wnh_dim, 400)}")
    slow_flags = result_obj.get("slow_execution_flags") if isinstance(result_obj.get("slow_execution_flags"), list) else []
    sol_flags = result_obj.get("solution_love_flags") if isinstance(result_obj.get("solution_love_flags"), list) else []
    if slow_flags:
        market_extra.append("Execution notes: " + "; ".join(str(x) for x in slow_flags[:8]))
    if sol_flags:
        market_extra.append("Product signals: " + "; ".join(str(x) for x in sol_flags[:8]))

    market_txt = _annotate_lines_with_source("\n".join(
        [
            "1) Heuristics",
            f"Saturation heuristic: {sat}",
            f"Timing signal: {timing_sig}",
            f"Competition read: {comps}",
            "",
            "2) Evidence / notes",
            "\n".join(market_extra) if market_extra else na,
            "",
            "3) Implication",
            "Heuristics are website-only and directional; validate with deck/call + real metrics.",
        ]
    ).strip(), "llm")

    next_step = str(
        result_obj.get("recommended_next_step")
        or row.get("gate2_recommendation_rationale")
        or row.get("gate2_summary")
        or ""
    ).strip()

    known_lines: list[str] = []
    if one_liner:
        known_lines.append(f"One-liner: {_clip(one_liner, 340)}")
    what_long = str(facts_obj.get("what_they_do") or "").strip()
    if what_long and what_long.lower() != (one_liner or "").lower():
        known_lines.append(f"What they do: {_clip(what_long, 560)}")
    if founders_summary:
        known_lines.append(f"Founders / team: {_clip(founders_summary, 320)}")
    if founder_nationality:
        known_lines.append(f"Founders nationalities (hint): {_clip(founder_nationality, 220)}")
    if registration_geo:
        known_lines.append(f"Company registration geo (hint): {_clip(registration_geo, 220)}")
    if founded_year:
        known_lines.append(f"Founded: {founded_year}")
    sector_row = str(row.get("gate1_detected_sector") or "").strip()
    sector_f = str(facts_obj.get("market") or facts_obj.get("sector") or "").strip()
    if sector_row:
        known_lines.append(f"Sector (pipeline): {sector_row}")
    elif sector_f:
        known_lines.append(f"Sector (extracted): {_clip(sector_f, 220)}")
    stage_f = str(facts_obj.get("stage") or facts_obj.get("stage_guess") or "").strip()
    if stage_f:
        known_lines.append(f"Stage (facts): {stage_f}")
    cust = str(
        facts_obj.get("customers")
        or facts_obj.get("customer")
        or facts_obj.get("target_customer")
        or ""
    ).strip()
    if cust:
        known_lines.append(f"Customers / ICP: {_clip(cust, 300)}")
    pricing = str(facts_obj.get("pricing") or facts_obj.get("pricing_signals") or "").strip()
    if pricing:
        known_lines.append(f"Pricing / model: {_clip(pricing, 260)}")
    traction_f = str(facts_obj.get("traction") or facts_obj.get("traction_signals") or "").strip()
    if traction_f:
        known_lines.append(f"Traction (claimed): {_clip(traction_f, 420)}")
    fr = str(facts_obj.get("fundraising_ask") or "").strip()
    uf = str(facts_obj.get("use_of_funds") or "").strip()
    if fr or uf:
        ru = " · ".join([p for p in [fr, uf] if p])
        known_lines.append(f"Raise / use of funds: {_clip(ru, 360)}")
    if not is_website and sender:
        known_lines.append(f"Sender: {sender}")
    if received_iso:
        known_lines.append(f"Received: {received_iso}")
    if primary_link:
        known_lines.append(f"{link_label}: {primary_link}")
    if not known_lines:
        fb = str(row.get("gate2_summary") or row.get("company_one_liner") or "").strip()
        if fb:
            known_lines.append(_clip(fb, 950))

    named_lines: list[str] = []
    f_founders = str(facts_obj.get("founders") or "").strip()
    if f_founders and f_founders.lower() not in ("unknown", "n/a", "none"):
        named_lines.append(f"Founders: {_clip(f_founders, 360)}")
    # logos_or_case_studies holds actual customer names; customer_proof is often
    # a marketing claim — use logos first and only fall back to customer_proof
    # when it contains concrete evidence (numbers / percentages).
    _logos = str(facts_obj.get("logos_or_case_studies") or "").strip()
    _proof = str(facts_obj.get("customer_proof") or "").strip()
    _logos_valid = bool(_logos) and _logos.lower() not in ("unknown", "n/a", "none", "not stated", "—")
    _proof_has_data = bool(re.search(r'\d', _proof))  # has at least one digit → concrete
    cust2 = _logos if _logos_valid else (_proof if _proof_has_data else "")
    if cust2:
        named_lines.append(f"Customers / logos: {_clip(cust2, 360)}")
    integ = str(facts_obj.get("integrations") or "").strip()
    if integ:
        named_lines.append(f"Integrations: {_clip(integ, 360)}")
    sec = str(facts_obj.get("security_compliance_signals") or "").strip()
    if sec:
        named_lines.append(f"Security / compliance: {_clip(sec, 360)}")

    unknown_lines: list[str] = []
    miss_txt = str(facts_obj.get("unclear_or_missing_data") or "").strip()
    if miss_txt:
        unknown_lines.append(_clip(miss_txt, 520))

    summary1 = _annotate_lines_with_source("\n".join(
        [
            "1) What we know (from source)",
            "\n".join(known_lines) if known_lines else na,
            "",
            "2) Named entities / specifics",
            "\n".join(named_lines) if named_lines else na,
            "",
            "3) Unknown / missing",
            "\n".join(unknown_lines) if unknown_lines else na,
        ]
    ).strip(), "website_crawl+llm")

    exec_sum = str(row.get("gate2_summary") or "").strip()
    cds = _compact_dim_scores(result_obj)
    internal_label2 = "Website VC score" if is_website else "Deck Evidence"
    part1 = "\n".join(
        [
            f"Verdict: {verdict or na}",
            f"{internal_label2} decision: {deck_ev or na} (score: {na if deck_score is None else deck_score})",
            f"Fund Fit: {fund_fit or na} (score: {na if fund_score is None else fund_score})",
            f"Generic VC Interest: {generic or na}",
            f"Final Action: {final_action or na}",
        ]
    ).strip()
    exec_for_display = _clip(exec_sum, 900) if exec_sum else ""
    if is_website and exec_for_display and re.search(r"\bMissing:\s*", exec_for_display, re.I):
        # Avoid repeating the whole Missing section inside the executive summary.
        exec_for_display = re.sub(
            r"\s*Missing:\s*[\s\S]+$",
            "",
            exec_for_display,
            flags=re.I,
        ).strip()
    part2 = "\n".join(
        [
            "Executive summary:",
            exec_for_display if exec_for_display else na,
            "",
            f"Rationale: {rationale or na}",
            (f"Full dimension scorecard: {cds}" if cds else ""),
        ]
    ).strip()
    part3 = "\n".join(
        [
            f"Screening Depth: {depth or na}",
            (f"Auth Risk: {auth_risk or na}" if not is_website else ""),
            f"Stage: {stage or na}",
            f"Geography: {geo or na}",
        ]
    ).strip()
    summary2 = _annotate_lines_with_source("\n".join(
        [
            "1) Signal / decision",
            part1,
            "",
            "2) Why",
            part2,
            "",
            "3) Context",
            part3,
        ]
    ).strip(), "llm+database+rules")

    mf_parts = [
        "Missing data:",
        missing_txt if missing_txt else "(nothing flagged in screening)",
        "",
        "Kill / quality flags:",
        kill_txt if kill_txt else "(none)",
        "",
        "Follow-up questions:",
        follow_txt if follow_txt else "(none suggested)",
    ]
    mf_txt = _annotate_lines_with_source("\n".join(mf_parts), "llm+website_crawl")

    if not strengths_txt:
        slf = result_obj.get("solution_love_flags") if isinstance(result_obj.get("solution_love_flags"), list) else []
        if slf:
            strengths_txt = "\n".join(f"• {str(x)}" for x in slf[:14])
    if not strengths_txt:
        prob = result_obj.get("problem")
        if isinstance(prob, dict):
            eu = prob.get("evidence_used") or []
            if isinstance(eu, list) and eu:
                strengths_txt = "\n".join(f"• {str(x)}" for x in eu[:10])
            elif str(prob.get("reasoning") or "").strip():
                strengths_txt = f"• Problem / pain (scoreboard): {_clip(str(prob.get('reasoning')), 520)}"

    if not risks_txt:
        sef = result_obj.get("slow_execution_flags") if isinstance(result_obj.get("slow_execution_flags"), list) else []
        if sef:
            risks_txt = "\n".join(f"• {str(x)}" for x in sef[:14])

    # Enforce 3-subsection structure for narrative sections (kept inside a single Notion paragraph).
    ev_rows = result_obj.get("evidence_table") if isinstance(result_obj, dict) else None
    ev_lines: list[str] = []
    if isinstance(ev_rows, list):
        for r in ev_rows[:10]:
            if not isinstance(r, dict):
                continue
            aspect = str(r.get("aspect") or "").strip()
            finding = str(r.get("finding") or "").strip()
            kind = str(r.get("kind") or "").strip()
            if aspect and finding:
                ev_lines.append(f"• {aspect} [{kind or 'fact'}]: {_clip(finding, 520)}")
    ev_txt = "\n".join(ev_lines) if ev_lines else na
    ask_txt = "\n".join([f"• {q}" for q in followups[:8]]) if followups else na

    strengths_txt = _annotate_lines_with_source("\n".join(
        [
            "1) Strong signals",
            strengths_txt if strengths_txt else na,
            "",
            "2) Evidence (on-site / extracted)",
            ev_txt,
            "",
            "3) Validate next",
            ask_txt,
        ]
    ).strip(), "llm+website_crawl")
    ev_dup_note = (
        "Same on-site crawl ledger as in 💪 Strengths (not duplicated here)."
        if ev_txt and ev_txt != na
        else na
    )
    risks_txt = _annotate_lines_with_source("\n".join(
        [
            "1) Main risks / weak signals",
            risks_txt if risks_txt else na,
            "",
            "2) On-site evidence",
            ev_dup_note,
            "",
            "3) Follow-ups",
            ask_txt,
        ]
    ).strip(), "llm+website_crawl")

    gsum = str(row.get("gate2_summary") or "").strip()
    rationale_clean = str(rationale or "").strip()
    rec_main = next_step or (_clip(gsum, 1100) if gsum else "")
    if is_website and rationale_clean and str(final_action or "").upper() in (
        "ASK_FOR_MORE_INFO",
        "RUN_ENRICHED_SCREEN",
    ):
        rec_why = rationale_clean
    elif gsum and rec_main and gsum not in rec_main:
        rec_why = _clip(gsum, 520)
    else:
        rec_why = rationale_clean or na
    rec_ask = "\n".join([f"• {q}" for q in followups[:8]]) if followups else na
    rec_txt = _annotate_lines_with_source("\n".join(
        [
            "1) Recommendation",
            rec_main or na,
            "",
            "2) Why",
            rec_why or na,
            "",
            "3) What to request / validate",
            rec_ask,
        ]
    ).strip(), "llm+rules")

    # VC-first memo layout (partner readable): decision first, then fit and facts.
    def _fit_mark(value: bool | None) -> str:
        if value is True:
            return "✅"
        if value is False:
            return "❌"
        return "?"

    def _is_unknown(v: str) -> bool:
        t = (v or "").strip().lower()
        return (not t) or t in ("unknown", "n/a", "none", "not stated", "—")

    def _strip_source(v: str) -> str:
        return re.sub(r"\s*\(source:\s*[^)]*\)\s*$", "", (v or "").strip(), flags=re.I)

    geo_low = (geo or "").lower()
    has_cee_hint = bool(founder_nationality) or ("cee" in inferred_blob.lower()) or ("diaspora" in inferred_blob.lower())
    geo_fit: bool | None
    if has_cee_hint:
        geo_fit = True
    elif _is_unknown(geo):
        geo_fit = None
    elif any(x in geo_low for x in ("poland", "lithuania", "latvia", "estonia", "croatia", "serbia", "ukraine", "romania", "bulgaria", "slovenia", "czech", "hungary", "slovakia")):
        geo_fit = True
    else:
        geo_fit = False

    st_low = (stage or "").lower()
    if _is_unknown(stage):
        stage_fit: bool | None = None
    elif "pre-seed" in st_low or "preseed" in st_low or "seed" in st_low or "series a" in st_low or "early" in st_low:
        stage_fit = True
    else:
        stage_fit = False

    sec_low = (sector_snap or "").lower()
    if _is_unknown(sector_snap):
        sector_fit: bool | None = None
    elif any(x in sec_low for x in ("ai", "developer", "dev", "data", "health", "saas", "marketplace", "b2b", "fintech", "security", "automation", "credit", "risk")):
        sector_fit = True
    else:
        sector_fit = False

    if (not _is_unknown(founders_summary)) and (not _is_unknown(founder_nationality)):
        founder_fit: bool | None = True
    elif not _is_unknown(founders_summary):
        founder_fit = None
    else:
        founder_fit = None

    if (not _is_unknown(one_liner)) and ((not _is_unknown(cust2)) or (not _is_unknown(traction_f))):
        product_fit: bool | None = True
    elif not _is_unknown(one_liner):
        product_fit = None
    else:
        product_fit = None

    # ── helpers ──────────────────────────────────────────────────────────────
    def _fit_label(v: bool | None, *, uncertain_label: str = "UNCERTAIN") -> str:
        if v is True:
            return "YES"
        if v is False:
            return "NO"
        return uncertain_label

    def _src(label: str, url: str = "") -> str:
        """Format a source tag: (label · [domain](url)) or (label)."""
        if url:
            domain = re.sub(r"https?://(?:www\.)?([^/]+).*", r"\1", url)
            return f"([{domain}]({url}))"
        return f"({label})"

    company_name_str = str(row.get("company_name") or "")

    overall_fit_raw = str(fund_fit or "").upper()
    overall_fit_label = (
        "FAIL" if overall_fit_raw == "FAIL"
        else "PASS" if overall_fit_raw == "PASS"
        else "UNCERTAIN"
    )
    rec_action = (
        "REQUEST_DECK"
        if str(final_action or "").upper() in ("ASK_FOR_MORE_INFO", "RUN_ENRICHED_SCREEN")
        else (final_action or na)
    )
    confidence_display = str(row.get("gate2_confidence") or "LOW").capitalize()

    # ── Layer 0: Decision Header ──────────────────────────────────────────────
    def _clean_signal_line(raw: str) -> str:
        """Extract meaningful signal from scoring noise.

        Handles two formats:
          'dim name — N/10 [BAND] — Band: X-Y (BAND) — description'  (website_scores)
          'dim name (N/10): Band: X-Y (BAND) — description'           (gate2_strengths)
        Result: 'dim name — description'
        """
        s = _strip_source(raw)
        # Remove Quote: "..." evidence block (may span to end)
        s = re.sub(r'\s*Quote:\s*["""].*$', '', s, flags=re.DOTALL)
        # Remove [fact_on_site] / [inferred] / [missing] kind tags
        s = re.sub(r'\s*\[(?:fact_on_site|inferred|missing|fact)\]', '', s)
        # Split on em-dash (—) to isolate dimension name and description
        em = '—'
        parts = [p.strip() for p in s.split(em) if p.strip()]
        # Filter out parts that are purely score/band noise
        _noise_pat = re.compile(r'^\d+/10$|^Band:|^\d+[–\-]\d+\s*\(|^\s*\d+\s*$', re.I)
        clean_parts = [p for p in parts if not _noise_pat.match(p)]
        # Also strip inline "N/10 [LABEL]" from remaining parts
        clean_parts = [re.sub(r'\s*\d+/10\s*\[[^\]]+\]', '', p).strip() for p in clean_parts]
        clean_parts = [p for p in clean_parts if p]
        if len(clean_parts) >= 2:
            return f"{clean_parts[0]} — {clean_parts[-1]}"
        return clean_parts[0] if clean_parts else ""

    followup_set = {q.lower().strip() for q in followups}

    positives_lines: list[str] = []
    for line in (strengths_txt or "").splitlines():
        s = line.strip()
        if s.startswith("• "):
            cleaned = _clean_signal_line(s[2:])
            if cleaned and cleaned.lower().strip() not in followup_set:
                positives_lines.append(cleaned)

    risks_lines: list[str] = []
    for line in (risks_txt or "").splitlines():
        s = line.strip()
        if s.startswith("• "):
            cleaned = _clean_signal_line(s[2:])
            # skip lines that are actually follow-up questions
            if cleaned and cleaned.lower().strip() not in followup_set:
                risks_lines.append(cleaned)

    why_bullets: list[str] = []
    if positives_lines:
        why_bullets.append(positives_lines[0])
    if risks_lines:
        why_bullets.append(risks_lines[0])
    if why_blocked and why_blocked != na:
        why_bullets.append(why_blocked)
    if filtered_missing and len(why_bullets) < 4:
        why_bullets.append(f"Missing: {filtered_missing[0]}")
    why_bullets = why_bullets[:4]

    decision_txt = "\n".join([
        f"**Company:** {company_name_str or na}",
        "",
        f"**Verdict:** {rec_action}    **Fit:** {overall_fit_label}    **Confidence:** {confidence_display}",
        "",
        "**Why:**",
        *[f"- {b}" for b in why_bullets],
    ])

    # ── Layer 1: Investment Fit Tree ──────────────────────────────────────────
    # Tokens that are sub-words of country names but useless standalone (false positives)
    _CEE_TOKEN_NOISE = {"republic", "new", "north", "south", "east", "west", "land", "island"}

    cee_pairs = [
        (tok, url) for tok, url in _parse_cee_osint(inferred_blob)
        if tok.lower() not in _CEE_TOKEN_NOISE and len(tok) >= 4
    ]
    cee_geo_str = ""
    if cee_pairs:
        token, src_url = cee_pairs[0]
        cee_geo_str = token.capitalize()
        if src_url:
            domain = re.sub(r"https?://(?:www\.)?([^/]+).*", r"\1", src_url)
            cee_geo_str += f" ([{domain}]({src_url}))"
        else:
            cee_geo_str += " (osint)"
    elif not _is_unknown(founder_nationality) and "republic" not in founder_nationality.lower():
        # Use raw nationality hint only if it's not just "republic (osint)"
        first_hint = founder_nationality.split(";")[0].strip()
        if first_hint and first_hint.lower() not in _CEE_TOKEN_NOISE:
            cee_geo_str = f"{first_hint}"

    geo_evidence = geo if not _is_unknown(geo) else ""
    if cee_geo_str:
        # cee_geo_str already contains its own source annotation — don't double-tag
        geo_evidence = (f"{geo_evidence} · {cee_geo_str}" if geo_evidence else cee_geo_str)

    geo_node = _fit_label(geo_fit, uncertain_label="UNCERTAIN") + (f"  ({geo_evidence})" if geo_evidence else "")
    stage_node = _fit_label(stage_fit, uncertain_label="UNCERTAIN") + (f"  ({stage})" if not _is_unknown(stage) else "")
    sector_node = _fit_label(sector_fit) + (f"  ({sector_snap})" if not _is_unknown(sector_snap) else "")

    # Founders in fit tree: name only (no LinkedIn clutter here)
    founder_node = _fit_label(founder_fit, uncertain_label="UNKNOWN") + (f"  ({founders_summary})" if not _is_unknown(founders_summary) else "")

    fit_tree_txt = "\n".join([
        "**INVESTMENT FIT**",
        "",
        f"├── **Ticket size**      → UNKNOWN  (no funding data on website)",
        f"├── **Geography**        → {geo_node}",
        f"├── **Stage**            → {stage_node}",
        f"├── **Sector**           → {sector_node}",
        f"├── **Founder quality**  → {founder_node}",
        f"└── **Product (10x)**    → {_fit_label(product_fit, uncertain_label='UNCERTAIN')}  ({_clip(one_liner, 120) if not _is_unknown(one_liner) else 'unknown'})",
        "",
        f"→ **RESULT: {overall_fit_label}**",
    ])

    # ── Layer 2: Company Snapshot (pure facts + source on every line) ─────────
    # Founders: "Name ([LinkedIn](url))  (website)"
    def _format_founders_with_linkedin(raw: str) -> str:
        if _is_unknown(raw):
            return "unknown"
        company_low = company_name_str.lower().strip()
        parts = []
        for chunk in raw.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            name = chunk.split(" — ")[0].strip() if " — " in chunk else chunk
            # Skip if name IS the company name (OSINT false positive)
            if not name or name.lower() == company_low:
                continue
            li = _linkedin_search_url(name, company_name_str)
            parts.append(f"{name} ([LinkedIn]({li}))")
        return ", ".join(parts) if parts else "unknown"

    founders_display = _format_founders_with_linkedin(founders_summary)
    founders_src = "(website)" if not _is_unknown(founders_summary) else ""

    geo_signals_display = geo_evidence if geo_evidence else na
    # Source tag: cee_geo_str already carries its own annotation inline
    geo_signals_src = "(pipeline)" if (not cee_geo_str and not _is_unknown(geo)) else ""

    f_round = str(facts_obj.get("funding_round") or "").strip()
    f_amount = str(facts_obj.get("funding_amount") or "").strip()
    f_date = str(facts_obj.get("funding_date") or "").strip()
    f_val = str(facts_obj.get("valuation") or "").strip()

    def _funding_line() -> str:
        parts = []
        if not _is_unknown(f_round):
            parts.append(f_round)
        if not _is_unknown(f_amount):
            parts.append(f_amount)
        if not _is_unknown(f_date):
            parts.append(f_date)
        if not _is_unknown(f_val):
            parts.append(f"val. {f_val}")
        return "  ·  ".join(parts) if parts else "unknown"

    funding_display = _funding_line()
    funding_src = "(website)" if funding_display != "unknown" else ""

    stage_with_funding = stage if not _is_unknown(stage) else "unknown"
    if funding_display != "unknown":
        stage_with_funding += f"  ({funding_display})"

    snapshot_txt = "\n".join([
        f"**Company:**           {company_name_str or na}",
        f"**Website:**           [{primary_link}]({primary_link})" if primary_link else f"**Website:**           {na}",
        f"**Founded:**           {founded_year or 'unknown'}  (website)",
        f"**Founders:**          {founders_display}  {founders_src}",
        f"**HQ:**                unknown",
        f"**Geography signals:** {geo_signals_display}  {geo_signals_src}",
        "",
        f"**Sector:**            {sector_snap if not _is_unknown(sector_snap) else 'unknown'}  (pipeline)",
        f"**Stage:**             {stage_with_funding}  (pipeline{' · website' if funding_src else ''})",
        f"**Business model:**    {pricing if not _is_unknown(pricing) else 'unknown'}  {'(website)' if not _is_unknown(pricing) else ''}",
    ])

    # ── Layer 3: Product & Business ───────────────────────────────────────────
    pain_raw = str(facts_obj.get("problem") or facts_obj.get("pain_point") or "").strip()
    value_raw = str(facts_obj.get("use_cases") or facts_obj.get("market_claims") or "").strip()
    if _is_unknown(pain_raw) and not _is_unknown(value_raw):
        pain_display = value_raw
        value_display = na
    else:
        pain_display = pain_raw if not _is_unknown(pain_raw) else na
        value_display = value_raw if not _is_unknown(value_raw) else na

    how_it_works = str(facts_obj.get("product_description") or facts_obj.get("what_they_do") or "").strip()
    integ_raw = str(facts_obj.get("integrations") or "").strip()
    sec_raw = str(facts_obj.get("security_compliance_signals") or "").strip()

    signal_lines: list[str] = []
    if not _is_unknown(traction_f):
        signal_lines.append(f"{traction_f}  (website)")
    if not _is_unknown(cust2):
        signal_lines.append(f"Logos / proof: {cust2}  (website)")
    if not _is_unknown(integ_raw):
        signal_lines.append(f"Integrations: {integ_raw}  (website)")
    if not _is_unknown(sec_raw):
        signal_lines.append(f"Compliance: {sec_raw}  (website)")

    product_txt = "\n".join([
        f"**One-liner:**  {_clip(one_liner, 200) if not _is_unknown(one_liner) else na}  {'(website)' if not _is_unknown(one_liner) else ''}",
        "",
        f"**ICP:**  {cust if not _is_unknown(cust) else na}  {'(website)' if not _is_unknown(cust) else ''}",
        "",
        "**Value:**",
        f"- {_clip(pain_display, 260)}  (website)" if pain_display != na else f"- {na}",
        *(([f"- {_clip(value_display, 260)}  (website)"] if value_display != na else [])),
        "",
        f"**How it works:**  {_clip(how_it_works, 400) if not _is_unknown(how_it_works) else na}  {'(website)' if not _is_unknown(how_it_works) else ''}",
        "",
        "**Signals:**",
        *(([f"- {_clip(s, 300)}" for s in signal_lines[:4]]) if signal_lines else [f"- {na}"]),
    ])

    # ── Layer 4: Upside ───────────────────────────────────────────────────────
    upside_lines = positives_lines[:4]
    upside_txt = "\n".join(f"- {l}  (website)" for l in upside_lines) if upside_lines else f"- {na}"

    # ── Layer 5: Risks ────────────────────────────────────────────────────────
    risk_output = risks_lines[:4]
    if why_blocked and why_blocked != na and not any(why_blocked.lower()[:30] in r.lower() for r in risk_output):
        risk_output.append(why_blocked)
    risks_txt_final = "\n".join(f"- {l}  (website)" for l in risk_output[:5]) if risk_output else f"- {na}"

    # ── Layer 6: Open Questions ───────────────────────────────────────────────
    questions_txt = "\n".join(f"- {q}" for q in followups[:6]) if followups else f"- {na}"

    # ── Layer 7: Evidence ─────────────────────────────────────────────────────
    used_tavily = "osint" in inferred_blob.lower() or "tavily" in market_txt.lower()
    evidence_txt = "\n".join([
        f"- **Website:**    [{primary_link}]({primary_link})" if primary_link else f"- **Website:**    {na}",
        f"- **External:**   {'Tavily/OSINT · ' + str(len(cee_pairs)) + ' CEE hit(s)' if used_tavily else 'not used'}",
        f"- **Extraction:** Gate1 + Gate2 website prompts",
        f"- **HQ hint:**    {registration_geo or 'not available'}",
        f"- **Store:**      pipeline.db / main.py routing",
    ])

    # ── Notion blocks ─────────────────────────────────────────────────────────
    def _h2(title: str) -> dict:
        return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _as_rich_text(title)}}

    def _p(text: str) -> dict:
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _as_rich_text(text)}}

    return [
        _h2("⚡ 0. Decision"),
        _p(decision_txt),
        _h2("🧭 1. Investment Fit"),
        _p(fit_tree_txt),
        _h2("🧾 2. Snapshot"),
        _p(snapshot_txt),
        _h2("🧠 3. Product & Business"),
        _p(product_txt),
        _h2("📈 4. Upside"),
        _p(upside_txt),
        _h2("⚠️ 5. Risks"),
        _p(risks_txt_final),
        _h2("❓ 6. Open Questions"),
        _p(questions_txt),
        _h2("🧾 7. Evidence"),
        _p(evidence_txt),
    ]


def _ensure_page_summary_blocks(
    client: httpx.Client,
    *,
    api_key: str,
    page_id: str | None,
    row: dict[str, Any],
) -> None:
    # Do not overwrite partner notes (Raw Notes), but keep other sections up to date.
    if not page_id:
        return
    children: list[dict[str, Any]] = []
    try:
        r = client.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
            headers=_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        children = (r.json() or {}).get("results") or []
    except Exception:
        return
    desired = _build_deal_summary_blocks(row)
    desired_headings_order = [
        "⚡ 0. Decision",
        "🧭 1. Investment Fit",
        "🧾 2. Snapshot",
        "🧠 3. Product & Business",
        "📈 4. Upside",
        "⚠️ 5. Risks",
        "❓ 6. Open Questions",
        "🧾 7. Evidence",
    ]

    # Build index: heading text -> (heading_id, first paragraph until next heading_2)
    heading_map: dict[str, tuple[str, str | None]] = {}
    existing_headings_in_order: list[str] = []
    for i, b in enumerate(children):
        if (b.get("type") or "") != "heading_2":
            continue
        hid = b.get("id")
        if not hid:
            continue
        htxt = _plain_text_of_rich_text((b.get("heading_2") or {}).get("rich_text")).strip()
        if not htxt:
            continue
        existing_headings_in_order.append(htxt)
        pid: str | None = None
        j = i + 1
        while j < len(children):
            nb = children[j] or {}
            nbt = (nb.get("type") or "")
            if nbt == "heading_2":
                break
            if nbt == "paragraph":
                pid = nb.get("id")
                if pid:
                    break
            j += 1
        heading_map[htxt] = (hid, pid)

    # If managed section order differs (or duplicates exist), rebuild managed blocks in correct order.
    managed_set = set(desired_headings_order)
    managed_in_page = [h for h in existing_headings_in_order if h in managed_set]
    order_is_correct = managed_in_page == [h for h in desired_headings_order if h in managed_in_page]
    has_duplicates = len(managed_in_page) != len(set(managed_in_page))
    if (not order_is_correct) or has_duplicates:
        # Archive all existing managed blocks + legacy stray blocks.
        managed_first_idx: int | None = None
        for idx, b in enumerate(children):
            if (b.get("type") or "") != "heading_2":
                continue
            htxt = _plain_text_of_rich_text((b.get("heading_2") or {}).get("rich_text")).strip()
            if htxt in managed_set:
                managed_first_idx = idx
                break

        sigs = (
            "One-liner:", "Fund Fit:", "Missing:", "Score:", "Saturation:", "Founder call",
            "product_clarity", "target_customer", "problem_clarity",
            "founder_or_team", "distribution_signal", "urgency_and",
            "• ",
        )

        for idx, b in enumerate(children):
            bid = b.get("id")
            if not bid:
                continue
            bt = (b.get("type") or "")
            # Anything before first managed heading is legacy garbage (old syncs).
            if managed_first_idx is not None and idx < managed_first_idx:
                try:
                    client.patch(
                        f"https://api.notion.com/v1/blocks/{bid}",
                        headers=_headers(api_key),
                        json={"archived": True},
                        timeout=30,
                    ).raise_for_status()
                except Exception:
                    pass
                continue
            if bt == "heading_2":
                htxt = _plain_text_of_rich_text((b.get("heading_2") or {}).get("rich_text")).strip()
                if htxt in managed_set:
                    try:
                        client.patch(
                            f"https://api.notion.com/v1/blocks/{bid}",
                            headers=_headers(api_key),
                            json={"archived": True},
                            timeout=30,
                        ).raise_for_status()
                    except Exception:
                        pass
            elif bt in ("paragraph", "bulleted_list_item", "numbered_list_item", "quote"):
                txt = _block_plain_text(b)
                if any(sig in txt for sig in sigs):
                    try:
                        client.patch(
                            f"https://api.notion.com/v1/blocks/{bid}",
                            headers=_headers(api_key),
                            json={"archived": True},
                            timeout=30,
                        ).raise_for_status()
                    except Exception:
                        pass

        # Rebuild desired sections in correct order.
        rebuilt = _build_deal_summary_blocks(row)
        try:
            client.patch(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=_headers(api_key),
                json={"children": rebuilt},
                timeout=30,
            ).raise_for_status()
        except Exception:
            pass
        return

    # Desired sections: keep original rich_text chunks — never re-derive via
    # _plain_text_of_rich_text, which only returns the first chunk and loses bold/links.
    sections: list[tuple[str, list[dict], bool]] = []
    i = 0
    while i < len(desired):
        hb = desired[i]
        pb = desired[i + 1] if i + 1 < len(desired) else None
        if (hb.get("type") or "") != "heading_2" or not pb or (pb.get("type") or "") != "paragraph":
            i += 1
            continue
        heading = _plain_text_of_rich_text((hb.get("heading_2") or {}).get("rich_text")).strip()
        rich_chunks = list((pb.get("paragraph") or {}).get("rich_text") or [])
        sections.append((heading, rich_chunks, True))
        i += 2

    to_append: list[dict[str, Any]] = []
    for heading, rich_chunks, allow_update in sections:
        if not heading:
            continue
        existing = heading_map.get(heading)
        if existing:
            _hid, pid = existing
            if pid and allow_update:
                # Update paragraph in-place with full rich_text.
                try:
                    client.patch(
                        f"https://api.notion.com/v1/blocks/{pid}",
                        headers=_headers(api_key),
                        json={"paragraph": {"rich_text": rich_chunks}},
                        timeout=30,
                    ).raise_for_status()
                except Exception:
                    pass
            continue
        # Missing section -> append heading + paragraph with original rich_text.
        to_append.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": _as_rich_text(heading)}})
        to_append.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_chunks}})

    if to_append:
        try:
            client.patch(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=_headers(api_key),
                json={"children": to_append},
                timeout=30,
            ).raise_for_status()
        except Exception:
            pass


def _as_rich_text(text: str) -> list[dict[str, Any]]:
    """Parse simple inline markdown into Notion rich_text chunks.
    Supports: **bold text**, [label](url). Newlines preserved.
    """
    s = (text or "").strip()
    if not s:
        return []
    chunks: list[dict[str, Any]] = []
    pattern = re.compile(r'\*\*([^*\n]+)\*\*|\[([^\]]+)\]\(([^)]*)\)')
    last = 0
    for m in pattern.finditer(s):
        if m.start() > last:
            plain = s[last:m.start()]
            for i in range(0, len(plain), 1900):
                chunks.append({"type": "text", "text": {"content": plain[i:i+1900]}})
        if m.group(1) is not None:
            chunks.append({
                "type": "text",
                "text": {"content": m.group(1)[:500]},
                "annotations": {"bold": True},
            })
        else:
            chunks.append({
                "type": "text",
                "text": {"content": m.group(2)[:300], "link": {"url": m.group(3)[:500]}},
            })
        last = m.end()
    if last < len(s):
        tail = s[last:]
        for i in range(0, len(tail), 1900):
            chunks.append({"type": "text", "text": {"content": tail[i:i+1900]}})
    return chunks or [{"type": "text", "text": {"content": s[:1900]}}]


def _prop_type(db_props: dict[str, Any], name: str) -> str:
    meta = (db_props or {}).get(name) or {}
    t = meta.get("type")
    return str(t) if isinstance(t, str) else ""


def _title_property_name(db_props: dict[str, Any]) -> str | None:
    for name, meta in (db_props or {}).items():
        if isinstance(meta, dict) and meta.get("type") == "title":
            return name
    return None


def _notion_props_for_row(
    row: dict[str, Any],
    db_props: dict[str, Any],
    *,
    score_first_title: bool = False,
    compact_mode: bool = False,
) -> dict[str, Any]:
    """
    Write only properties that already exist in the Notion DB.
    Expected names (create only what you want): Company, Message ID, Status, Verdict, Score, Recommendation,
    Sector, Geography, Email, Subject, Created At, Updated At, Rejection Reason.
    """
    out: dict[str, Any] = {}

    title_prop = _title_property_name(db_props)
    if title_prop:
        out[title_prop] = {"title": _as_rich_text(_title(row, score_first=score_first_title))}
    if "Message ID" in db_props:
        out["Message ID"] = {"rich_text": _as_rich_text(str(row.get("message_id") or ""))}
    if "Status" in db_props:
        status_val = _status_select(row)
        t = _prop_type(db_props, "Status")
        if t == "select":
            out["Status"] = {"select": {"name": status_val}}
        else:
            out["Status"] = {"rich_text": _as_rich_text(status_val)}
    if "Verdict" in db_props:
        v = str(row.get("gate1_verdict") or "")
        t = _prop_type(db_props, "Verdict")
        if t == "select":
            out["Verdict"] = {"select": {"name": v or "UNKNOWN"}}
        else:
            out["Verdict"] = {"rich_text": _as_rich_text(v)}
    if "Score" in db_props:
        score = row.get("deck_evidence_score")
        if score is None:
            score = row.get("gate2_overall_score")
        t = _prop_type(db_props, "Score")
        if t == "number":
            out["Score"] = {"number": float(score) if score is not None else None}
        else:
            out["Score"] = {"rich_text": _as_rich_text("" if score is None else f"{float(score):.2f}")}
    if not compact_mode and "Recommendation" in db_props:
        out["Recommendation"] = {"rich_text": _as_rich_text(str(row.get("final_action") or row.get("gate2_recommendation") or ""))}
    # New staged fields
    is_website = bool(str(row.get("source_url") or "").strip())
    for pname, value in (
        ("Fund Fit Decision", str(row.get("fund_fit_decision") or "")),
        ("Deck Evidence Decision", str(row.get("deck_evidence_decision") or "")),
        ("Generic VC Interest", str(row.get("generic_vc_interest") or "")),
        ("Final Action", str(row.get("final_action") or "")),
        ("Screening Depth", str(row.get("screening_depth") or "")),
        ("Auth Risk", str(row.get("auth_risk") or "")),
    ):
        if is_website and pname == "Auth Risk":
            continue
        if pname in db_props:
            t = _prop_type(db_props, pname)
            if t == "select":
                out[pname] = {"select": {"name": value or "UNKNOWN"}}
            else:
                out[pname] = {"rich_text": _as_rich_text(value)}
    for pname, value in (
        ("Deck Evidence Score", row.get("deck_evidence_score")),
        ("External Opportunity Score", row.get("external_opportunity_score")),
        ("Fund Fit Score", row.get("fund_fit_score")),
    ):
        if pname in db_props:
            t = _prop_type(db_props, pname)
            if t == "number":
                out[pname] = {"number": (float(value) if value is not None else None)}
            else:
                out[pname] = {"rich_text": _as_rich_text("" if value is None else f"{float(value):.2f}")}
    for pname, value in (
        ("Debug Override Used", bool(row.get("debug_override_used"))),
        ("Test Case", bool(row.get("test_case"))),
    ):
        if pname in db_props:
            t = _prop_type(db_props, pname)
            if t == "checkbox":
                out[pname] = {"checkbox": value}
            else:
                out[pname] = {"rich_text": _as_rich_text("yes" if value else "no")}
    if "Sector" in db_props:
        sector_val = str(row.get("gate1_detected_sector") or "")
        t = _prop_type(db_props, "Sector")
        if t == "select":
            out["Sector"] = {"select": {"name": sector_val or "Unknown"}}
        else:
            out["Sector"] = {"rich_text": _as_rich_text(sector_val)}
    if not compact_mode and "Geography" in db_props:
        out["Geography"] = {"rich_text": _as_rich_text(str(row.get("gate1_detected_geography") or ""))}
    if not compact_mode and "Email" in db_props:
        out["Email"] = {"email": (str(row.get("sender_email") or "").strip() or None)}
    if "Sender" in db_props:
        out["Sender"] = {"rich_text": _as_rich_text(str(row.get("sender_name") or ""))}
    if not compact_mode and "Mail Subject" in db_props:
        out["Mail Subject"] = {"rich_text": _as_rich_text(str(row.get("subject") or ""))}
    if "Received At" in db_props:
        d = _to_notion_date(row.get("created_at"))
        t = _prop_type(db_props, "Received At")
        if t == "date":
            out["Received At"] = {"date": {"start": d} if d else None}
        else:
            out["Received At"] = {"rich_text": _as_rich_text(d or "")}
    if not compact_mode and "PDF Filename" in db_props:
        out["PDF Filename"] = {"rich_text": _as_rich_text(str(row.get("pdf_filename") or ""))}
    if not compact_mode and "Has PDF" in db_props:
        has_pdf = bool(row.get("has_pdf"))
        t = _prop_type(db_props, "Has PDF")
        if t == "checkbox":
            out["Has PDF"] = {"checkbox": has_pdf}
        else:
            out["Has PDF"] = {"rich_text": _as_rich_text("yes" if has_pdf else "no")}
    if "Gmail Link" in db_props:
        url = _gmail_message_url(str(row.get("message_id") or ""))
        t = _prop_type(db_props, "Gmail Link")
        if t == "url":
            out["Gmail Link"] = {"url": (url or None)}
        else:
            out["Gmail Link"] = {"rich_text": _as_rich_text(url)}
    if "Source" in db_props:
        src = _source_label(row)
        t = _prop_type(db_props, "Source")
        if t == "select":
            out["Source"] = {"select": {"name": src}}
        else:
            out["Source"] = {"rich_text": _as_rich_text(src)}
    if not compact_mode and "Subject" in db_props:
        out["Subject"] = {"rich_text": _as_rich_text(str(row.get("subject") or ""))}
    if not compact_mode and "Created At" in db_props:
        created = str(row.get("created_at") or "").strip()
        out["Created At"] = {"rich_text": _as_rich_text(created)}
    if not compact_mode and "Updated At" in db_props:
        updated = str(row.get("updated_at") or "").strip()
        out["Updated At"] = {"rich_text": _as_rich_text(updated)}
    if not compact_mode and "Rejection Reason" in db_props:
        out["Rejection Reason"] = {"rich_text": _as_rich_text(str(row.get("gate1_rejection_reason") or ""))}
    founded_year, founders_summary, one_liner = _profile_fields(row)
    if "Founded Year" in db_props:
        t = _prop_type(db_props, "Founded Year")
        if t == "number":
            yr_num = None
            if founded_year.isdigit() and len(founded_year) == 4:
                yr_num = float(founded_year)
            out["Founded Year"] = {"number": yr_num}
        else:
            out["Founded Year"] = {"rich_text": _as_rich_text(founded_year)}
    if "Founders" in db_props:
        out["Founders"] = {"rich_text": _as_rich_text(founders_summary)}
    if "Product One-liner" in db_props:
        out["Product One-liner"] = {"rich_text": _as_rich_text(one_liner)}
    return out


def _find_page_by_message_id(
    client: httpx.Client,
    *,
    api_key: str,
    database_id: str,
    message_id: str,
) -> str | None:
    payload = {
        "filter": {"property": "Message ID", "rich_text": {"equals": message_id}},
        "page_size": 1,
    }
    r = client.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    if not results:
        return None
    return results[0].get("id")


def _find_page_by_title_and_date(
    client: httpx.Client,
    *,
    api_key: str,
    database_id: str,
    db_props: dict[str, Any],
    row: dict[str, Any],
) -> str | None:
    title_prop = _title_property_name(db_props)
    if not title_prop:
        return None
    title_val = _title(row).strip()
    if not title_val:
        return None
    payload: dict[str, Any] = {
        "filter": {
            "property": title_prop,
            "title": {"equals": title_val},
        },
        "page_size": 10,
    }
    r = client.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    if not results:
        return None
    recv = _to_notion_date(row.get("created_at"))
    if not recv:
        return results[0].get("id")
    # If Received At exists, disambiguate duplicates by date.
    if "Received At" in db_props:
        for p in results:
            props = p.get("properties") or {}
            d = ((props.get("Received At") or {}).get("date") or {}).get("start")
            if d == recv:
                return p.get("id")
    return results[0].get("id")


def _discover_database_hints(client: httpx.Client, *, api_key: str) -> str:
    """Return a short list of database IDs visible to this integration."""
    try:
        r = client.post(
            "https://api.notion.com/v1/search",
            headers=_headers(api_key),
            json={"filter": {"property": "object", "value": "database"}, "page_size": 10},
            timeout=30,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("results") or []
        hints: list[str] = []
        for db in rows[:5]:
            db_id = str(db.get("id") or "")
            title_blocks = (db.get("title") or [])
            title = ""
            if title_blocks:
                title = str(title_blocks[0].get("plain_text") or "")
            hints.append(f"{title or '(untitled)'}: {db_id}")
        return "; ".join(hints) if hints else "no databases visible via /search"
    except Exception as e:
        return f"unable to fetch database hints ({e})"


def _ensure_notion_schema(
    client: httpx.Client,
    *,
    api_key: str,
    database_id: str,
    db_props: dict[str, Any],
    compact_mode: bool = False,
) -> dict[str, Any]:
    """
    Add a minimal useful schema for pipeline ops if properties are missing.
    Only adds missing properties; does not alter existing types.
    """
    if compact_mode:
        wanted: dict[str, dict[str, Any]] = {
            "Score": {"number": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Reject", "color": "red"},
                        {"name": "Review", "color": "yellow"},
                        {"name": "Pass", "color": "green"},
                        {"name": "TEST_CASE", "color": "gray"},
                    ]
                }
            },
            "Sector": {"select": {"options": []}},
            "Received At": {"date": {}},
        }
    else:
        wanted = {
            "Status": {
                "select": {
                    "options": [
                        {"name": "Reject", "color": "red"},
                        {"name": "Review", "color": "yellow"},
                        {"name": "Pass", "color": "green"},
                        {"name": "TEST_CASE", "color": "gray"},
                    ]
                }
            },
            "Source": {"rich_text": {}},
            "Score": {"number": {}},
            "Recommendation": {"rich_text": {}},
            "Verdict": {"rich_text": {}},
            "Sector": {"select": {"options": []}},
            "Geography": {"rich_text": {}},
            "Founded Year": {"rich_text": {}},
            "Founders": {"rich_text": {}},
            "Product One-liner": {"rich_text": {}},
            "Email": {"email": {}},
            "Sender": {"rich_text": {}},
            "Mail Subject": {"rich_text": {}},
            "Received At": {"date": {}},
            "PDF Filename": {"rich_text": {}},
            "Has PDF": {"checkbox": {}},
            "Gmail Link": {"url": {}},
            "Subject": {"rich_text": {}},
            "Updated At": {"rich_text": {}},
            "Created At": {"rich_text": {}},
            "Rejection Reason": {"rich_text": {}},
            "Fund Fit Decision": {"rich_text": {}},
            "Deck Evidence Decision": {"rich_text": {}},
            "Generic VC Interest": {"rich_text": {}},
            "Final Action": {"rich_text": {}},
            "Screening Depth": {"rich_text": {}},
            "Auth Risk": {"rich_text": {}},
            "Deck Evidence Score": {"number": {}},
            "External Opportunity Score": {"number": {}},
            "Fund Fit Score": {"number": {}},
            "Debug Override Used": {"checkbox": {}},
            "Test Case": {"checkbox": {}},
        }
    to_add: dict[str, Any] = {}
    for name, conf in wanted.items():
        if name not in db_props:
            to_add[name] = conf
    if not to_add:
        return db_props

    rr = client.patch(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=_headers(api_key),
        json={"properties": to_add},
        timeout=30,
    )
    try:
        rr.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            body = rr.json()
            detail = str(body.get("message") or body)[:400]
        except Exception:
            detail = (rr.text or "")[:400]
        raise RuntimeError(f"Failed to extend Notion schema: {detail}") from e
    return (rr.json() or {}).get("properties") or db_props


def _prune_notion_schema(
    client: httpx.Client,
    *,
    api_key: str,
    database_id: str,
    db_props: dict[str, Any],
    compact_mode: bool = True,
) -> dict[str, Any]:
    """
    Remove extra properties from Notion database to keep a lean operational table.
    Keeps only compact operating columns.
    """
    title_prop = _title_property_name(db_props) or "Name"
    if compact_mode:
        keep = {
            title_prop,
            "Score",
            "Status",
            "Sector",
            "Received At",
        }
    else:
        keep = {title_prop}

    to_delete: dict[str, Any] = {}
    for name in (db_props or {}).keys():
        if name not in keep:
            to_delete[name] = None
    if not to_delete:
        return db_props

    rr = client.patch(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=_headers(api_key),
        json={"properties": to_delete},
        timeout=30,
    )
    try:
        rr.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            body = rr.json()
            detail = str(body.get("message") or body)[:400]
        except Exception:
            detail = (rr.text or "")[:400]
        raise RuntimeError(f"Failed to prune Notion columns: {detail}") from e
    return (rr.json() or {}).get("properties") or db_props


def _archive_pages_with_message_prefix(
    client: httpx.Client,
    *,
    api_key: str,
    database_id: str,
    prefix: str,
) -> int:
    """Archive rows whose Message ID starts with a prefix (e.g. test_)."""
    archived = 0
    next_cursor: str | None = None
    while True:
        payload: dict[str, Any] = {
            "filter": {"property": "Message ID", "rich_text": {"starts_with": prefix}},
            "page_size": 100,
        }
        if next_cursor:
            payload["start_cursor"] = next_cursor
        r = client.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=_headers(api_key),
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        body = r.json() or {}
        results = body.get("results") or []
        for page in results:
            pid = page.get("id")
            if not pid:
                continue
            rr = client.patch(
                f"https://api.notion.com/v1/pages/{pid}",
                headers=_headers(api_key),
                json={"archived": True},
                timeout=30,
            )
            rr.raise_for_status()
            archived += 1
        if not body.get("has_more"):
            break
        next_cursor = body.get("next_cursor")
    return archived


def sync_pipeline_to_notion(
    days: int = 30,
    *,
    prune_test_rows: bool = False,
    ensure_schema: bool = False,
    prune_columns: bool = False,
) -> SyncStats:
    api_key = os.getenv("NOTION_API_KEY", "").strip()
    db_id_raw = os.getenv("NOTION_DATABASE_ID", "").strip()
    db_id = _normalize_database_id(db_id_raw)
    if not api_key or not db_id:
        raise RuntimeError("Missing NOTION_API_KEY or NOTION_DATABASE_ID in environment.")

    rows = get_deals_for_notion(days=days)
    include_tests = os.getenv("NOTION_INCLUDE_TEST_DEALS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    compact_mode = os.getenv("NOTION_COMPACT_MODE", "1").strip().lower() in ("1", "true", "yes", "on")
    score_first_title = os.getenv("NOTION_TITLE_SCORE_PREFIX", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not include_tests:
        rows = [r for r in rows if not str(r.get("message_id") or "").lower().startswith("test_")]
    stats = SyncStats(scanned=len(rows))

    with httpx.Client() as client:
        schema_resp = client.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers=_headers(api_key),
            timeout=30,
        )
        try:
            schema_resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                body = schema_resp.json()
                detail = str(body.get("message") or body)[:300]
            except Exception:
                detail = (schema_resp.text or "")[:300]
            hints = _discover_database_hints(client, api_key=api_key)
            raise RuntimeError(
                "Notion lookup failed. Use DATABASE id (not Data Source id), and ensure DB is shared with integration. "
                f"normalized_id={db_id}. notion_error={detail}. visible_databases={hints}"
            ) from e
        db_props = (schema_resp.json() or {}).get("properties") or {}
        if ensure_schema:
            db_props = _ensure_notion_schema(
                client,
                api_key=api_key,
                database_id=db_id,
                db_props=db_props,
                compact_mode=compact_mode,
            )
        if prune_columns:
            db_props = _prune_notion_schema(
                client,
                api_key=api_key,
                database_id=db_id,
                db_props=db_props,
                compact_mode=compact_mode,
            )
        if prune_test_rows:
            _archive_pages_with_message_prefix(
                client,
                api_key=api_key,
                database_id=db_id,
                prefix="test_",
            )

        for row in rows:
            props = _notion_props_for_row(
                row,
                db_props,
                score_first_title=score_first_title,
                compact_mode=compact_mode,
            )
            if not props:
                stats.skipped += 1
                continue

            msg_id = str(row.get("message_id") or "").strip()
            page_id = None
            if "Message ID" in db_props and msg_id:
                page_id = _find_page_by_message_id(
                    client,
                    api_key=api_key,
                    database_id=db_id,
                    message_id=msg_id,
                )
            if not page_id:
                page_id = _find_page_by_title_and_date(
                    client,
                    api_key=api_key,
                    database_id=db_id,
                    db_props=db_props,
                    row=row,
                )
            pid: str | None = page_id
            if page_id:
                rr = client.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=_headers(api_key),
                    json={"properties": props},
                    timeout=30,
                )
                try:
                    rr.raise_for_status()
                except httpx.HTTPStatusError as e:
                    detail = ""
                    try:
                        body = rr.json()
                        detail = str(body.get("message") or body)[:400]
                    except Exception:
                        detail = (rr.text or "")[:400]
                    raise RuntimeError(
                        f"Notion update failed for key={msg_id or _title(row)}: {detail}"
                    ) from e
                stats.updated += 1
            else:
                rr = client.post(
                    "https://api.notion.com/v1/pages",
                    headers=_headers(api_key),
                    json={
                        "parent": {"database_id": db_id},
                        "properties": props,
                    },
                    timeout=30,
                )
                try:
                    rr.raise_for_status()
                except httpx.HTTPStatusError as e:
                    detail = ""
                    try:
                        body = rr.json()
                        detail = str(body.get("message") or body)[:400]
                    except Exception:
                        detail = (rr.text or "")[:400]
                    raise RuntimeError(
                        f"Notion create failed for key={msg_id or _title(row)}: {detail}"
                    ) from e
                stats.created += 1
                pid = (rr.json() or {}).get("id")

            _ensure_page_summary_blocks(
                client,
                api_key=api_key,
                page_id=pid,
                row=row,
            )

            page_snapshot_on = os.getenv("NOTION_PAGE_SNAPSHOT", "1").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            snapshot = str(row.get("gate2_snapshot_md") or "").strip()
            if page_snapshot_on and snapshot:
                _upsert_page_snapshot(
                    client,
                    api_key=api_key,
                    page_id=pid,
                    message_id=msg_id or _title(row),
                    snapshot_text=snapshot,
                )

    return stats


def sync_one_deal_to_notion(
    message_id: str,
    *,
    ensure_schema: bool = False,
    prune_columns: bool = False,
) -> SyncStats:
    """
    Upsert exactly one deal row to Notion.
    Useful for auto-sync right after processing an email, without scanning N days.
    """
    api_key = os.getenv("NOTION_API_KEY", "").strip()
    db_id_raw = os.getenv("NOTION_DATABASE_ID", "").strip()
    db_id = _normalize_database_id(db_id_raw)
    if not api_key or not db_id:
        raise RuntimeError("Missing NOTION_API_KEY or NOTION_DATABASE_ID in environment.")

    row = get_deal_for_notion(message_id)
    if not row:
        return SyncStats(scanned=0, skipped=1)

    compact_mode = os.getenv("NOTION_COMPACT_MODE", "1").strip().lower() in ("1", "true", "yes", "on")
    score_first_title = os.getenv("NOTION_TITLE_SCORE_PREFIX", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    stats = SyncStats(scanned=1)

    with httpx.Client() as client:
        schema_resp = client.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers=_headers(api_key),
            timeout=30,
        )
        schema_resp.raise_for_status()
        db_props = (schema_resp.json() or {}).get("properties") or {}
        if ensure_schema:
            db_props = _ensure_notion_schema(
                client,
                api_key=api_key,
                database_id=db_id,
                db_props=db_props,
                compact_mode=compact_mode,
            )
        if prune_columns:
            db_props = _prune_notion_schema(
                client,
                api_key=api_key,
                database_id=db_id,
                db_props=db_props,
                compact_mode=compact_mode,
            )

        props = _notion_props_for_row(
            row,
            db_props,
            score_first_title=score_first_title,
            compact_mode=compact_mode,
        )
        if not props:
            stats.skipped += 1
            return stats

        msg_id = str(row.get("message_id") or "").strip()
        page_id = None
        if "Message ID" in db_props and msg_id:
            page_id = _find_page_by_message_id(
                client,
                api_key=api_key,
                database_id=db_id,
                message_id=msg_id,
            )
        if not page_id:
            page_id = _find_page_by_title_and_date(
                client,
                api_key=api_key,
                database_id=db_id,
                db_props=db_props,
                row=row,
            )
        pid: str | None = page_id
        if page_id:
            rr = client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=_headers(api_key),
                json={"properties": props},
                timeout=30,
            )
            rr.raise_for_status()
            stats.updated += 1
        else:
            rr = client.post(
                "https://api.notion.com/v1/pages",
                headers=_headers(api_key),
                json={"parent": {"database_id": db_id}, "properties": props},
                timeout=30,
            )
            rr.raise_for_status()
            stats.created += 1
            pid = (rr.json() or {}).get("id")

        _ensure_page_summary_blocks(
            client,
            api_key=api_key,
            page_id=pid,
            row=row,
        )

        page_snapshot_on = os.getenv("NOTION_PAGE_SNAPSHOT", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        snapshot = str(row.get("gate2_snapshot_md") or "").strip()
        if page_snapshot_on and snapshot:
            _upsert_page_snapshot(
                client,
                api_key=api_key,
                page_id=pid,
                message_id=msg_id or _title(row),
                snapshot_text=snapshot,
            )
    return stats


def _plain_text_of_rich_text(rt: Any) -> str:
    if not rt:
        return ""
    if isinstance(rt, list) and rt:
        first = rt[0] or {}
        return str(first.get("plain_text") or first.get("text", {}).get("content") or "")
    return ""


def _block_plain_text(block: dict[str, Any]) -> str:
    if not block or not isinstance(block, dict):
        return ""
    t = str(block.get("type") or "")
    payload = block.get(t) or {}
    if not isinstance(payload, dict):
        return ""
    return _plain_text_of_rich_text(payload.get("rich_text"))


def _upsert_page_snapshot(
    client: httpx.Client,
    *,
    api_key: str,
    page_id: str | None,
    message_id: str,
    snapshot_text: str,
) -> None:
    """
    Maintain a single auto-generated snapshot paragraph block on the page.
    Idempotent: updates existing marker block if found; otherwise appends.
    """
    if not page_id:
        return
    marker = f"VC Snapshot (auto) [message_id={message_id}]"
    text = (snapshot_text or "").strip()
    if not text:
        return

    # Ensure first line is a stable marker.
    lines = text.splitlines()
    if not lines:
        return
    if not lines[0].startswith("VC Snapshot (auto)"):
        lines.insert(0, marker)
    else:
        lines[0] = marker
    text = "\n".join(lines).strip()

    existing_block_id: str | None = None
    try:
        r = client.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
            headers=_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
        for b in results:
            if (b.get("type") or "") != "paragraph":
                continue
            para = b.get("paragraph") or {}
            plain = _plain_text_of_rich_text(para.get("rich_text"))
            if plain.startswith("VC Snapshot (auto) [message_id="):
                if f"[message_id={message_id}]" in plain:
                    existing_block_id = b.get("id")
                    break
                existing_block_id = existing_block_id or b.get("id")
    except Exception:
        existing_block_id = None

    content = text[:1900]
    rich_text = [{"type": "text", "text": {"content": content}}]
    if existing_block_id:
        client.patch(
            f"https://api.notion.com/v1/blocks/{existing_block_id}",
            headers=_headers(api_key),
            json={"paragraph": {"rich_text": rich_text}},
            timeout=30,
        )
    else:
        client.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_headers(api_key),
            json={"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text}}]},
            timeout=30,
        )

