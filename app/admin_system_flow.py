"""Админка: системные настройки и очистка данных."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.admin_common import admin_fsm_clear_step, admin_fsm_merge, admin_kb_home
from maxapi.types.attachments.buttons import CallbackButton
from services.cleanup_service import (
    cleanup_resolved_tickets_older_than,
    cleanup_sessions,
    cleanup_temp_files,
)
from utils.keyboard_helper import keyboard_to_attachment
from utils.safe_reply import safe_answer
from utils.settings_service import get_system_settings

if TYPE_CHECKING:
    from maxapi.types.updates.message_callback import MessageCallback
    from maxapi.types.updates.message_created import MessageCreated

logger = logging.getLogger(__name__)

TEXT_MAX = 4000


async def admin_settings_menu(event: MessageCallback, db: Session, max_id: str) -> None:
    s = get_system_settings(db)
    text = (
        "⚙️ Системные настройки\n\n"
        f"SLA срочных заявок: {s.urgent_sla_hours} ч\n"
        f"SLA по умолчанию (обычные): {s.normal_default_sla_hours} ч\n"
        f"Хранение решённых заявок: {s.retention_resolved_tickets_days} дн.\n"
        f"Сессии FSM (очистка неактивных): {s.retention_sessions_days} дн.\n"
        f"Логи (резерв под будущий журнал): {s.retention_logs_days} дн.\n"
        f"Временные файлы: {s.retention_temp_files_days} дн.\n\n"
        "Выберите параметр для изменения:"
    )
    kb = [
        [CallbackButton(text=f"Срочный SLA ({s.urgent_sla_hours} ч)", payload="admin_cfg_u")],
        [CallbackButton(text=f"Обычный SLA по умолч. ({s.normal_default_sla_hours} ч)", payload="admin_cfg_n")],
        [
            CallbackButton(
                text=f"Хранение заявок ({s.retention_resolved_tickets_days} дн.)",
                payload="admin_cfg_rt",
            )
        ],
        [CallbackButton(text=f"Сессии ({s.retention_sessions_days} дн.)", payload="admin_cfg_rs")],
        [CallbackButton(text=f"Логи ({s.retention_logs_days} дн.)", payload="admin_cfg_rl")],
        [CallbackButton(text=f"Temp-файлы ({s.retention_temp_files_days} дн.)", payload="admin_cfg_rf")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, text, attachments=[keyboard_to_attachment(kb)])


async def admin_cleanup_menu(event: MessageCallback, db: Session, max_id: str) -> None:
    s = get_system_settings(db)
    text = (
        "🧹 Очистка данных\n\n"
        f"Удаление решённых заявок старше {s.retention_resolved_tickets_days} дн.\n"
        f"Очистка сессий (истёкшие и неактивные > {s.retention_sessions_days} дн.)\n"
        f"Удаление временных файлов старше {s.retention_temp_files_days} дн.\n"
        f"(Параметр «логи» {s.retention_logs_days} дн. — для будущего журнала)\n\n"
        "Выберите операцию:"
    )
    kb = [
        [CallbackButton(text="🗑 Старые решённые заявки", payload="admin_cln_rtick")],
        [CallbackButton(text="🧹 Сессии FSM", payload="admin_cln_rsess")],
        [CallbackButton(text="📁 Временные файлы", payload="admin_cln_rtemp")],
    ]
    kb.extend(admin_kb_home())
    await safe_answer(event, max_id, text, attachments=[keyboard_to_attachment(kb)])


def _parse_positive_int(raw: str, max_val: int = 10_000) -> int | None:
    raw = (raw or "").strip()
    if not raw.isdigit():
        return None
    v = int(raw)
    if v <= 0 or v > max_val:
        return None
    return v


async def handle_admin_system_callbacks(
    event: MessageCallback,
    callback_data: str,
    user_id: int,
    db: Session,
    max_id: str,
) -> bool:
    if callback_data == "admin_settings":
        admin_fsm_clear_step(user_id)
        await admin_settings_menu(event, db, max_id)
        return True

    if callback_data == "admin_cleanup":
        admin_fsm_clear_step(user_id)
        await admin_cleanup_menu(event, db, max_id)
        return True

    cfg_map = {
        "admin_cfg_u": "set_urgent_sla",
        "admin_cfg_n": "set_normal_sla",
        "admin_cfg_rt": "set_retention_tickets",
        "admin_cfg_rs": "set_retention_sessions",
        "admin_cfg_rl": "set_retention_logs",
        "admin_cfg_rf": "set_retention_temp",
    }
    if callback_data in cfg_map:
        step = cfg_map[callback_data]
        admin_fsm_merge(user_id, admin_step=step)
        prompts = {
            "set_urgent_sla": "Введите SLA для срочных заявок (часы, целое число):",
            "set_normal_sla": "Введите SLA по умолчанию для обычных заявок (часы):",
            "set_retention_tickets": "Введите срок хранения решённых заявок (дни, потом их можно удалить очисткой):",
            "set_retention_sessions": "Введите период неактивности сессий для очистки (дни):",
            "set_retention_logs": "Введите срок хранения логов (дни, для будущего функционала):",
            "set_retention_temp": "Введите срок хранения временных файлов (дни):",
        }
        await safe_answer(
            event,
            max_id,
            prompts[step],
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        return True

    if callback_data == "admin_cln_rtick":
        s = get_system_settings(db)
        kb = [
            [
                CallbackButton(text="✅ Выполнить", payload="admin_cln_rtick_y"),
                CallbackButton(text="Отмена", payload="admin_cleanup"),
            ]
        ]
        kb.extend(admin_kb_home())
        await safe_answer(
            event,
            max_id,
            f"Удалить решённые заявки старше {s.retention_resolved_tickets_days} дн.?",
            attachments=[keyboard_to_attachment(kb)],
        )
        return True

    if callback_data == "admin_cln_rtick_y":
        s = get_system_settings(db)
        n = cleanup_resolved_tickets_older_than(db, s.retention_resolved_tickets_days)
        await safe_answer(event, max_id, f"✅ Удалено заявок: {n}")
        await admin_cleanup_menu(event, db, max_id)
        return True

    if callback_data == "admin_cln_rsess":
        s = get_system_settings(db)
        kb = [
            [
                CallbackButton(text="✅ Выполнить", payload="admin_cln_rsess_y"),
                CallbackButton(text="Отмена", payload="admin_cleanup"),
            ]
        ]
        kb.extend(admin_kb_home())
        await safe_answer(
            event,
            max_id,
            "Удалить истёкшие и давно неактивные сессии FSM?",
            attachments=[keyboard_to_attachment(kb)],
        )
        return True

    if callback_data == "admin_cln_rsess_y":
        s = get_system_settings(db)
        n = cleanup_sessions(db, s.retention_sessions_days)
        await safe_answer(event, max_id, f"✅ Удалено сессий: {n}")
        await admin_cleanup_menu(event, db, max_id)
        return True

    if callback_data == "admin_cln_rtemp":
        s = get_system_settings(db)
        kb = [
            [
                CallbackButton(text="✅ Выполнить", payload="admin_cln_rtemp_y"),
                CallbackButton(text="Отмена", payload="admin_cleanup"),
            ]
        ]
        kb.extend(admin_kb_home())
        await safe_answer(
            event,
            max_id,
            f"Удалить временные файлы старше {s.retention_temp_files_days} дн.?",
            attachments=[keyboard_to_attachment(kb)],
        )
        return True

    if callback_data == "admin_cln_rtemp_y":
        s = get_system_settings(db)
        n = cleanup_temp_files(s.retention_temp_files_days)
        await safe_answer(event, max_id, f"✅ Удалено файлов: {n}")
        await admin_cleanup_menu(event, db, max_id)
        return True

    return False


async def process_system_admin_text(
    event: MessageCreated,
    user_id: int,
    text: str,
    db: Session,
    max_id: str,
) -> bool:
    from app.fsm import FSM

    data = FSM.get_data(user_id)
    step = data.get("admin_step")
    raw = (text or "").strip()
    if len(raw) > TEXT_MAX:
        await safe_answer(event, max_id, "Слишком длинное значение.")
        return True

    s = get_system_settings(db)
    v = _parse_positive_int(raw)

    mapping = {
        "set_urgent_sla": ("urgent_sla_hours", v),
        "set_normal_sla": ("normal_default_sla_hours", v),
        "set_retention_tickets": ("retention_resolved_tickets_days", v),
        "set_retention_sessions": ("retention_sessions_days", v),
        "set_retention_logs": ("retention_logs_days", v),
        "set_retention_temp": ("retention_temp_files_days", v),
    }
    if step not in mapping:
        return False

    field, val = mapping[step]
    if val is None:
        await safe_answer(event, max_id, "Нужно положительное целое число (в разумных пределах).")
        return True

    setattr(s, field, val)
    db.commit()
    admin_fsm_clear_step(user_id)
    await safe_answer(
        event,
        max_id,
        "✅ Настройки сохранены. Откройте «⚙️ Настройки» снова, чтобы увидеть актуальные значения.",
        attachments=[keyboard_to_attachment(admin_kb_home())],
    )
    return True
