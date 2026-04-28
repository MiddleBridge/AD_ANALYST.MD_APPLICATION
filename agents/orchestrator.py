from __future__ import annotations

import os
from config.prompts import (
    APPROVAL_EMAIL_TEMPLATE,
    REJECTION_EMAIL_TEMPLATE,
    REJECTION_SPECIFIC,
)
from storage.models import EmailData, Gate2Result, HITLDecision
from tools.gmail_client import GmailClient

REVIEWER_NAME = os.getenv("REVIEWER_NAME", "Adrian")
CALENDLY_LINK = os.getenv("CALENDLY_LINK", "")


def _rejection_paragraph(company: str, kind: str) -> str:
    k = (kind or "generic").strip().lower()
    if k not in REJECTION_SPECIFIC:
        k = "generic"
    para = REJECTION_SPECIFIC[k]
    if "{company_name}" in para:
        return para.format(company_name=company)
    return para


def draft_approval_email(
    email: EmailData,
    gate2: Gate2Result,
    decision: HITLDecision,
    gmail: GmailClient,
) -> str:
    founder_first = email.sender_name.split()[0] if email.sender_name else "there"
    company = gate2.company_name or "your company"

    proposed_slots = _format_proposed_slots()

    body = APPROVAL_EMAIL_TEMPLATE.format(
        original_subject=email.subject,
        founder_first_name=founder_first,
        company_name=company,
        proposed_slots=proposed_slots,
        calendly_link=CALENDLY_LINK or "[add Calendly link]",
        reviewer_name=REVIEWER_NAME,
    )

    subject = f"Re: {email.subject}"
    draft_id = gmail.create_draft(
        to=email.sender_email,
        subject=subject,
        body=body,
        thread_id=email.thread_id,
    )
    return draft_id


def draft_rejection_email(
    email: EmailData,
    gate2: Gate2Result | None,
    decision: HITLDecision,
    gmail: GmailClient,
) -> str:
    founder_first = email.sender_name.split()[0] if email.sender_name else "there"
    company = (gate2.company_name if gate2 else None) or "your company"
    specific = _rejection_paragraph(company, getattr(decision, "rejection_kind", "generic"))

    body = REJECTION_EMAIL_TEMPLATE.format(
        original_subject=email.subject,
        founder_first_name=founder_first,
        company_name=company,
        specific_paragraph=specific,
        reviewer_name=REVIEWER_NAME,
    )

    subject = f"Re: {email.subject}"
    draft_id = gmail.create_draft(
        to=email.sender_email,
        subject=subject,
        body=body,
        thread_id=email.thread_id,
    )
    return draft_id


def _format_proposed_slots() -> str:
    from datetime import datetime, timedelta
    base = datetime.now()
    slots = []
    days_added = 0
    while len(slots) < 3:
        days_added += 1
        candidate = base + timedelta(days=days_added)
        if candidate.weekday() < 5:
            slots.append(candidate.strftime("- %A %B %d, 10:00–10:30 AM CET"))
    return "\n".join(slots)
