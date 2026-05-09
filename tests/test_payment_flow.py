def test_payment_finalize_is_idempotent(db):
    student_id = db.add_student(
        full_name="Petr Petrov",
        telegram_id=10002,
        phone="+79990002233",
    )
    teacher_id = db.add_teacher_if_not_exists("Ekaterina Smirnova", telegram_id=20002)
    db.add_student_lesson(
        student_id=student_id,
        teacher_id=teacher_id,
        subject_name="Social Studies",
        lesson_balance=2,
        tariff_type="single",
    )
    direction_id = db.get_student_directions(student_id)[0][0]

    payment_request_id = db.create_payment_request(
        telegram_user_id=10002,
        telegram_username="@petrov",
        telegram_full_name="Petr Petrov",
        caption_text="payment for lessons",
        file_id="fake-file-id",
        file_type="photo",
    )

    transitioned = db.try_transition_payment_request_status(
        payment_request_id=payment_request_id,
        allowed_from_statuses=["pending"],
        new_status="processing",
        admin_id=90001,
    )
    assert transitioned is True

    finalized_first = db.finalize_payment_with_topup(
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_count=4,
        admin_id=90001,
        comment="Test approval",
    )
    assert finalized_first is True

    finalized_second = db.finalize_payment_with_topup(
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_count=4,
        admin_id=90001,
        comment="Retry",
    )
    assert finalized_second is False

    updated_direction = db.get_student_lesson_by_id(direction_id)
    assert updated_direction is not None
    assert updated_direction[4] == 6

    payment = db.get_payment_request_by_id(payment_request_id)
    assert payment is not None
    assert payment[7] == "approved"


def test_recent_payment_history_for_student(db):
    student_id = db.add_student(
        full_name="Anna Petrova",
        telegram_id=10003,
        phone="+79990003344",
    )
    teacher_id = db.add_teacher_if_not_exists("Darya Petrova", telegram_id=20003)
    db.add_student_lesson(
        student_id=student_id,
        teacher_id=teacher_id,
        subject_name="Russian",
        lesson_balance=3,
        tariff_type="package",
    )
    direction_id = db.get_student_directions(student_id)[0][0]

    approved_payment_id = db.create_payment_request(
        telegram_user_id=10003,
        telegram_username="@anna",
        telegram_full_name="Anna Petrova",
        caption_text="April payment",
        file_id="approved-file-id",
        file_type="photo",
    )
    db.try_transition_payment_request_status(
        payment_request_id=approved_payment_id,
        allowed_from_statuses=["pending"],
        new_status="processing",
        admin_id=90001,
    )
    db.finalize_payment_with_topup(
        payment_request_id=approved_payment_id,
        direction_id=direction_id,
        lessons_count=4,
        admin_id=90001,
        comment=f"Начисление после подтверждения оплаты #{approved_payment_id}",
    )

    pending_payment_id = db.create_payment_request(
        telegram_user_id=10003,
        telegram_username="@anna",
        telegram_full_name="Anna Petrova",
        caption_text="May payment",
        file_id="pending-file-id",
        file_type="photo",
    )

    history = db.get_recent_payment_history_by_telegram_user(10003, limit=4)

    assert len(history) == 2
    assert history[0][0] == pending_payment_id
    assert history[0][1] == "pending"
    assert history[0][5] == 0
    assert history[1][0] == approved_payment_id
    assert history[1][1] == "approved"
    assert history[1][5] == 4


def test_attendance_writeoff_can_create_debt(db):
    student_id = db.add_student(
        full_name="Olga Sidorova",
        telegram_id=10004,
        phone="+79990004455",
    )
    teacher_id = db.add_teacher_if_not_exists("Irina Volkova", telegram_id=20004)
    # First direction auto-bumps balance to 1 (diagnostic lesson). To force a
    # debt we need to consume two lessons: first attendance brings balance to 0,
    # second one brings it into the negative territory.
    db.add_student_lesson(
        student_id=student_id,
        teacher_id=teacher_id,
        subject_name="Mathematics",
        lesson_balance=0,
        tariff_type="single",
    )
    direction_id = db.get_student_directions(student_id)[0][0]

    db.mark_attendance(direction_id=direction_id, status="present", marked_by=90001)
    db.mark_attendance(direction_id=direction_id, status="present", marked_by=90001)

    updated_direction = db.get_student_lesson_by_id(direction_id)
    assert updated_direction is not None
    assert updated_direction[4] == -1

    writeoff_rows = [
        row
        for row in db.get_balance_history_by_student(student_id)
        if row[4] == "attendance_writeoff"
    ]
    assert len(writeoff_rows) == 2
    assert all(row[5] == -1 for row in writeoff_rows)
