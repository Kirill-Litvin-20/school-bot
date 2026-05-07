#!/usr/bin/env python3
"""
Скрипт для скрытия админа из ЛК учеников.
Используется для скрытия дублирующихся админов (например, оставляем только основной аккаунт).
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from shared.database import set_admin_visibility, get_users_by_role, get_connection


def main():
    print("🔍 Поиск администраторов...")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT telegram_id, full_name
        FROM users
        WHERE role IN ('admin', 'superadmin')
          AND is_active = 1
        ORDER BY full_name
        """
    )
    admins = cur.fetchall()
    conn.close()

    if not admins:
        print("❌ Администраторов не найдено")
        return

    print("\n📋 Список администраторов:")
    for i, (telegram_id, full_name) in enumerate(admins, 1):
        print(f"{i}. {full_name} (ID: {telegram_id})")

    print("\n")
    try:
        choice = input("Введите номер админа, которого нужно скрыть из ЛК учеников (0 для отмены): ").strip()
        if choice == "0":
            print("❌ Отмено")
            return

        idx = int(choice) - 1
        if idx < 0 or idx >= len(admins):
            print("❌ Неверный номер")
            return

        telegram_id, full_name = admins[idx]

        confirm = input(f"\n⚠️ Скрыть '{full_name}' из ЛК учеников? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("❌ Отмено")
            return

        if set_admin_visibility(telegram_id, is_visible=False):
            print(f"✅ Админ '{full_name}' скрыт из ЛК учеников")
        else:
            print(f"❌ Не удалось скрыть админа")

    except (ValueError, KeyboardInterrupt):
        print("\n❌ Отмено")


if __name__ == "__main__":
    main()
