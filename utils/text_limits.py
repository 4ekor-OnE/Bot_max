"""Ограничения длины текста (ТЗ: не более 4000 символов)."""

from config import MAX_MESSAGE_TEXT_LENGTH


def validate_message_text(text: str | None) -> str | None:
    """Возвращает None если ок, иначе текст ошибки для пользователя."""
    if text is None:
        return "Пустой текст."
    if len(text) > MAX_MESSAGE_TEXT_LENGTH:
        return f"Слишком длинный текст (максимум {MAX_MESSAGE_TEXT_LENGTH} символов)."
    return None
