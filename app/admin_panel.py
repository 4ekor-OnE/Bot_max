"""
Админ-панель: пользователи, магазины, категории, просмотр заявок.
Состояние admin_mode + поле data['admin_step'] для пошагового ввода текста.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.admin_common import (
    admin_fsm_clear_step,
    admin_fsm_merge,
    admin_kb_home,
    shorten_text,
)
from app.admin_documents_flow import (
    handle_admin_documents_callbacks,
    process_documents_admin_text,
)
from app.admin_system_flow import handle_admin_system_callbacks, process_system_admin_text
from app.admin_tickets_admin import (
    admin_tickets_hub_message,
    handle_admin_tickets_extra_callbacks,
    process_tf_dates_entry,
)
from app.fsm import FSM
from keyboards.keyboards import get_admin_menu_keyboard
from maxapi.types.attachments.buttons import CallbackButton
from models.category import Category
from models.database import get_db_session
from models.shop import Shop
from models.ticket import Ticket, TicketStatus
from models.user import User, UserRole
from utils.keyboard_helper import keyboard_to_attachment
from utils.safe_reply import safe_answer

if TYPE_CHECKING:
    from maxapi.types.updates.message_callback import MessageCallback
    from maxapi.types.updates.message_created import MessageCreated

logger = logging.getLogger(__name__)

TEXT_MAX = 4000
USER_PAGE_SIZE = 8


async def process_admin_text(event: MessageCreated, user_id: int, text: str, max_id: str) -> bool:
    """Обрабатывает текст в режиме admin_mode при активном admin_step."""
    if FSM.get_state(user_id) != "admin_mode":
        return False
    data = FSM.get_data(user_id)
    step = data.get("admin_step")

    raw = (text or "").strip()
    if step and len(raw) > TEXT_MAX:
        await safe_answer(
            event,
            max_id,
            f"Слишком длинный текст (макс. {TEXT_MAX} символов). Попробуйте короче.",
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        return True

    db = next(get_db_session())
    try:
        if await process_documents_admin_text(event, user_id, text, db, max_id):
            return True
        if await process_system_admin_text(event, user_id, text, db, max_id):
            return True
        if await process_tf_dates_entry(event, user_id, text, max_id):
            return True

        if not step:
            return False

        if step == "user_search":
            admin_fsm_clear_step(user_id)
            await _admin_users_search_result(event, db, raw, max_id)
            return True

        if step == "shop_add_name":
            if not raw:
                await safe_answer(event, max_id, "Название не может быть пустым.")
                return True
            exists = db.query(Shop).filter(Shop.name == raw).first()
            if exists:
                await safe_answer(event, max_id, "Магазин с таким названием уже есть. Введите другое.")
                return True
            db.add(Shop(name=raw))
            db.commit()
            admin_fsm_clear_step(user_id)
            await safe_answer(
                event,
                max_id,
                f"✅ Магазин «{raw}» добавлен.",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            await admin_shops_menu_message(event, db, max_id)
            return True

        if step == "shop_edit_name":
            sid = data.get("edit_shop_id")
            if not sid:
                admin_fsm_clear_step(user_id)
                return True
            shop = db.query(Shop).filter(Shop.id == sid).first()
            if not shop:
                admin_fsm_clear_step(user_id)
                await safe_answer(event, max_id, "Магазин не найден.")
                return True
            other = db.query(Shop).filter(Shop.name == raw, Shop.id != sid).first()
            if other:
                await safe_answer(event, max_id, "Такое название уже занято.")
                return True
            shop.name = raw
            db.commit()
            admin_fsm_clear_step(user_id)
            await safe_answer(
                event,
                max_id,
                f"✅ Название обновлено: «{raw}»",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            await admin_shop_detail_message(event, db, sid, max_id)
            return True

        if step == "cat_add_name":
            admin_fsm_merge(user_id, admin_step="cat_add_desc", pending_cat_name=raw)
            await safe_answer(
                event,
                max_id,
                f"Название: «{raw}»\n\nВведите краткое описание категории (или «-» без описания):",
            )
            return True

        if step == "cat_add_desc":
            name = data.get("pending_cat_name") or ""
            desc = "" if raw == "-" else raw
            admin_fsm_merge(
                user_id,
                admin_step="cat_add_sla",
                pending_cat_description=desc,
            )
            await safe_answer(event, max_id, "Введите SLA в часах (целое число, например 24):")
            return True

        if step == "cat_add_sla":
            if not raw.isdigit() or int(raw) <= 0:
                await safe_answer(event, max_id, "Введите положительное целое число часов.")
                return True
            hours = int(raw)
            name = data.get("pending_cat_name") or "Без названия"
            desc = data.get("pending_cat_description") or ""
            db.add(Category(name=name, description=desc, sla_hours=hours))
            db.commit()
            fd = FSM.get_data(user_id).copy()
            fd.pop("pending_cat_name", None)
            fd.pop("pending_cat_description", None)
            fd.pop("admin_step", None)
            FSM.set_state(user_id, "admin_mode", fd)
            await safe_answer(
                event,
                max_id,
                f"✅ Категория «{name}» создана (SLA {hours} ч).",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            await admin_categories_menu_message(event, db, max_id)
            return True

        if step == "cat_edit_name":
            cid = data.get("edit_cat_id")
            cat = db.query(Category).filter(Category.id == cid).first() if cid else None
            if not cat:
                admin_fsm_clear_step(user_id)
                await safe_answer(event, max_id, "Категория не найдена.")
                return True
            cat.name = raw
            db.commit()
            admin_fsm_clear_step(user_id)
            await safe_answer(
                event, max_id, "✅ Название обновлено.", attachments=[keyboard_to_attachment(admin_kb_home())]
            )
            await admin_category_detail_message(event, db, cid, max_id)
            return True

        if step == "cat_edit_desc":
            cid = data.get("edit_cat_id")
            cat = db.query(Category).filter(Category.id == cid).first() if cid else None
            if not cat:
                admin_fsm_clear_step(user_id)
                await safe_answer(event, max_id, "Категория не найдена.")
                return True
            cat.description = "" if raw == "-" else raw
            db.commit()
            admin_fsm_clear_step(user_id)
            await safe_answer(
                event, max_id, "✅ Описание обновлено.", attachments=[keyboard_to_attachment(admin_kb_home())]
            )
            await admin_category_detail_message(event, db, cid, max_id)
            return True

        if step == "cat_edit_sla":
            cid = data.get("edit_cat_id")
            cat = db.query(Category).filter(Category.id == cid).first() if cid else None
            if not cat:
                admin_fsm_clear_step(user_id)
                await safe_answer(event, max_id, "Категория не найдена.")
                return True
            if not raw.isdigit() or int(raw) <= 0:
                await safe_answer(event, max_id, "Введите положительное целое число часов.")
                return True
            cat.sla_hours = int(raw)
            db.commit()
            admin_fsm_clear_step(user_id)
            await safe_answer(
                event, max_id, "✅ SLA обновлён.", attachments=[keyboard_to_attachment(admin_kb_home())]
            )
            await admin_category_detail_message(event, db, cid, max_id)
            return True

    finally:
        db.close()

    return False


async def _admin_users_search_result(event: MessageCreated, db: Session, q: str, max_id: str) -> None:
    kb: list = []
    query = db.query(User)
    if q.isdigit():
        uid = int(q)
        query = query.filter(or_(User.id == uid, User.max_id.contains(q)))
    else:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.max_id.ilike(like),
                User.username.ilike(like),
                User.first_name.ilike(like),
            )
        )
    users = query.order_by(User.id.desc()).limit(20).all()
    if not users:
        await safe_answer(
            event,
            max_id,
            f"По запросу «{shorten_text(q, 80)}» никого не найдено.",
            attachments=[keyboard_to_attachment(_admin_users_list_kb(0) + admin_kb_home())],
        )
        return
    lines = [f"Найдено: {len(users)}\n"]
    for u in users:
        lines.append(f"#{u.id} · {u.role.value} · max_id={u.max_id} · {shorten_text(u.first_name or u.username or '—', 40)}")
        kb.append(
            [
                CallbackButton(
                    text=f"#{u.id} {shorten_text(u.first_name or u.username or u.max_id, 28)}",
                    payload=f"admin_u_{u.id}",
                )
            ]
        )
    kb.extend(_admin_users_list_kb(0))
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, "\n".join(lines), attachments=[keyboard_to_attachment(kb)])


def _admin_users_list_kb(page: int) -> list:
    return [
        [CallbackButton(text="📋 Список пользователей", payload=f"admin_ul_{page}")],
        [CallbackButton(text="🔍 Поиск (по ID / имени)", payload="admin_us")],
    ]


async def admin_users_menu_message(
    event: MessageCallback | MessageCreated, db: Session, max_id: str
) -> None:
    total = db.query(User).count()
    kb = _admin_users_list_kb(0)
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"👥 Пользователи\n\nВсего в базе: {total}\n\nВыберите действие:",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_users_page_message(event: MessageCallback, db: Session, page: int, max_id: str) -> None:
    q = db.query(User).order_by(User.id.desc())
    total = q.count()
    start = page * USER_PAGE_SIZE
    users = q.offset(start).limit(USER_PAGE_SIZE).all()
    lines = [f"Страница {page + 1} · показано {len(users)} из {total}\n"]
    kb = []
    for u in users:
        nm = shorten_text(u.first_name or u.username or "—", 22)
        lines.append(f"#{u.id} · {u.role.value} · {nm}")
        kb.append([CallbackButton(text=f"#{u.id} {nm}", payload=f"admin_u_{u.id}")])
    nav = []
    if page > 0:
        nav.append(CallbackButton(text="⬅️ Пред.", payload=f"admin_ul_{page - 1}"))
    if start + len(users) < total:
        nav.append(CallbackButton(text="След. ➡️", payload=f"admin_ul_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.extend(_admin_users_list_kb(page))
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, "\n".join(lines), attachments=[keyboard_to_attachment(kb)])


async def admin_user_detail_message(
    event: MessageCallback | MessageCreated, db: Session, uid: int, max_id: str
) -> None:
    u = db.query(User).filter(User.id == uid).first()
    if not u:
        await safe_answer(
            event, max_id, "Пользователь не найден.", attachments=[keyboard_to_attachment(admin_kb_home())]
        )
        return
    login_line = f"Логин: @{u.username}" if u.username else "Логин: —"
    text = (
        f"👤 Пользователь #{u.id}\n"
        f"max_id: {u.max_id}\n"
        f"Имя: {u.first_name or '—'}\n"
        f"{login_line}\n"
        f"Роль: {u.role.value}\n"
        f"Уведомления: {'вкл' if u.notifications_enabled else 'выкл'}"
    )
    kb = [
        [
            CallbackButton(text="Роль: user", payload=f"admin_ur_{u.id}_user"),
            CallbackButton(text="Роль: support", payload=f"admin_ur_{u.id}_support"),
        ],
        [CallbackButton(text="Роль: director", payload=f"admin_ur_{u.id}_director")],
        [CallbackButton(text="◀️ К списку пользователей", payload="admin_users")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, text, attachments=[keyboard_to_attachment(kb)])


async def admin_shops_menu_message(
    event: MessageCallback | MessageCreated, db: Session, max_id: str
) -> None:
    shops = db.query(Shop).order_by(Shop.id).all()
    kb = []
    for s in shops:
        kb.append([CallbackButton(text=shorten_text(s.name, 40), payload=f"admin_s_{s.id}")])
    kb.append([CallbackButton(text="➕ Добавить магазин", payload="admin_sadd")])
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"🏪 Магазины ({len(shops)})\n\nВыберите магазин или добавьте новый:",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_shop_detail_message(
    event: MessageCallback | MessageCreated, db: Session, sid: int, max_id: str
) -> None:
    s = db.query(Shop).filter(Shop.id == sid).first()
    if not s:
        await safe_answer(
            event, max_id, "Магазин не найден.", attachments=[keyboard_to_attachment(admin_kb_home())]
        )
        return
    tickets = db.query(Ticket).filter(Ticket.shop_id == sid).count()
    kb = [
        [CallbackButton(text="✏️ Изменить название", payload=f"admin_se_{s.id}")],
        [CallbackButton(text="🗑 Удалить", payload=f"admin_sd_{s.id}")],
        [CallbackButton(text="◀️ К списку магазинов", payload="admin_shops")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"🏪 {s.name}\n\nЗаявок с этим магазином: {tickets}",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_categories_menu_message(
    event: MessageCallback | MessageCreated, db: Session, max_id: str
) -> None:
    cats = db.query(Category).order_by(Category.id).all()
    kb = []
    for c in cats:
        kb.append(
            [
                CallbackButton(
                    text=f"{shorten_text(c.name, 30)} ({c.sla_hours}ч)",
                    payload=f"admin_c_{c.id}",
                )
            ]
        )
    kb.append([CallbackButton(text="➕ Новая категория", payload="admin_cadd")])
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"📂 Категории ({len(cats)})\n\nВыберите категорию:",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_category_detail_message(
    event: MessageCallback | MessageCreated, db: Session, cid: int, max_id: str
) -> None:
    c = db.query(Category).filter(Category.id == cid).first()
    if not c:
        await safe_answer(
            event, max_id, "Категория не найдена.", attachments=[keyboard_to_attachment(admin_kb_home())]
        )
        return
    tcount = db.query(Ticket).filter(Ticket.category_id == cid).count()
    desc = c.description or "—"
    kb = [
        [CallbackButton(text="✏️ Название", payload=f"admin_cn_{c.id}")],
        [CallbackButton(text="✏️ Описание", payload=f"admin_cd_{c.id}")],
        [CallbackButton(text="✏️ SLA (часы)", payload=f"admin_cs_{c.id}")],
        [CallbackButton(text="🗑 Удалить", payload=f"admin_cdel_{c.id}")],
        [CallbackButton(text="◀️ К категориям", payload="admin_categories")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"📂 {c.name}\n\nОписание: {desc}\nSLA: {c.sla_hours} ч\nЗаявок: {tcount}",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_ticket_detail_message(event: MessageCallback, db: Session, tid: int, max_id: str) -> None:
    t = db.query(Ticket).filter(Ticket.id == tid).first()
    if not t:
        await safe_answer(
            event, max_id, "Заявка не найдена.", attachments=[keyboard_to_attachment(admin_kb_home())]
        )
        return
    shop = db.query(Shop).filter(Shop.id == t.shop_id).first()
    cat = db.query(Category).filter(Category.id == t.category_id).first()
    author = db.query(User).filter(User.id == t.user_id).first()
    spec = db.query(User).filter(User.id == t.assigned_to).first() if t.assigned_to else None
    body = (
        f"#{t.id} · {t.status.value}\n"
        f"Приоритет: {t.priority.value} · Срочная: {'да' if t.is_urgent else 'нет'}\n"
        f"Магазин: {shop.name if shop else t.shop_id}\n"
        f"Категория: {cat.name if cat else t.category_id}\n"
        f"Автор: #{t.user_id} ({author.max_id if author else '—'})\n"
        f"Исполнитель: {spec.id if spec else '—'}\n"
        f"Создана: {t.created_at}\n"
        f"SLA до: {t.sla_deadline or '—'}\n\n"
        f"📝 {t.title}\n\n{t.description}"
    )
    if len(body) > 3500:
        body = body[:3490] + "…"
    kb = [
        [CallbackButton(text="👤 Назначить специалиста", payload=f"admin_asgp_{t.id}_0")],
        [
            CallbackButton(text="🆕 new", payload=f"admin_ts_{t.id}_new"),
            CallbackButton(text="⚙️ in_progress", payload=f"admin_ts_{t.id}_in_progress"),
        ],
        [
            CallbackButton(text="✅ resolved", payload=f"admin_ts_{t.id}_resolved"),
            CallbackButton(text="⏸ postponed", payload=f"admin_ts_{t.id}_postponed"),
        ],
        [CallbackButton(text="◀️ К списку заявок", payload="admin_tickets")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, body, attachments=[keyboard_to_attachment(kb)])


async def handle_admin_callback(
    event: MessageCallback,
    callback_data: str,
    user_id: int | None,
    max_id: str,
    is_admin_mode: bool,
) -> bool:
    """
    Обрабатывает callback, начинающийся с admin_.
    Возвращает True, если событие поглощено.
    """
    if not callback_data.startswith("admin_"):
        return False

    if not user_id:
        await safe_answer(event, max_id, "❌ Пользователь не найден в базе.")
        return True

    if callback_data != "admin_exit" and not is_admin_mode:
        await safe_answer(event, max_id, "❌ Доступ запрещён. Войдите: /admin <пароль>")
        return True

    db = next(get_db_session())
    try:
        if callback_data == "admin_home":
            admin_fsm_clear_step(user_id)
            await safe_answer(
                event,
                max_id,
                "🔐 Админ-панель",
                attachments=[keyboard_to_attachment(get_admin_menu_keyboard())],
            )
            return True

        if await handle_admin_documents_callbacks(event, callback_data, user_id, db, max_id):
            return True
        if await handle_admin_system_callbacks(event, callback_data, user_id, db, max_id):
            return True

        if await handle_admin_tickets_extra_callbacks(event, callback_data, user_id, max_id, db):
            return True

        if callback_data == "admin_users":
            admin_fsm_clear_step(user_id)
            await admin_users_menu_message(event, db, max_id)
            return True

        m = re.match(r"^admin_ul_(\d+)$", callback_data)
        if m:
            admin_fsm_clear_step(user_id)
            await admin_users_page_message(event, db, int(m.group(1)), max_id)
            return True

        if callback_data == "admin_us":
            admin_fsm_merge(user_id, admin_step="user_search")
            await safe_answer(
                event,
                max_id,
                "🔍 Введите ID пользователя, max_id или часть имени/логина:",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            return True

        m = re.match(r"^admin_u_(\d+)$", callback_data)
        if m:
            admin_fsm_clear_step(user_id)
            await admin_user_detail_message(event, db, int(m.group(1)), max_id)
            return True

        m = re.match(r"^admin_ur_(\d+)_(user|support|director)$", callback_data)
        if m:
            uid = int(m.group(1))
            rname = m.group(2)
            u = db.query(User).filter(User.id == uid).first()
            if not u:
                await safe_answer(event, max_id, "Пользователь не найден.")
                return True
            u.role = UserRole(rname)
            db.commit()
            await safe_answer(event, max_id, f"✅ Роль обновлена: {rname}")
            await admin_user_detail_message(event, db, uid, max_id)
            return True

        if callback_data == "admin_shops":
            admin_fsm_clear_step(user_id)
            await admin_shops_menu_message(event, db, max_id)
            return True

        if callback_data == "admin_sadd":
            admin_fsm_merge(user_id, admin_step="shop_add_name")
            await safe_answer(
                event,
                max_id,
                "Введите название нового магазина:",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            return True

        m = re.match(r"^admin_s_(\d+)$", callback_data)
        if m:
            admin_fsm_clear_step(user_id)
            await admin_shop_detail_message(event, db, int(m.group(1)), max_id)
            return True

        m = re.match(r"^admin_se_(\d+)$", callback_data)
        if m:
            sid = int(m.group(1))
            admin_fsm_merge(user_id, admin_step="shop_edit_name", edit_shop_id=sid)
            await safe_answer(
                event,
                max_id,
                "Введите новое название магазина:",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            return True

        m = re.match(r"^admin_sd_(\d+)$", callback_data)
        if m:
            sid = int(m.group(1))
            s = db.query(Shop).filter(Shop.id == sid).first()
            if not s:
                await safe_answer(event, max_id, "Магазин не найден.")
                return True
            cnt = db.query(Ticket).filter(Ticket.shop_id == sid).count()
            if cnt:
                await safe_answer(
                    event,
                    max_id,
                    f"Нельзя удалить: есть {cnt} заявок с этим магазином.",
                    attachments=[keyboard_to_attachment(admin_kb_home())],
                )
                return True
            kb = [
                [
                    CallbackButton(text="✅ Да, удалить", payload=f"admin_sdy_{sid}"),
                    CallbackButton(text="Отмена", payload=f"admin_s_{sid}"),
                ]
            ]
            kb.extend(admin_kb_home())
            await safe_answer(
                event,
                max_id,
                f"Удалить магазин «{s.name}»?",
                attachments=[keyboard_to_attachment(kb)],
            )
            return True

        m = re.match(r"^admin_sdy_(\d+)$", callback_data)
        if m:
            sid = int(m.group(1))
            s = db.query(Shop).filter(Shop.id == sid).first()
            if s:
                cnt = db.query(Ticket).filter(Ticket.shop_id == sid).count()
                if cnt == 0:
                    db.delete(s)
                    db.commit()
                    await safe_answer(event, max_id, "✅ Магазин удалён.")
                else:
                    await safe_answer(event, max_id, "Удаление отменено: появились заявки.")
            await admin_shops_menu_message(event, db, max_id)
            return True

        if callback_data == "admin_categories":
            admin_fsm_clear_step(user_id)
            await admin_categories_menu_message(event, db, max_id)
            return True

        if callback_data == "admin_cadd":
            admin_fsm_merge(
                user_id,
                admin_step="cat_add_name",
                pending_cat_name=None,
                pending_cat_description=None,
            )
            await safe_answer(
                event,
                max_id,
                "Введите название категории:",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            return True

        m = re.match(r"^admin_c_(\d+)$", callback_data)
        if m:
            admin_fsm_clear_step(user_id)
            await admin_category_detail_message(event, db, int(m.group(1)), max_id)
            return True

        m = re.match(r"^admin_cn_(\d+)$", callback_data)
        if m:
            cid = int(m.group(1))
            admin_fsm_merge(user_id, admin_step="cat_edit_name", edit_cat_id=cid)
            await safe_answer(
                event, max_id, "Введите новое название:", attachments=[keyboard_to_attachment(admin_kb_home())]
            )
            return True

        m = re.match(r"^admin_cd_(\d+)$", callback_data)
        if m:
            cid = int(m.group(1))
            admin_fsm_merge(user_id, admin_step="cat_edit_desc", edit_cat_id=cid)
            await safe_answer(
                event,
                max_id,
                "Введите новое описание (или «-» чтобы очистить):",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            return True

        m = re.match(r"^admin_cs_(\d+)$", callback_data)
        if m:
            cid = int(m.group(1))
            admin_fsm_merge(user_id, admin_step="cat_edit_sla", edit_cat_id=cid)
            await safe_answer(
                event,
                max_id,
                "Введите SLA в часах (целое число):",
                attachments=[keyboard_to_attachment(admin_kb_home())],
            )
            return True

        m = re.match(r"^admin_cdel_(\d+)$", callback_data)
        if m:
            cid = int(m.group(1))
            c = db.query(Category).filter(Category.id == cid).first()
            if not c:
                await safe_answer(event, max_id, "Категория не найдена.")
                return True
            cnt = db.query(Ticket).filter(Ticket.category_id == cid).count()
            if cnt:
                await safe_answer(
                    event,
                    max_id,
                    f"Нельзя удалить: {cnt} заявок с этой категорией.",
                    attachments=[keyboard_to_attachment(admin_kb_home())],
                )
                return True
            kb = [
                [
                    CallbackButton(text="✅ Да, удалить", payload=f"admin_cdy_{cid}"),
                    CallbackButton(text="Отмена", payload=f"admin_c_{cid}"),
                ]
            ]
            kb.extend(admin_kb_home())
            await safe_answer(
                event, max_id, f"Удалить категорию «{c.name}»?", attachments=[keyboard_to_attachment(kb)]
            )
            return True

        m = re.match(r"^admin_cdy_(\d+)$", callback_data)
        if m:
            cid = int(m.group(1))
            c = db.query(Category).filter(Category.id == cid).first()
            if c:
                cnt = db.query(Ticket).filter(Ticket.category_id == cid).count()
                if cnt == 0:
                    db.delete(c)
                    db.commit()
                    await safe_answer(event, max_id, "✅ Категория удалена.")
            await admin_categories_menu_message(event, db, max_id)
            return True

        if callback_data == "admin_tickets":
            admin_fsm_clear_step(user_id)
            await admin_tickets_hub_message(event, db, user_id, max_id, 0)
            return True

        m = re.match(r"^admin_t_(\d+)$", callback_data)
        if m:
            admin_fsm_clear_step(user_id)
            await admin_ticket_detail_message(event, db, int(m.group(1)), max_id)
            return True

        m = re.match(r"^admin_ts_(\d+)_(new|in_progress|resolved|postponed)$", callback_data)
        if m:
            tid = int(m.group(1))
            st = TicketStatus(m.group(2))
            t = db.query(Ticket).filter(Ticket.id == tid).first()
            if not t:
                await safe_answer(event, max_id, "Заявка не найдена.")
                return True
            t.status = st
            if st == TicketStatus.RESOLVED:
                t.resolved_at = datetime.now(timezone.utc)
            db.commit()
            await safe_answer(event, max_id, f"✅ Статус заявки #{tid}: {st.value}")
            await admin_ticket_detail_message(event, db, tid, max_id)
            return True

        if callback_data == "admin_exit":
            FSM.clear(user_id)
            from keyboards.keyboards import get_main_menu_keyboard

            role = None
            u = db.query(User).filter(User.max_id == max_id).first()
            if u:
                role = u.role.value
            await safe_answer(
                event,
                max_id,
                "✅ Вы вышли из административной панели.",
                attachments=[keyboard_to_attachment(get_main_menu_keyboard(role or "user"))],
            )
            return True

        logger.warning("Необработанный admin callback: %s", callback_data)
        await safe_answer(
            event,
            max_id,
            "Команда админ-панели не распознана.",
            attachments=[keyboard_to_attachment(get_admin_menu_keyboard())],
        )
        return True

    except Exception:
        logger.exception("Ошибка обработки admin callback %s", callback_data)
        try:
            await safe_answer(
                event,
                max_id,
                "Ошибка в админ-панели. Проверьте логи сервера и что после обновления выполнен "
                "запуск с init_db() (новые таблицы instruction_documents, system_settings).",
                attachments=[keyboard_to_attachment(get_admin_menu_keyboard())],
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке админки")
        return True
    finally:
        db.close()
