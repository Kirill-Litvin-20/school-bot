"""Tests for the admin dashboard aggregator."""

from __future__ import annotations


def test_dashboard_zero_state(db):
    metrics = db.get_admin_dashboard_metrics()
    for value in metrics.values():
        assert value == 0


def test_dashboard_counts_pending_and_debt(db):
    sid = db.add_student("Debtor", telegram_id=1, phone=None)
    tid = db.add_teacher_if_not_exists("T")
    db.add_student_lesson(sid, tid, "Математика", lesson_balance=0, tariff_type="single")
    direction_id = db.get_student_directions(sid)[0][0]
    # Diagnostic gives +1; consume two lessons to push the balance to -1 (debt).
    db.mark_attendance(direction_id, "present", marked_by=42)
    db.mark_attendance(direction_id, "present", marked_by=42)

    db.create_payment_request(
        telegram_user_id=1,
        telegram_username="@d",
        telegram_full_name="Debtor",
        caption_text=None,
        file_id="fid",
        file_type="photo",
    )
    pid = db.create_payment_request(
        telegram_user_id=2,
        telegram_username="@d2",
        telegram_full_name="Other",
        caption_text=None,
        file_id="fid",
        file_type="photo",
    )
    db.try_transition_payment_request_status(pid, ["pending"], "processing", admin_id=999)

    metrics = db.get_admin_dashboard_metrics()
    assert metrics["payments_pending"] == 1
    assert metrics["payments_processing"] == 1
    assert metrics["new_students_week"] == 1
    assert metrics["debtors_count"] == 1
    assert metrics["debt_lessons_total"] == 1
    assert metrics["lessons_attended_week"] == 2


def test_dashboard_referrals_aggregate(db):
    db.capture_referral(101, 202)
    db.capture_referral(101, 203)
    sid = db.add_student("Linked", telegram_id=202, phone=None)
    db.link_invitee_student(202, sid)

    metrics = db.get_admin_dashboard_metrics()
    assert metrics["referrals_captured"] == 1
    assert metrics["referrals_linked"] == 1
    assert metrics["referrals_rewarded"] == 0
