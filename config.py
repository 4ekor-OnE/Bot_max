import os
import hashlib
from pathlib import Path

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent
# Загружаем .env из каталога проекта (не зависит от текущей рабочей директории)
load_dotenv(_BASE_DIR / ".env")
# Токен только из окружения (.env / переменные сервера), без значения по умолчанию в коде
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("MAX_BOT_TOKEN") or ""
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///support_bot.db")

INSTRUCTIONS_DIR = Path(
    os.getenv("INSTRUCTIONS_DIR", str(_BASE_DIR / "data" / "instructions"))
).resolve()
TEMP_FILES_DIR = Path(
    os.getenv("TEMP_FILES_DIR", str(_BASE_DIR / "data" / "temp"))
).resolve()
TICKET_PHOTOS_DIR = Path(
    os.getenv("TICKET_PHOTOS_DIR", str(_BASE_DIR / "data" / "ticket_photos"))
).resolve()
INSTRUCTION_MAX_BYTES = int(os.getenv("INSTRUCTION_MAX_BYTES", str(20 * 1024 * 1024)))
TICKET_PHOTO_MAX_BYTES = int(os.getenv("TICKET_PHOTO_MAX_BYTES", str(15 * 1024 * 1024)))

# Лимит текста сообщений MAX API (см. ТЗ п. 7)
MAX_MESSAGE_TEXT_LENGTH = int(os.getenv("MAX_MESSAGE_TEXT_LENGTH", "4000"))

# Веб-админка Flask (python -m web_admin)
# SECRET_KEY для сессий; в production задайте длинную случайную строку.
WEB_ADMIN_SECRET_KEY = os.getenv("WEB_ADMIN_SECRET_KEY", "")
WEB_ADMIN_HOST = os.getenv("WEB_ADMIN_HOST", "127.0.0.1")
WEB_ADMIN_PORT = int(os.getenv("WEB_ADMIN_PORT", "5000"))


def ensure_data_dirs() -> None:
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_FILES_DIR.mkdir(parents=True, exist_ok=True)
    TICKET_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

# Пароль: либо явный хеш SHA-256 (рекомендуется в production), либо ADMIN_PASSWORD для dev
_ADMIN_HASH_ENV = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip().lower()
_ADMIN_PLAIN = os.getenv("ADMIN_PASSWORD", "admin")


def _expected_admin_password_sha256() -> str:
    if _ADMIN_HASH_ENV:
        return _ADMIN_HASH_ENV
    return hashlib.sha256(_ADMIN_PLAIN.encode()).hexdigest()


def verify_admin_password(password: str) -> bool:
    """Проверка пароля администратора (сравнение с SHA-256)."""
    if not password:
        return False
    return hashlib.sha256(password.encode()).hexdigest() == _expected_admin_password_sha256()
