"""Tests for the per-teacher weekly self-report query."""

from __future__ import annotations


def test_per_teacher_weekly_returns_only_own_lessons(db):
    teacher_a = db.add_teacher_if_not_exists("Anna", telegram_id=1001)
    teacher_b = db.add_teacher_if_not_exists("Boris", telegram_id=1002)
    student = db.add_student("S", telegram_id=2001, phone=None)

    db.add_student_lesson(student, teacher_a, "Математика", lesson_balance=5, tariff_type="package")
    db.add_student_lesson(student, teacher_b, "Физика", lesson_balance=5, tariff_type="package")
    direction_a = db.get_student_directions(student)[0][0]
    direction_b = db.get_student_directions(student)[1][0]

    db.mark_attendance(direction_a, "present", marked_by=1001)
    db.mark_attendance(direction_a, "present", marked_by=1001)
    db.mark_attendance(direction_b, "present", marked_by=1002)

    report_a = db.get_weekly_lessons_report_for_teacher_telegram(1001)
    assert len(report_a) == 1
    assert report_a[0][2] == "Математика"
    assert report_a[0][3] == 2

    report_b = db.get_weekly_lessons_report_for_teacher_telegram(1002)
    assert len(report_b) == 1
    assert report_b[0][2] == "Физика"
    assert report_b[0][3] == 1


def test_per_teacher_weekly_empty(db):
    db.add_teacher_if_not_exists("Empty", telegram_id=999)
    assert db.get_weekly_lessons_report_for_teacher_telegram(999) == []
