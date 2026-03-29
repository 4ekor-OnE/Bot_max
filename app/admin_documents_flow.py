"""Админка: документы инструкций."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.admin_common import admin_fsm_clear_step, admin_fsm_merge, admin_kb_home, shorten_text
from keyboards.keyboards import get_admin_menu_keyboard
from maxapi.types.attachments.buttons import CallbackButton
from models.instruction_document import InstructionDocument
from utils.keyboard_helper import keyboard_to_attachment
from utils.safe_reply import safe_answer

if TYPE_CHECKING:
    from maxapi.types.updates.message_callback import MessageCallback
    from maxapi.types.updates.message_created import MessageCreated

logger = logging.getLogger(__name__)

TEXT_MAX = 4000


async def admin_documents_menu(
    event: MessageCallback | MessageCreated, db: Session, max_id: str
) -> None:
    docs = db.query(InstructionDocument).order_by(InstructionDocument.id.desc()).all()
    kb = []
    for d in docs:
        kb.append(
            [
                CallbackButton(
                    text=f"📄 {shorten_text(d.title, 36)}",
                    payload=f"admin_idoc_{d.id}",
                )
            ]
        )
    kb.append([CallbackButton(text="➕ Загрузить документ", payload="admin_idoc_add")])
    kb.extend(admin_kb_home())
    await safe_answer(
        event,
        max_id,
        f"📄 Документы инструкций ({len(docs)})\n\n"
        "Выберите документ для управления или загрузите новый "
        "(сначала название, затем файл .txt / .pdf / .docx).",
        attachments=[keyboard_to_attachment(kb)],
    )


async def admin_document_detail(
    event: MessageCallback | MessageCreated,
    db: Session,
    doc_id: int,
    max_id: str,
) -> None:
    d = db.query(InstructionDocument).filter(InstructionDocument.id == doc_id).first()
    if not d:
        await safe_answer(
            event, max_id, "Документ не найден.", attachments=[keyboard_to_attachment(admin_kb_home())]
        )
        return
    kb = [
        [CallbackButton(text="✏️ Изменить название", payload=f"admin_idoc_rn_{d.id}")],
        [CallbackButton(text="🗑 Удалить", payload=f"admin_idoc_del_{d.id}")],
        [CallbackButton(text="◀️ К списку документов", payload="admin_documents")],
    ]
    kb.extend(admin_kb_home())
    fn = d.original_filename or d.stored_filename
    await safe_answer(
        event,
        max_id,
        f"📄 {d.title}\n\nФайл: {fn}\nID: {d.id}",
        attachments=[keyboard_to_attachment(kb)],
    )


async def handle_admin_documents_callbacks(
    event: MessageCallback,
    callback_data: str,
    user_id: int,
    db: Session,
    max_id: str,
) -> bool:
    if callback_data == "admin_documents":
        admin_fsm_clear_step(user_id)
        await admin_documents_menu(event, db, max_id)
        return True

    if callback_data == "admin_idoc_add":
        admin_fsm_merge(
            user_id,
            admin_step="instr_doc_title",
            pending_instr_title=None,
            pending_instr_file=None,
        )
        await safe_answer(
            event,
            max_id,
            "Введите отображаемое название документа (как увидят пользователи):",
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        return True

    m = re.match(r"^admin_idoc_(\d+)$", callback_data)
    if m:
        admin_fsm_clear_step(user_id)
        await admin_document_detail(event, db, int(m.group(1)), max_id)
        return True

    m = re.match(r"^admin_idoc_rn_(\d+)$", callback_data)
    if m:
        admin_fsm_merge(user_id, admin_step="instr_doc_rename", rename_doc_id=int(m.group(1)))
        await safe_answer(
            event,
            max_id,
            "Введите новое название документа:",
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        return True

    m = re.match(r"^admin_idoc_del_(\d+)$", callback_data)
    if m:
        did = int(m.group(1))
        d = db.query(InstructionDocument).filter(InstructionDocument.id == did).first()
        if not d:
            await safe_answer(event, max_id, "Документ не найден.")
            return True
        kb = [
            [
                CallbackButton(text="✅ Удалить", payload=f"admin_idoc_dely_{did}"),
                CallbackButton(text="Отмена", payload=f"admin_idoc_{did}"),
            ]
        ]
        kb.extend(admin_kb_home())
        await safe_answer(
            event,
            max_id,
            f"Удалить документ «{d.title}»?",
            attachments=[keyboard_to_attachment(kb)],
        )
        return True

    m = re.match(r"^admin_idoc_dely_(\d+)$", callback_data)
    if m:
        did = int(m.group(1))
        d = db.query(InstructionDocument).filter(InstructionDocument.id == did).first()
        if d:
            from pathlib import Path

            from config import INSTRUCTIONS_DIR

            path = INSTRUCTIONS_DIR / d.stored_filename
            try:
                if path.is_file():
                    path.unlink()
            except OSError as e:
                logger.warning("Не удалось удалить файл %s: %s", path, e)
            db.delete(d)
            db.commit()
            await safe_answer(event, max_id, "✅ Документ удалён.")
        await admin_documents_menu(event, db, max_id)
        return True

    return False


async def process_documents_admin_text(
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

    if step == "instr_doc_title":
        if not raw:
            await safe_answer(event, max_id, "Название не может быть пустым.")
            return True
        if len(raw) > 200:
            await safe_answer(event, max_id, "Название не длиннее 200 символов.")
            return True
        admin_fsm_merge(user_id, admin_step="instr_doc_file", pending_instr_title=raw)
        await safe_answer(
            event,
            max_id,
            "Отправьте файл вложением (txt, pdf или docx).",
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        return True

    if step == "instr_doc_rename":
        if not raw:
            await safe_answer(event, max_id, "Название не может быть пустым.")
            return True
        if len(raw) > 200:
            await safe_answer(event, max_id, "Название не длиннее 200 символов.")
            return True
        rid = data.get("rename_doc_id")
        d = db.query(InstructionDocument).filter(InstructionDocument.id == rid).first() if rid else None
        if not d:
            admin_fsm_clear_step(user_id)
            await safe_answer(event, max_id, "Документ не найден.")
            return True
        d.title = raw
        db.commit()
        admin_fsm_clear_step(user_id)
        await safe_answer(
            event, max_id, "✅ Название обновлено.", attachments=[keyboard_to_attachment(admin_kb_home())]
        )
        await admin_document_detail(event, db, d.id, max_id)
        return True

    return False


def _file_url_from_message(message) -> tuple[str | None, str | None]:
    body = getattr(message, "body", None)
    if not body:
        return None, None
    attachments = getattr(body, "attachments", None) or []
    for att in attachments:
        t = getattr(att, "type", None)
        ts = t if isinstance(t, str) else getattr(t, "value", str(t))
        if str(ts).lower() != "file":
            continue
        payload = getattr(att, "payload", None)
        url = getattr(payload, "url", None) if payload is not None else None
        if url is None and isinstance(payload, dict):
            url = payload.get("url")
        fn = getattr(att, "filename", None)
        return (url, fn)
    return None, None


async def try_consume_instruction_file_upload(
    event: MessageCreated, user_id: int, max_id: str
) -> bool:
    """Если ожидается файл инструкции — скачивает, сохраняет, создаёт запись в БД."""
    from app.fsm import FSM

    if FSM.get_state(user_id) != "admin_mode":
        return False
    data = FSM.get_data(user_id)
    if data.get("admin_step") != "instr_doc_file":
        return False
    title = data.get("pending_instr_title")
    if not title:
        return False

    url, orig = _file_url_from_message(event.message)
    if not url:
        await safe_answer(event, max_id, "Пришлите файл вложением (txt, pdf или docx).")
        return True

    from models.database import get_db_session

    from services.instruction_files import download_url_to_file, safe_extension, save_instruction_disk

    if not safe_extension(orig):
        await safe_answer(event, max_id, "Допустимы только файлы .txt, .pdf, .docx")
        return True

    import tempfile
    from pathlib import Path

    import os

    fd, tmp_name = tempfile.mkstemp(suffix=".bin")
    tmp_path = Path(tmp_name)
    try:
        await download_url_to_file(url, tmp_path)
    except Exception:
        logger.exception("Ошибка загрузки файла инструкции")
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        await safe_answer(event, max_id, "Не удалось получить файл. Попробуйте отправить снова.")
        return True

    try:
        os.close(fd)
    except OSError:
        pass

    try:
        raw = tmp_path.read_bytes()
        try:
            stored = save_instruction_disk(raw, orig)
        except ValueError as e:
            await safe_answer(event, max_id, str(e))
            return True

        db = next(get_db_session())
        try:
            doc = InstructionDocument(
                title=title,
                stored_filename=stored,
                original_filename=orig,
                mime_type=None,
            )
            db.add(doc)
            db.commit()
        finally:
            db.close()

        admin_fsm_clear_step(user_id)
        await safe_answer(
            event,
            max_id,
            f"✅ Документ «{title}» загружен.",
            attachments=[keyboard_to_attachment(admin_kb_home())],
        )
        db2 = next(get_db_session())
        try:
            await admin_documents_menu(event, db2, max_id)
        finally:
            db2.close()
        return True
    finally:
        tmp_path.unlink(missing_ok=True)
