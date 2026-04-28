"""Cost caps, run metadata, migrations, and status preservation."""

from __future__ import annotations

from datetime import datetime

import pytest

import storage.database as database


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_pipeline.db")
    database.init_db()
    return tmp_path / "test_pipeline.db"


class _EmailStub:
    def __init__(
        self,
        message_id: str,
        *,
        sender_email: str = "x@y.com",
        sender_name: str = "Founder",
        subject: str = "Pitch",
        body: str = "Pitch deck",
        has_pdf: bool = False,
        pdf_filename: str | None = None,
        website_url: str | None = None,
    ):
        self.message_id = message_id
        self.sender_email = sender_email
        self.sender_name = sender_name
        self.subject = subject
        self.body = body
        self.has_pdf = has_pdf
        self.pdf_filename = pdf_filename
        self.website_url = website_url


def test_init_db_has_new_columns(tmp_db):
    conn = database._conn()
    cols = database._existing_columns(conn, "deals")
    conn.close()
    for name in (
        "run_id",
        "gate25_cost_usd",
        "actual_total_cost_usd",
        "cost_cap_triggered",
    ):
        assert name in cols


def test_save_deal_email_preserves_terminal_hitl(tmp_db):
    mid = "msg_terminal_x"
    ed = _EmailStub(mid)
    database.save_deal_email(ed, status=database.STATUS_NEW)
    conn = database._conn()
    conn.execute(
        "UPDATE deals SET status=? WHERE message_id=?",
        (database.STATUS_WAITING_HITL, mid),
    )
    conn.commit()
    conn.close()

    database.save_deal_email(ed, status=database.STATUS_NEW)
    assert database.get_deal_status(mid) == database.STATUS_WAITING_HITL


def test_cost_aggregation(tmp_db):
    mid = "msg_cost_agg"
    ed = _EmailStub(mid)
    database.save_deal_email(ed, status=database.STATUS_NEW)
    conn = database._conn()
    conn.execute(
        """UPDATE deals SET gate1_cost_usd=?, gate2_cost_usd=?, gate25_cost_usd=?
           WHERE message_id=?""",
        (0.1, 0.2, 0.05, mid),
    )
    conn.commit()
    conn.close()

    assert abs(database.get_deal_cost_usd(mid) - 0.35) < 1e-9
    database.update_actual_total_cost(mid)
    conn = database._conn()
    row = conn.execute(
        "SELECT actual_total_cost_usd, run_total_cost_usd FROM deals WHERE message_id=?",
        (mid,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert abs(row["actual_total_cost_usd"] - 0.35) < 1e-9
    assert abs(row["run_total_cost_usd"] - 0.35) < 1e-9


def test_get_spend_since_utc_midnight(tmp_db):
    now = datetime.utcnow().isoformat()
    mid = "msg_daily"
    ed = _EmailStub(mid)
    database.save_deal_email(ed, status=database.STATUS_NEW)
    conn = database._conn()
    conn.execute(
        """UPDATE deals SET created_at=?, gate1_cost_usd=?, gate2_cost_usd=? WHERE message_id=?""",
        (now, 0.03, 0.07, mid),
    )
    conn.commit()
    conn.close()

    spend = database.get_spend_since_utc_midnight()
    assert spend >= 0.09


def test_save_cost_cap_skip(tmp_db):
    mid = "msg_cap_z"
    ed = _EmailStub(mid)
    database.save_deal_email(ed, status=database.STATUS_NEW)
    database.save_cost_cap_skip(
        mid,
        estimated_extra_cost_usd=0.5,
        daily_cap_usd=20.0,
        run_cap_usd=1.0,
        reason="RUN_CAP projected",
    )
    assert database.get_deal_status(mid) == database.STATUS_SKIPPED_COST_CAP
    conn = database._conn()
    row = conn.execute(
        "SELECT last_error_code, cost_cap_triggered FROM deals WHERE message_id=?",
        (mid,),
    ).fetchone()
    conn.close()
    assert row["last_error_code"] == "COST_CAP"
    assert row["cost_cap_triggered"] == 1


def test_is_already_processed_terminal_only(tmp_db):
    mid = "msg_new"
    ed = _EmailStub(mid)
    database.save_deal_email(ed, status=database.STATUS_NEW)
    assert database.is_already_processed(mid) is False
    assert database.deal_exists(mid) is True

    conn = database._conn()
    conn.execute(
        "UPDATE deals SET status=? WHERE message_id=?",
        (database.STATUS_WAITING_HITL, mid),
    )
    conn.commit()
    conn.close()
    assert database.is_already_processed(mid) is True

