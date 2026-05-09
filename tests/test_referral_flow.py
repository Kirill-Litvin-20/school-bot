"""Tests for the referral program: capture, link, discount, bonus, idempotency."""

from __future__ import annotations


def _create_inviter_with_direction(db):
    inviter_sid = db.add_student("Inviter", telegram_id=111, phone=None)
    teacher_id = db.add_teacher_if_not_exists("Teacher Anna")
    db.add_student_lesson(
        inviter_sid, teacher_id, "Математика", lesson_balance=5, tariff_type="package"
    )
    return inviter_sid, teacher_id


def _bring_invitee(db, teacher_id, inviter_tg=111, invitee_tg=222):
    assert db.capture_referral(inviter_tg, invitee_tg) is True
    invitee_sid = db.add_student("Invitee", telegram_id=invitee_tg, phone=None)
    assert db.link_invitee_student(invitee_tg, invitee_sid) is True
    db.add_student_lesson(
        invitee_sid, teacher_id, "Математика", lesson_balance=0, tariff_type="single"
    )
    return invitee_sid


def test_capture_referral_rejects_self_and_duplicate(db):
    assert db.capture_referral(111, 111) is False
    assert db.capture_referral(111, 222) is True
    assert db.capture_referral(333, 222) is False
    row = db.get_referral_by_invitee_telegram_id(222)
    assert row is not None
    assert row[1] == 111
    assert row[4] == "captured"


def test_link_invitee_marks_status_and_backfills_student(db):
    db.capture_referral(111, 222)
    sid = db.add_student("Invitee", telegram_id=222, phone=None)
    assert db.link_invitee_student(222, sid) is True
    row = db.get_referral_by_invitee_telegram_id(222)
    assert row[4] == "student_linked"
    assert row[3] == sid


def test_first_direction_grants_diagnostic_lesson(db):
    sid = db.add_student("Diag", telegram_id=42, phone=None)
    tid = db.add_teacher_if_not_exists("T")
    db.add_student_lesson(sid, tid, "Математика", lesson_balance=0, tariff_type="single")
    directions = db.get_student_directions(sid)
    assert directions[0][3] == 1, "first direction must auto-bump balance to 1"


def test_second_direction_does_not_grant_extra_diagnostic(db):
    sid = db.add_student("Diag2", telegram_id=43, phone=None)
    tid = db.add_teacher_if_not_exists("T2")
    db.add_student_lesson(sid, tid, "Математика", lesson_balance=0, tariff_type="single")
    db.add_student_lesson(sid, tid, "Физика", lesson_balance=0, tariff_type="single")
    balances = [d[3] for d in db.get_student_directions(sid)]
    assert balances == [1, 0]


def test_invitee_discount_active_until_first_paid(db):
    _, teacher_id = _create_inviter_with_direction(db)
    invitee_sid = _bring_invitee(db, teacher_id)
    assert db.get_active_invitee_discount_percent(invitee_sid) == 20
    assert db.attach_first_payment(invitee_sid, payment_request_id=999) is True
    assert db.get_active_invitee_discount_percent(invitee_sid) is None
    assert db.attach_first_payment(invitee_sid, payment_request_id=1000) is False


def test_award_referral_bonus_credits_inviter_oldest_direction(db):
    _, teacher_id = _create_inviter_with_direction(db)
    invitee_sid = _bring_invitee(db, teacher_id)
    db.attach_first_payment(invitee_sid, 999)
    bonus = db.award_referral_bonus_to_inviter(invitee_sid, admin_id=42)
    assert bonus is not None
    assert bonus["lessons_added"] == 1
    assert bonus["inviter_telegram_id"] == 111
    inviter_dirs = db.get_student_directions(
        db.find_students_by_telegram_id(111)[0][0]
    )
    assert inviter_dirs[0][3] == 6, "5 + 1 bonus"
    row = db.get_referral_by_invitee_telegram_id(222)
    assert row[4] == "rewarded"
    assert row[6] is not None  # rewarded_at set


def test_award_referral_bonus_is_idempotent(db):
    _, teacher_id = _create_inviter_with_direction(db)
    invitee_sid = _bring_invitee(db, teacher_id)
    db.attach_first_payment(invitee_sid, 999)
    first = db.award_referral_bonus_to_inviter(invitee_sid)
    second = db.award_referral_bonus_to_inviter(invitee_sid)
    assert first is not None and second is None
    inviter_dirs = db.get_student_directions(
        db.find_students_by_telegram_id(111)[0][0]
    )
    assert inviter_dirs[0][3] == 6  # still 5 + 1, not +2


def test_award_referral_bonus_returns_none_without_inviter_directions(db):
    # inviter exists as student but has zero student_lessons rows
    db.add_student("Inviter no dirs", telegram_id=555, phone=None)
    teacher_id = db.add_teacher_if_not_exists("T")
    db.capture_referral(555, 666)
    invitee_sid = db.add_student("Invitee", telegram_id=666, phone=None)
    db.link_invitee_student(666, invitee_sid)
    db.add_student_lesson(
        invitee_sid, teacher_id, "Математика", lesson_balance=0, tariff_type="single"
    )
    db.attach_first_payment(invitee_sid, 999)
    assert db.award_referral_bonus_to_inviter(invitee_sid) is None
    row = db.get_referral_by_invitee_telegram_id(666)
    assert row[4] == "student_linked", "without inviter direction status stays linked"


def test_finalize_payment_atomic_topup(db):
    sid = db.add_student("S", telegram_id=10, phone=None)
    tid = db.add_teacher_if_not_exists("T")
    db.add_student_lesson(sid, tid, "Математика", lesson_balance=2, tariff_type="single")
    direction_id = db.get_student_directions(sid)[0][0]
    pr_id = db.create_payment_request(
        telegram_user_id=10,
        telegram_username="@s",
        telegram_full_name="S",
        caption_text=None,
        file_id="fid",
        file_type="photo",
    )
    db.try_transition_payment_request_status(pr_id, ["pending"], "processing", admin_id=1)
    assert db.finalize_payment_with_topup(
        payment_request_id=pr_id,
        direction_id=direction_id,
        lessons_count=4,
        admin_id=1,
        comment="test",
    ) is True
    # second call must fail because status moved to approved
    assert db.finalize_payment_with_topup(
        payment_request_id=pr_id,
        direction_id=direction_id,
        lessons_count=4,
        admin_id=1,
    ) is False
    direction_after = db.get_student_lesson_by_id(direction_id)
    # initial 2 (no diagnostic bump because we passed >=1) -> wait, initial was
    # 2 but add_student_lesson runs diagnostic on first dir. Diagnostic only
    # bumps when passed_balance < 1, so 2 stays as 2. + 4 from topup = 6.
    assert direction_after[4] == 6
