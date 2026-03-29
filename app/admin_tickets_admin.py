"""Админка: фильтры списка заявок и назначение специалиста."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.admin_common import admin_fsm_merge, admin_kb_home, shorten_text
from app.fsm import FSM
from maxapi.types.attachments.buttons import CallbackButton
from models.shop import Shop
from models.ticket import Ticket, TicketStatus
from models.ticket_comment import TicketComment
from models.user import User, UserRole
from services.notification_service import notify_specialist_assigned, notify_user_status_change
from utils.keyboard_helper import keyboard_to_attachment
from utils.safe_reply import get_bot, safe_answer

from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated

logger = logging.getLogger(__name__)

TICK_PAGE = 12
PICK_PAGE = 8


def _status_label(st: str | None) -> str:
    if not st:
        return "все"
    return {
        "new": "новые",
        "in_progress": "в работе",
        "resolved": "решённые",
        "postponed": "отложенные",
    }.get(st, st)


def _filter_summary(data: dict) -> str:
    parts = [f"статус: {_status_label(data.get('tf_status'))}"]
    sid = data.get("tf_shop_id")
    parts.append(f"магазин: #{sid}" if sid else "магазин: все")
    asn = data.get("tf_assign")
    if asn is None:
        parts.append("исполнитель: все")
    elif asn == 0:
        parts.append("исполнитель: не назначен")
    else:
        parts.append(f"исполнитель: user #{asn}")
    df, dt = data.get("tf_date_from"), data.get("tf_date_to")
    if df and dt:
        parts.append(f"даты: {df} — {dt}")
    else:
        parts.append("даты: без ограничения")
    return " · ".join(parts)


def _parse_iso(s: str):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _filtered_query(db: Session, data: dict):
    q = db.query(Ticket)
    st = data.get("tf_status")
    if st:
        q = q.filter(Ticket.status == TicketStatus(st))
    sid = data.get("tf_shop_id")
    if sid is not None:
        q = q.filter(Ticket.shop_id == int(sid))
    asn = data.get("tf_assign")
    if asn is not None:
        if asn == 0:
            q = q.filter(Ticket.assigned_to.is_(None))
        else:
            q = q.filter(Ticket.assigned_to == int(asn))
    df, dt = data.get("tf_date_from"), data.get("tf_date_to")
    if df:
        d0 = _parse_iso(df)
        if d0:
            start = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)
            q = q.filter(Ticket.created_at >= start)
    if dt:
        d1 = _parse_iso(dt)
        if d1:
            end_excl = datetime(d1.year, d1.month, d1.day, tzinfo=timezone.utc) + timedelta(days=1)
            q = q.filter(Ticket.created_at < end_excl)
    return q


async def admin_tickets_hub_message(
    event: MessageCallback | MessageCreated,
    db: Session,
    user_id: int,
    max_id: str,
    page: int,
) -> None:
    data = FSM.get_data(user_id)
    q = _filtered_query(db, data)
    total = q.count()
    start = page * TICK_PAGE
    tickets = q.order_by(Ticket.id.desc()).offset(start).limit(TICK_PAGE).all()
    summary = _filter_summary(data)
    lines = [f"📋 Заявки (всего по фильтру: {total})\n", f"🔍 {summary}\n"]
    if not tickets:
        lines.append("Ничего не найдено. Измените фильтры.")
    kb = [
        [CallbackButton(text="🔧 Фильтры", payload="admin_tf_menu")],
        [CallbackButton(text="🔄 Сбросить фильтры", payload="admin_tf_reset")],
    ]
    for t in tickets:
        st = t.status.value
        lines.append(f"#{t.id} · {st} · {shorten_text(t.title, 40)}")
        kb.append([CallbackButton(text=f"#{t.id}", payload=f"admin_t_{t.id}")])
    nav = []
    if page > 0:
        nav.append(CallbackButton(text="⬅️ Пред.", payload=f"admin_tl_{page - 1}"))
    if start + len(tickets) < total:
        nav.append(CallbackButton(text="След. ➡️", payload=f"admin_tl_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, "\n".join(lines), attachments=[keyboard_to_attachment(kb)])


async def admin_ticket_filters_menu(event: MessageCallback, db: Session, user_id: int, max_id: str) -> None:
    data = FSM.get_data(user_id)
    text = "🔧 Фильтры заявок\n\n" + _filter_summary(data) + "\n\nВыберите параметр:"
    cur = data.get("tf_status")
    kb = [
        [
            CallbackButton(text="✓ Все статусы" if not cur else "Все статусы", payload="admin_tf_s_all"),
            CallbackButton(text="🆕 new", payload="admin_tf_s_new"),
            CallbackButton(text="⚙️ in_pr.", payload="admin_tf_s_ip"),
        ],
        [
            CallbackButton(text="✅ resol.", payload="admin_tf_s_rs"),
            CallbackButton(text="⏸ postp.", payload="admin_tf_s_pp"),
        ],
        [
            CallbackButton(text="🏪 Магазин…", payload="admin_tf_shp_0"),
            CallbackButton(text="👤 Исполнитель…", payload="admin_tf_spp_0"),
        ],
        [
            CallbackButton(text="📅 Период (даты)", payload="admin_tf_dates"),
        ],
        [CallbackButton(text="◀️ К списку заявок", payload="admin_tickets")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, text, attachments=[keyboard_to_attachment(kb)])


async def admin_shop_pick_for_filter(
    event: MessageCallback, db: Session, user_id: int, max_id: str, page: int
) -> None:
    shops = db.query(Shop).order_by(Shop.id).all()
    start = page * PICK_PAGE
    chunk = shops[start : start + PICK_PAGE]
    kb = [[CallbackButton(text="✓ Все магазины", payload="admin_tf_sh_clear")]]
    for s in chunk:
        kb.append([CallbackButton(text=shorten_text(s.name, 36), payload=f"admin_tf_sh_{s.id}")])
    nav = []
    if page > 0:
        nav.append(CallbackButton(text="⬅️", payload=f"admin_tf_shp_{page - 1}"))
    if start + len(chunk) < len(shops):
        nav.append(CallbackButton(text="➡️", payload=f"admin_tf_shp_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([CallbackButton(text="◀️ К фильтрам", payload="admin_tf_menu")])
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"🏪 Магазин (стр. {page + 1})\n\nВыберите или «Все магазины»:",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_spec_pick_for_filter(
    event: MessageCallback, db: Session, user_id: int, max_id: str, page: int
) -> None:
    specs = db.query(User).filter(User.role == UserRole.SUPPORT).order_by(User.id).all()
    start = page * PICK_PAGE
    chunk = specs[start : start + PICK_PAGE]
    kb = [
        [CallbackButton(text="✓ Все исполнители", payload="admin_tf_sp_clear")],
        [CallbackButton(text="∅ Только без исполнителя", payload="admin_tf_sp_0")],
    ]
    for u in chunk:
        nm = shorten_text(u.first_name or u.username or str(u.id), 28)
        kb.append([CallbackButton(text=f"#{u.id} {nm}", payload=f"admin_tf_sp_{u.id}")])
    nav = []
    if page > 0:
        nav.append(CallbackButton(text="⬅️", payload=f"admin_tf_spp_{page - 1}"))
    if start + len(chunk) < len(specs):
        nav.append(CallbackButton(text="➡️", payload=f"admin_tf_spp_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([CallbackButton(text="◀️ К фильтрам", payload="admin_tf_menu")])
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"👤 Исполнитель (стр. {page + 1})",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_assign_pick_message(
    event: MessageCallback, db: Session, tid: int, max_id: str, page: int
) -> None:
    t = db.query(Ticket).filter(Ticket.id == tid).first()
    if not t:
        await safe_answer(event, max_id, "Заявка не найдена.", attachments=[keyboard_to_attachment(admin_kb_home())])
        return
    specs = db.query(User).filter(User.role == UserRole.SUPPORT).order_by(User.id).all()
    start = page * PICK_PAGE
    chunk = specs[start : start + PICK_PAGE]
    kb = []
    if t.assigned_to:
        kb.append([CallbackButton(text="🚫 Снять исполнителя", payload=f"admin_asg_{tid}_0")])
    for u in chunk:
        nm = shorten_text(u.first_name or u.username or str(u.id), 26)
        kb.append([CallbackButton(text=f"#{u.id} {nm}", payload=f"admin_asg_{tid}_{u.id}")])
    nav = []
    if page > 0:
        nav.append(CallbackButton(text="⬅️", payload=f"admin_asgp_{tid}_{page - 1}"))
    if start + len(chunk) < len(specs):
        nav.append(CallbackButton(text="➡️", payload=f"admin_asgp_{tid}_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([CallbackButton(text="◀️ К заявке", payload=f"admin_t_{tid}")])
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"Назначить исполнителя для #{tid}:",
        attachments=[keyboard_to_attachment(kb)],
    )


async def process_tf_dates_entry(
    event: MessageCreated,
    user_id: int,
    text: str,
    max_id: str,
) -> bool:
    if FSM.get_data(user_id).get("admin_step") != "tf_dates_entry":
        return False
    from models.database import get_db_session

    raw = (text or "").strip()
    parts = raw.split()
    if len(parts) != 2:
        await safe_answer(
            event,
            max_id,
            "Введите две даты через пробел: ГГГГ-ММ-ДД ГГГГ-ММ-ДД (период по дате создания).",
        )
        return True
    d0, d1 = _parse_iso(parts[0]), _parse_iso(parts[1])
    if not d0 or not d1 or d1 < d0:
        await safe_answer(event, max_id, "Неверные даты. Формат: ГГГГ-ММ-ДД ГГГГ-ММ-ДД, конец ≥ начала.")
        return True
    admin_fsm_merge(
        user_id,
        admin_step=None,
        tf_date_from=parts[0],
        tf_date_to=parts[1],
    )
    db = next(get_db_session())
    try:
        await admin_tickets_hub_message(event, db, user_id, max_id, 0)
    finally:
        db.close()
    return True


async def handle_admin_tickets_extra_callbacks(
    event: MessageCallback,
    callback_data: str,
    user_id: int,
    max_id: str,
    db: Session,
) -> bool:
    """Фильтры списка заявок и назначение исполнителя."""
    import re

    m = re.match(r"^admin_tl_(\d+)$", callback_data)
    if m:
        await admin_tickets_hub_message(event, db, user_id, max_id, int(m.group(1)))
        return True

    if callback_data == "admin_tf_menu":
        await admin_ticket_filters_menu(event, db, user_id, max_id)
        return True

    if callback_data == "admin_tf_reset":
        admin_fsm_merge(
            user_id,
            tf_status=None,
            tf_shop_id=None,
            tf_assign=None,
            tf_date_from=None,
            tf_date_to=None,
        )
        await admin_tickets_hub_message(event, db, user_id, max_id, 0)
        return True

    if callback_data == "admin_tf_dates":
        admin_fsm_merge(user_id, admin_step="tf_dates_entry")
        await safe_answer(
            event,
            max_id,
            "Введите две даты через пробел (по дате создания заявки):\nГГГГ-ММ-ДД ГГГГ-ММ-ДД",
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        return True

    if callback_data == "admin_tf_s_all":
        admin_fsm_merge(user_id, tf_status=None)
        await admin_ticket_filters_menu(event, db, user_id, max_id)
        return True
    for code, val in (
        ("admin_tf_s_new", "new"),
        ("admin_tf_s_ip", "in_progress"),
        ("admin_tf_s_rs", "resolved"),
        ("admin_tf_s_pp", "postponed"),
    ):
        if callback_data == code:
            admin_fsm_merge(user_id, tf_status=val)
            await admin_ticket_filters_menu(event, db, user_id, max_id)
            return True

    m = re.match(r"^admin_tf_shp_(\d+)$", callback_data)
    if m:
        await admin_shop_pick_for_filter(event, db, user_id, max_id, int(m.group(1)))
        return True

    if callback_data == "admin_tf_sh_clear":
        admin_fsm_merge(user_id, tf_shop_id=None)
        await admin_ticket_filters_menu(event, db, user_id, max_id)
        return True

    m = re.match(r"^admin_tf_sh_(\d+)$", callback_data)
    if m:
        admin_fsm_merge(user_id, tf_shop_id=int(m.group(1)))
        await admin_ticket_filters_menu(event, db, user_id, max_id)
        return True

    m = re.match(r"^admin_tf_spp_(\d+)$", callback_data)
    if m:
        await admin_spec_pick_for_filter(event, db, user_id, max_id, int(m.group(1)))
        return True

    if callback_data == "admin_tf_sp_clear":
        admin_fsm_merge(user_id, tf_assign=None)
        await admin_ticket_filters_menu(event, db, user_id, max_id)
        return True

    m = re.match(r"^admin_tf_sp_(\d+)$", callback_data)
    if m:
        admin_fsm_merge(user_id, tf_assign=int(m.group(1)))
        await admin_ticket_filters_menu(event, db, user_id, max_id)
        return True

    m = re.match(r"^admin_asgp_(\d+)_(\d+)$", callback_data)
    if m:
        await admin_assign_pick_message(event, db, int(m.group(1)), max_id, int(m.group(2)))
        return True

    m = re.match(r"^admin_asg_(\d+)_(\d+)$", callback_data)
    if m:
        tid, sp_raw = int(m.group(1)), int(m.group(2))
        ticket = db.query(Ticket).filter(Ticket.id == tid).first()
        if not ticket:
            await safe_answer(event, max_id, "Заявка не найдена.")
            return True
        bot = get_bot()
        if sp_raw == 0:
            ticket.assigned_to = None
            if ticket.status == TicketStatus.IN_PROGRESS:
                ticket.status = TicketStatus.NEW
            db.add(
                TicketComment(
                    ticket_id=ticket.id,
                    user_id=user_id,
                    text="Исполнитель снят (админ-панель)",
                    is_system=True,
                )
            )
            db.commit()
            if bot:
                await notify_user_status_change(
                    bot,
                    db,
                    ticket,
                    f"Заявка #{tid}: исполнитель снят администратором.",
                )
        else:
            spec = db.query(User).filter(User.id == sp_raw, User.role == UserRole.SUPPORT).first()
            if not spec:
                await safe_answer(event, max_id, "Пользователь не найден или не специалист ТП.")
                return True
            ticket.assigned_to = sp_raw
            ticket.status = TicketStatus.IN_PROGRESS
            db.add(
                TicketComment(
                    ticket_id=ticket.id,
                    user_id=user_id,
                    text=f"Назначен исполнитель #{sp_raw} (админ-панель)",
                    is_system=True,
                )
            )
            db.commit()
            if bot:
                await notify_user_status_change(
                    bot,
                    db,
                    ticket,
                    f"Заявка #{tid} назначена на специалиста.",
                )
                await notify_specialist_assigned(bot, db, ticket, sp_raw)

        from app.admin_panel import admin_ticket_detail_message

        await admin_ticket_detail_message(event, db, tid, max_id)
        return True

    return False
