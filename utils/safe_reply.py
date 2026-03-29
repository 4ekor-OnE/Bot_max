"""Ответ пользователю: через event.message или send_message, если сообщения нет (callback после удаления клавиатуры)."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


def get_bot():
    """Текущий экземпляр Bot (после set_bot); для уведомлений из модулей без циклического импорта."""
    return _bot


async def safe_answer(
    event: Any,
    max_id: str,
    text: str,
    attachments=None,
) -> None:
    m = getattr(event, "message", None)
    if m is not None:
        try:
            if attachments is not None:
                await m.answer(text, attachments=attachments)
            else:
                await m.answer(text)
            return
        except Exception as e:
            logger.warning(
                "safe_answer: message.answer не удался (%s), пробуем send_message",
                e,
            )

    if not max_id or not str(max_id).strip():
        logger.warning("safe_answer: нет рабочего max_id для send_message")
        return
    if _bot is None:
        logger.error("safe_answer: бот не зарегистрирован (set_bot)")
        return
    try:
        uid = int(str(max_id).strip())
    except (TypeError, ValueError):
        logger.error("safe_answer: некорректный max_id=%r", max_id)
        return
    logger.info("safe_answer: ответ через send_message(user_id=%s)", uid)
    await _bot.send_message(user_id=uid, text=text, attachments=attachments)
