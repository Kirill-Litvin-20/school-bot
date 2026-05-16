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
    get_main_menu_keyboard,
    get_subjects_keyboard,
    get_teacher_choice_keyboard,
    get_teachers_keyboard,
    get_user_type_keyboard,
)
from states import ApplicationForm

from .common import (
    build_application_text,
    flow_edit,
    flow_message,
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
    await flow_edit(
        callback, state,
        "👤 <b>Кто оставляет заявку?</b>\n\nВыберите вариант:",
        reply_markup=get_user_type_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ApplicationForm.user_type)
async def get_user_type_text(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if "родител" in text:
        user_type = "Родитель"
    elif "учен" in text:
        user_type = "Ученик"
    else:
        await message.answer("❓ Напишите: <b>ученик</b> или <b>родитель</b>.", parse_mode="HTML")
        return

    await state.update_data(user_type=user_type)
    await state.set_state(ApplicationForm.name)
    await flow_message(message, state, "👤 Как к вам обращаться? Напишите имя.")


@router.callback_query(lambda c: c.data == "back_step")
async def back_step(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()

    if current_state == ApplicationForm.school_class.state:
        await state.set_state(ApplicationForm.name)
        await flow_edit(callback, state, "👤 Как к вам обращаться? Напишите имя.")

    elif current_state == ApplicationForm.goal.state:
        await state.set_state(ApplicationForm.school_class)
        await flow_edit(callback, state, "📚 Выберите класс:", get_class_keyboard())

    elif current_state == ApplicationForm.lesson_type.state:
        await state.set_state(ApplicationForm.goal)
        await flow_edit(callback, state, "🎯 Выберите цель обучения:", get_goal_keyboard())

    elif current_state == ApplicationForm.subjects.state:
        await state.set_state(ApplicationForm.lesson_type)
        await flow_edit(callback, state, "👥 Выберите формат занятий:", get_lesson_type_keyboard())

    elif current_state == ApplicationForm.teacher_choice.state:
        subjects = data.get("subjects", [])
        await state.set_state(ApplicationForm.subjects)
        await flow_edit(callback, state,
                        "📖 Выберите предметы, затем нажмите «Готово»:",
                        get_subjects_keyboard(subjects))

    elif current_state == ApplicationForm.teacher_name.state:
        await state.set_state(ApplicationForm.teacher_choice)
        await flow_edit(callback, state, "👨‍🏫 Как подобрать преподавателя?", get_teacher_choice_keyboard())

    elif current_state == ApplicationForm.contact_method.state:
        from_teacher_card = data.get("from_teacher_card", False)
        teacher_choice = data.get("teacher_choice")
        if from_teacher_card:
            await state.set_state(ApplicationForm.lesson_type)
            await flow_edit(callback, state, "👥 Выберите формат занятий:", get_lesson_type_keyboard())
        elif teacher_choice == "Выбрать конкретного":
            await state.set_state(ApplicationForm.teacher_name)
            await flow_edit(callback, state, "👨‍🏫 Выберите преподавателя:", get_teachers_keyboard())
        else:
            await state.set_state(ApplicationForm.teacher_choice)
            await flow_edit(callback, state, "👨‍🏫 Как подобрать преподавателя?", get_teacher_choice_keyboard())

    elif current_state == ApplicationForm.contact_value.state:
        await state.set_state(ApplicationForm.contact_method)
        await flow_edit(callback, state, "📞 Выберите способ связи:", get_contact_method_keyboard())

    await callback.answer()


@router.message(ApplicationForm.name)
async def get_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(ApplicationForm.school_class)
    await flow_message(message, state, "📚 Выберите класс:", get_class_keyboard())


@router.callback_query(ApplicationForm.school_class, lambda c: c.data.startswith("class_"))
async def get_class(callback: CallbackQuery, state: FSMContext):
    await state.update_data(school_class=callback.data.split("_")[1])
    await state.set_state(ApplicationForm.goal)
    await flow_edit(callback, state, "🎯 Выберите цель обучения:", get_goal_keyboard())
    await callback.answer()


@router.callback_query(ApplicationForm.goal, lambda c: c.data.startswith("goal_"))
async def get_goal(callback: CallbackQuery, state: FSMContext):
    await state.update_data(goal=callback.data.split("_", 1)[1])
    await state.set_state(ApplicationForm.lesson_type)
    await flow_edit(callback, state, "👥 Выберите формат занятий:", get_lesson_type_keyboard())
    await callback.answer()


@router.callback_query(ApplicationForm.lesson_type, lambda c: c.data.startswith("lesson_"))
async def get_lesson_type(callback: CallbackQuery, state: FSMContext):
    lesson_map = {"lesson_individual": "Индивидуально", "lesson_group": "Мини-группа"}
    await state.update_data(lesson_type=lesson_map.get(callback.data, callback.data))

    data = await state.get_data()
    if data.get("from_teacher_card"):
        await state.set_state(ApplicationForm.contact_method)
        await flow_edit(callback, state, "📞 Выберите способ связи:", get_contact_method_keyboard())
    else:
        await state.update_data(subjects=[])
        await state.set_state(ApplicationForm.subjects)
        await flow_edit(callback, state,
                        "📖 Выберите предметы, затем нажмите «Готово»:",
                        get_subjects_keyboard([]))
    await callback.answer()


@router.callback_query(ApplicationForm.subjects, lambda c: c.data.startswith("subject_"))
async def toggle_subject(callback: CallbackQuery, state: FSMContext):
    selected = callback.data.split("_", 1)[1]
    data = await state.get_data()
    subjects = data.get("subjects", [])
    if selected in subjects:
        subjects.remove(selected)
    else:
        subjects.append(selected)
    await state.update_data(subjects=subjects)
    await callback.message.edit_reply_markup(reply_markup=get_subjects_keyboard(subjects))
    await callback.answer()


@router.callback_query(ApplicationForm.subjects, lambda c: c.data == "subjects_done")
async def finish_subjects(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("subjects"):
        await callback.answer("❓ Выберите хотя бы один предмет.", show_alert=True)
        return
    await state.set_state(ApplicationForm.teacher_choice)
    await flow_edit(callback, state, "👨‍🏫 Как подобрать преподавателя?", get_teacher_choice_keyboard())
    await callback.answer()


@router.callback_query(
    ApplicationForm.teacher_choice,
    lambda c: c.data in ["teacher_pick", "teacher_specific"],
)
async def choose_teacher_mode(callback: CallbackQuery, state: FSMContext):
    if callback.data == "teacher_pick":
        await state.update_data(teacher_choice="Подобрать преподавателя", teacher_name="Не выбран")
        await state.set_state(ApplicationForm.contact_method)
        await flow_edit(callback, state, "📞 Выберите способ связи:", get_contact_method_keyboard())
    else:
        await state.update_data(teacher_choice="Выбрать конкретного")
        await state.set_state(ApplicationForm.teacher_name)
        await flow_edit(callback, state, "👨‍🏫 Выберите преподавателя:", get_teachers_keyboard())
    await callback.answer()


@router.callback_query(ApplicationForm.teacher_name, lambda c: c.data.startswith("pick_teacher_"))
async def choose_teacher_name(callback: CallbackQuery, state: FSMContext):
    teachers = get_all_teacher_names()
    try:
        teacher_name = teachers[int(callback.data.split("pick_teacher_", 1)[1])]
    except (ValueError, IndexError):
        await callback.answer("Преподаватель не найден.", show_alert=True)
        return
    await state.update_data(teacher_name=teacher_name)
    await state.set_state(ApplicationForm.contact_method)
    await flow_edit(callback, state, "📞 Выберите способ связи:", get_contact_method_keyboard())
    await callback.answer()


@router.callback_query(
    ApplicationForm.contact_method, lambda c: c.data.startswith("contact_")
)
async def choose_contact_method(callback: CallbackQuery, state: FSMContext):
    contact_method = callback.data.split("_", 1)[1]
    await state.update_data(contact_method=contact_method)
    hint = "@username" if contact_method == "Telegram" else "+79991234567"
    await state.set_state(ApplicationForm.contact_value)
    await flow_edit(callback, state, f"📞 Укажите контакт для связи ({hint}):")
    await callback.answer()


@router.message(ApplicationForm.contact_value)
async def get_contact_value(message: Message, state: FSMContext):
    contact_value = (message.text or "").strip()
    data = await state.get_data()
    contact_method = data.get("contact_method")

    if contact_method == "Telegram" and not is_valid_telegram_username(contact_value):
        await message.answer("❓ Укажите username в формате @username.")
        return
    if contact_method in ("MAX", "Звонок") and not is_valid_phone(contact_value):
        await message.answer("❓ Укажите номер телефона. Пример: +79991234567.")
        return

    await state.update_data(contact_value=contact_value)
    await state.set_state(ApplicationForm.comment)
    await flow_message(
        message, state,
        "💬 Добавьте комментарий (или напишите «-» если не нужен):"
    )


@router.message(ApplicationForm.comment)
async def get_comment(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "назад":
        await state.set_state(ApplicationForm.contact_value)
        await flow_message(message, state, "📞 Укажите контакт для связи:")
        return

    await state.update_data(comment=message.text)
    data = await state.get_data()
    text = build_application_text(data)
    await message.bot.send_message(APPLICATIONS_CHAT_ID, text, parse_mode="HTML")
    await message.answer(
        "✅ Заявка отправлена! Мы свяжемся с вами в ближайшее время.",
        reply_markup=get_main_menu_keyboard(),
    )
    await state.clear()
    await state.set_state(ApplicationForm.menu)
