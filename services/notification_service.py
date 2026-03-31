"""Уведомления пользователям через MAX API (вне callback-ответов)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import or_

from models.user import User, UserRole
from services.ticket_photos import media_attachments_for_ticket_photo

if TYPE_CHECKING:
    from maxapi import Bot

logger = logging.getLogger(__name__)


async def notify_specialists_new_ticket(bot: "Bot", db, ticket) -> None:
    """Пуш специалистам и директорам о новой заявке (с учётом настроек)."""
    specialists = (
        db.query(User)
        .filter(
            User.notifications_enabled.is_(True),
            or_(User.role == UserRole.SUPPORT, User.role == UserRole.DIRECTOR),
        )
        .all()
    )
    urgent = bool(getattr(ticket, "is_urgent", False))
    prefix = "🚨 [Новая срочная заявка]" if urgent else "🆕 [Новая заявка]"
    body = (
        f"{prefix} #{ticket.id}\n"
        f"{ticket.title[:200]}{'…' if len(ticket.title) > 200 else ''}\n\n"
        "Откройте «Новые заявки» в меню."
    )
    for u in specialists:
        if not getattr(u, "notify_new_tickets", True):
            continue
        if getattr(u, "notify_urgent_only", False) and not urgent:
            continue
        try:
            mid = u.max_id
            if mid is None or str(mid).strip() == "":
                continue
            uid = int(mid)
        except (TypeError, ValueError):
            logger.warning("Пропуск уведомления: некорректный max_id у пользователя %s", u.id)
            continue
        try:
            att = media_attachments_for_ticket_photo(getattr(ticket, "photo_path", None))
            if att:
                await bot.send_message(user_id=uid, text=body, attachments=att)
            else:
                await bot.send_message(user_id=uid, text=body)
        except Exception as e:
            logger.warning("Не удалось уведомить специалиста %s: %s", u.id, e)


async def notify_ticket_comment_participants(
    bot: "Bot",
    db,
    ticket,
    comment_author_id: int,
    preview: str,
) -> None:
    """Уведомление участников заявки о комментарии (кроме автора комментария)."""
    preview = (preview or "")[:500]
    base = f"💬 Новый комментарий к заявке #{ticket.id}\n{preview}"

    recipients: set[int] = set()

    if ticket.user_id != comment_author_id:
        recipients.add(ticket.user_id)

    if ticket.assigned_to and ticket.assigned_to != comment_author_id:
        recipients.add(ticket.assigned_to)

    if not ticket.assigned_to and ticket.user_id == comment_author_id:
        for sp in (
            db.query(User)
            .filter(
                User.role == UserRole.SUPPORT,
                User.notifications_enabled.is_(True),
                User.id != comment_author_id,
            )
            .all()
        ):
            if getattr(sp, "notify_new_tickets", True):
                recipients.add(sp.id)

    for uid in recipients:
        u = db.query(User).filter(User.id == uid).first()
        if not u or not u.notifications_enabled:
            continue
        try:
            mid = u.max_id
            if mid is None or str(mid).strip() == "":
                continue
            mx = int(mid)
        except (TypeError, ValueError):
            logger.warning("Пропуск уведомления о комментарии: некорректный max_id user=%s", uid)
            continue
        try:
            await bot.send_message(user_id=mx, text=base)
        except Exception as e:
            logger.warning("Не удалось отправить уведомление о комментарии user=%s: %s", uid, e)


async def notify_specialist_assigned(
    bot: "Bot",
    db,
    ticket,
    specialist_user_id: int,
) -> None:
    """Пуш специалисту при назначении заявки из админки (не дублирует самоназначение из «взять в работу»)."""
    u = db.query(User).filter(User.id == specialist_user_id).first()
    if not u or u.role != UserRole.SUPPORT or not u.notifications_enabled:
        return
    body = (
        f"📌 Вам назначена заявка #{ticket.id}\n"
        f"{(ticket.title or '')[:200]}{'…' if len(ticket.title or '') > 200 else ''}\n\n"
        "Откройте «В работе» в меню."
    )
    try:
        mid = u.max_id
        if mid is None or str(mid).strip() == "":
            return
        mx = int(mid)
    except (TypeError, ValueError):
        logger.warning("Пропуск уведомления о назначении: некорректный max_id user=%s", specialist_user_id)
        return
    try:
        await bot.send_message(user_id=mx, text=body)
    except Exception as e:
        logger.warning("Не удалось уведомить назначенного специалиста %s: %s", specialist_user_id, e)


async def notify_user_status_change(
    bot: "Bot",
    db,
    ticket,
    message: str,
) -> None:
    """Уведомление автора заявки о смене статуса / назначении."""
    u = db.query(User).filter(User.id == ticket.user_id).first()
    if not u or not u.notifications_enabled:
        return
    try:
        mid = u.max_id
        if mid is None or str(mid).strip() == "":
            return
        mx = int(mid)
    except (TypeError, ValueError):
        logger.warning("Пропуск уведомления автору: некорректный max_id user=%s", ticket.user_id)
        return
    try:
        await bot.send_message(user_id=mx, text=message[:3900])
    except Exception as e:
        logger.warning("Не удалось уведомить автора заявки: %s", e)
