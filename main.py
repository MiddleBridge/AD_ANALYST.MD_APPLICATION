#!/usr/bin/env python3
from __future__ import annotations

"""
Example VC Fund Inbound Screening Agent
Usage:
  python main.py              # continuous polling mode
  python main.py --once       # process all current unread emails, then exit
  python main.py --report     # print weekly pipeline report
  python main.py --sync-notion --days 30  # sync recent deals to Notion DB
  python main.py sync-call --company "ACME" --call-id "ff_123" --source fireflies --title "Founder intro" --url "https://..." --date "2026-04-28" --attendees "A,B" --summary "..." --tasks "send IC memo;book follow-up"
  python main.py --setup      # run Gmail OAuth setup
  python main.py --test FILE  # test with local PDF file (skips Gmail)
  python main.py assess-url https://example.com  # website-only screening (crawl + LLM)
  python main.py --test FILE --force  # delete legacy test id test_<stem> if present
"""
import json
import os
import re
import sys
import time
import hashlib
import argparse
import uuid
import urllib.parse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from rich.console import Console
from agents.fund_decision import (
    Blockers,
    apply_fund_geo_rule,
    build_fund_mandate_fit,
    classify_stage,
    fund_verdict,
    investment_interest_from_scores,
    map_verdict_to_action,
)
from agents.fund_domain import FundGeoAssessment

load_dotenv()

console = Console()
POLL_INTERVAL = int(os.getenv("POLLING_INTERVAL_MINUTES", "15")) * 60


def _should_run_ai_on_email(email_data) -> tuple[bool, str]:
    """
    Cheap deterministic guardrail before any LLM call.
    - Blocks likely legal/confidential docs from being sent to AI.
    - Requires pitch-like intent for deck processing.
    """
    subj = (email_data.subject or "").lower()
    body = (email_data.body or "").lower()
    fn = (email_data.pdf_filename or "").lower()
    blob = f"{subj}\n{body}\n{fn}"

    # Denylist: keep short acronyms on word boundaries to avoid false positives
    # (e.g. "and demand" contains "nda" as a substring).
    deny_phrases = [
        "confidential",
        "non-disclosure",
        "agreement",
        "master service agreement",
        "statement of work",
        "invoice",
        "privacy policy",
        "terms of service",
        "umowa",
        "aneks",
        "kontrakt",
        "regulamin",
    ]
    deny_acronyms = ["nda", "msa", "dpa", "sow"]
    for k in deny_phrases:
        if k in blob:
            return False, f"blocked_confidential_or_legal_doc:{k}"
    for a in deny_acronyms:
        if re.search(rf"\b{re.escape(a)}\b", blob, re.I):
            return False, f"blocked_confidential_or_legal_doc:{a}"

    # Must look like a funding pitch/deck to proceed to AI.
    allow_patterns = [
        r"\bpitch\b",
        r"\bdeck\b",
        r"\bseed\b",
        r"\bpre[- ]?seed\b",
        r"\braising\b",
        r"\bfundraising\b",
        r"\binvest(or|ment)\b",
        r"\bseries a\b",
    ]
    if not any(re.search(p, blob, re.I) for p in allow_patterns):
        return False, "blocked_non_pitch_email"

    return True, "ok_pitch_like"


def _linkedin_search_url(sender_name: str, company_name: str) -> str:
    query = " ".join(x for x in [(sender_name or "").strip(), (company_name or "").strip()] if x)
    q = urllib.parse.quote_plus(query)
    return f"https://www.linkedin.com/search/results/people/?keywords={q}"


def _assess_sender_authority(email_data, company_name: str) -> tuple[bool, str, str]:
    """
    Pareto guardrail: cheap heuristic for sender-company relationship.
    Returns (is_suspicious, reason, linkedin_search_url).
    """
    sender_email = (email_data.sender_email or "").strip().lower()
    sender_name = (email_data.sender_name or "").strip()
    company = (company_name or "").strip()
    body = (email_data.body or "").lower()
    subject = (email_data.subject or "").lower()
    blob = f"{subject}\n{body}"
    li_url = _linkedin_search_url(sender_name, company)

    if not sender_email:
        return True, "sender_email_missing", li_url

    # Signals of likely ownership/authority in pitch context.
    authority_terms = (
        "founder",
        "co-founder",
        "ceo",
        "coo",
        "cto",
        "cfo",
        "owner",
        "managing partner",
    )
    has_authority_claim = any(t in blob for t in authority_terms)

    local, _, domain = sender_email.partition("@")
    domain = domain.lower()
    local = local.lower()
    free_domains = {
        "example-mail.com",
        "example-outlook.com",
        "example-hotmail.com",
        "example-yahoo.com",
        "example-icloud.com",
        "example-proton.me",
        "example-protonmail.com",
    }

    # Company-domain style email is usually enough for MVP pass.
    if domain and domain not in free_domains:
        return False, "corporate_domain_sender", li_url

    # Free mailbox + no authority signals => suspicious / manual verify.
    if domain in free_domains and not has_authority_claim:
        return True, "free_mailbox_without_role_signal", li_url

    # If sender local-part and company have no obvious overlap, keep cautious.
    company_key = re.sub(r"[^a-z0-9]", "", company.lower())
    local_key = re.sub(r"[^a-z0-9]", "", local)
    if company_key and local_key and company_key[:6] not in local_key and local_key[:6] not in company_key:
        if not has_authority_claim:
            return True, "sender_name_company_mismatch", li_url

    return False, "no_major_sender_risk_signal", li_url


def _log_extracted_markdown(pdf_bytes: bytes, filename: str) -> None:
    from tools.pdf_utils import pdf_bytes_to_markdown
    import os as _os
    _os.makedirs("logs", exist_ok=True)
    md = pdf_bytes_to_markdown(pdf_bytes)
    log_path = f"logs/{filename.replace('.pdf', '')}_extracted.md"
    with open(log_path, "w") as f:
        f.write(md)
    console.print(f"[dim]Extracted Markdown saved → {log_path}[/dim]")


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _is_test_case(email_data, gate1) -> bool:
    txt = f"{(email_data.subject or '').lower()} {(email_data.body or '').lower()} {(email_data.pdf_filename or '').lower()}"
    markers = ("fwd:", "forwarded", "sample", "benchmark", "test case", "historical")
    if any(m in txt for m in markers):
        return True
    cname = (gate1.company_name or "").lower()
    if cname in {"uber", "airbnb"}:
        return True
    return False


def _auth_risk_from_sender(suspicious_sender: bool) -> str:
    return "HIGH" if suspicious_sender else "LOW"


def _fund_fit_score_from_gate1(gate1, *, auth_risk: str) -> float:
    if gate1.verdict == "FAIL_CONFIDENT":
        return 1.0
    base = 7.0 if gate1.verdict == "PASS" else 5.0
    if auth_risk == "HIGH":
        base = min(base, 4.0)
    return float(base)


def _initial_output_template(
    *,
    company: str,
    auth_risk: str,
    auth_reason: str,
    fund_fit_line: str,
    what_they_do: str,
    evidence: str,
    missing: str,
    deck_score: str,
    external_score: str,
    fund_fit_score: str,
    decision_line: str,
    next_action: str,
) -> str:
    text = (
        f"Company: {company}\n"
        f"Source/Auth Risk: {auth_risk} — {auth_reason}\n"
        f"Fund Fit: {fund_fit_line}\n"
        f"What They Do: {what_they_do}\n"
        f"Evidence: {evidence}\n"
        f"Missing / Blockers: {missing}\n"
        f"Scores: Deck Evidence {deck_score}; External Opportunity {external_score}; Fund Fit {fund_fit_score}.\n"
        f"Decision: {decision_line}\n"
        f"Next Action: {next_action}"
    ).strip()
    # Hard cap: max 350 words in INITIAL mode.
    words = text.split()
    if len(words) <= 350:
        return text
    return " ".join(words[:350]).rstrip(" .,;:") + "…"


def _website_message_id(url: str) -> str:
    u = (url or "").strip()
    parsed = urllib.parse.urlparse(u if "://" in u else f"https://{u}")
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.netloc or parsed.path or "").lower().strip()
    path = (parsed.path or "").rstrip("/")
    canonical = f"{scheme}://{host}{path}"
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()[:12]
    return f"website_{digest}"


def _print_token_report(parts: list[dict], *, title: str = "Token usage") -> None:
    if not parts:
        return
    total_in = sum(int(p.get("input_tokens", 0) or 0) for p in parts)
    total_out = sum(int(p.get("output_tokens", 0) or 0) for p in parts)
    total_tok = total_in + total_out
    total_cost = sum(float(p.get("cost_usd", 0.0) or 0.0) for p in parts)
    console.print(f"\n[bold]{title}[/bold] — total {total_tok:,} tokens (in {total_in:,} / out {total_out:,}) | ${total_cost:.4f}")
    if total_tok <= 0:
        return
    for p in parts:
        name = str(p.get("name") or "stage")
        t = int(p.get("total_tokens", (int(p.get("input_tokens", 0) or 0) + int(p.get("output_tokens", 0) or 0))))
        pct = (t / total_tok) * 100.0
        c = float(p.get("cost_usd", 0.0) or 0.0)
        console.print(f"  • {name}: {t:,} tokens ({pct:.1f}%) | ${c:.4f}")


def process_email(email_data, gmail_client=None, *, force_rescan: bool = False) -> None:
    from storage import database as db
    from agents.screener import ScreeningAgent
    from hitl.terminal import display_brief, get_decision
    from agents.orchestrator import draft_approval_email, draft_rejection_email
    from tools.pdf_utils import (
        PDFExtractionError,
        assess_deck_markdown_quality,
        pdf_bytes_to_markdown,
    )

    if (not force_rescan) and db.is_already_processed(email_data.message_id):
        console.print(f"[dim]Already processed: {email_data.message_id}[/dim]")
        return

    db.save_deal_email(email_data, status=db.STATUS_NEW)
    from config.llm_cost import OPENAI_MODEL, OPENAI_MODEL_LIGHT
    run_id = str(uuid.uuid4())
    db.attach_run_metadata(
        email_data.message_id,
        run_id=run_id,
        model_heavy=OPENAI_MODEL,
        model_light=OPENAI_MODEL_LIGHT,
        prompt_version=os.getenv("PROMPT_VERSION"),
        rubric_version=os.getenv("RUBRIC_VERSION"),
        scoring_config_version=os.getenv("SCORING_CONFIG_VERSION"),
        code_version=os.getenv("CODE_VERSION") or os.getenv("GIT_COMMIT"),
        external_mode=os.getenv("EXTERNAL_WEB_SEARCH", "auto"),
    )

    def _pipeline() -> None:
        allow_ai, prefilter_reason = _should_run_ai_on_email(email_data)
        if not allow_ai:
            console.print(
                f"[yellow]Skipped before AI[/yellow] — {prefilter_reason} "
                f"(subject: {email_data.subject[:80]})"
            )
            db.update_status(email_data.message_id, db.STATUS_SKIPPED)
            if gmail_client:
                # Keep for manual review; do not run LLM on potentially sensitive/non-pitch docs.
                gmail_client.mark_as_needs_review(email_data.message_id)
            return
    
        screener = ScreeningAgent()
    
        console.rule(f"[cyan]Screening: {email_data.sender_name} — {email_data.subject[:60]}[/cyan]")
        console.print(f"[dim]{email_data.sender_email} | {email_data.date}[/dim]")
        console.print()
    
        # ── Gate 1 ───────────────────────────────────────────────────────────────
        db.update_status(email_data.message_id, db.STATUS_GATE1_RUNNING)
        console.print("[yellow]Gate 1:[/yellow] Running fit classification...")
        try:
            gate1, tel_g1 = screener.gate1_fit_check(email_data)
        except Exception as e:
            db.save_error(
                email_data.message_id,
                "GATE1_ERROR",
                str(e),
                pipeline_status=db.STATUS_ERROR,
            )
            if gmail_client:
                gmail_client.mark_as_needs_review(email_data.message_id)
            console.print(f"[red]Gate 1 failed: {e}[/red]")
            return
    
        suspicious_sender, sender_reason, li_url = _assess_sender_authority(
            email_data,
            company_name=gate1.company_name or gate1.company_one_liner or "",
        )
        if suspicious_sender:
            if "sender_identity_unverified" not in gate1.flags:
                gate1.flags.append("sender_identity_unverified")
            console.print(
                f"[yellow]⚠ Sender-company link not verified[/yellow] — {sender_reason}\n"
                f"[dim]Manual check (LinkedIn): {li_url}[/dim]"
            )
        else:
            console.print(f"[dim]LinkedIn check URL:[/dim] {li_url}")
    
        db.save_gate1(email_data.message_id, gate1, telemetry=tel_g1)
    
        screening_depth = os.getenv("SCREENING_DEPTH", "INITIAL").strip().upper() or "INITIAL"
        debug_override = _bool_env("DEBUG_OVERRIDE", "0")
        manual_override = _bool_env("MANUAL_OVERRIDE", "0")
        continued_because_debug_override = False
        auth_risk = _auth_risk_from_sender(suspicious_sender)
        test_case = _is_test_case(email_data, gate1)
        fund_fit_score = _fund_fit_score_from_gate1(gate1, auth_risk=auth_risk)
    
        if gate1.verdict == "FAIL_CONFIDENT":
            allow_continue = debug_override or manual_override or force_rescan
            if not allow_continue:
                initial_stop = _initial_output_template(
                    company=(gate1.company_name or "Unknown"),
                    auth_risk=auth_risk,
                    auth_reason=sender_reason,
                    fund_fit_line=f"FAIL — {gate1.rejection_reason or 'Out of Fund mandate.'}",
                    what_they_do=(gate1.company_one_liner or "unknown"),
                    evidence="[EMAIL] Gate 1 fit classification. [MISSING] Deck-level evidence not analyzed due to hard stop.",
                    missing="[MISSING] deck extraction, traction metrics, pricing/monetization, verified sender-company link.",
                    deck_score="NOT_RUN",
                    external_score="NOT_RUN",
                    fund_fit_score="1.0/10",
                    decision_line="Fund Fit FAIL; Deck Evidence NEEDS_MORE_INFO; Generic VC Interest REJECT.",
                    next_action="STOP",
                )
                console.print("[red]✗ Gate 1 FAIL_CONFIDENT[/red]")
                console.print(initial_stop)
                db.save_screening_decisions(
                    email_data.message_id,
                    screening_depth="INITIAL",
                    auth_risk=auth_risk,
                    fund_fit_decision="FAIL",
                    deck_evidence_decision="NEEDS_MORE_INFO",
                    generic_vc_interest="REJECT",
                    final_action="STOP",
                    deck_evidence_score=None,
                    external_opportunity_score=None,
                    fund_fit_score=1.0,
                    debug_override_used=False,
                    continued_because_debug_override=False,
                    test_case=test_case,
                )
                if gmail_client:
                    gmail_client.mark_as_processed(email_data.message_id)
                return
            if debug_override and not manual_override:
                continued_because_debug_override = True
                console.print(
                    "[yellow]⚠ Fund Fit: FAIL. Analysis continued only because debug/manual override is enabled.[/yellow]"
                )
                console.print(
                    "[dim]Production decision would have stopped at Gate 1. Gate 2 was run only for debugging/test evaluation.[/dim]"
                )
    
        verdict_note = ""
        if gate1.verdict == "UNCERTAIN_READ_DECK":
            verdict_note = " (uncertain from email — reading deck)"
        console.print(
            f"[green]✓ Gate 1 {gate1.verdict}[/green] — {gate1.company_name or 'Unknown'} | "
            f"{gate1.detected_geography} | {gate1.detected_stage} | {gate1.detected_sector}"
            f"{verdict_note}"
        )
    
        # ── PDF required path ───────────────────────────────────────────────────
        deck_markdown: str | None = None
        analysis_mode = "ANALYZED_EMAIL_ONLY"
    
        if email_data.has_pdf and email_data.attachment_id and gmail_client:
            console.print(f"[dim]Downloading PDF: {email_data.pdf_filename}...[/dim]")
            try:
                email_data.pdf_bytes = gmail_client.download_attachment(
                    email_data.message_id, email_data.attachment_id
                )
                console.print(f"[dim]PDF downloaded ({len(email_data.pdf_bytes):,} bytes)[/dim]")
                db.update_status(email_data.message_id, db.STATUS_PDF_DOWNLOADED)
            except Exception as e:
                console.print(f"[red]PDF download failed: {e}[/red]")
                db.save_error(
                    email_data.message_id,
                    "PDF_DOWNLOAD_FAILED",
                    str(e),
                    pipeline_status=db.STATUS_PDF_DOWNLOAD_FAILED,
                )
                gmail_client.mark_as_needs_review(email_data.message_id)
                return
    
        if email_data.has_pdf and not email_data.pdf_bytes:
            detail = "PDF attachment present but could not be loaded (missing attachment ID or bytes)."
            console.print(f"[red]{detail}[/red]")
            db.save_error(
                email_data.message_id,
                "PDF_DOWNLOAD_FAILED",
                detail,
                pipeline_status=db.STATUS_PDF_DOWNLOAD_FAILED,
            )
            if gmail_client:
                gmail_client.mark_as_needs_review(email_data.message_id)
            return
    
        if email_data.pdf_bytes:
            try:
                screener._check_pdf_size(email_data.pdf_bytes)
                deck_markdown = pdf_bytes_to_markdown(email_data.pdf_bytes)
                analysis_mode = "ANALYZED_WITH_DECK"
                if email_data.pdf_filename:
                    _log_extracted_markdown(email_data.pdf_bytes, email_data.pdf_filename)
                qwarn = assess_deck_markdown_quality(deck_markdown)
                if qwarn:
                    console.print(f"[yellow]⚠ {qwarn}[/yellow]")
                    skip_bad = os.getenv("SKIP_GATE2_ON_BAD_DECK", "1").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                    if skip_bad:
                        # Token-saver + safety: don't run Gate2 LLM on near-empty decks.
                        db.save_error(
                            email_data.message_id,
                            "DECK_TEXT_UNREADABLE",
                            qwarn,
                            pipeline_status=db.STATUS_PDF_EXTRACTION_FAILED,
                        )
                        if gmail_client:
                            gmail_client.mark_as_needs_review(email_data.message_id)
                        console.print(
                            "[yellow]Skipping Gate 2[/yellow] — deck text extraction too weak. "
                            "Marked as NeedsReview."
                        )
                        return
            except PDFExtractionError as e:
                console.print(f"[red]PDF extraction failed: {e}[/red]")
                db.save_error(
                    email_data.message_id,
                    "PDF_EXTRACTION_FAILED",
                    str(e),
                    pipeline_status=db.STATUS_PDF_EXTRACTION_FAILED,
                )
                if gmail_client:
                    gmail_client.mark_as_needs_review(email_data.message_id)
                return
    
        from utils import cost_control as _cost_ctl

        _skip_g2, _why_g2 = _cost_ctl.should_block_stage(
            email_data.message_id,
            estimated_extra_usd=_cost_ctl.estimate_gate2_usd(),
        )
        if _skip_g2:
            console.print(f"[yellow]Cost cap:[/yellow] skipping Gate 2 — {_why_g2}")
            db.save_cost_cap_skip(
                email_data.message_id,
                estimated_extra_cost_usd=_cost_ctl.estimate_gate2_usd(),
                daily_cap_usd=_cost_ctl.max_cost_per_day_usd(),
                run_cap_usd=_cost_ctl.max_cost_per_run_usd(),
                reason=_why_g2,
            )
            if gmail_client:
                gmail_client.mark_as_needs_review(email_data.message_id)
            return

        # ── Gate 2 (extract → score → brief) ───────────────────────────────────
        db.update_status(email_data.message_id, db.STATUS_GATE2_RUNNING)
        console.print(
            f"[yellow]Gate 2:[/yellow] Running pipeline ({analysis_mode})..."
        )
        try:
            gate2, tel_g2, facts_json, dimensions_json = screener.run_gate2_pipeline(
                email_data,
                gate1,
                deck_markdown=deck_markdown,
                analysis_mode=analysis_mode,
                screening_depth=screening_depth,
            )
        except Exception as e:
            db.save_error(
                email_data.message_id,
                "GATE2_ERROR",
                str(e),
                pipeline_status=db.STATUS_GATE2_FAILED,
            )
            if gmail_client:
                gmail_client.mark_as_needs_review(email_data.message_id)
            console.print(f"[red]Gate 2 failed: {e}[/red]")
            return
    
        token_parts: list[dict] = [
            {
                "name": "gate1",
                "input_tokens": int(tel_g1.get("input_tokens") or 0),
                "output_tokens": int(tel_g1.get("output_tokens") or 0),
                "total_tokens": int(tel_g1.get("input_tokens") or 0) + int(tel_g1.get("output_tokens") or 0),
                "cost_usd": float(tel_g1.get("cost_usd") or 0.0),
            },
            {
                "name": "gate2",
                "input_tokens": int(tel_g2.get("input_tokens") or 0),
                "output_tokens": int(tel_g2.get("output_tokens") or 0),
                "total_tokens": int(tel_g2.get("input_tokens") or 0) + int(tel_g2.get("output_tokens") or 0),
                "cost_usd": float(tel_g2.get("cost_usd") or 0.0),
            },
        ]
    
        # Decision split: Fund Fit vs Deck Evidence vs Generic VC Interest
        deck_evidence_score = float(gate2.overall_score)
        fund_fit_decision = "PASS" if gate1.verdict == "PASS" else ("UNCERTAIN" if gate1.verdict == "UNCERTAIN_READ_DECK" else "FAIL")
        deck_evidence_decision = "PASS" if gate2.passes else "NEEDS_MORE_INFO"
        generic_vc_interest = "PASS_TO_CALL" if gate2.overall_score >= 7.0 else ("WATCHLIST" if gate2.overall_score >= 5.0 else "REJECT")
    
        # ENRICHED trigger gating
        fit_ok_for_enriched = fund_fit_decision in ("PASS", "UNCERTAIN")
        enriched_trigger = fit_ok_for_enriched and auth_risk != "HIGH" and deck_evidence_score >= 5.5
        wants_enriched_depth = screening_depth in ("ENRICHED", "DEEP_DIVE")
        enable_external = wants_enriched_depth and enriched_trigger and _bool_env("ENABLE_EXTERNAL_CHECK", "1")
        if test_case and not _bool_env("ALLOW_EXTERNAL_FOR_TEST_CASES", "0"):
            enable_external = False
        external_score_for_db: float | None = None
        final_action = "RUN_ENRICHED_SCREEN" if enable_external else "STOP"
        if test_case:
            final_action = "TEST_CASE_ONLY"
        if continued_because_debug_override and fund_fit_decision == "FAIL":
            final_action = "TEST_CASE_ONLY"
        defer = enable_external and gate2.passes
    
        # VC Snapshot Card (token-cheap: built from stored structured outputs)
        snapshot_max = int(os.getenv("VC_SNAPSHOT_MAX_CHARS", "1800") or "1800")
        snapshot = ""
        try:
            from agents.vc_snapshot import render_vc_snapshot_card
    
            snapshot = render_vc_snapshot_card(
                company_name=(gate2.company_name or gate1.company_name or "Unknown"),
                gate1_detected_geography=(gate1.detected_geography or ""),
                gate1_detected_sector=(gate1.detected_sector or ""),
                gate1_detected_stage=(gate1.detected_stage or ""),
                gate2_overall_score=getattr(gate2, "overall_score", None),
                gate2_recommendation=getattr(gate2, "recommendation", "") or "",
                gate2_strengths_json=json.dumps(getattr(gate2, "top_strengths", []) or []),
                gate2_concerns_json=json.dumps(getattr(gate2, "top_concerns", []) or []),
                gate2_missing_critical_data_json=json.dumps(getattr(gate2, "missing_critical_data", []) or []),
                gate2_should_ask_founder_json=json.dumps(getattr(gate2, "should_ask_founder", []) or []),
                facts_json=facts_json,
                dimensions_json=dimensions_json,
                max_chars=snapshot_max,
            )
        except Exception:
            snapshot = ""
    
        db.save_gate2(
            email_data.message_id,
            gate2,
            analysis_mode=analysis_mode,
            facts_json=facts_json,
            dimensions_json=dimensions_json,
            snapshot_md=snapshot or None,
            screening_depth=screening_depth,
            auth_risk=auth_risk,
            fund_fit_decision=fund_fit_decision,
            deck_evidence_decision=deck_evidence_decision,
            generic_vc_interest=generic_vc_interest,
            final_action=final_action,
            deck_evidence_score=deck_evidence_score,
            external_opportunity_score=external_score_for_db,
            fund_fit_score=fund_fit_score,
            debug_override_used=(debug_override or manual_override),
            continued_because_debug_override=continued_because_debug_override,
            test_case=test_case,
            quality_flags=gate2.quality_flags,
            telemetry=tel_g2,
            defer_hitl=defer,
        )
    
        if snapshot:
            console.print()
            console.print("[bold]VC Snapshot[/bold]")
            console.print(snapshot)
            console.print()
    
        if screening_depth == "INITIAL":
            initial_note = _initial_output_template(
                company=(gate2.company_name or gate1.company_name or "Unknown"),
                auth_risk=auth_risk,
                auth_reason=sender_reason,
                fund_fit_line=f"{fund_fit_decision} — {gate1.rejection_reason or gate1.detected_geography}",
                what_they_do=(gate2.what_they_do or gate2.company_one_liner or "unknown"),
                evidence="[DECK] See evidence lines in VC Snapshot. [EMAIL] sender/pitch context. [INFERRED] explicitly marked in concerns.",
                missing="[MISSING] " + ", ".join((gate2.missing_critical_data or [])[:6]),
                deck_score=f"{deck_evidence_score:.1f}/10",
                external_score=("NOT_RUN" if not enable_external else "PENDING"),
                fund_fit_score=f"{fund_fit_score:.1f}/10",
                decision_line=f"Fund Fit {fund_fit_decision}; Deck Evidence {deck_evidence_decision}; Generic VC Interest {generic_vc_interest}.",
                next_action=final_action,
            )
            console.print(initial_note[:2200])
    
        threshold = float(os.getenv("GATE2_PASS_THRESHOLD", "6.0"))
        if not gate2.passes:
            console.print(
                f"[red]✗ Gate 2 FAIL[/red] — Internal (deck): {gate2.overall_score:.1f}/10 "
                f"(threshold: {threshold}) — {gate2.recommendation}"
            )
            compact = _initial_output_template(
                company=(gate2.company_name or gate1.company_name or "Unknown"),
                auth_risk=auth_risk,
                auth_reason=sender_reason,
                fund_fit_line=f"{fund_fit_decision} — {gate1.rejection_reason or gate1.detected_geography}",
                what_they_do=(gate2.what_they_do or gate2.company_one_liner or "unknown"),
                evidence="[DECK]/[EMAIL] See VC Snapshot evidence block.",
                missing="[MISSING] " + ", ".join((gate2.missing_critical_data or [])[:6]),
                deck_score=f"{gate2.overall_score:.1f}/10",
                external_score="NOT_RUN",
                fund_fit_score=f"{fund_fit_score:.1f}/10",
                decision_line=f"Fund Fit {fund_fit_decision}; Deck Evidence {deck_evidence_decision}; Generic VC Interest {generic_vc_interest}.",
                next_action=final_action,
            )
            console.print()
            console.print(compact[:3500])
            if gmail_client:
                gmail_client.mark_as_processed(email_data.message_id)
            return
    
        console.print(
            f"[green]✓ Gate 2 PASS (internal)[/green] — Deck-implied: {gate2.overall_score:.1f}/10 — {gate2.recommendation}"
        )
    
        external = None
        final_decision = None
        run_gate25 = enable_external and db.get_deal_status(email_data.message_id) != db.STATUS_SKIPPED_COST_CAP
        if run_gate25 and not _bool_env("DEBUG_OVERRIDE_GATE25", "0"):
            _skip_g25, _why_g25 = _cost_ctl.should_block_stage(
                email_data.message_id,
                estimated_extra_usd=_cost_ctl.estimate_gate25_usd(),
            )
            _ext_lim, _why_ext = _cost_ctl.should_block_external_budget()
            if _skip_g25 or _ext_lim:
                console.print(
                    f"[yellow]Cost / budget cap:[/yellow] skipping Gate 2.5 — "
                    f"{_why_g25 or _why_ext}"
                )
                db.save_cost_cap_skip(
                    email_data.message_id,
                    estimated_extra_cost_usd=_cost_ctl.estimate_gate25_usd(),
                    daily_cap_usd=_cost_ctl.max_cost_per_day_usd(),
                    run_cap_usd=_cost_ctl.max_cost_per_run_usd(),
                    reason=_why_g25 or _why_ext or "gate25_blocked",
                )
                run_gate25 = False

        if run_gate25:
            from agents.external_check import run_gate25_external_check
            from agents.final_scoring import (
                apply_hard_cap_to_final,
                build_final_investment_decision,
                compute_final_score_before_cap,
            )

            db.update_status(email_data.message_id, db.STATUS_GATE25_RUNNING)
            console.print("[magenta]Gate 2.5:[/magenta] External market check…")
            facts_dict = json.loads(facts_json)
            try:
                external, _tel_g25 = run_gate25_external_check(
                    facts_dict=facts_dict,
                    dimensions_json=dimensions_json,
                    gate1=gate1,
                    gate2=gate2,
                )
            except Exception as e:
                db.save_error(
                    email_data.message_id,
                    "GATE25_ERROR",
                    str(e),
                    pipeline_status=db.STATUS_ERROR,
                )
                if gmail_client:
                    gmail_client.mark_as_needs_review(email_data.message_id)
                console.print(f"[red]Gate 2.5 failed: {e}[/red]")
                return
            token_parts.append(
                {
                    "name": "gate2_5_external",
                    "input_tokens": int((_tel_g25 or {}).get("input_tokens") or 0),
                    "output_tokens": int((_tel_g25 or {}).get("output_tokens") or 0),
                    "total_tokens": int((_tel_g25 or {}).get("input_tokens") or 0)
                    + int((_tel_g25 or {}).get("output_tokens") or 0),
                    "cost_usd": float((_tel_g25 or {}).get("cost_usd") or 0.0),
                }
            )
    
            final_threshold = float(os.getenv("FINAL_PASS_THRESHOLD", "6.3"))
            override_fatal = os.getenv("OVERRIDE_FATAL_KILL_FLAGS", "").lower() in (
                "1",
                "true",
                "yes",
            )
            final_before = compute_final_score_before_cap(
                gate2.overall_score,
                external.external_score,
                external.risk_penalty,
            )
            cap_ceiling = 10.0 if external.hard_cap is None else float(external.hard_cap)
            final_score = apply_hard_cap_to_final(final_before, cap_ceiling)
    
            final_decision = build_final_investment_decision(
                gate1=gate1,
                gate2=gate2,
                external=external,
                final_score=final_score,
                gate2_threshold=threshold,
                final_threshold=final_threshold,
                override_fatal=override_fatal,
            )
    
            console.print(
                f"[dim]External score:[/dim] {external.external_score:.2f} "
                f"({external.external_confidence}) | "
                f"[dim]Final:[/dim] {final_score:.2f} "
                f"(threshold {final_threshold}) | "
                f"[dim]Verdict:[/dim] {final_decision.final_verdict}"
            )
            external_score_for_db = float(external.external_score)
            final_action = "PASS_TO_PARTNER" if float(final_score) >= 7.0 else "ASK_FOR_MORE_INFO"
            if test_case or continued_because_debug_override:
                final_action = "TEST_CASE_ONLY"
            db.save_screening_decisions(
                email_data.message_id,
                screening_depth=screening_depth,
                auth_risk=auth_risk,
                fund_fit_decision=fund_fit_decision,
                deck_evidence_decision=deck_evidence_decision,
                generic_vc_interest=generic_vc_interest,
                final_action=final_action,
                deck_evidence_score=deck_evidence_score,
                external_opportunity_score=external_score_for_db,
                fund_fit_score=fund_fit_score,
                debug_override_used=(debug_override or manual_override),
                continued_because_debug_override=continued_because_debug_override,
                test_case=test_case,
            )
    
            if final_decision.final_verdict == "REJECT_AUTO":
                db.save_gate25(
                    email_data.message_id,
                    external=external,
                    final_decision=final_decision,
                    status=db.STATUS_REJECTED_EXTERNAL,
                    telemetry=_tel_g25,
                )
                console.print(
                    f"[red]✗ Rejected after external check[/red] — {final_decision.rationale}"
                )
                if gmail_client:
                    gmail_client.mark_as_processed(email_data.message_id)
                return
    
            db.save_gate25(
                email_data.message_id,
                external=external,
                final_decision=final_decision,
                status=db.STATUS_WAITING_HITL,
                telemetry=_tel_g25,
            )
        else:
            _post_g2_status = db.get_deal_status(email_data.message_id)
            if _post_g2_status != db.STATUS_SKIPPED_COST_CAP:
                db.update_status(email_data.message_id, db.STATUS_WAITING_HITL)
            if _post_g2_status == db.STATUS_SKIPPED_COST_CAP:
                final_action = "STOP"
            else:
                final_action = "STOP" if fund_fit_decision == "FAIL" else (
                    "ASK_FOR_MORE_INFO" if deck_evidence_score < 7.0 else "PASS_TO_PARTNER"
                )
            if test_case or continued_because_debug_override:
                final_action = "TEST_CASE_ONLY"
            db.save_screening_decisions(
                email_data.message_id,
                screening_depth=screening_depth,
                auth_risk=auth_risk,
                fund_fit_decision=fund_fit_decision,
                deck_evidence_decision=deck_evidence_decision,
                generic_vc_interest=generic_vc_interest,
                final_action=final_action,
                deck_evidence_score=deck_evidence_score,
                external_opportunity_score=None,
                fund_fit_score=fund_fit_score,
                debug_override_used=(debug_override or manual_override),
                continued_because_debug_override=continued_because_debug_override,
                test_case=test_case,
            )
    
        # ── Gate 3: HITL (optional) ──────────────────────────────────────────────
        hitl_mode = os.getenv("HITL_MODE", "skip").strip().lower()  # interactive | skip
        if hitl_mode != "interactive":
            # In automation mode we do NOT block waiting for terminal decisions.
            if final_action == "TEST_CASE_ONLY":
                status_val = "DEBUG_ANALYZED_NON_FIT" if continued_because_debug_override else "TEST_CASE"
                db.update_status(email_data.message_id, status_val)
            else:
                db.update_status(email_data.message_id, db.STATUS_WAITING_HITL)
            if gmail_client:
                gmail_client.mark_as_processed(email_data.message_id)
            _print_token_report(token_parts, title="Token usage (this scan)")
            console.print("[dim]HITL skipped (HITL_MODE != interactive).[/dim]")
            return
    
        brief = screener.build_brief(
            email_data,
            gate1,
            gate2,
            external_market=external,
            final_investment_decision=final_decision,
        )
        console.print()
        display_brief(brief)
        decision = get_decision(brief)
        db.save_hitl_decision(email_data.message_id, decision)
    
        if gmail_client:
            if decision.approved:
                draft_id = draft_approval_email(email_data, gate2, decision, gmail_client)
                db.update_status(email_data.message_id, db.STATUS_APPROVED_DRAFT_CREATED)
                console.print(f"[green]Draft approval email created[/green] (draft ID: {draft_id})")
                console.print("[dim]→ Go to Gmail Drafts to review and send[/dim]")
            else:
                if decision.rejection_reason != "SKIPPED — no action taken":
                    draft_id = draft_rejection_email(email_data, gate2, decision, gmail_client)
                    db.update_status(email_data.message_id, db.STATUS_REJECTED_DRAFT_CREATED)
                    console.print(f"[dim]Rejection draft created (draft ID: {draft_id})[/dim]")
            gmail_client.mark_as_processed(email_data.message_id)
    
        _print_token_report(token_parts, title="Token usage (this scan)")
        console.print()
    
    

    try:
        _pipeline()
    finally:
        db.finish_run(email_data.message_id)


def run_gmail_loop(once: bool = False) -> None:
    from tools.gmail_client import GmailClient
    from storage.database import init_db, count_deals_since_utc_midnight
    import storage.database as db
    from agents.notion_sync import sync_pipeline_to_notion

    init_db()
    console.print("[bold cyan]Example VC Fund Screening Agent[/bold cyan] — starting")
    gmail = GmailClient()
    console.print("[green]Gmail authenticated[/green]")

    daily_cap = int(os.getenv("DAILY_DEAL_LIMIT", "50"))
    max_per_run = int(os.getenv("MAX_EMAILS_PER_RUN", "50"))
    notion_auto_sync = os.getenv("NOTION_AUTO_SYNC", "0").strip().lower() in ("1", "true", "yes", "on")
    notion_days = int(os.getenv("NOTION_SYNC_DAYS", "30"))
    notion_prune_tests = os.getenv("NOTION_PRUNE_TESTS", "1").strip().lower() in ("1", "true", "yes", "on")
    notion_ensure_schema = os.getenv("NOTION_ENSURE_SCHEMA", "1").strip().lower() in ("1", "true", "yes", "on")
    notion_mode = os.getenv("NOTION_AUTO_SYNC_MODE", "per_deal").strip().lower()  # per_deal | batch

    while True:
        console.print(f"\n[dim]{datetime.now().strftime('%Y-%m-%d %H:%M')} — checking for new pitch decks...[/dim]")
        processed_any = False
        try:
            emails = gmail.get_unread_pitchdecks()
            if emails:
                processed_today = count_deals_since_utc_midnight()
                remaining = max(0, daily_cap - processed_today)
                if remaining == 0:
                    console.print(f"[yellow]Daily deal limit reached ({daily_cap} created today UTC). Skipping run.[/yellow]")
                else:
                    cap = min(remaining, max_per_run, len(emails))
                    if len(emails) > cap:
                        console.print(
                            f"[dim]Capping batch: {len(emails)} found → processing {cap} "
                            f"(daily remaining {remaining}, max per run {max_per_run})[/dim]"
                        )
                        emails = emails[:cap]
                    console.print(f"[cyan]Found {len(emails)} email(s) to screen[/cyan]")
                    for email_data in emails:
                        try:
                            process_email(email_data, gmail)
                            processed_any = True
                            if notion_auto_sync and notion_mode == "per_deal":
                                try:
                                    from agents.notion_sync import sync_one_deal_to_notion

                                    s = sync_one_deal_to_notion(
                                        email_data.message_id,
                                        ensure_schema=notion_ensure_schema,
                                    )
                                    console.print(
                                        "[dim]Notion auto-sync:[/dim] "
                                        f"scanned={s.scanned}, created={s.created}, updated={s.updated}, skipped={s.skipped}"
                                    )
                                except Exception as e:
                                    console.print(f"[yellow]Notion auto-sync failed:[/yellow] {e}")
                        except Exception as e:
                            db.save_error(
                                email_data.message_id,
                                "PROCESSING_ERROR",
                                str(e),
                                pipeline_status=db.STATUS_ERROR,
                            )
                            try:
                                gmail.mark_as_needs_review(email_data.message_id)
                            except Exception:
                                pass
                            console.print(f"[red]Failed email {email_data.message_id}: {e}[/red]")
                            continue
            else:
                console.print("[dim]No new pitch decks found.[/dim]")
        except Exception as e:
            console.print(f"[red]Error during processing: {e}[/red]")
            raise

        if notion_auto_sync and processed_any and notion_mode != "per_deal":
            try:
                s = sync_pipeline_to_notion(
                    days=notion_days,
                    prune_test_rows=notion_prune_tests,
                    ensure_schema=notion_ensure_schema,
                )
                console.print(
                    "[dim]Notion auto-sync:[/dim] "
                    f"scanned={s.scanned}, created={s.created}, updated={s.updated}, skipped={s.skipped}"
                )
            except Exception as e:
                console.print(f"[yellow]Notion auto-sync failed:[/yellow] {e}")

        if once:
            break
        console.print(f"[dim]Next check in {POLL_INTERVAL // 60} minutes...[/dim]")
        time.sleep(POLL_INTERVAL)


def run_test_mode(pdf_path: str, force: bool = False) -> None:
    from storage.database import init_db, delete_deal
    from storage.models import EmailData
    import storage.database as db

    init_db()
    stem = Path(pdf_path).stem
    legacy_id = f"test_{stem}"
    if force:
        delete_deal(legacy_id)

    pdf_bytes = Path(pdf_path).read_bytes()
    message_id = f"test_{stem}_{int(time.time())}"

    console.print(f"[cyan]Test mode[/cyan] — loading PDF: {pdf_path} (id {message_id})")

    email_data = EmailData(
        message_id=message_id,
        sender_email="founder@example.com",
        sender_name="Test Founder",
        subject="Pitch Deck — TestCo",
        body="Hi, I'm the founder of TestCo. We're a pre-seed startup from Warsaw building AI tools for developers. Raising €1.5M. Please find our deck attached.",
        date=datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
        has_pdf=True,
        pdf_filename=Path(pdf_path).name,
        pdf_bytes=pdf_bytes,
        attachment_id=None,
        thread_id=None,
    )

    try:
        process_email(email_data, gmail_client=None, force_rescan=True)
    except Exception as e:
        db.save_error(
            message_id,
            "PROCESSING_ERROR",
            str(e),
            pipeline_status=db.STATUS_ERROR,
        )
        console.print(f"[red]Test run failed: {e}[/red]")
        raise


def run_report(days: int = 7) -> None:
    from storage.database import init_db
    from agents.reporter import generate_weekly_report
    init_db()
    report = generate_weekly_report(days=days)
    console.print(report)


def run_sync_notion(
    days: int = 30,
    *,
    prune_test_rows: bool = False,
    ensure_schema: bool = False,
    prune_columns: bool = False,
) -> None:
    from storage.database import init_db
    from agents.notion_sync import sync_pipeline_to_notion

    init_db()
    stats = sync_pipeline_to_notion(
        days=days,
        prune_test_rows=prune_test_rows,
        ensure_schema=ensure_schema,
        prune_columns=prune_columns,
    )
    console.print(
        "[green]Notion sync complete[/green] — "
        f"scanned={stats.scanned}, created={stats.created}, updated={stats.updated}, skipped={stats.skipped}"
    )


def run_rescan_message(message_id: str) -> None:
    from storage.database import init_db
    from tools.gmail_client import GmailClient

    init_db()
    gmail = GmailClient()
    email = gmail.get_message(message_id)
    if not email:
        console.print(f"[red]Could not fetch Gmail message:[/red] {message_id}")
        return
    console.print(f"[cyan]Rescan[/cyan] — {email.subject[:80]}  [dim]({message_id})[/dim]")
    process_email(email, gmail_client=gmail, force_rescan=True)
    sync_on_rescan = os.getenv("NOTION_SYNC_ON_RESCAN", "1").strip().lower() in ("1", "true", "yes", "on")
    ensure_schema = os.getenv("NOTION_ENSURE_SCHEMA", "1").strip().lower() in ("1", "true", "yes", "on")
    if sync_on_rescan:
        try:
            from agents.notion_sync import sync_one_deal_to_notion

            s = sync_one_deal_to_notion(message_id, ensure_schema=ensure_schema)
            console.print(
                "[dim]Notion sync (rescan):[/dim] "
                f"scanned={s.scanned}, created={s.created}, updated={s.updated}, skipped={s.skipped}"
            )
        except Exception as e:
            console.print(f"[yellow]Notion sync (rescan) failed:[/yellow] {e}")


def run_pick_and_rescan(limit: int = 15) -> None:
    import sys as _sys
    from storage.database import init_db, get_recent_deals

    init_db()
    rows = get_recent_deals(limit=limit, include_tests=False)
    if not rows:
        console.print("[yellow]No deals in pipeline.db to pick from.[/yellow]")
        return
    console.print(f"[bold]Pick a deal to rescan (latest {len(rows)}):[/bold]")
    for i, r in enumerate(rows, start=1):
        mid = str(r.get('message_id') or '')
        subj = str(r.get('subject') or '')[:70]
        comp = str(r.get('company_name') or '').strip()
        st = str(r.get('status') or '')
        pdf = str(r.get('pdf_filename') or '')
        label = comp or subj or mid
        console.print(f"  {i}) {label}  [dim]{st} • {pdf} • {mid}[/dim]")

    if not _sys.stdin.isatty():
        console.print("[yellow]Non-interactive terminal.[/yellow] Re-run with: --rescan <message_id>")
        return
    raw = input(f"Enter number (1-{len(rows)}) or blank to cancel: ").strip()
    if not raw:
        console.print("[dim]Cancelled.[/dim]")
        return
    try:
        idx = int(raw)
    except Exception:
        console.print("[red]Invalid number.[/red]")
        return
    if idx < 1 or idx > len(rows):
        console.print("[red]Out of range.[/red]")
        return
    mid = str(rows[idx - 1].get("message_id") or "").strip()
    if not mid:
        console.print("[red]Missing message_id for selected row.[/red]")
        return
    run_rescan_message(mid)


def run_assess_url(url: str) -> None:
    """Website screening: crawl → LLM facts/scores → optional Gate 2.5 with website-weighted final."""
    from agents.external_check import run_gate25_external_check
    from agents.final_scoring import (
        apply_hard_cap_to_final,
        build_final_investment_decision,
        compute_final_score_before_cap,
        gate2_proxy_from_website_dimensions,
    )
    from agents.website_screener import (
        WebsiteScreeningAgent,
        website_dimension_int_scores,
        website_facts_to_external_dict,
    )
    from agents.website_vc_llm import get_vc_llm_telemetry, reset_vc_llm_telemetry
    from config.website_scoring import resolve_blended_website_verdict
    from storage.models import EmailData
    from storage import database as db

    raw = (url or "").strip()
    if not raw:
        console.print("[red]No URL provided.[/red]")
        return

    console.rule(f"[cyan]Website screening[/cyan]")
    console.print(f"[dim]URL:[/dim] {raw}\n")

    agent = WebsiteScreeningAgent()
    reset_vc_llm_telemetry()
    db.init_db()
    existing_mid = db.get_message_id_by_source_url(raw)
    message_id = existing_mid or _website_message_id(raw)
    # Persist a first-class pipeline record early so downstream steps (errors/external) can update status.
    website_email = EmailData(
        message_id=message_id,
        sender_email="website@scanner.local",
        sender_name="Website Scanner",
        subject=f"Website scan: {raw}",
        body=f"Website-only screening for {raw}",
        date=datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
        has_pdf=False,
        source_type="website",
        website_url=raw,
    )
    db.save_deal_email(website_email, status=db.STATUS_NEW)
    from config.llm_cost import OPENAI_MODEL, OPENAI_MODEL_LIGHT
    from utils import cost_control as _cc_web

    db.attach_run_metadata(
        message_id,
        run_id=str(uuid.uuid4()),
        model_heavy=OPENAI_MODEL,
        model_light=OPENAI_MODEL_LIGHT,
        prompt_version=os.getenv("PROMPT_VERSION"),
        rubric_version=os.getenv("RUBRIC_VERSION"),
        scoring_config_version=os.getenv("SCORING_CONFIG_VERSION"),
        code_version=os.getenv("CODE_VERSION") or os.getenv("GIT_COMMIT"),
        external_mode=os.getenv("EXTERNAL_WEB_SEARCH", "auto"),
    )
    _wb, _wr = _cc_web.should_block_stage(
        message_id,
        estimated_extra_usd=_cc_web.estimate_website_pipeline_usd(),
    )
    if _wb:
        console.print(f"[yellow]Cost cap:[/yellow] skipping website pipeline — {_wr}")
        db.save_cost_cap_skip(
            message_id,
            estimated_extra_cost_usd=_cc_web.estimate_website_pipeline_usd(),
            daily_cap_usd=_cc_web.max_cost_per_day_usd(),
            run_cap_usd=_cc_web.max_cost_per_run_usd(),
            reason=_wr,
        )
        db.finish_run(message_id)
        return

    try:
        assessment, gate1, facts_d, scores_out, md = agent.run(raw)
    except Exception as e:
        # Ensure website scans still appear in pipeline on failure.
        db.init_db()
        db.save_deal_email(
            EmailData(
                message_id=message_id,
                sender_email="website@scanner.local",
                sender_name="Website Scanner",
                subject=f"Website scan failed: {raw}",
                body=f"Website-only screening failed for {raw}",
                date=datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
                has_pdf=False,
                source_type="website",
                website_url=raw,
            ),
            status=db.STATUS_NEW,
        )
        db.save_error(
            message_id,
            "WEBSITE_SCAN_ERROR",
            str(e),
            pipeline_status=db.STATUS_ERROR,
        )
        console.print(f"[red]Website scan failed:[/red] {e}")
        console.print(f"[yellow]Saved error to pipeline[/yellow] — message_id: {message_id}")
        db.finish_run(message_id)
        return

    if md.fetch_warnings:
        for w in md.fetch_warnings[:6]:
            console.print(f"[yellow]⚠ {w}[/yellow]")
    console.print(f"[dim]Pages fetched OK:[/dim] {sum(1 for p in md.pages if p.fetch_ok)} / {len(md.pages)}")
    console.print(f"[dim]Extraction quality (heuristic):[/dim] {md.extraction_quality_score}/10")
    ok_pages_for_log = [p for p in md.pages if p.fetch_ok and p.text_length > 50]
    if ok_pages_for_log:
        console.print("[dim]Top fetched pages (URL · chars):[/dim]")
        for p in ok_pages_for_log[:8]:
            console.print(f"  • {p.url}  ·  {p.text_length:,} chars")
    enrichment_notes = list(getattr(agent, "_last_enrichment_notes", []) or [])
    if enrichment_notes:
        console.print("[dim]Deterministic enrichment (post-crawl backfill):[/dim]")
        for note in enrichment_notes[:8]:
            console.print(f"  • {note}")
    console.print()

    screening_depth_web = os.getenv("SCREENING_DEPTH", "INITIAL").strip().upper() or "INITIAL"
    min_vc = float(os.getenv("WEBSITE_EXTERNAL_MIN_VC_SCORE", "5.5"))
    wants_enriched_web = screening_depth_web in ("ENRICHED", "DEEP_DIVE")
    allow_initial_g25 = _bool_env("ALLOW_GATE25_ON_INITIAL_WEBSITE", "0")
    debug_g25_web = _bool_env("DEBUG_OVERRIDE_GATE25", "0")

    enable_external = os.getenv("ENABLE_EXTERNAL_CHECK", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    gate25_candidate = (
        enable_external
        and scores_out is not None
        and assessment.verdict != "REJECT_AUTO"
        and gate1.verdict != "FAIL_CONFIDENT"
        and float(assessment.vc_score or 0) >= min_vc
        and (wants_enriched_web or allow_initial_g25 or debug_g25_web)
    )
    run_website_g25 = gate25_candidate and db.get_deal_status(message_id) != db.STATUS_SKIPPED_COST_CAP
    if run_website_g25 and not debug_g25_web:
        _sw, _sr = _cc_web.should_block_stage(
            message_id,
            estimated_extra_usd=_cc_web.estimate_gate25_usd(),
        )
        _xw, _xr = _cc_web.should_block_external_budget()
        if _sw or _xw:
            console.print(f"[yellow]Gate 2.5 skipped[/yellow] — {_sr or _xr}")
            db.save_cost_cap_skip(
                message_id,
                estimated_extra_cost_usd=_cc_web.estimate_gate25_usd(),
                daily_cap_usd=_cc_web.max_cost_per_day_usd(),
                run_cap_usd=_cc_web.max_cost_per_run_usd(),
                reason=_sr or _xr or "website_gate25_blocked",
            )
            run_website_g25 = False

    external = None
    final_decision = None
    _tel = None
    if run_website_g25:
        console.print("[magenta]External check (Gate 2.5, website mode)…[/magenta]")
        try:
            db.update_status(message_id, db.STATUS_GATE25_RUNNING)
            dim_map = website_dimension_int_scores(scores_out)
            gate2_proxy = gate2_proxy_from_website_dimensions(
                dim_scores=dim_map,
                company_one_liner=str(facts_d.get("one_liner", "")),
                website_overall_score=assessment.website_score,
            )
            ext_facts = website_facts_to_external_dict(facts_d)
            dimensions_json = json.dumps(scores_out.model_dump(), ensure_ascii=False, indent=2)
            external, _tel = run_gate25_external_check(
                facts_dict=ext_facts,
                dimensions_json=dimensions_json,
                gate1=gate1,
                gate2=gate2_proxy,
                screening_mode="website",
            )
            final_before = compute_final_score_before_cap(
                assessment.website_score,
                external.external_score,
                external.risk_penalty,
                screening_mode="website",
            )
            cap_ceiling = 10.0 if external.hard_cap is None else float(external.hard_cap)
            final_score = apply_hard_cap_to_final(final_before, cap_ceiling)
            assessment.external_score = external.external_score
            assessment.final_score = final_score

            gate2_threshold = float(os.getenv("GATE2_PASS_THRESHOLD", "6.0"))
            final_threshold = float(os.getenv("FINAL_PASS_THRESHOLD", "6.3"))
            override_fatal = os.getenv("OVERRIDE_FATAL_KILL_FLAGS", "").lower() in (
                "1",
                "true",
                "yes",
            )
            final_decision = build_final_investment_decision(
                gate1=gate1,
                gate2=gate2_proxy,
                external=external,
                final_score=final_score,
                gate2_threshold=gate2_threshold,
                final_threshold=final_threshold,
                override_fatal=override_fatal,
            )
            n_src = len(getattr(external, "sources", None) or [])
            blended, blend_note = resolve_blended_website_verdict(
                website_verdict=assessment.verdict,
                website_score=assessment.website_score,
                website_llm_confidence=str(assessment.confidence),
                final_score=final_score,
                external_score=float(external.external_score),
                external_confidence=str(external.external_confidence or ""),
                n_sources=n_src,
                provider_unavailable_warning=external.provider_unavailable_warning,
            )
            assessment.blended_verdict = blended
            if blend_note:
                console.print(f"[dim]{blend_note}[/dim]")
        except Exception as e:
            console.print(f"[red]External check failed: {e}[/red]")

    console.print(f"[bold]Company:[/bold] {assessment.company_name or 'Unknown'}")
    console.print(f"[bold]Canonical URL:[/bold] {assessment.website_url}")
    console.print(f"[bold]Gate 1:[/bold] {assessment.gate1_verdict}")
    console.print(
        f"[bold]VC score (capped):[/bold] {assessment.vc_score:.2f}  "
        f"[dim]quality {assessment.quality_score:.2f} · legacy 12-dim raw {assessment.raw_website_score:.2f}[/dim]"
    )
    if getattr(assessment, "why_not_higher", None):
        console.print("[bold]Why not higher (VC layer)[/bold]")
        for line in assessment.why_not_higher[:10]:
            console.print(f"  • {line[:220]}")
    va = getattr(assessment, "vc_analysis", None)
    if va is not None and getattr(va, "must_validate_next", None):
        console.print("\n[bold]Must validate next[/bold]")
        for q in va.must_validate_next[:6]:
            t = getattr(q, "topic", "")
            qq = getattr(q, "question", "")
            wm = getattr(q, "why_it_matters", "")
            console.print(f"  [cyan]{t}[/cyan]: {qq}")
            if wm:
                console.print(f"    [dim]{wm}[/dim]")
    if assessment.external_score is not None:
        console.print(f"[bold]External score:[/bold] {assessment.external_score:.2f}")
    if assessment.final_score is not None:
        console.print(f"[bold]Final score:[/bold] {assessment.final_score:.2f}  [dim](0.45×website + 0.55×external − penalty, then cap)[/dim]")
    console.print(f"[bold]Verdict (website):[/bold] {assessment.verdict}")
    if assessment.blended_verdict:
        console.print(f"[bold]Verdict (blended):[/bold] {assessment.blended_verdict}")
    console.print("\n[bold]Top strengths[/bold]")
    for s in assessment.top_strengths:
        console.print(f"  • {s}")
    console.print("\n[bold]Top risks / concerns[/bold]")
    for s in assessment.top_concerns:
        console.print(f"  • {s}")
    if assessment.missing_critical_data:
        console.print("\n[bold]Missing critical data[/bold]")
        for s in assessment.missing_critical_data[:8]:
            console.print(f"  • {s}")
    if assessment.kill_flags:
        console.print("\n[dim]Kill flags:[/dim] " + ", ".join(assessment.kill_flags[:12]))
    console.print(f"\n[bold]Recommended next step:[/bold] {assessment.recommended_next_step}")
    console.print()
    token_parts = agent.get_telemetry() + get_vc_llm_telemetry()
    _print_token_report(token_parts, title="Token usage (website scan)")

    # Persist website scan into pipeline.db as a first-class pipeline record
    db.init_db()
    db.save_deal_email(
        EmailData(
            message_id=message_id,
            sender_email="website@scanner.local",
            sender_name="Website Scanner",
            subject=f"Website scan: {assessment.company_name or raw}",
            body=f"Website-only screening for {raw}",
            date=datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
            has_pdf=False,
            source_type="website",
            website_url=raw,
        ),
        status=db.STATUS_NEW,
    )
    # Optional stage resolver (no LLM) — only if stage is missing/unknown.
    try:
        from agents.stage_resolver import enabled as _stage_enabled, resolve_stage

        if _stage_enabled():
            st = (gate1.detected_stage or "").strip().lower()
            if st in ("", "unknown", "n/a", "none", "not stated", "missing"):
                sr = resolve_stage(
                    company_name=(assessment.company_name or facts_d.get("company_name") or "startup"),
                    domain=raw,
                    max_results=int(os.getenv("STAGE_RESOLVER_MAX_RESULTS", "6") or "6"),
                )
                if sr.status in ("VERIFIED", "LIKELY") and sr.stage:
                    gate1.detected_stage = sr.stage
    except Exception:
        pass

    # gate1 from website pipeline is normalized to Gate1Result in website_screener
    db.save_gate1(message_id, gate1, telemetry={})
    db.save_website_assessment_details(
        message_id,
        assessment=assessment,
        facts_json=json.dumps(facts_d, ensure_ascii=False),
        website_scores=(scores_out.model_dump() if scores_out is not None else None),
        telemetry_parts=token_parts,
    )

    v = str((assessment.blended_verdict or assessment.verdict) or "")
    inferred_blob = str(facts_d.get("inferred_signals") or "").lower()
    geo_txt = (gate1.detected_geography or "").strip().lower()
    if gate1.geography_match and any(
        x in (geo_txt + " " + inferred_blob)
        for x in (
            "poland", "lithuania", "latvia", "estonia", "croatia", "serbia", "ukraine",
            "romania", "bulgaria", "slovenia", "czech", "hungary", "slovakia", "cee", "diaspora"
        )
    ):
        geo_status = "confirmed_cee"
    elif gate1.geography_match or ("cee" in inferred_blob) or ("diaspora" in inferred_blob):
        geo_status = "possible_cee"
    elif gate1.verdict == "FAIL_CONFIDENT":
        geo_status = "no_cee_signal"
    else:
        geo_status = "unknown"
    geo_decision = apply_fund_geo_rule(
        FundGeoAssessment(
            status=geo_status,
            strongest_signal=gate1.detected_geography or None,
            confidence=0.8 if geo_status == "confirmed_cee" else (0.55 if geo_status == "possible_cee" else 0.3),
            decision="UNCERTAIN",
        )
    )
    stage_class = classify_stage(gate1.detected_stage or "", None, None)
    if stage_class in ("pre-seed", "seed", "seed-extension"):
        stage_decision = "PASS"
    elif stage_class in ("late-seed", "series-a-ready", "series-a", "unknown"):
        stage_decision = "UNCERTAIN"
    else:
        stage_decision = "FAIL"
    sec = (gate1.detected_sector or str(facts_d.get("sector") or "")).lower()
    if not sec or sec in ("unknown", "n/a", "none"):
        sector_decision = "UNCERTAIN"
    elif any(x in sec for x in ("ai", "developer", "dev", "data", "health", "saas", "enterprise", "fintech", "automation", "marketplace", "security")):
        sector_decision = "PASS"
    elif any(x in sec for x in ("agency", "consulting", "services", "gambling", "adult", "real estate", "tobacco")):
        sector_decision = "FAIL"
    else:
        sector_decision = "UNCERTAIN"
    mandate = build_fund_mandate_fit(
        geo_decision=geo_decision,
        stage_decision=stage_decision,
        sector_decision=sector_decision,
        ticket_decision="UNKNOWN",
        software_decision="PASS" if (facts_d.get("product_description") or facts_d.get("one_liner")) else "UNCERTAIN",
    )
    # Hard rule: without confirmed CEE signal, do not allow PASS overall.
    if geo_status != "confirmed_cee" and mandate.overall == "PASS":
        mandate.overall = "UNCERTAIN"
    interest = investment_interest_from_scores(
        product_clarity=int(getattr(scores_out.product_clarity, "score", 5) if scores_out else 5),
        team_signal=int(getattr(scores_out.founder_or_team_signal, "score", 5) if scores_out else 5),
        market_potential=int(getattr(scores_out.market_potential, "score", 5) if scores_out else 5),
        traction_signal=int(getattr(scores_out.traction_evidence, "score", 5) if scores_out else 5),
        distribution_signal=int(getattr(scores_out.distribution_signal, "score", 5) if scores_out else 5),
        defensibility_signal=int(getattr(scores_out.technical_depth_or_defensibility, "score", 5) if scores_out else 5),
        regulatory_risk=5,
    )
    blockers = Blockers(
        has_hard_fail=(gate1.verdict == "FAIL_CONFIDENT" and geo_status == "no_cee_signal"),
        reasons=(["gate1_fail_confident"] if gate1.verdict == "FAIL_CONFIDENT" else []),
    )
    verdict_new = fund_verdict(
        mandate_fit=mandate.overall,
        investment_interest=interest.overall,
        confidence=0.8 if str(getattr(gate1, "confidence", "")).upper() == "HIGH" else 0.6,
        blockers=blockers,
    )
    final_action = map_verdict_to_action(verdict_new)
    if final_decision is not None and getattr(final_decision, "final_verdict", "") == "REJECT_AUTO":
        final_action = "STOP"
    fund_fit_decision = mandate.overall
    deck_evidence_decision = "PASS" if float(assessment.vc_score or 0) >= 6.0 else "NEEDS_MORE_INFO"
    generic_vc_interest = (
        "PASS_TO_CALL" if interest.overall == "HIGH" else
        ("WATCHLIST" if interest.overall in ("MEDIUM_HIGH", "MEDIUM") else "REJECT")
    )

    # VC-first terminal memo (same order as Notion contract)
    console.print("\n[bold]0. Fund Decision[/bold]")
    console.print(f"Company: {assessment.company_name or 'Unknown'}")
    console.print(f"Website: {assessment.website_url}")
    console.print(f"Verdict: {verdict_new}")
    console.print(f"Fund Fit: {mandate.overall}")
    console.print(f"Investment Interest: {interest.overall}")
    console.print(f"Confidence: {'High' if str(getattr(gate1, 'confidence', '')).upper() == 'HIGH' else 'Medium'}")
    console.print(f"Next Action: {final_action}")
    one_reason = (gate1.rejection_reason or assessment.recommended_next_step or "").strip()
    if one_reason:
        console.print(f"One-line reason: {one_reason[:220]}")

    console.print("\n[bold]1. Fund Fit Check[/bold]")
    console.print(f"- Geography / CEE link: {mandate.geography} ({gate1.detected_geography or 'unknown'})")
    console.print(f"- Stage: {mandate.stage} ({gate1.detected_stage or 'unknown'})")
    console.print(f"- Sector: {mandate.sector} ({gate1.detected_sector or facts_d.get('sector') or 'unknown'})")
    console.print(f"- Ticket size: {mandate.ticket_size}")
    console.print(f"- Product software layer: {mandate.software_component}")

    db.save_screening_decisions(
        message_id,
        screening_depth=os.getenv("SCREENING_DEPTH", "INITIAL").strip().upper() or "INITIAL",
        auth_risk="LOW",
        fund_fit_decision=fund_fit_decision,
        deck_evidence_decision=deck_evidence_decision,
        generic_vc_interest=generic_vc_interest,
        final_action=final_action,
        deck_evidence_score=float(assessment.vc_score or 0),
        external_opportunity_score=(float(assessment.external_score) if assessment.external_score is not None else None),
        fund_fit_score=(
            1.0
            if fund_fit_decision == "FAIL"
            else (
                8.0
                if gate1.verdict == "PASS"
                else (6.5 if str(getattr(gate1, "confidence", "") or "").upper() == "HIGH" else 6.0)
            )
        ),
        debug_override_used=False,
        continued_because_debug_override=False,
        test_case=False,
    )
    if external is not None and final_decision is not None:
        status_val = db.STATUS_REJECTED_EXTERNAL if final_decision.final_verdict == "REJECT_AUTO" else db.STATUS_WAITING_HITL
        db.save_gate25(
            message_id,
            external=external,
            final_decision=final_decision,
            status=status_val,
            telemetry=_tel,
        )
    else:
        # Keep top-level status aligned with final action
        if db.get_deal_status(message_id) != db.STATUS_SKIPPED_COST_CAP:
            status_map = {
                "PASS_TO_PARTNER": db.STATUS_WAITING_HITL,
                "ASK_FOR_MORE_INFO": db.STATUS_WAITING_HITL,
                "STOP": db.STATUS_REJECTED_GATE1 if fund_fit_decision == "FAIL" else db.STATUS_REJECTED_GATE2,
            }
            db.update_status(message_id, status_map.get(final_action, db.STATUS_WAITING_HITL))

    notion_auto_sync = os.getenv("NOTION_AUTO_SYNC", "0").strip().lower() in ("1", "true", "yes", "on")
    if notion_auto_sync:
        try:
            from agents.notion_sync import sync_one_deal_to_notion

            s = sync_one_deal_to_notion(
                message_id,
                ensure_schema=os.getenv("NOTION_ENSURE_SCHEMA", "1").strip().lower() in ("1", "true", "yes", "on"),
            )
            console.print(
                "[dim]Notion sync (website scan):[/dim] "
                f"scanned={s.scanned}, created={s.created}, updated={s.updated}, skipped={s.skipped}"
            )
        except Exception as e:
            console.print(f"[yellow]Notion sync (website scan) failed:[/yellow] {e}")

    db.finish_run(message_id)
    console.print(f"[green]Saved to pipeline[/green] — message_id: {message_id}")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "sync-call":
        p = argparse.ArgumentParser(description="One-shot founder call sync to Notion")
        p.add_argument("--company", required=True, help="Company title in Notion deals database")
        p.add_argument("--call-id", default="", help="External call id (dedupe marker), e.g. fireflies id")
        p.add_argument("--source", default="fireflies", help="Call source label, e.g. fireflies/calendly/manual")
        p.add_argument("--title", default="", help="Call title")
        p.add_argument("--url", default="", help="Transcript or recording URL")
        p.add_argument("--date", default="", help="Call date YYYY-MM-DD")
        p.add_argument("--attendees", default="", help="Comma-separated attendees")
        p.add_argument("--summary", default="", help="Short call summary")
        p.add_argument(
            "--tasks",
            default="",
            help="Semicolon-separated tasks to push to NOTION_TASKS_DATABASE_ID (optional)",
        )
        args = p.parse_args(sys.argv[2:])
        from agents.call_sync import sync_founder_call_oneshot

        tasks = [x.strip() for x in str(args.tasks or "").split(";") if x.strip()]
        result = sync_founder_call_oneshot(
            company=args.company,
            source=args.source,
            call_id=args.call_id,
            title=args.title,
            transcript_url=args.url,
            occurred_at=args.date,
            attendees=args.attendees,
            summary=args.summary,
            tasks=tasks,
        )
        console.print(
            "[green]Call sync done[/green] — "
            f"company={result.company} | "
            f"call_appended={result.call_appended} | "
            f"duplicate={result.call_skipped_duplicate} | "
            f"tasks_created={result.tasks_created}"
        )
        return

    if len(sys.argv) >= 3 and sys.argv[1] == "assess-url":
        run_assess_url(sys.argv[2])
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "resolve-hq":
        from agents.hq_resolver import resolve_hq_country

        args = [x for x in sys.argv[2:] if x != "--debug-hq"]
        debug_hq = ("--debug-hq" in sys.argv[2:])
        raw = (args[0] if len(args) >= 1 else "").strip()
        name_arg = (args[1] if len(args) >= 2 else "").strip()
        strict = False
        if not name_arg:
            # Domain-only input is ambiguous; require domain match in snippets to avoid wrong companies.
            strict = True
        name = name_arg or raw
        res = resolve_hq_country(
            domain=raw,
            company_name=name,
            max_results=int(os.getenv("HQ_RESOLVER_MAX_RESULTS", "10") or "10"),
            strict_domain_match=strict,
            debug_hq=debug_hq,
        )
        console.print(res.status)
        console.print(res.summary)
        console.print(
            f"Cost: llm_tokens={int(getattr(res, 'llm_tokens_used', 0) or 0)} | "
            f"search_calls={int(getattr(res, 'search_calls', 0) or 0)} | "
            f"tavily_credits_estimated={int(getattr(res, 'tavily_credits_estimated', 0) or 0)}"
        )
        for e in (res.evidence or [])[:3]:
            console.print(f"- {e.source_type}: {e.raw_quote} ({e.source_url})")
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "resolve-stage":
        from agents.stage_resolver import resolve_stage

        raw = (sys.argv[2] or "").strip()
        name = (sys.argv[3] if len(sys.argv) >= 4 else "").strip() or raw
        res = resolve_stage(
            company_name=name,
            domain=raw,
            max_results=int(os.getenv("STAGE_RESOLVER_MAX_RESULTS", "10") or "10"),
        )
        console.print(res.status)
        console.print(res.summary)
        console.print(
            f"Cost: llm_tokens={int(getattr(res, 'llm_tokens_used', 0) or 0)} | "
            f"search_calls={int(getattr(res, 'search_calls', 0) or 0)} | "
            f"tavily_credits_estimated={int(getattr(res, 'tavily_credits_estimated', 0) or 0)}"
        )
        for e in (res.evidence or [])[:3]:
            console.print(f"- {e.source_type}: {e.raw_quote} ({e.source_url})")
        return

    parser = argparse.ArgumentParser(description="Example VC Fund Screening Agent")
    parser.add_argument("--once", action="store_true", help="Process current emails and exit")
    parser.add_argument("--report", action="store_true", help="Print weekly pipeline report")
    parser.add_argument("--sync-notion", action="store_true", help="Sync recent deals to Notion database")
    parser.add_argument(
        "--notion-prune-tests",
        action="store_true",
        help="When syncing Notion, archive rows with Message ID starting with test_",
    )
    parser.add_argument(
        "--notion-ensure-schema",
        action="store_true",
        help="When syncing Notion, add missing useful pipeline properties automatically",
    )
    parser.add_argument(
        "--notion-prune-columns",
        action="store_true",
        help="When syncing Notion, delete non-essential columns from the Notion database",
    )
    parser.add_argument("--setup", action="store_true", help="Run Gmail OAuth setup")
    parser.add_argument(
        "--test",
        metavar="PDF_FILE",
        help='Test with a local PDF (quote path if it contains & or spaces, e.g. --test "deck A&B.pdf")',
    )
    parser.add_argument("--force", action="store_true", help="With --test: delete legacy test_<stem> row")
    parser.add_argument("--days", type=int, default=7, help="Days for report (default: 7)")
    parser.add_argument(
        "--rescan",
        metavar="MESSAGE_ID",
        help="Re-run scoring for a specific Gmail message id (overwrites the DB row)",
    )
    parser.add_argument(
        "--pick",
        nargs="?",
        const=15,
        type=int,
        help="Pick a recent deal from pipeline.db and rescan (optionally set how many to list, default 15)",
    )
    args = parser.parse_args()

    if args.setup:
        from setup_gmail import run_setup
        run_setup()
    elif args.test:
        run_test_mode(args.test, force=args.force)
    elif args.sync_notion:
        run_sync_notion(
            days=args.days,
            prune_test_rows=args.notion_prune_tests,
            ensure_schema=args.notion_ensure_schema,
            prune_columns=args.notion_prune_columns,
        )
    elif args.report:
        run_report(days=args.days)
    elif args.rescan:
        run_rescan_message(args.rescan)
    elif args.pick is not None:
        run_pick_and_rescan(limit=int(args.pick))
    else:
        run_gmail_loop(once=args.once)


if __name__ == "__main__":
    main()
