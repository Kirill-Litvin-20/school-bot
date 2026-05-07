from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import APPLICATIONS_CHAT_ID
from keyboards import (
    get_all_teacher_names,
    get_class_keyboard,
    get_contact_method_keyboard,
    get_goal_keyboard,
    get_lesson_type_keyboard,
    get_subjects_keyboard,
    get_teacher_choice_keyboard,
    get_teachers_keyboard,
)
from states import ApplicationForm

from .common import (
    build_application_text,
    is_valid_phone,
    is_valid_telegram_username,
    show_main_menu,
)

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_signup")
async def menu_signup(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ApplicationForm.user_type)
    await callback.message.answer("👤 Пожалуйста, напишите, как к Вам обращаться.")
    await state.set_state(ApplicationForm.user_type)
    await callback.answer()


@router.message(ApplicationForm.user_type)
async def get_user_type_text(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if "родител" in text:
        user_type = "Родитель"
    elif "учен" in text:
        user_type = "Ученик"
    else:
        await message.answer("❓ Пожалуйста, напишите: ученик или родитель.")
        return

    await state.update_data(user_type=user_type)
    await message.answer("👤 Пожалуйста, напишите, как к Вам обращаться.")
    await state.set_state(ApplicationForm.name)


@router.callback_query(lambda c: c.data == "back_step")
async def back_step(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()

    if current_state == ApplicationForm.school_class.state:
        await callback.message.answer("👤 Пожалуйста, напишите, как к Вам обращаться.")
        await state.set_state(ApplicationForm.name)

    elif current_state == ApplicationForm.goal.state:
        await callback.message.answer(
            "📚 Пожалуйста, выберите класс:",
            reply_markup=get_class_keyboard(),
        )
        await state.set_state(ApplicationForm.school_class)

    elif current_state == ApplicationForm.lesson_type.state:
        await callback.message.answer(
            "🎯 Пожалуйста, выберите цель обучения:",
            reply_markup=get_goal_keyboard(),
        )
        await state.set_state(ApplicationForm.goal)

    elif current_state == ApplicationForm.subjects.state:
        await callback.message.answer(
            "👥 Пожалуйста, выберите формат занятий:",
            reply_markup=get_lesson_type_keyboard(),
        )
        await state.set_state(ApplicationForm.lesson_type)

    elif current_state == ApplicationForm.teacher_choice.state:
        subjects = data.get("subjects", [])
        await callback.message.answer(
            "📖 Пожалуйста, выберите один или несколько предметов, затем нажмите «Готово»:",
            reply_markup=get_subjects_keyboard(subjects),
        )
        await state.set_state(ApplicationForm.subjects)

    elif current_state == ApplicationForm.teacher_name.state:
        await callback.message.answer(
            "👨‍🏫 Пожалуйста, выберите вариант с преподавателем:",
            reply_markup=get_teacher_choice_keyboard(),
        )
        await state.set_state(ApplicationForm.teacher_choice)

    elif current_state == ApplicationForm.contact_method.state:
        from_teacher_card = data.get("from_teacher_card", False)
        teacher_choice = data.get("teacher_choice")

        if from_teacher_card:
            await callback.message.answer(
                "👥 Пожалуйста, выберите формат занятий:",
                reply_markup=get_lesson_type_keyboard(),
            )
            await state.set_state(ApplicationForm.lesson_type)

        elif teacher_choice == "Выбрать конкретного":
            await callback.message.answer(
                "👨‍🏫 Пожалуйста, выберите преподавателя:",
                reply_markup=get_teachers_keyboard(),
            )
            await state.set_state(ApplicationForm.teacher_name)

        else:
            await callback.message.answer(
                "👨‍🏫 Пожалуйста, выберите вариант с преподавателем:",
                reply_markup=get_teacher_choice_keyboard(),
            )
            await state.set_state(ApplicationForm.teacher_choice)

    elif current_state == ApplicationForm.contact_value.state:
        await callback.message.answer(
            "📞 Пожалуйста, выберите способ связи:",
            reply_markup=get_contact_method_keyboard(),
        )
        await state.set_state(ApplicationForm.contact_method)

    await callback.answer()


@router.message(ApplicationForm.name)
async def get_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📚 Пожалуйста, выберите класс:", reply_markup=get_class_keyboard())
    await state.set_state(ApplicationForm.school_class)


@router.callback_query(ApplicationForm.school_class, lambda c: c.data.startswith("class_"))
async def get_class(callback: CallbackQuery, state: FSMContext):
    selected_class = callback.data.split("_")[1]
    await state.update_data(school_class=selected_class)

    await callback.message.answer(
        "🎯 Пожалуйста, выберите цель обучения:",
        reply_markup=get_goal_keyboard(),
    )
    await state.set_state(ApplicationForm.goal)
    await callback.answer()


@router.callback_query(ApplicationForm.goal, lambda c: c.data.startswith("goal_"))
async def get_goal(callback: CallbackQuery, state: FSMContext):
    selected_goal = callback.data.split("_", 1)[1]
    await state.update_data(goal=selected_goal)

    await callback.message.answer(
        "👥 Пожалуйста, выберите формат занятий:",
        reply_markup=get_lesson_type_keyboard(),
    )
    await state.set_state(ApplicationForm.lesson_type)
    await callback.answer()


@router.callback_query(ApplicationForm.lesson_type, lambda c: c.data.startswith("lesson_"))
async def get_lesson_type(callback: CallbackQuery, state: FSMContext):
    lesson_map = {
        "lesson_individual": "Индивидуально",
        "lesson_group": "Мини-группа",
    }

    selected_lesson_type = lesson_map.get(callback.data, callback.data)
    await state.update_data(lesson_type=selected_lesson_type)

    data = await state.get_data()
    from_teacher_card = data.get("from_teacher_card", False)

    if from_teacher_card:
        await callback.message.answer(
            "📞 Пожалуйста, выберите способ связи:",
            reply_markup=get_contact_method_keyboard(),
        )
        await state.set_state(ApplicationForm.contact_method)
    else:
        await state.update_data(subjects=[])
        await callback.message.answer(
            "📖 Пожалуйста, выберите один или несколько предметов, затем нажмите «Готово»:",
            reply_markup=get_subjects_keyboard([]),
        )
        await state.set_state(ApplicationForm.subjects)

    await callback.answer()


@router.callback_query(ApplicationForm.subjects, lambda c: c.data.startswith("subject_"))
async def toggle_subject(callback: CallbackQuery, state: FSMContext):
    selected_subject = callback.data.split("_", 1)[1]

    data = await state.get_data()
    subjects = data.get("subjects", [])

    if selected_subject in subjects:
        subjects.remove(selected_subject)
    else:
        subjects.append(selected_subject)

    await state.update_data(subjects=subjects)

    await callback.message.edit_reply_markup(
        reply_markup=get_subjects_keyboard(subjects)
    )
    await callback.answer()


@router.callback_query(ApplicationForm.subjects, lambda c: c.data == "subjects_done")
async def finish_subjects(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    subjects = data.get("subjects", [])

    if not subjects:
        await callback.answer("❓ Пожалуйста, выберите хотя бы один предмет.", show_alert=True)
        return

    await callback.message.answer(
        "👨‍🏫 Пожалуйста, выберите вариант с преподавателем:",
        reply_markup=get_teacher_choice_keyboard(),
    )
    await state.set_state(ApplicationForm.teacher_choice)
    await callback.answer()


@router.callback_query(
    ApplicationForm.teacher_choice,
    lambda c: c.data in ["teacher_pick", "teacher_specific"],
)
async def choose_teacher_mode(callback: CallbackQuery, state: FSMContext):
    if callback.data == "teacher_pick":
        await state.update_data(
            teacher_choice="Подобрать преподавателя",
            teacher_name="Не выбран",
        )
        await callback.message.answer(
            "📞 Пожалуйста, выберите способ связи:",
            reply_markup=get_contact_method_keyboard(),
        )
        await state.set_state(ApplicationForm.contact_method)
    else:
        await state.update_data(teacher_choice="Выбрать конкретного")
        await callback.message.answer(
            "👨‍🏫 Пожалуйста, выберите преподавателя:",
            reply_markup=get_teachers_keyboard(),
        )
        await state.set_state(ApplicationForm.teacher_name)

    await callback.answer()


@router.callback_query(ApplicationForm.teacher_name, lambda c: c.data.startswith("teacher_"))
async def choose_teacher_name(callback: CallbackQuery, state: FSMContext):
    teacher_name = callback.data.split("_", 1)[1]
    await state.update_data(teacher_name=teacher_name)

    await callback.message.answer(
        "📞 Пожалуйста, выберите способ связи:",
        reply_markup=get_contact_method_keyboard(),
    )
    await state.set_state(ApplicationForm.contact_method)
    await callback.answer()


@router.callback_query(
    ApplicationForm.contact_method, lambda c: c.data.startswith("contact_")
)
async def choose_contact_method(callback: CallbackQuery, state: FSMContext):
    contact_method = callback.data.split("_", 1)[1]
    await state.update_data(contact_method=contact_method)

    await callback.message.answer("📞 Пожалуйста, укажите контакт для связи:")
    await state.set_state(ApplicationForm.contact_value)
    await callback.answer()


@router.message(ApplicationForm.contact_value)
async def get_contact_value(message: Message, state: FSMContext):
    contact_value = message.text.strip()
    data = await state.get_data()
    contact_method = data.get("contact_method")

    if contact_method == "Telegram":
        if not is_valid_telegram_username(contact_value):
            await message.answer(
                "❓ Пожалуйста, укажите корректный Telegram username в формате @username."
            )
            return

    elif contact_method in ["MAX", "Звонок"]:
        if not is_valid_phone(contact_value):
            await message.answer(
                "❓ Пожалуйста, укажите корректный номер телефона. Пример: +79991234567."
            )
            return

    await state.update_data(contact_value=contact_value)
    await message.answer(
        "💬 Пожалуйста, напишите комментарий. Если комментария нет, укажите: -\n"
        "Чтобы вернуться на предыдущий шаг, напишите: назад"
    )
    await state.set_state(ApplicationForm.comment)


@router.message(ApplicationForm.comment)
async def get_comment(message: Message, state: FSMContext):
    if message.text.strip().lower() == "назад":
        await message.answer("📞 Пожалуйста, укажите контакт для связи:")
        await state.set_state(ApplicationForm.contact_value)
        return

    await state.update_data(comment=message.text)
    data = await state.get_data()

    text = build_application_text(data)

    await message.bot.send_message(APPLICATIONS_CHAT_ID, text, parse_mode="HTML")
    await message.answer(
        "✅ Благодарим, Ваша заявка отправлена. Мы свяжемся с Вами в ближайшее время."
    )
    await show_main_menu(message, state)
