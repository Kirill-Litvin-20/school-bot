from aiogram.fsm.state import State, StatesGroup


class ApplicationForm(StatesGroup):
    user_type = State()
    menu = State()

    teacher_subject = State()
    teacher_card = State()
    review_card = State()

    direction_choice = State()
    payment_type_choice = State()
    package_selection = State()
    payment_proof = State()
    entering_promo_code = State()

    name = State()
    school_class = State()
    goal = State()
    lesson_type = State()
    subjects = State()
    teacher_choice = State()
    teacher_name = State()
    contact_method = State()
    contact_value = State()
    comment = State()