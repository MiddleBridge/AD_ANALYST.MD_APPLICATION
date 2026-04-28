from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


NOTION_VERSION = "2022-06-28"


@dataclass
class CallSyncResult:
    company: str
    page_id: str | None
    call_appended: bool
    call_skipped_duplicate: bool
    tasks_created: int


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _normalize_id(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.split("?", 1)[0].strip().rstrip("/")
    m = re.search(
        r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        s,
    )
    if not m:
        return s
    token = m.group(1).replace("-", "")
    if len(token) != 32:
        return s
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


def _as_rich_text(text: str) -> list[dict[str, Any]]:
    s = (text or "").strip()
    if not s:
        return []
    return [{"type": "text", "text": {"content": s[:1900]}}]


def _plain_text_of_rich_text(rt: Any) -> str:
    if not rt:
        return ""
    if isinstance(rt, list) and rt:
        first = rt[0] or {}
        return str(first.get("plain_text") or first.get("text", {}).get("content") or "")
    return ""


def _block_text(block: dict[str, Any]) -> str:
    if not isinstance(block, dict):
        return ""
    btype = str(block.get("type") or "")
    payload = block.get(btype) or {}
    if not isinstance(payload, dict):
        return ""
    return _plain_text_of_rich_text(payload.get("rich_text"))


def _title_property_name(db_props: dict[str, Any]) -> str | None:
    for name, meta in (db_props or {}).items():
        if isinstance(meta, dict) and meta.get("type") == "title":
            return name
    return None


def _get_database_props(client: httpx.Client, *, api_key: str, database_id: str) -> dict[str, Any]:
    r = client.get(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=_headers(api_key),
        timeout=30,
    )
    r.raise_for_status()
    return (r.json() or {}).get("properties") or {}


def _find_company_page_id(
    client: httpx.Client,
    *,
    api_key: str,
    database_id: str,
    company: str,
) -> str | None:
    props = _get_database_props(client, api_key=api_key, database_id=database_id)
    title_prop = _title_property_name(props)
    if not title_prop:
        return None
    payload = {
        "filter": {
            "property": title_prop,
            "title": {"equals": company},
        },
        "page_size": 1,
    }
    r = client.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    rows = (r.json() or {}).get("results") or []
    if not rows:
        return None
    return rows[0].get("id")


def _ensure_heading(
    client: httpx.Client,
    *,
    api_key: str,
    page_id: str,
    heading: str,
) -> None:
    r = client.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
        headers=_headers(api_key),
        timeout=30,
    )
    r.raise_for_status()
    children = (r.json() or {}).get("results") or []
    for b in children:
        if (b.get("type") or "") != "heading_2":
            continue
        txt = _plain_text_of_rich_text((b.get("heading_2") or {}).get("rich_text")).strip()
        if txt == heading:
            return
    client.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=_headers(api_key),
        json={
            "children": [
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": _as_rich_text(heading)},
                }
            ]
        },
        timeout=30,
    ).raise_for_status()


def _has_call_marker(
    client: httpx.Client,
    *,
    api_key: str,
    page_id: str,
    call_id: str,
) -> bool:
    if not call_id:
        return False
    marker = f"[call_id={call_id}]"
    r = client.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
        headers=_headers(api_key),
        timeout=30,
    )
    r.raise_for_status()
    children = (r.json() or {}).get("results") or []
    marker = f"[call_id={call_id}]"
    for b in children:
        txt = _block_text(b)
        if marker in txt:
            return True
    return False


def _append_call_note(
    client: httpx.Client,
    *,
    api_key: str,
    page_id: str,
    source: str,
    call_id: str,
    title: str,
    transcript_url: str,
    occurred_at: str,
    attendees: str,
    summary: str,
) -> None:
    marker = f"[call_id={call_id}]" if call_id else "[call_id=none]"
    date_txt = occurred_at or datetime.utcnow().strftime("%Y-%m-%d")
    call_title = title or "Founder call"
    source_label = (source or "source").strip().lower()
    header = f"{date_txt} — {call_title} ({source_label})"
    meta_line = f"Date: {date_txt} | Attendees: {attendees or 'n/a'}"
    summary_line = f"Summary: {summary or 'n/a'}"
    link_line = f"Recording/Transcript: {transcript_url or 'n/a'}"
    marker_line = f"Sync marker: {marker}"
    client.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=_headers(api_key),
        json={
            "children": [
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": _as_rich_text(header)},
                },
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _as_rich_text(meta_line)},
                },
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _as_rich_text(summary_line)},
                },
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _as_rich_text(link_line)},
                },
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _as_rich_text(marker_line)},
                }
            ]
        },
        timeout=30,
    ).raise_for_status()


def _create_tasks(
    client: httpx.Client,
    *,
    api_key: str,
    tasks_database_id: str,
    company: str,
    call_title: str,
    occurred_at: str,
    tasks: list[str],
) -> int:
    if not tasks:
        return 0
    props = _get_database_props(client, api_key=api_key, database_id=tasks_database_id)
    title_prop = _title_property_name(props)
    if not title_prop:
        raise RuntimeError("Tasks database has no title property.")
    created = 0
    for t in tasks:
        text = (t or "").strip()
        if not text:
            continue
        payload_props: dict[str, Any] = {
            title_prop: {"title": _as_rich_text(f"{company}: {text}")},
        }
        if "Company" in props:
            payload_props["Company"] = {"rich_text": _as_rich_text(company)}
        if "Call" in props:
            payload_props["Call"] = {"rich_text": _as_rich_text(call_title or "Founder call")}
        if "Status" in props and (props.get("Status") or {}).get("type") == "select":
            payload_props["Status"] = {"select": {"name": "Open"}}
        if "Due Date" in props and (props.get("Due Date") or {}).get("type") == "date" and occurred_at:
            payload_props["Due Date"] = {"date": {"start": occurred_at[:10]}}
        client.post(
            "https://api.notion.com/v1/pages",
            headers=_headers(api_key),
            json={"parent": {"database_id": tasks_database_id}, "properties": payload_props},
            timeout=30,
        ).raise_for_status()
        created += 1
    return created


def sync_founder_call_oneshot(
    *,
    company: str,
    source: str = "fireflies",
    call_id: str = "",
    title: str = "",
    transcript_url: str = "",
    occurred_at: str = "",
    attendees: str = "",
    summary: str = "",
    tasks: list[str] | None = None,
) -> CallSyncResult:
    api_key = os.getenv("NOTION_API_KEY", "").strip()
    deals_db = _normalize_id(os.getenv("NOTION_DATABASE_ID", ""))
    tasks_db = _normalize_id(os.getenv("NOTION_TASKS_DATABASE_ID", ""))
    if not api_key or not deals_db:
        raise RuntimeError("Missing NOTION_API_KEY or NOTION_DATABASE_ID.")
    tasks = tasks or []
    with httpx.Client() as client:
        page_id = _find_company_page_id(
            client,
            api_key=api_key,
            database_id=deals_db,
            company=company,
        )
        if not page_id:
            raise RuntimeError(f"Company page not found in Notion DB: {company}")
        _ensure_heading(
            client,
            api_key=api_key,
            page_id=page_id,
            heading="Founder Calls",
        )
        duplicate = _has_call_marker(
            client,
            api_key=api_key,
            page_id=page_id,
            call_id=call_id,
        )
        appended = False
        if not duplicate:
            _append_call_note(
                client,
                api_key=api_key,
                page_id=page_id,
                source=source,
                call_id=call_id,
                title=title,
                transcript_url=transcript_url,
                occurred_at=occurred_at,
                attendees=attendees,
                summary=summary,
            )
            appended = True
        tasks_created = 0
        if tasks and tasks_db:
            tasks_created = _create_tasks(
                client,
                api_key=api_key,
                tasks_database_id=tasks_db,
                company=company,
                call_title=title,
                occurred_at=occurred_at,
                tasks=tasks,
            )
    return CallSyncResult(
        company=company,
        page_id=page_id,
        call_appended=appended,
        call_skipped_duplicate=duplicate,
        tasks_created=tasks_created,
    )
