"""Извлечение идентификатора пользователя MAX из событий API."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maxapi.types.updates.message_callback import MessageCallback
    from maxapi.types.updates.message_created import MessageCreated


def normalize_bot_command_line(text: str | None) -> str:
    """
    Приводит текст команды к каноническому виду: убирает суффикс @botname у /start и /admin.

    Примеры: ``/admin@MyBot pass`` → ``/admin pass``, ``/start@MyBot`` → ``/start``.
    """
    text = (text or "").strip()
    if not text:
        return text
    parts = text.split(None, 1)
    cmd_token = parts[0]
    base_cmd = cmd_token.split("@", 1)[0]
    if len(parts) == 1:
        return base_cmd
    return f"{base_cmd} {parts[1]}"


def max_user_id_from_message_created(event: MessageCreated) -> str | None:
    """User ID отправителя для события ``message_created`` (кортеж get_ids: chat_id, user_id)."""
    try:
        _chat_id, uid = event.get_ids()
        if uid is not None:
            return str(uid)
    except Exception:
        pass
    try:
        sender = getattr(event.message, "sender", None)
        if sender is not None and getattr(sender, "user_id", None) is not None:
            return str(sender.user_id)
    except Exception:
        pass
    try:
        fu = getattr(event, "from_user", None)
        if fu is not None and getattr(fu, "user_id", None) is not None:
            return str(fu.user_id)
    except Exception:
        pass
    return None


def max_user_id_from_message_callback(event: MessageCallback) -> str | None:
    """User ID для callback: сначала callback.user, затем второй элемент get_ids."""
    try:
        if event.callback and event.callback.user and event.callback.user.user_id is not None:
            return str(event.callback.user.user_id)
    except Exception:
        pass
    try:
        _chat_id, uid = event.get_ids()
        if uid is not None:
            return str(uid)
    except Exception:
        pass
    try:
        fu = getattr(event, "from_user", None)
        if fu is not None and getattr(fu, "user_id", None) is not None:
            return str(fu.user_id)
    except Exception:
        pass
    return None
