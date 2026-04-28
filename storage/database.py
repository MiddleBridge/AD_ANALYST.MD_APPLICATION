import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent.parent / "pipeline.db"

# Pipeline status values (state machine)
STATUS_NEW = "NEW"
STATUS_GATE1_RUNNING = "GATE1_RUNNING"
STATUS_GATE1_FAILED = "GATE1_FAILED"
STATUS_GATE1_PASSED = "GATE1_PASSED"
STATUS_PDF_DOWNLOAD_FAILED = "PDF_DOWNLOAD_FAILED"
STATUS_PDF_DOWNLOADED = "PDF_DOWNLOADED"
STATUS_PDF_EXTRACTION_FAILED = "PDF_EXTRACTION_FAILED"
STATUS_GATE2_RUNNING = "GATE2_RUNNING"
STATUS_GATE2_FAILED = "GATE2_FAILED"
STATUS_ANALYZED_EMAIL_ONLY = "ANALYZED_EMAIL_ONLY"
STATUS_ANALYZED_WITH_DECK = "ANALYZED_WITH_DECK"
STATUS_WAITING_HITL = "WAITING_HITL"
STATUS_GATE2_INTERNAL_PASS = "GATE2_INTERNAL_PASS"
STATUS_GATE25_RUNNING = "GATE25_RUNNING"
STATUS_REJECTED_EXTERNAL = "REJECTED_EXTERNAL_CHECK"
STATUS_APPROVED_DRAFT_CREATED = "APPROVED_DRAFT_CREATED"
STATUS_REJECTED_DRAFT_CREATED = "REJECTED_DRAFT_CREATED"
STATUS_SKIPPED = "SKIPPED"
STATUS_SKIPPED_COST_CAP = "SKIPPED_COST_CAP"
STATUS_ERROR = "ERROR"
# Backward compat for reporter
STATUS_REJECTED_GATE1 = "REJECTED_GATE1"
STATUS_REJECTED_GATE2 = "REJECTED_GATE2"
STATUS_REJECTED_HITL = "REJECTED_HITL"
STATUS_APPROVED = "APPROVED"

# Terminal / in-flight — used by save_deal_email status preservation + is_already_processed
_TERMINAL_PROCESSING_STATUSES = frozenset(
    {
        STATUS_WAITING_HITL,
        STATUS_GATE2_INTERNAL_PASS,
        STATUS_APPROVED,
        STATUS_REJECTED_HITL,
        STATUS_REJECTED_GATE1,
        STATUS_REJECTED_GATE2,
        STATUS_REJECTED_EXTERNAL,
        STATUS_APPROVED_DRAFT_CREATED,
        STATUS_REJECTED_DRAFT_CREATED,
        STATUS_SKIPPED,
        STATUS_SKIPPED_COST_CAP,
    }
)

_IN_PROGRESS_STATUSES = frozenset(
    {
        STATUS_GATE1_RUNNING,
        STATUS_PDF_DOWNLOADED,
        STATUS_PDF_DOWNLOAD_FAILED,
        STATUS_PDF_EXTRACTION_FAILED,
        STATUS_GATE2_RUNNING,
        STATUS_GATE25_RUNNING,
    }
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    cols = _existing_columns(conn, "deals")
    additions = [
        ("source_url", "TEXT"),
        ("gate1_verdict", "TEXT"),
        ("gate2_analysis_mode", "TEXT"),
        ("last_error_code", "TEXT"),
        ("last_error_detail", "TEXT"),
        ("gate1_started_at", "TEXT"),
        ("gate1_finished_at", "TEXT"),
        ("gate1_latency_ms", "INTEGER"),
        ("gate1_input_tokens", "INTEGER"),
        ("gate1_output_tokens", "INTEGER"),
        ("gate1_cost_usd", "REAL"),
        ("gate2_started_at", "TEXT"),
        ("gate2_finished_at", "TEXT"),
        ("gate2_latency_ms", "INTEGER"),
        ("gate2_input_tokens", "INTEGER"),
        ("gate2_output_tokens", "INTEGER"),
        ("gate2_cost_usd", "REAL"),
        ("gate2_confidence", "TEXT"),
        ("gate2_missing_critical_data", "TEXT"),
        ("gate2_should_ask_founder", "TEXT"),
        ("gate2_facts_json", "TEXT"),
        ("gate2_dimensions_json", "TEXT"),
        ("gate2_quality_flags", "TEXT"),
        ("gate2_snapshot_md", "TEXT"),
        ("screening_depth", "TEXT"),
        ("auth_risk", "TEXT"),
        ("innovo_fit_decision", "TEXT"),
        ("deck_evidence_decision", "TEXT"),
        ("generic_vc_interest", "TEXT"),
        ("final_action", "TEXT"),
        ("deck_evidence_score", "REAL"),
        ("external_opportunity_score", "REAL"),
        ("innovo_fit_score", "REAL"),
        ("debug_override_used", "INTEGER"),
        ("continued_because_debug_override", "INTEGER"),
        ("test_case", "INTEGER"),
        ("hitl_rejection_kind", "TEXT"),
        ("internal_deck_score", "REAL"),
        ("gate25_external_score", "REAL"),
        ("gate25_final_score", "REAL"),
        ("gate25_risk_penalty", "REAL"),
        ("gate25_hard_cap", "REAL"),
        ("gate25_external_json", "TEXT"),
        ("gate25_final_decision_json", "TEXT"),
        # Run / audit / cost control
        ("run_id", "TEXT"),
        ("run_started_at", "TEXT"),
        ("run_finished_at", "TEXT"),
        ("run_total_cost_usd", "REAL"),
        ("estimated_cost_usd", "REAL"),
        ("actual_total_cost_usd", "REAL"),
        ("cost_cap_usd", "REAL"),
        ("cost_cap_triggered", "INTEGER"),
        ("cost_cap_reason", "TEXT"),
        ("prompt_version", "TEXT"),
        ("rubric_version", "TEXT"),
        ("scoring_config_version", "TEXT"),
        ("code_version", "TEXT"),
        ("model_heavy", "TEXT"),
        ("model_light", "TEXT"),
        ("external_mode", "TEXT"),
        ("gate25_started_at", "TEXT"),
        ("gate25_finished_at", "TEXT"),
        ("gate25_latency_ms", "INTEGER"),
        ("gate25_input_tokens", "INTEGER"),
        ("gate25_output_tokens", "INTEGER"),
        ("gate25_cost_usd", "REAL"),
        ("gate25_search_calls", "INTEGER"),
        ("gate25_tavily_credits", "INTEGER"),
    ]
    for name, sql_type in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE deals ADD COLUMN {name} {sql_type}")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            message_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sender_email TEXT,
            sender_name TEXT,
            subject TEXT,
            email_body TEXT,
            source_url TEXT,
            company_name TEXT,
            company_one_liner TEXT,
            has_pdf INTEGER DEFAULT 0,
            pdf_filename TEXT,

            status TEXT DEFAULT 'NEW',

            gate1_status TEXT,
            gate1_geography_match INTEGER,
            gate1_stage_match INTEGER,
            gate1_sector_match INTEGER,
            gate1_detected_stage TEXT,
            gate1_detected_geography TEXT,
            gate1_detected_sector TEXT,
            gate1_rejection_reason TEXT,
            gate1_flags TEXT,
            gate1_confidence TEXT,

            gate2_status TEXT,
            gate2_overall_score REAL,
            gate2_problem_score INTEGER,
            gate2_solution_score INTEGER,
            gate2_market_score INTEGER,
            gate2_business_model_score INTEGER,
            gate2_traction_score INTEGER,
            gate2_team_score INTEGER,
            gate2_ask_score INTEGER,
            gate2_deck_quality_score INTEGER,
            gate2_summary TEXT,
            gate2_strengths TEXT,
            gate2_concerns TEXT,
            gate2_comparable TEXT,
            gate2_recommendation TEXT,
            gate2_recommendation_rationale TEXT,

            hitl_decision TEXT,
            hitl_decided_at TEXT,
            hitl_notes TEXT,
            hitl_rejection_reason TEXT,

            raw_email_json TEXT
        )
    """)
    conn.commit()
    _migrate(conn)
    conn.commit()
    conn.close()


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_deal_status(message_id: str) -> Optional[str]:
    with _conn() as conn:
        row = conn.execute("SELECT status FROM deals WHERE message_id=?", (message_id,)).fetchone()
        if not row or row["status"] is None:
            return None
        return str(row["status"])


def is_terminal_status(status: Optional[str]) -> bool:
    if not status:
        return False
    return status in _TERMINAL_PROCESSING_STATUSES


def get_deal_cost_usd(message_id: str) -> float:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(gate1_cost_usd, 0)
                + COALESCE(gate2_cost_usd, 0)
                + COALESCE(gate25_cost_usd, 0) AS t
            FROM deals WHERE message_id=?
            """,
            (message_id,),
        ).fetchone()
    if not row or row["t"] is None:
        return 0.0
    return float(row["t"])


def get_spend_since_utc_midnight() -> float:
    from datetime import timezone

    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%S")
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(
                COALESCE(gate1_cost_usd, 0)
                + COALESCE(gate2_cost_usd, 0)
                + COALESCE(gate25_cost_usd, 0)
            ), 0) AS s
            FROM deals
            WHERE created_at >= ?
            """,
            (start,),
        ).fetchone()
    if not row or row["s"] is None:
        return 0.0
    return float(row["s"])


def would_exceed_daily_budget(estimated_extra_cost_usd: float, daily_cap_usd: float) -> bool:
    return get_spend_since_utc_midnight() + float(estimated_extra_cost_usd) > float(daily_cap_usd) + 1e-12


def update_actual_total_cost(message_id: str) -> None:
    total = get_deal_cost_usd(message_id)
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE deals SET
                actual_total_cost_usd=?,
                run_total_cost_usd=?,
                updated_at=?
            WHERE message_id=?
            """,
            (total, total, now, message_id),
        )


def count_gate25_completions_since_utc_midnight() -> int:
    from datetime import timezone

    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%S")
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM deals
            WHERE gate25_finished_at IS NOT NULL
              AND gate25_finished_at >= ?
            """,
            (start,),
        ).fetchone()
    if not row or row["c"] is None:
        return 0
    return int(row["c"])


def save_cost_cap_skip(
    message_id: str,
    *,
    estimated_extra_cost_usd: float,
    daily_cap_usd: Optional[float] = None,
    run_cap_usd: Optional[float] = None,
    reason: str,
) -> None:
    now = datetime.utcnow().isoformat()
    cap_val: Optional[float] = None
    if daily_cap_usd is not None and run_cap_usd is not None:
        cap_val = min(float(daily_cap_usd), float(run_cap_usd))
    elif daily_cap_usd is not None:
        cap_val = float(daily_cap_usd)
    elif run_cap_usd is not None:
        cap_val = float(run_cap_usd)
    with _conn() as conn:
        conn.execute(
            """
            UPDATE deals SET
                updated_at=?,
                status=?,
                last_error_code=?,
                last_error_detail=?,
                cost_cap_triggered=?,
                estimated_cost_usd=?,
                cost_cap_usd=?,
                cost_cap_reason=?
            WHERE message_id=?
            """,
            (
                now,
                STATUS_SKIPPED_COST_CAP,
                "COST_CAP",
                (reason or "")[:8000],
                1,
                float(estimated_extra_cost_usd),
                cap_val,
                (reason or "")[:2000],
                message_id,
            ),
        )
    update_actual_total_cost(message_id)


def attach_run_metadata(
    message_id: str,
    *,
    run_id: str,
    model_heavy: Optional[str] = None,
    model_light: Optional[str] = None,
    prompt_version: Optional[str] = None,
    rubric_version: Optional[str] = None,
    scoring_config_version: Optional[str] = None,
    code_version: Optional[str] = None,
    external_mode: Optional[str] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE deals SET
                updated_at=?,
                run_id=?,
                run_started_at=COALESCE(run_started_at, ?),
                model_heavy=COALESCE(?, model_heavy),
                model_light=COALESCE(?, model_light),
                prompt_version=COALESCE(?, prompt_version),
                rubric_version=COALESCE(?, rubric_version),
                scoring_config_version=COALESCE(?, scoring_config_version),
                code_version=COALESCE(?, code_version),
                external_mode=COALESCE(?, external_mode)
            WHERE message_id=?
            """,
            (
                now,
                run_id,
                now,
                model_heavy,
                model_light,
                prompt_version,
                rubric_version,
                scoring_config_version,
                code_version,
                external_mode,
                message_id,
            ),
        )


def finish_run(message_id: str) -> None:
    """Mark run ended and refresh stored totals."""
    now = datetime.utcnow().isoformat()
    update_actual_total_cost(message_id)
    with _conn() as conn:
        conn.execute(
            "UPDATE deals SET updated_at=?, run_finished_at=? WHERE message_id=?",
            (now, now, message_id),
        )


def delete_deal(message_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM deals WHERE message_id=?", (message_id,))


def update_status(message_id: str, status: str) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            "UPDATE deals SET updated_at=?, status=? WHERE message_id=?",
            (now, status, message_id),
        )


def save_error(
    message_id: str,
    code: str,
    detail: str,
    *,
    pipeline_status: Optional[str] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    st = pipeline_status or STATUS_ERROR
    with _conn() as conn:
        conn.execute(
            """UPDATE deals SET updated_at=?, last_error_code=?, last_error_detail=?,
               status=? WHERE message_id=?""",
            (now, code, detail[:8000], st, message_id),
        )


def _should_update_status_on_resave(
    existing: Optional[str], new_status: str, force_status_reset: bool
) -> bool:
    if force_status_reset:
        return True
    if not existing:
        return True
    if is_terminal_status(existing):
        return False
    if existing in _IN_PROGRESS_STATUSES:
        return False
    if existing in (STATUS_NEW, STATUS_ERROR):
        return True
    return False


def save_deal_email(
    email_data, status: str = STATUS_NEW, *, force_status_reset: bool = False
) -> None:
    now = datetime.utcnow().isoformat()
    mid = email_data.message_id
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO deals
            (message_id, created_at, updated_at, sender_email, sender_name,
             subject, email_body, source_url, has_pdf, pdf_filename, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                now,
                now,
                email_data.sender_email,
                email_data.sender_name,
                email_data.subject,
                email_data.body,
                getattr(email_data, "website_url", None),
                1 if email_data.has_pdf else 0,
                email_data.pdf_filename,
                status,
            ),
        )
        row = conn.execute(
            "SELECT status FROM deals WHERE message_id=?",
            (mid,),
        ).fetchone()
        existing_status = str(row["status"]) if row and row["status"] is not None else None
        touch_status = _should_update_status_on_resave(existing_status, status, force_status_reset)

        if touch_status:
            conn.execute(
                """
                UPDATE deals SET
                    updated_at=?,
                    sender_email=?,
                    sender_name=?,
                    subject=?,
                    email_body=?,
                    source_url=?,
                    has_pdf=?,
                    pdf_filename=?,
                    status=?
                WHERE message_id=?
                """,
                (
                    now,
                    email_data.sender_email,
                    email_data.sender_name,
                    email_data.subject,
                    email_data.body,
                    getattr(email_data, "website_url", None),
                    1 if email_data.has_pdf else 0,
                    email_data.pdf_filename,
                    status,
                    mid,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE deals SET
                    updated_at=?,
                    sender_email=?,
                    sender_name=?,
                    subject=?,
                    email_body=?,
                    source_url=?,
                    has_pdf=?,
                    pdf_filename=?
                WHERE message_id=?
                """,
                (
                    now,
                    email_data.sender_email,
                    email_data.sender_name,
                    email_data.subject,
                    email_data.body,
                    getattr(email_data, "website_url", None),
                    1 if email_data.has_pdf else 0,
                    email_data.pdf_filename,
                    mid,
                ),
            )


def get_message_id_by_source_url(source_url: str) -> Optional[str]:
    src = (source_url or "").strip()
    if not src:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT message_id FROM deals WHERE source_url=? ORDER BY updated_at DESC LIMIT 1",
            (src,),
        ).fetchone()
        return str(row["message_id"]) if row and row["message_id"] else None


def save_gate1(
    message_id: str,
    result,
    *,
    telemetry: Optional[dict[str, Any]] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    verdict = getattr(result, "verdict", None)
    fail = verdict == "FAIL_CONFIDENT"
    gate1_status = "FAIL" if fail else "PASS"
    if fail:
        pipeline_status = STATUS_GATE1_FAILED
    else:
        pipeline_status = STATUS_GATE1_PASSED

    tel = telemetry or {}
    with _conn() as conn:
        conn.execute("""
            UPDATE deals SET
                updated_at=?, company_name=?, company_one_liner=?,
                gate1_status=?, gate1_geography_match=?, gate1_stage_match=?,
                gate1_sector_match=?, gate1_detected_stage=?, gate1_detected_geography=?,
                gate1_detected_sector=?, gate1_rejection_reason=?, gate1_flags=?,
                gate1_confidence=?, gate1_verdict=?,
                gate1_started_at=?, gate1_finished_at=?, gate1_latency_ms=?,
                gate1_input_tokens=?, gate1_output_tokens=?, gate1_cost_usd=?,
                status=?
            WHERE message_id=?
        """, (
            now, result.company_name, result.company_one_liner,
            gate1_status, result.geography_match, result.stage_match,
            result.sector_match, result.detected_stage, result.detected_geography,
            result.detected_sector, result.rejection_reason,
            json.dumps(getattr(result, "flags", [])), result.confidence,
            verdict,
            tel.get("started_at"), tel.get("finished_at"), tel.get("latency_ms"),
            tel.get("input_tokens"), tel.get("output_tokens"), tel.get("cost_usd"),
            pipeline_status, message_id,
        ))
    update_actual_total_cost(message_id)


def save_gate2(
    message_id: str,
    result,
    *,
    analysis_mode: str,
    facts_json: Optional[str],
    dimensions_json: str,
    snapshot_md: Optional[str] = None,
    screening_depth: Optional[str] = None,
    auth_risk: Optional[str] = None,
    innovo_fit_decision: Optional[str] = None,
    deck_evidence_decision: Optional[str] = None,
    generic_vc_interest: Optional[str] = None,
    final_action: Optional[str] = None,
    deck_evidence_score: Optional[float] = None,
    external_opportunity_score: Optional[float] = None,
    innovo_fit_score: Optional[float] = None,
    debug_override_used: bool = False,
    continued_because_debug_override: bool = False,
    test_case: bool = False,
    quality_flags: Optional[list[str]] = None,
    telemetry: Optional[dict[str, Any]] = None,
    defer_hitl: bool = False,
) -> None:
    now = datetime.utcnow().isoformat()
    status = "PASS" if result.passes else "FAIL"
    if not result.passes:
        pipeline_status = STATUS_REJECTED_GATE2
    elif defer_hitl:
        pipeline_status = STATUS_GATE2_INTERNAL_PASS
    else:
        pipeline_status = STATUS_WAITING_HITL

    tel = telemetry or {}
    g2_conf = getattr(result, "gate2_confidence", None) or getattr(result, "confidence_g2", None)
    missing = getattr(result, "missing_critical_data", None) or []
    ask_f = getattr(result, "should_ask_founder", None) or []

    with _conn() as conn:
        conn.execute("""
            UPDATE deals SET
                updated_at=?, company_name=?, company_one_liner=?,
                gate2_status=?, gate2_overall_score=?,
                gate2_problem_score=?, gate2_solution_score=?,
                gate2_market_score=?, gate2_business_model_score=?,
                gate2_traction_score=?, gate2_team_score=?,
                gate2_ask_score=?, gate2_deck_quality_score=?,
                gate2_summary=?, gate2_strengths=?, gate2_concerns=?,
                gate2_comparable=?, gate2_recommendation=?,
                gate2_recommendation_rationale=?,
                gate2_analysis_mode=?, gate2_facts_json=?,
                gate2_dimensions_json=?, gate2_quality_flags=?,
                gate2_snapshot_md=?,
                screening_depth=?, auth_risk=?,
                innovo_fit_decision=?, deck_evidence_decision=?,
                generic_vc_interest=?, final_action=?,
                deck_evidence_score=?, external_opportunity_score=?, innovo_fit_score=?,
                debug_override_used=?, continued_because_debug_override=?, test_case=?,
                gate2_confidence=?, gate2_missing_critical_data=?,
                gate2_should_ask_founder=?,
                gate2_started_at=?, gate2_finished_at=?, gate2_latency_ms=?,
                gate2_input_tokens=?, gate2_output_tokens=?, gate2_cost_usd=?,
                internal_deck_score=?,
                status=?
            WHERE message_id=?
        """, (
            now,
            result.company_name or None, result.company_one_liner or None,
            status, result.overall_score,
            result.problem.score if result.problem else None,
            None,
            result.market.score if result.market else None,
            result.business_model.score if result.business_model else None,
            result.traction.score if result.traction else None,
            result.founder_market_fit.score if result.founder_market_fit else None,
            None, None,
            result.executive_summary,
            json.dumps(result.top_strengths),
            json.dumps(result.top_concerns),
            result.comparable_portfolio_company,
            result.recommendation,
            result.recommendation_rationale,
            analysis_mode,
            facts_json,
            dimensions_json,
            json.dumps(quality_flags or []),
            snapshot_md,
            screening_depth,
            auth_risk,
            innovo_fit_decision,
            deck_evidence_decision,
            generic_vc_interest,
            final_action,
            deck_evidence_score,
            external_opportunity_score,
            innovo_fit_score,
            1 if debug_override_used else 0,
            1 if continued_because_debug_override else 0,
            1 if test_case else 0,
            g2_conf,
            json.dumps(list(missing)),
            json.dumps(list(ask_f)),
            tel.get("started_at"), tel.get("finished_at"), tel.get("latency_ms"),
            tel.get("input_tokens"), tel.get("output_tokens"), tel.get("cost_usd"),
            result.overall_score,
            pipeline_status, message_id,
        ))
    update_actual_total_cost(message_id)


def save_gate25(
    message_id: str,
    *,
    external: Any,
    final_decision: Any,
    status: str,
    telemetry: Optional[dict[str, Any]] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    ext_score = float(getattr(external, "external_score", 0) or 0)
    final_s = float(getattr(final_decision, "final_score", 0) or 0)
    rp = float(getattr(external, "risk_penalty", 0) or 0)
    hc = getattr(external, "hard_cap", None)
    hc_val = float(hc) if hc is not None else None

    ext_json = external.model_dump_json() if hasattr(external, "model_dump_json") else json.dumps({})
    fd_json = (
        final_decision.model_dump_json()
        if hasattr(final_decision, "model_dump_json")
        else json.dumps({})
    )

    tel = telemetry or {}

    with _conn() as conn:
        conn.execute(
            """
            UPDATE deals SET
                updated_at=?,
                gate25_external_score=?,
                gate25_final_score=?,
                gate25_risk_penalty=?,
                gate25_hard_cap=?,
                gate25_external_json=?,
                gate25_final_decision_json=?,
                gate25_started_at=?,
                gate25_finished_at=?,
                gate25_latency_ms=?,
                gate25_input_tokens=?,
                gate25_output_tokens=?,
                gate25_cost_usd=?,
                gate25_search_calls=?,
                gate25_tavily_credits=?,
                status=?
            WHERE message_id=?
            """,
            (
                now,
                ext_score,
                final_s,
                rp,
                hc_val,
                ext_json,
                fd_json,
                tel.get("started_at"),
                tel.get("finished_at"),
                tel.get("latency_ms"),
                tel.get("input_tokens"),
                tel.get("output_tokens"),
                tel.get("cost_usd"),
                tel.get("search_calls"),
                tel.get("tavily_credits"),
                status,
                message_id,
            ),
        )
    update_actual_total_cost(message_id)


def save_hitl_decision(message_id: str, decision) -> None:
    now = datetime.utcnow().isoformat()
    hitl_val = "APPROVED" if decision.approved else "REJECTED"
    skip = getattr(decision, "rejection_reason", "") == "SKIPPED — no action taken"
    if skip:
        pipeline_status = STATUS_SKIPPED
    elif decision.approved:
        pipeline_status = STATUS_APPROVED
    else:
        pipeline_status = STATUS_REJECTED_HITL
    kind = getattr(decision, "rejection_kind", None) or ""
    with _conn() as conn:
        conn.execute("""
            UPDATE deals SET
                updated_at=?, hitl_decision=?, hitl_decided_at=?,
                hitl_notes=?, hitl_rejection_reason=?, hitl_rejection_kind=?,
                status=?
            WHERE message_id=?
        """, (
            now, hitl_val, decision.decided_at,
            decision.notes, decision.rejection_reason, kind,
            pipeline_status, message_id,
        ))


def save_screening_decisions(
    message_id: str,
    *,
    screening_depth: str,
    auth_risk: str,
    innovo_fit_decision: str,
    deck_evidence_decision: str,
    generic_vc_interest: str,
    final_action: str,
    deck_evidence_score: Optional[float],
    external_opportunity_score: Optional[float],
    innovo_fit_score: Optional[float],
    debug_override_used: bool = False,
    continued_because_debug_override: bool = False,
    test_case: bool = False,
) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE deals SET
                updated_at=?,
                screening_depth=?,
                auth_risk=?,
                innovo_fit_decision=?,
                deck_evidence_decision=?,
                generic_vc_interest=?,
                final_action=?,
                deck_evidence_score=?,
                external_opportunity_score=?,
                innovo_fit_score=?,
                debug_override_used=?,
                continued_because_debug_override=?,
                test_case=?
            WHERE message_id=?
            """,
            (
                now,
                screening_depth,
                auth_risk,
                innovo_fit_decision,
                deck_evidence_decision,
                generic_vc_interest,
                final_action,
                deck_evidence_score,
                external_opportunity_score,
                innovo_fit_score,
                1 if debug_override_used else 0,
                1 if continued_because_debug_override else 0,
                1 if test_case else 0,
                message_id,
            ),
        )


def _website_summary_top_gaps(
    missing_items: list[Any],
    unclear_blob: str,
    *,
    max_items: int = 3,
    max_total_len: int = 240,
) -> str:
    """Short, single-line gap hint for gate2_summary — never paste multi-line 'missing' blobs."""
    out: list[str] = []
    seen: set[str] = set()
    for x in missing_items or []:
        if len(out) >= max_items:
            break
        line = str(x).strip().split("\n")[0].strip().lstrip("-• ").strip()
        if not line:
            continue
        if len(line) > 72:
            line = line[:69].rstrip() + "…"
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    cb = (unclear_blob or "").strip()
    if len(out) < max_items and cb:
        first = cb.split("\n")[0].strip().lstrip("-• ").strip()
        if first and first.lower() not in seen:
            if len(first) > 72:
                first = first[:69].rstrip() + "…"
            out.append(first)
    if not out:
        return ""
    text = "; ".join(out[:max_items])
    return text[:max_total_len]


def save_website_assessment_details(
    message_id: str,
    *,
    assessment: Any,
    facts_json: str,
    website_scores: Optional[Any] = None,
    telemetry_parts: Optional[list[dict[str, Any]]] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    strengths = json.dumps(list(getattr(assessment, "top_strengths", []) or []))
    concerns = json.dumps(list(getattr(assessment, "top_concerns", []) or []))
    missing_list = list(getattr(assessment, "missing_critical_data", []) or [])
    missing = json.dumps(missing_list)
    followups = json.dumps(list(getattr(assessment, "founder_questions", []) or []))
    kill_flags = json.dumps(list(getattr(assessment, "kill_flags", []) or []))
    vc = getattr(assessment, "vc_analysis", None)
    vc_scores_obj = getattr(vc, "vc_scores", None)
    if hasattr(vc_scores_obj, "model_dump"):
        vc_scores_val = vc_scores_obj.model_dump()
    elif isinstance(vc_scores_obj, dict):
        vc_scores_val = vc_scores_obj
    else:
        vc_scores_val = {}

    scores_dump: dict[str, Any] = {}
    if website_scores is not None:
        if hasattr(website_scores, "model_dump"):
            scores_dump = website_scores.model_dump()
        elif isinstance(website_scores, dict):
            scores_dump = website_scores

    def _jsonable(v: Any) -> Any:
        if v is None:
            return None
        if hasattr(v, "model_dump"):
            try:
                return v.model_dump()
            except Exception:
                return str(v)
        if isinstance(v, (str, int, float, bool, dict, list)):
            return v
        return str(v)

    ev_table = getattr(assessment, "evidence_table", None)
    ev_val: list[Any] = []
    if isinstance(ev_table, list):
        for row in ev_table:
            ev_val.append(_jsonable(row))
    elif ev_table is not None:
        out = _jsonable(ev_table)
        ev_val = out if isinstance(out, list) else [out]

    result_blob = {
        "company_name": str(getattr(assessment, "company_name", "") or ""),
        "canonical_url": str(getattr(assessment, "website_url", "") or ""),
        "vc_score": float(getattr(assessment, "vc_score", 0.0) or 0.0),
        "quality_score": float(getattr(assessment, "quality_score", 0.0) or 0.0),
        "raw_website_score": float(getattr(assessment, "raw_website_score", 0.0) or 0.0),
        "top_strengths": list(getattr(assessment, "top_strengths", []) or []),
        "strengths": list(getattr(assessment, "top_strengths", []) or []),
        "top_risks": list(getattr(assessment, "top_concerns", []) or []),
        "top_concerns": list(getattr(assessment, "top_concerns", []) or []),
        "follow_up_questions": list(getattr(assessment, "founder_questions", []) or []),
        "follow_ups": list(getattr(assessment, "founder_questions", []) or []),
        "why_not_higher": list(getattr(assessment, "why_not_higher", []) or []),
        "must_validate_next": [
            {
                "topic": str(getattr(x, "topic", "") or ""),
                "question": str(getattr(x, "question", "") or ""),
                "why_it_matters": str(getattr(x, "why_it_matters", "") or ""),
            }
            for x in (getattr(getattr(assessment, "vc_analysis", None), "must_validate_next", None) or [])
        ],
        "kill_flags": list(getattr(assessment, "kill_flags", []) or []),
        "red_flags": list(getattr(assessment, "kill_flags", []) or []),
        "recommended_next_step": str(getattr(assessment, "recommended_next_step", "") or ""),
        "verdict": str(getattr(assessment, "verdict", "") or ""),
        "telemetry_parts": list(telemetry_parts or []),
        "website_scores": scores_dump,
        "evidence_table": ev_val,
        "market_saturation": getattr(vc, "market_saturation", None),
        "timing_score": getattr(vc, "timing_score", None),
        "competition_density": getattr(vc, "competition_density", None),
        "vc_scores": vc_scores_val,
    }
    verdict = str(getattr(assessment, "blended_verdict", None) or getattr(assessment, "verdict", "") or "")
    next_step = str(getattr(assessment, "recommended_next_step", "") or "")
    one_liner = str(
        getattr(assessment, "company_one_liner", "")
        or ""
    )
    # Website runs often don't have a separate summary; keep a short deterministic one in gate2_summary
    # so Notion "Executive summary" isn't just the next-step sentence.
    summary = ""
    try:
        fd = json.loads(facts_json) if facts_json else {}
        if isinstance(fd, dict):
            what = str(fd.get("product_description") or fd.get("one_liner") or "").strip()
            icp = str(fd.get("target_customer") or "").strip()
            team = str(fd.get("founders") or fd.get("team_signals") or "").strip()
            proof = str(fd.get("customer_proof") or fd.get("logos_or_case_studies") or "").strip()
            miss_blob = str(fd.get("unclear_or_missing_data") or "").strip()
            parts: list[str] = []
            if what:
                parts.append(what)
            if icp:
                parts.append(f"ICP: {icp}")
            if proof:
                parts.append(f"Proof: {proof}")
            if team:
                parts.append(f"Team: {team}")
            gaps = _website_summary_top_gaps(missing_list, miss_blob)
            if gaps:
                parts.append(f"Top gaps (site): {gaps}")
            summary = " · ".join(parts)[:950]
    except Exception:
        summary = ""
    confidence = str(getattr(assessment, "confidence", "") or "")
    tel = telemetry_parts or []
    in_tok = sum(int(p.get("input_tokens", 0) or 0) for p in tel)
    out_tok = sum(int(p.get("output_tokens", 0) or 0) for p in tel)
    cost = sum(float(p.get("cost_usd", 0.0) or 0.0) for p in tel)
    with _conn() as conn:
        conn.execute(
            """
            UPDATE deals SET
                updated_at=?,
                company_name=COALESCE(NULLIF(?, ''), company_name),
                company_one_liner=COALESCE(NULLIF(?, ''), company_one_liner),
                gate2_recommendation=?,
                gate2_recommendation_rationale=?,
                gate2_summary=?,
                gate2_strengths=?,
                gate2_concerns=?,
                gate2_confidence=?,
                gate2_missing_critical_data=?,
                gate2_should_ask_founder=?,
                gate2_quality_flags=?,
                gate2_facts_json=?,
                gate2_dimensions_json=?,
                gate2_input_tokens=?,
                gate2_output_tokens=?,
                gate2_cost_usd=?
            WHERE message_id=?
            """,
            (
                now,
                str(getattr(assessment, "company_name", "") or ""),
                one_liner,
                verdict,
                next_step,
                summary or next_step,
                strengths,
                concerns,
                confidence,
                missing,
                followups,
                kill_flags,
                facts_json,
                json.dumps(result_blob, ensure_ascii=False),
                in_tok,
                out_tok,
                cost,
                message_id,
            ),
        )
    update_actual_total_cost(message_id)


def is_already_processed(message_id: str) -> bool:
    return is_terminal_status(get_deal_status(message_id))


def deal_exists(message_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 AS o FROM deals WHERE message_id=? LIMIT 1",
            (message_id,),
        ).fetchone()
        return row is not None


def count_deals_since_utc_midnight() -> int:
    from datetime import timezone
    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%S")
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM deals WHERE created_at >= ?",
            (start,),
        ).fetchone()
        if row is None:
            return 0
        return int(row[0])


def get_pipeline_summary(days: int = 7) -> list:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT company_name, sender_name, sender_email, status,
                   gate1_verdict, gate1_rejection_reason, gate1_confidence,
                   gate2_overall_score, gate2_recommendation,
                   gate1_detected_sector, gate1_detected_geography,
                   gate1_latency_ms, gate2_latency_ms,
                   gate1_cost_usd, gate2_cost_usd,
                   created_at
            FROM deals
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]


def get_deals_for_notion(days: int = 30) -> list:
    """Rows needed for lightweight Notion pipeline sync."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT
                message_id,
                company_name,
                company_one_liner,
                sender_name,
                sender_email,
                subject,
                source_url,
                pdf_filename,
                has_pdf,
                status,
                gate1_verdict,
                gate1_rejection_reason,
                gate2_overall_score,
                gate2_summary,
                gate2_recommendation,
                gate2_recommendation_rationale,
                gate2_strengths,
                gate2_concerns,
                gate2_missing_critical_data,
                gate2_should_ask_founder,
                gate2_quality_flags,
                gate2_facts_json,
                gate2_dimensions_json,
                gate2_snapshot_md,
                screening_depth,
                auth_risk,
                innovo_fit_decision,
                deck_evidence_decision,
                generic_vc_interest,
                final_action,
                deck_evidence_score,
                external_opportunity_score,
                innovo_fit_score,
                debug_override_used,
                continued_because_debug_override,
                test_case,
                gate2_input_tokens,
                gate2_output_tokens,
                gate2_cost_usd,
                gate1_detected_sector,
                gate1_detected_geography,
                gate1_detected_stage,
                created_at,
                updated_at
            FROM deals
            WHERE created_at >= datetime('now', ?)
            ORDER BY updated_at DESC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_deal_for_notion(message_id: str) -> Optional[dict[str, Any]]:
    """Single-row fetch for upsert."""
    mid = (message_id or "").strip()
    if not mid:
        return None
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT
                message_id,
                company_name,
                company_one_liner,
                sender_name,
                sender_email,
                subject,
                source_url,
                pdf_filename,
                has_pdf,
                status,
                gate1_verdict,
                gate1_rejection_reason,
                gate2_overall_score,
                gate2_summary,
                gate2_recommendation,
                gate2_recommendation_rationale,
                gate2_strengths,
                gate2_concerns,
                gate2_missing_critical_data,
                gate2_should_ask_founder,
                gate2_quality_flags,
                gate2_facts_json,
                gate2_dimensions_json,
                gate2_snapshot_md,
                screening_depth,
                auth_risk,
                innovo_fit_decision,
                deck_evidence_decision,
                generic_vc_interest,
                final_action,
                deck_evidence_score,
                external_opportunity_score,
                innovo_fit_score,
                debug_override_used,
                continued_because_debug_override,
                test_case,
                gate2_input_tokens,
                gate2_output_tokens,
                gate2_cost_usd,
                gate1_detected_sector,
                gate1_detected_geography,
                gate1_detected_stage,
                created_at,
                updated_at
            FROM deals
            WHERE message_id = ?
            """,
            (mid,),
        ).fetchone()
        return dict(row) if row else None


def get_recent_deals(limit: int = 15, *, include_tests: bool = False) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT
                message_id,
                company_name,
                subject,
                sender_email,
                status,
                pdf_filename,
                updated_at
            FROM deals
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            mid = str(d.get("message_id") or "")
            if not include_tests and mid.lower().startswith("test_"):
                continue
            out.append(d)
        return out


def get_approved_deals() -> list:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT * FROM deals WHERE status IN ('APPROVED', 'APPROVED_DRAFT_CREATED')
            ORDER BY created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
