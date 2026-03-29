#!/usr/bin/env python3
"""
Интерактивная первичная настройка (по мотивам ТЗ): .env, проверка токена, init_db.

Не настраивает webhook — бот по умолчанию работает через long polling (см. bot.py).
Запуск: python installer.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def _prompt(msg: str, default: str | None = None, secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    if secret:
        try:
            import getpass

            raw = getpass.getpass(f"{msg}{hint}: ")
        except Exception:
            raw = input(f"{msg}{hint}: ")
    else:
        raw = input(f"{msg}{hint}: ")
    raw = (raw or "").strip()
    if not raw and default is not None:
        return default
    return raw


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)
    env_path = root / ".env"
    example = root / ".env.example"

    print("=== MAX Support Bot — установщик ===\n")

    env_type = _prompt("Среда: 1) свой VPS/ПК  2) хостинг вроде Jino (только подсказки)", "1")
    if env_type.startswith("2"):
        print(
            "\nПодсказка: на Jino обычно задают переменные окружения в панели и запускают "
            "бот через python bot.py или supervisor/systemd. Webhook при необходимости — отдельный сервис.\n"
        )

    token = _prompt("Токен бота MAX (BOT_TOKEN)", secret=True)
    if not token:
        print("Токен обязателен.")
        return 1

    admin_plain = _prompt("Пароль администратора (/admin) — будет сохранён как ADMIN_PASSWORD", secret=True)
    use_hash = False
    if admin_plain:
        h = _sha256_hex(admin_plain)
        use_hash = _prompt("Сохранить только хэш ADMIN_PASSWORD_HASH вместо пароля? (y/n)", "y").lower() in (
            "y",
            "д",
            "yes",
        )
    else:
        print("Пароль админа пуст — задайте позже в .env вручную.")

    lines = [
        f"BOT_TOKEN={token}",
        "",
        "# Админ-панель: либо пароль в открытом виде (только для разработки), либо SHA-256 в hex:",
    ]
    if use_hash and admin_plain:
        lines.append(f"ADMIN_PASSWORD_HASH={_sha256_hex(admin_plain)}")
    elif admin_plain:
        lines.append(f"ADMIN_PASSWORD={admin_plain}")
    else:
        lines.append("# ADMIN_PASSWORD=your_password")

    lines.extend(
        [
            "",
            "# DATABASE_URL=sqlite:///./data/support_bot.db",
            "",
        ]
    )

    if env_path.exists():
        ow = _prompt(f"Файл {env_path} уже есть. Перезаписать? (y/n)", "n").lower()
        if ow not in ("y", "д", "yes"):
            print("Отмена.")
            return 0

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n✅ Записан {env_path}")
    if example.exists():
        print(f"   (шаблон полей см. также {example.name})")

    # Проверка API
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        from maxapi import Bot

        async def ping():
            b = Bot(os.environ.get("BOT_TOKEN", token))
            me = await b.get_me()
            print(f"✅ Подключение к MAX API: бот @{getattr(me, 'username', '') or '?'} id={getattr(me, 'id', '?')}")

        import asyncio

        asyncio.run(ping())
    except Exception as e:
        print(f"⚠️ Не удалось вызвать get_me (проверьте токен и сеть): {e}")

    # БД
    try:
        from config import ensure_data_dirs
        from models.database import init_db

        ensure_data_dirs()
        init_db()
        print("✅ База данных инициализирована (init_db).")
    except Exception as e:
        print(f"⚠️ init_db: {e}")

    print("\nДальше:\n  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n  .venv/bin/python bot.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
