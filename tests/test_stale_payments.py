"""Tests for the auto-expire of stale payment requests."""

from __future__ import annotations

from datetime import datetime, timedelta


def _make_payment(db, telegram_user_id: int = 100, age_days: int = 0) -> int:
    pr_id = db.create_payment_request(
        telegram_user_id=telegram_user_id,
        telegram_username=f"@u{telegram_user_id}",
        telegram_full_name=f"User {telegram_user_id}",
        caption_text=None,
        file_id="fid",
        file_type="photo",
    )
    if age_days > 0:
        # Forge created_at into the past so the worker considers the row stale.
        cur = db.get_connection().cursor()
        try:
            past = (datetime.now() - timedelta(days=age_days)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            cur.execute(
                "UPDATE payment_requests SET created_at = ? WHERE id = ?",
                (past, pr_id),
            )
            cur.connection.commit()
        finally:
            cur.connection.close()
    return pr_id


def test_get_stale_returns_only_old_pending_or_processing(db):
    fresh_id = _make_payment(db, telegram_user_id=1, age_days=0)
    old_pending = _make_payment(db, telegram_user_id=2, age_days=40)
    old_processing = _make_payment(db, telegram_user_id=3, age_days=40)
    db.try_transition_payment_request_status(
        old_processing, ["pending"], "processing", admin_id=999
    )
    old_approved = _make_payment(db, telegram_user_id=4, age_days=40)
    db.try_transition_payment_request_status(
        old_approved, ["pending"], "processing", admin_id=999
    )
    sid = db.add_student("S", telegram_id=4, phone=None)
    tid = db.add_teacher_if_not_exists("T")
    db.add_student_lesson(sid, tid, "Математика", lesson_balance=0, tariff_type="single")
    direction_id = db.get_student_directions(sid)[0][0]
    db.finalize_payment_with_topup(
        old_approved, direction_id, lessons_count=1, admin_id=999
    )

    stale = db.get_stale_pending_payment_requests(older_than_days=30)
    stale_ids = [row[0] for row in stale]
    assert fresh_id not in stale_ids, "fresh payment must not be stale"
    assert old_approved not in stale_ids, "approved payment must not be stale"
    assert old_pending in stale_ids
    assert old_processing in stale_ids


def test_transition_expired_idempotent(db):
    pid = _make_payment(db, telegram_user_id=10, age_days=40)
    assert db.try_transition_payment_request_status(
        pid, ["pending", "processing"], "expired"
    ) is True
    assert db.try_transition_payment_request_status(
        pid, ["pending", "processing"], "expired"
    ) is False
    row = db.get_payment_request_by_id(pid)
    assert row[7] == "expired"
