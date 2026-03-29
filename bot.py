import asyncio
import csv
import io
import logging
from maxapi import Bot, Dispatcher, F
from maxapi.enums.upload_type import UploadType
from maxapi.types import MessageCreated, MessageCallback
from maxapi.types.input_media import InputMediaBuffer
from maxapi.utils.message import process_input_media
from config import BOT_TOKEN, verify_admin_password, ensure_data_dirs, INSTRUCTIONS_DIR, MAX_MESSAGE_TEXT_LENGTH
from utils.max_user import (
    max_user_id_from_message_callback,
    max_user_id_from_message_created,
    normalize_bot_command_line,
)
from models.database import init_db, get_db_session
from models.user import User, UserRole
from models.shop import Shop
from models.category import Category
from models.ticket import Ticket, TicketStatus, TicketPriority
from models.instruction_document import InstructionDocument
from models.ticket_comment import TicketComment
from datetime import datetime, timedelta, timezone
from keyboards.keyboards import get_main_menu_keyboard, get_back_button, get_ticket_filters_keyboard, get_admin_menu_keyboard
from utils.keyboard_helper import keyboard_to_attachment
from utils.callback_ack import acknowledge_callback
from utils.text_limits import validate_message_text
from services.notification_service import (
    notify_specialists_new_ticket,
    notify_ticket_comment_participants,
    notify_user_status_change,
)
from services.ticket_photos import is_image_attachment, persist_ticket_photo_from_attachment
from app.fsm import FSM, FSMState
from app.admin_panel import handle_admin_callback, process_admin_text
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.input_media import InputMedia
from utils.safe_reply import set_bot, safe_answer as safe_answer_core

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(BOT_TOKEN)
set_bot(bot)
dp = Dispatcher()


async def safe_answer_ui(event, max_id: str, text, attachments=None):
    """Делегирует в utils.safe_reply (единая логика для bot и админ-модулей)."""
    return await safe_answer_core(event, max_id, text, attachments)


def get_or_create_user(max_id: str, username: str = None, first_name: str = None):
    """Получить пользователя из БД или создать нового"""
    db = next(get_db_session())
    try:
        user = db.query(User).filter(User.max_id == max_id).first()
        if not user:
            user = User(
                max_id=max_id,
                username=username,
                first_name=first_name,
                role=UserRole.USER
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Создан новый пользователь: {max_id}")
        else:
            # Обновляем данные пользователя
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
            db.commit()
            logger.info(f"Пользователь найден: {max_id}")
        
        user_id = user.id
        role = user.role.value
        db.expunge(user)
        return user_id, role
    finally:
        db.close()


def get_user_role(max_id: str):
    """Получить роль пользователя"""
    db = next(get_db_session())
    try:
        user = db.query(User).filter(User.max_id == max_id).first()
        if user:
            return user.role.value
        return 'user'
    finally:
        db.close()


def get_user_id_by_max_id(max_id: str):
    """Получить user_id по max_id"""
    db = next(get_db_session())
    try:
        user = db.query(User).filter(User.max_id == max_id).first()
        if user:
            return user.id
        return None
    finally:
        db.close()


async def show_filtered_tickets(user_id: int, status_filter: str = None, event=None):
    """Показать отфильтрованные заявки пользователя"""
    db = next(get_db_session())
    try:
        row_user = db.query(User).filter(User.id == user_id).first()
        max_mid = row_user.max_id if row_user else ""

        # Базовый запрос - все заявки пользователя
        query = db.query(Ticket).filter(Ticket.user_id == user_id)
        
        # Применяем фильтр по статусу, если указан
        if status_filter and status_filter != 'all':
            try:
                filter_status = TicketStatus(status_filter)
                query = query.filter(Ticket.status == filter_status)
            except ValueError:
                # Если статус невалидный, показываем все заявки
                pass
        
        # Сортируем по дате создания (новые первыми)
        tickets = query.order_by(Ticket.created_at.desc()).all()
        
        if not tickets:
            filter_text = {
                'all': 'все',
                'new': 'новые',
                'in_progress': 'в работе',
                'resolved': 'решенные',
                'postponed': 'отложенные'
            }.get(status_filter, 'все')
            
            await safe_answer_ui(
                event,
                max_mid,
                f"📋 Мои заявки\n\nУ вас нет {filter_text} заявок.",
                attachments=[keyboard_to_attachment(get_ticket_filters_keyboard(status_filter))],
            )
            return
        
        # Создаем клавиатуру со списком заявок
        tickets_kb = []
        for ticket in tickets[:10]:  # Показываем максимум 10 заявок
            status_emoji = {
                TicketStatus.NEW: '🆕',
                TicketStatus.IN_PROGRESS: '⚙️',
                TicketStatus.RESOLVED: '✅',
                TicketStatus.POSTPONED: '⏸️'
            }.get(ticket.status, '📋')
            
            cd = ticket.created_at.strftime("%d.%m") if ticket.created_at else ""
            photo_mark = " 📷" if ticket.photo_path else ""
            title_cut = (ticket.title or "")[:22] + ("…" if len(ticket.title or "") > 22 else "")
            button_text = f"{status_emoji} #{ticket.id} {cd}{photo_mark} {title_cut}"
            
            tickets_kb.append([CallbackButton(text=button_text, payload=f'ticket_{ticket.id}')])
        
        # Добавляем кнопки фильтров
        filters_kb = get_ticket_filters_keyboard(status_filter or 'all')
        tickets_kb.extend(filters_kb)
        
        # Формируем сообщение со статистикой
        all_tickets = db.query(Ticket).filter(Ticket.user_id == user_id).all()
        status_counts = {}
        for ticket in all_tickets:
            status = ticket.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
        
        filter_name = {
            'all': 'Все заявки',
            'new': 'Новые заявки',
            'in_progress': 'Заявки в работе',
            'resolved': 'Решенные заявки',
            'postponed': 'Отложенные заявки'
        }.get(status_filter or 'all', 'Все заявки')
        
        stats_text = f"📋 Мои заявки\n\n🔍 Фильтр: {filter_name}\n\n"
        stats_text += f"Найдено: {len(tickets)} из {len(all_tickets)}\n\n"
        stats_text += f"📊 Статистика:\n"
        stats_text += f"🆕 Новые: {status_counts.get('new', 0)}\n"
        stats_text += f"⚙️ В работе: {status_counts.get('in_progress', 0)}\n"
        stats_text += f"✅ Решенные: {status_counts.get('resolved', 0)}\n"
        stats_text += f"⏸️ Отложенные: {status_counts.get('postponed', 0)}\n\n"
        stats_text += "Выберите заявку для просмотра или используйте фильтры:"
        
        await safe_answer_ui(
            event,
            max_mid,
            stats_text,
            attachments=[keyboard_to_attachment(tickets_kb)],
        )
    finally:
        db.close()


async def create_ticket_from_fsm(user_id: int, max_id: str, event):
    """Создать заявку из данных FSM"""
    # Получаем данные из FSM
    fsm_data = FSM.get_data(user_id)
    logger.info(f"Создание заявки с данными FSM: {fsm_data}")
    
    # Проверяем наличие всех необходимых данных
    required_fields = ['shop_id', 'category_id', 'title', 'description']
    missing_fields = [field for field in required_fields if field not in fsm_data]
    
    if missing_fields:
        await safe_answer_ui(
            event,
            max_id,
            f"Ошибка: не хватает данных для создания заявки. Отсутствуют: {', '.join(missing_fields)}",
            attachments=[keyboard_to_attachment(get_back_button())],
        )
        return
    
    # Создаем заявку
    try:
        db = next(get_db_session())
        try:
            from utils.settings_service import get_system_settings

            category = db.query(Category).filter(Category.id == fsm_data['category_id']).first()
            settings = get_system_settings(db)
            is_urgent = bool(fsm_data.get("urgent_ticket"))
            if is_urgent:
                sla_hours = settings.urgent_sla_hours
                priority = TicketPriority.HIGH
            else:
                sla_hours = category.sla_hours if category else settings.normal_default_sla_hours
                priority = TicketPriority.NORMAL

            # Получаем название магазина
            shop = db.query(Shop).filter(Shop.id == fsm_data['shop_id']).first()
            shop_name = shop.name if shop else f"Магазин #{fsm_data['shop_id']}"
            
            # Создаем заявку
            ticket = Ticket(
                user_id=user_id,
                shop_id=fsm_data['shop_id'],
                category_id=fsm_data['category_id'],
                title=fsm_data['title'],
                description=fsm_data['description'],
                is_urgent=is_urgent,
                status=TicketStatus.NEW,
                priority=priority,
                photo_path=fsm_data.get('photo_path'),  # Может быть None
                sla_deadline=datetime.now(timezone.utc) + timedelta(hours=sla_hours)
            )
            db.add(ticket)
            db.commit()
            db.refresh(ticket)
            ticket_id = ticket.id
            logger.info(f"Заявка #{ticket_id} создана успешно")
            try:
                await notify_specialists_new_ticket(bot, db, ticket)
            except Exception as notify_err:
                logger.warning(
                    "Заявка #%s создана, но уведомления специалистам не отправлены: %s",
                    ticket_id,
                    notify_err,
                    exc_info=True,
                )
        finally:
            db.close()
        
        # Очищаем FSM
        FSM.clear(user_id)
        
        # Отправляем подтверждение
        role = get_user_role(max_id)
        keyboard = get_main_menu_keyboard(role)
        photo_text = "📷 Фото прикреплено" if fsm_data.get('photo_path') else ""
        urgent_hdr = "🚨 Срочная заявка\n\n" if fsm_data.get("urgent_ticket") else ""
        pr_line = ""
        if fsm_data.get("urgent_ticket"):
            pr_line = "⚡ Приоритет: высокий\n"
        title_line = (fsm_data.get("title") or "")[:500]
        success_body = (
            f"✅ Заявка #{ticket_id} успешно создана!\n\n"
            f"{urgent_hdr}"
            f"📝 Заголовок: {title_line}\n"
            f"🏪 Магазин: {shop_name}\n"
            f"📋 Категория: {fsm_data.get('category_name', 'Не указана')}\n"
            f"{pr_line}"
            f"{photo_text}\n\n"
            f"Ваша заявка будет обработана в ближайшее время."
        )
        if len(success_body) > MAX_MESSAGE_TEXT_LENGTH:
            success_body = success_body[: MAX_MESSAGE_TEXT_LENGTH - 1] + "…"
        await safe_answer_ui(
            event,
            max_id,
            success_body,
            attachments=[keyboard_to_attachment(keyboard)],
        )
    except Exception as e:
        logger.error(f"Ошибка при создании заявки: {e}", exc_info=True)
        await safe_answer_ui(
            event,
            max_id,
            "Произошла ошибка при создании заявки. Попробуйте позже.",
            attachments=[keyboard_to_attachment(get_back_button())],
        )


def format_ticket_confirmation_summary(fsm_data: dict) -> str:
    """Текст шага подтверждения (ТЗ п. 4.1)."""
    urgent = fsm_data.get("urgent_ticket")
    head = "🚨 Срочная заявка — подтверждение\n\n" if urgent else "📝 Подтверждение заявки\n\n"
    photo = "да" if fsm_data.get("photo_path") else "нет"
    lines = [
        head,
        f"🏪 Магазин: {fsm_data.get('shop_name', '—')}",
        f"📂 Категория: {fsm_data.get('category_name', '—')}",
        f"📝 Заголовок: {fsm_data.get('title', '—')}",
        f"📄 Описание:\n{fsm_data.get('description', '—')}",
        f"📷 Фото: {photo}",
        "",
        "Создать заявку с этими данными?",
    ]
    return "\n".join(lines)[:MAX_MESSAGE_TEXT_LENGTH]


async def show_ticket_confirmation(user_id: int, max_id: str, event):
    """Переход к состоянию подтверждения перед записью в БД."""
    fsm_data = FSM.get_data(user_id)
    FSM.set_state(user_id, FSMState.CONFIRM.value, fsm_data)
    kb = [
        [CallbackButton(text="✅ Подтвердить", payload="ticket_confirm_submit")],
        [CallbackButton(text="❌ Отмена", payload="ticket_confirm_cancel")],
        [CallbackButton(text="◀️ Назад", payload="back_to_main")],
    ]
    await safe_answer_ui(
        event,
        max_id,
        format_ticket_confirmation_summary(fsm_data),
        attachments=[keyboard_to_attachment(kb)],
    )


def parse_iso_date(s: str):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _tickets_for_period(date_from_s: str, date_to_s: str) -> tuple[list, str | None]:
    """Возвращает (список заявок, текст_ошибки)."""
    d0 = parse_iso_date(date_from_s)
    d1 = parse_iso_date(date_to_s)
    if not d0 or not d1:
        return [], "Неверный формат дат. Ожидается ГГГГ-ММ-ДД."
    if d1 < d0:
        return [], "Дата окончания не может быть раньше даты начала."
    start = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)
    end_excl = datetime(d1.year, d1.month, d1.day, tzinfo=timezone.utc) + timedelta(days=1)
    db = next(get_db_session())
    try:
        tickets = (
            db.query(Ticket)
            .filter(Ticket.created_at >= start, Ticket.created_at < end_excl)
            .order_by(Ticket.id)
            .all()
        )
        return tickets, None
    finally:
        db.close()


def _tickets_to_csv_bytes(tickets: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "id",
            "user_id",
            "shop_id",
            "category_id",
            "title",
            "status",
            "priority",
            "is_urgent",
            "created_at",
            "resolved_at",
            "assigned_to",
        ]
    )
    for t in tickets:
        w.writerow(
            [
                t.id,
                t.user_id,
                t.shop_id,
                t.category_id,
                (t.title or "").replace("\n", " ")[:2000],
                t.status.value,
                t.priority.value,
                int(bool(t.is_urgent)),
                t.created_at.isoformat() if t.created_at else "",
                t.resolved_at.isoformat() if t.resolved_at else "",
                t.assigned_to or "",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def _tickets_to_xlsx_bytes(tickets: list) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "tickets"
    headers = [
        "id",
        "user_id",
        "shop_id",
        "category_id",
        "title",
        "status",
        "priority",
        "is_urgent",
        "created_at",
        "resolved_at",
        "assigned_to",
    ]
    ws.append(headers)
    for t in tickets:
        ws.append(
            [
                t.id,
                t.user_id,
                t.shop_id,
                t.category_id,
                (t.title or "").replace("\n", " ")[:2000],
                t.status.value,
                t.priority.value,
                int(bool(t.is_urgent)),
                t.created_at.isoformat() if t.created_at else "",
                t.resolved_at.isoformat() if t.resolved_at else "",
                t.assigned_to or "",
            ]
        )
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


async def deliver_director_period_reports(
    user_id: int, max_id: str, event, date_from_s: str, date_to_s: str, fmt: str
):
    """CSV и/или Excel по заявкам за период (ТЗ: отчёт директора). fmt: csv | xlsx | both"""
    tickets, err = _tickets_for_period(date_from_s, date_to_s)
    if err:
        await safe_answer_ui(event, max_id, err)
        return
    caption_base = f"За период {date_from_s} — {date_to_s}, заявок: {len(tickets)}"
    if fmt in ("csv", "both"):
        raw = _tickets_to_csv_bytes(tickets)
        im = InputMediaBuffer(buffer=raw, filename="report.csv", type=UploadType.FILE)
        att = await process_input_media(bot, bot, im)
        await bot.send_message(
            user_id=int(max_id),
            text=f"📄 CSV — {caption_base}",
            attachments=[att],
        )
    if fmt in ("xlsx", "both"):
        raw = _tickets_to_xlsx_bytes(tickets)
        im = InputMediaBuffer(buffer=raw, filename="report.xlsx", type=UploadType.FILE)
        att = await process_input_media(bot, bot, im)
        await bot.send_message(
            user_id=int(max_id),
            text=f"📗 Excel — {caption_base}",
            attachments=[att],
        )
    FSM.clear(user_id)


async def deliver_director_csv_report(user_id: int, max_id: str, event, date_from_s: str, date_to_s: str):
    """Обратная совместимость: только CSV."""
    await deliver_director_period_reports(user_id, max_id, event, date_from_s, date_to_s, "csv")


def get_shops_keyboard():
    """Получить клавиатуру со списком магазинов"""
    db = next(get_db_session())
    try:
        shops = db.query(Shop).all()
        keyboard = []
        for shop in shops:
            keyboard.append([CallbackButton(text=shop.name, payload=f'shop_{shop.id}')])
        keyboard.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
        return keyboard
    finally:
        db.close()


@dp.message_created(F.message.body.text == '/start')
async def handle_start(event: MessageCreated):
    """Обработчик команды /start"""
    await handle_start_command(event)


@dp.message_created(F.message.body.text.startswith('/start@'))
async def handle_start_with_bot_suffix(event: MessageCreated):
    """Команда /start@botname — то же, что /start."""
    await handle_start_command(event)


@dp.message_created(F.message.body.text.startswith('/admin'))
async def handle_admin_command(event: MessageCreated):
    """Обработчик команды /admin <пароль>"""
    try:
        # Получаем текст сообщения
        text = None
        try:
            if hasattr(event.message, 'body') and hasattr(event.message.body, 'text'):
                text = event.message.body.text
            elif hasattr(event.message, 'text'):
                text = event.message.text
        except Exception as e:
            logger.error(f"Ошибка получения текста сообщения: {e}")
        
        if not text:
            await event.message.answer("Ошибка: не удалось получить текст команды")
            return
        
        text = normalize_bot_command_line(text)
        logger.info("Получена команда /admin")
        
        parts = text.split(' ', 1)
        
        if len(parts) < 2:
            await event.message.answer(
                "❌ Неверный формат команды.\n\n"
                "Использование: /admin <пароль>\n\n"
                "Пример: /admin admin"
            )
            return
        
        password = parts[1].strip()
        
        # Проверяем пароль
        if verify_admin_password(password):
            max_id = max_user_id_from_message_created(event)
            
            if not max_id:
                await event.message.answer("Ошибка: не удалось определить пользователя")
                return
            
            username = None
            first_name = None
            try:
                s = getattr(event.message, "sender", None)
                if s:
                    first_name = getattr(s, "first_name", None)
                    username = getattr(s, "username", None)
            except Exception:
                pass
            
            user_id, _role = get_or_create_user(max_id, username, first_name)
            FSM.set_state(user_id, 'admin_mode', {'admin': True})
            
            # Показываем меню администратора
            admin_kb = get_admin_menu_keyboard()
            await event.message.answer(
                "🔐 Административная панель\n\n"
                "Добро пожаловать в панель администратора!\n\n"
                "Выберите раздел для управления:",
                attachments=[keyboard_to_attachment(admin_kb)]
            )
            logger.info(f"Пользователь max_id={max_id} вошел в админ-панель")
        else:
            await event.message.answer(
                "❌ Неверный пароль администратора.\n\n"
                "Доступ запрещен."
            )
            logger.warning("Попытка входа в админ-панель с неверным паролем")
    except Exception as e:
        logger.error(f"Ошибка при обработке /admin: {e}", exc_info=True)
        try:
            await event.message.answer("Произошла ошибка при обработке команды.")
        except:
            pass


@dp.message_created()
async def handle_message_with_photo(event: MessageCreated):
    """Обработчик сообщений с вложениями (фото заявки, файлы инструкций)."""
    max_id_early = max_user_id_from_message_created(event)
    if max_id_early:
        _uid = get_user_id_by_max_id(max_id_early)
        if _uid:
            from app.admin_documents_flow import try_consume_instruction_file_upload

            if await try_consume_instruction_file_upload(event, _uid, max_id_early):
                return

    image_attachment = None
    try:
        if hasattr(event.message, 'body') and hasattr(event.message.body, 'attachments'):
            attachments = event.message.body.attachments or []
            for attachment in attachments:
                if is_image_attachment(attachment):
                    image_attachment = attachment
                    break
    except Exception as e:
        logger.warning(f"Ошибка при проверке фото: {e}")
    
    if image_attachment is not None:
        max_id = max_user_id_from_message_created(event)
        if not max_id:
            return
        
        user_id = get_user_id_by_max_id(max_id)
        if not user_id:
            return
        
        # Проверяем FSM состояние
        state = FSM.get_state(user_id)
        if state == FSMState.ADD_PHOTO.value:
            try:
                photo_ref = await persist_ticket_photo_from_attachment(image_attachment)
            except Exception:
                logger.exception("Не удалось сохранить фото заявки для user_id %s", user_id)
                photo_ref = None
            logger.info(
                "Получено фото для user_id %s, сохранено как: %s",
                user_id,
                photo_ref or "uploaded",
            )
            
            # Сохраняем URL, local:файл или метку «uploaded»
            fsm_data = FSM.get_data(user_id)
            fsm_data = fsm_data.copy()
            fsm_data['photo_path'] = photo_ref or 'uploaded'
            FSM.set_state(user_id, FSMState.ADD_PHOTO.value, fsm_data)
            
            # Подтверждение перед созданием
            await show_ticket_confirmation(user_id, max_id, event)
        else:
            # Фото отправлено не в процессе создания заявки
            await event.message.answer("Фото получено, но сейчас не ожидается загрузка фото.")
        return
    
    # Если это текстовое сообщение, передаем обработку дальше
    if hasattr(event.message, 'body') and hasattr(event.message.body, 'text') and event.message.body.text:
        await handle_text_message(event)


@dp.message_created(F.message.body.text)
async def handle_text_message(event: MessageCreated):
    """Обработчик текстовых сообщений для FSM"""
    text = event.message.body.text
    
    # Пропускаем команды (обрабатываются отдельным обработчиком)
    if text and text.startswith('/'):
        # Команды /start и /admin обрабатываются отдельными обработчиками
        if text.startswith('/start') or text.startswith('/admin'):
            return
    
    logger.info(f"=== ОБРАБОТКА ТЕКСТОВОГО СООБЩЕНИЯ ===")
    logger.info(f"Текст: {text[:100]}")
    logger.info(f"Тип event: {type(event)}")
    logger.info(f"Атрибуты event: {[a for a in dir(event) if not a.startswith('_')][:10]}")
    
    # Проверяем FSM состояние
    try:
        max_id = max_user_id_from_message_created(event)
        if max_id:
            logger.info(f"Получен MAX ID пользователя: {max_id}")
        
        if not max_id:
            logger.error("Не удалось получить max_id для текстового сообщения. Доступные атрибуты:")
            logger.error(f"event attributes: {dir(event)}")
            if hasattr(event, 'message'):
                logger.error(f"event.message attributes: {dir(event.message)}")
            return
        
        logger.info(f"Ищем user_id для max_id={max_id}")
        user_id = get_user_id_by_max_id(max_id)
        logger.info(f"Найден user_id: {user_id}")
        if not user_id:
            logger.error(f"Пользователь с max_id={max_id} не найден в БД")
            return
        
        state = FSM.get_state(user_id)
        logger.info(f"FSM состояние: {state}, текст: {text[:50]}, user_id: {user_id}")
        logger.info(f"ENTER_TITLE.value = '{FSMState.ENTER_TITLE.value}', ENTER_DESCRIPTION.value = '{FSMState.ENTER_DESCRIPTION.value}'")
        logger.info(f"Сравнение с ENTER_TITLE: {state == FSMState.ENTER_TITLE.value}")
        logger.info(f"Сравнение с ENTER_DESCRIPTION: {state == FSMState.ENTER_DESCRIPTION.value}")
        
        if state == 'admin_mode':
            if await process_admin_text(event, user_id, text, max_id):
                return

        if state == FSMState.CONFIRM.value:
            await event.message.answer("Подтвердите создание заявки кнопками «Подтвердить» или «Отмена».")
            return

        if state == FSMState.ENTER_TICKET_COMMENT.value:
            if get_user_role(max_id) != "support":
                FSM.clear(user_id)
                return
            err = validate_message_text(text)
            if err:
                await event.message.answer(err)
                return
            data = FSM.get_data(user_id)
            tid = data.get("comment_ticket_id")
            if not tid:
                FSM.clear(user_id)
                return
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == tid).first()
                if not ticket:
                    await event.message.answer("Заявка не найдена.")
                    FSM.clear(user_id)
                    return
                db.add(
                    TicketComment(
                        ticket_id=tid,
                        user_id=user_id,
                        text=text,
                        is_system=False,
                    )
                )
                db.commit()
                await notify_ticket_comment_participants(bot, db, ticket, user_id, text)
            finally:
                db.close()
            FSM.clear(user_id)
            role = get_user_role(max_id)
            await event.message.answer(
                "✅ Комментарий сохранён.",
                attachments=[keyboard_to_attachment(get_main_menu_keyboard(role))],
            )
            return

        if state == FSMState.DIRECTOR_REPORT_FROM.value:
            if get_user_role(max_id) != "director":
                FSM.clear(user_id)
                return
            if not parse_iso_date(text):
                await event.message.answer("Неверный формат. Введите дату начала как ГГГГ-ММ-ДД (например 2025-01-01).")
                return
            prev = FSM.get_data(user_id)
            FSM.set_state(
                user_id,
                FSMState.DIRECTOR_REPORT_TO.value,
                {
                    "report_date_from": text.strip(),
                    "report_fmt": prev.get("report_fmt") or "csv",
                },
            )
            await event.message.answer("Шаг 2/2: введите дату окончания периода (ГГГГ-ММ-ДД) включительно.")
            return

        if state == FSMState.DIRECTOR_REPORT_TO.value:
            if get_user_role(max_id) != "director":
                FSM.clear(user_id)
                return
            if not parse_iso_date(text):
                await event.message.answer("Неверный формат. Используйте ГГГГ-ММ-ДД.")
                return
            data = FSM.get_data(user_id)
            df = data.get("report_date_from")
            if not df:
                FSM.clear(user_id)
                return
            fmt = data.get("report_fmt") or "csv"
            if fmt not in ("csv", "xlsx", "both"):
                fmt = "csv"
            await deliver_director_period_reports(user_id, max_id, event, df, text.strip(), fmt)
            return
        
        if state == FSMState.ENTER_TITLE.value:
            # Сохраняем заголовок
            logger.info("Обработка состояния ENTER_TITLE")
            err = validate_message_text(text)
            if err:
                await event.message.answer(err)
                return
            fsm_data = FSM.get_data(user_id)
            logger.info(f"Данные FSM перед сохранением заголовка: {fsm_data}")
            # Создаем новый словарь с обновленными данными
            fsm_data = fsm_data.copy()
            fsm_data['title'] = text
            FSM.set_state(user_id, FSMState.ENTER_DESCRIPTION.value, fsm_data)
            logger.info(f"Заголовок сохранен: {text[:50]}, данные FSM: {FSM.get_data(user_id)}")
            urgent = fsm_data.get("urgent_ticket")
            header = "🚨 Срочная заявка\n\n" if urgent else "📝 Создание заявки\n\n"
            step = "Шаг 3/4" if urgent else "Шаг 4/5"
            await event.message.answer(
                f"{header}✅ Заголовок: {text}\n\n{step}: введите подробное описание проблемы"
            )
        elif state == FSMState.ENTER_DESCRIPTION.value:
            # Сохраняем описание
            logger.info("Обработка состояния ENTER_DESCRIPTION")
            err = validate_message_text(text)
            if err:
                await event.message.answer(err)
                return
            fsm_data = FSM.get_data(user_id)
            logger.info(f"Данные FSM перед сохранением описания: {fsm_data}")
            # Создаем новый словарь с обновленными данными
            fsm_data = fsm_data.copy()
            fsm_data['description'] = text
            FSM.set_state(user_id, FSMState.ADD_PHOTO.value, fsm_data)
            logger.info(f"Описание сохранено: {text[:50]}, данные FSM: {FSM.get_data(user_id)}")
            urgent = fsm_data.get("urgent_ticket")
            header = "🚨 Срочная заявка\n\n" if urgent else "📝 Создание заявки\n\n"
            step = "Шаг 4/4" if urgent else "Шаг 5/5"
            try:
                keyboard = [
                    [CallbackButton(text='📷 Да, прикрепить фото', payload='add_photo_yes')],
                    [CallbackButton(text='⏭️ Пропустить', payload='add_photo_no')],
                    [CallbackButton(text='◀️ Назад', payload='back_to_main')]
                ]
                logger.info("Создание клавиатуры для запроса фото")
                attachment = keyboard_to_attachment(keyboard)
                logger.info(f"Клавиатура создана: {attachment}")
                logger.info("Отправка сообщения с запросом фото")
                await event.message.answer(
                    f"{header}✅ Описание сохранено\n\n{step}: хотите прикрепить фото?",
                    attachments=[attachment]
                )
                logger.info("Сообщение с запросом фото отправлено")
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения с запросом фото: {e}", exc_info=True)
                # Пробуем отправить без клавиатуры
                try:
                    await event.message.answer(
                        f"{header}✅ Описание сохранено\n\n{step}: хотите прикрепить фото?"
                    )
                except Exception as e2:
                    logger.error(f"Ошибка при отправке сообщения без клавиатуры: {e2}", exc_info=True)
        else:
            logger.warning(f"Текстовое сообщение получено, но состояние FSM не требует обработки: {state}")
            logger.warning(f"Ожидаемые состояния: {FSMState.ENTER_TITLE.value} или {FSMState.ENTER_DESCRIPTION.value}, текущее: {state}")
    except Exception as e:
        logger.error(f"Ошибка при обработке текстового сообщения: {e}", exc_info=True)


async def handle_start_command(event: MessageCreated):
    """Обработчик команды /start"""
    try:
        logger.info("Получена команда /start")
        
        max_id = max_user_id_from_message_created(event)
        if max_id:
            logger.info(f"✅ MAX ID пользователя: {max_id}")
            logger.info(f"📝 ВАШ MAX_ID ДЛЯ НАСТРОЙКИ РОЛИ: {max_id}")
        
        if not max_id:
            await event.message.answer("Ошибка: не удалось определить ваш ID")
            return
        
        username = None
        first_name = None
        try:
            s = getattr(event.message, "sender", None)
            if s:
                first_name = getattr(s, "first_name", None)
                username = getattr(s, "username", None)
        except Exception as e:
            logger.warning(f"Ошибка получения имени: {e}")
        
        # Создаем или получаем пользователя
        user_id, role = get_or_create_user(max_id, username, first_name)
        
        # Получаем клавиатуру в зависимости от роли
        keyboard = get_main_menu_keyboard(role)
        
        await event.message.answer(
            f"Привет! Добро пожаловать в службу поддержки.\n"
            f"Выберите действие:",
            attachments=[keyboard_to_attachment(keyboard)]
        )
        logger.info(f"Команда /start успешно обработана для пользователя {user_id} (роль: {role})")
    except Exception as e:
        logger.error(f"Ошибка при обработке /start: {e}", exc_info=True)
        try:
            await event.message.answer("Произошла ошибка. Попробуйте позже.")
        except:
            pass


@dp.message_callback()
async def handle_callback(event: MessageCallback):
    """Обработчик нажатий на inline-кнопки"""
    try:
        logger.info("=== ПОЛУЧЕН MESSAGECALLBACK ===")
        logger.info(f"Тип event: {type(event)}")
        logger.info(f"Атрибуты event: {[a for a in dir(event) if not a.startswith('_')]}")
        
        # Получаем callback_data - основной способ через event.callback.payload
        callback_data = None
        try:
            if hasattr(event, 'callback') and event.callback:
                callback_data = event.callback.payload
                logger.info(f"callback_data из event.callback.payload: {callback_data}")
        except Exception as e:
            logger.error(f"Ошибка при получении callback_data: {e}", exc_info=True)
        
        if not callback_data:
            logger.warning("Не удалось получить callback_data. Доступные атрибуты:")
            logger.warning(f"event attributes: {dir(event)}")
            if hasattr(event, 'callback'):
                logger.warning(f"event.callback: {event.callback}")
            if hasattr(event, 'button'):
                logger.warning(f"event.button: {event.button}")
            return

        callback_data = str(callback_data).strip()
        
        logger.info(f"Обрабатываем callback: {callback_data}")
        
        max_id = max_user_id_from_message_callback(event)
        if max_id:
            logger.info(f"max_id пользователя (callback): {max_id}")
        
        if not max_id:
            logger.error("Не удалось получить max_id")
            return
        
        role = get_user_role(max_id)
        
        # Подтверждаем callback без echo вложений (см. utils/callback_ack.py)
        try:
            await acknowledge_callback(event)
            logger.info("Ответ на callback отправлен")
        except Exception as e:
            logger.warning(f"Не удалось ответить на callback: {e}")
        
        # Обработка различных callback
        if callback_data.startswith('admin_'):
            aid = get_user_id_by_max_id(max_id)
            is_admin_mode = False
            if aid:
                is_admin_mode = FSM.get_state(aid) == 'admin_mode'
            if await handle_admin_callback(event, callback_data, aid, max_id, is_admin_mode):
                return
        
        if callback_data == 'back_to_main':
            # Очищаем FSM состояние при возврате в главное меню
            user_id = get_user_id_by_max_id(max_id)
            if user_id:
                FSM.clear(user_id)
            
            keyboard = get_main_menu_keyboard(role)
            await safe_answer_ui(event, max_id,
                "Главное меню:",
                attachments=[keyboard_to_attachment(keyboard)]
            )
        elif callback_data == 'create_ticket':
            logger.info("Обработка create_ticket")
            # Начинаем создание заявки - первый шаг: выбор магазина
            user_id = get_user_id_by_max_id(max_id)
            logger.info(f"user_id: {user_id}")
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем, есть ли магазины
            db = next(get_db_session())
            try:
                shops_count = db.query(Shop).count()
                logger.info(f"Количество магазинов: {shops_count}")
                if shops_count == 0:
                    await safe_answer_ui(event, max_id,
                        "📝 Создание заявки\n\n⚠️ Магазины не настроены. Обратитесь к администратору.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
            finally:
                db.close()
            
            # Устанавливаем состояние FSM
            FSM.set_state(user_id, FSMState.SELECT_SHOP.value, {})
            logger.info(f"FSM состояние установлено: {FSMState.SELECT_SHOP.value}")
            
            # Показываем список магазинов
            shops_kb = get_shops_keyboard()
            logger.info(f"Клавиатура магазинов создана, кнопок: {len(shops_kb)}")
            
            # Отправляем сообщение
            await safe_answer_ui(event, max_id,
                "📝 Создание заявки\n\nШаг 1/5: Выберите магазин",
                attachments=[keyboard_to_attachment(shops_kb)]
            )
            logger.info("Сообщение отправлено")
        elif callback_data == 'create_urgent_ticket':
            logger.info("Обработка create_urgent_ticket")
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            db = next(get_db_session())
            try:
                shops_count = db.query(Shop).count()
                if shops_count == 0:
                    await safe_answer_ui(event, max_id,
                        "🚨 Срочная заявка\n\n⚠️ Магазины не настроены. Обратитесь к администратору.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
            finally:
                db.close()
            FSM.set_state(user_id, FSMState.SELECT_SHOP.value, {"urgent_ticket": True})
            shops_kb = get_shops_keyboard()
            await safe_answer_ui(event, max_id,
                "🚨 Срочная заявка\n\n"
                "Категория будет назначена автоматически: «Касса и оплата».\n"
                "⚡ Приоритет: высокий, ⏱️ SLA — по настройкам срочных заявок.\n\n"
                "Шаг 1/4: выберите магазин",
                attachments=[keyboard_to_attachment(shops_kb)]
            )
        elif callback_data == 'my_tickets':
            # Показываем список заявок пользователя (все заявки)
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_filtered_tickets(user_id, 'all', event)
        elif callback_data == 'filter_tickets_all':
            # Фильтр: все заявки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_filtered_tickets(user_id, 'all', event)
        elif callback_data == 'filter_tickets_new':
            # Фильтр: новые заявки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_filtered_tickets(user_id, 'new', event)
        elif callback_data == 'filter_tickets_in_progress':
            # Фильтр: заявки в работе
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_filtered_tickets(user_id, 'in_progress', event)
        elif callback_data == 'filter_tickets_resolved':
            # Фильтр: решенные заявки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_filtered_tickets(user_id, 'resolved', event)
        elif callback_data == 'filter_tickets_postponed':
            # Фильтр: отложенные заявки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_filtered_tickets(user_id, 'postponed', event)
        elif callback_data.startswith("ticket_") and callback_data[7:].isdigit():
            # Просмотр деталей: только ticket_<число> (не ticket_confirm_* / ticket_comment_*)
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            ticket_id = int(callback_data[7:])
            
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.user_id == user_id).first()
                
                if not ticket:
                    await safe_answer_ui(event, max_id,
                        "Заявка не найдена или у вас нет доступа к ней.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Получаем дополнительную информацию
                shop = db.query(Shop).filter(Shop.id == ticket.shop_id).first()
                category = db.query(Category).filter(Category.id == ticket.category_id).first()
                
                shop_name = shop.name if shop else f"Магазин #{ticket.shop_id}"
                category_name = category.name if category else f"Категория #{ticket.category_id}"
                
                # Формируем статус
                status_text = {
                    TicketStatus.NEW: '🆕 Новая',
                    TicketStatus.IN_PROGRESS: '⚙️ В работе',
                    TicketStatus.RESOLVED: '✅ Решена',
                    TicketStatus.POSTPONED: '⏸️ Отложена'
                }.get(ticket.status, ticket.status.value)
                
                # Формируем приоритет
                priority_text = {
                    TicketPriority.LOW: '🟢 Низкий',
                    TicketPriority.NORMAL: '🟡 Обычный',
                    TicketPriority.HIGH: '🟠 Высокий',
                    TicketPriority.URGENT: '🔴 Срочный'
                }.get(ticket.priority, ticket.priority.value)
                
                # Формируем дату
                created_date = ticket.created_at.strftime('%d.%m.%Y %H:%M') if ticket.created_at else 'Не указана'
                
                # Формируем сообщение
                details_text = f"📋 Заявка #{ticket.id}\n\n"
                details_text += f"📝 Заголовок: {ticket.title}\n"
                details_text += f"📄 Описание: {ticket.description}\n\n"
                details_text += f"🏪 Магазин: {shop_name}\n"
                details_text += f"📂 Категория: {category_name}\n"
                details_text += f"📊 Статус: {status_text}\n"
                details_text += f"⚡ Приоритет: {priority_text}\n"
                if ticket.is_urgent:
                    details_text += "🚨 Срочная заявка\n"
                details_text += f"📅 Создана: {created_date}\n"

                ucomments = (
                    db.query(TicketComment)
                    .filter(TicketComment.ticket_id == ticket.id)
                    .order_by(TicketComment.created_at.desc())
                    .limit(12)
                    .all()
                )
                if ucomments:
                    details_text += "\n💬 Комментарии:\n"
                    for c in reversed(ucomments):
                        au = db.query(User).filter(User.id == c.user_id).first()
                        if c.is_system:
                            label = "система"
                        else:
                            label = (au.first_name or au.username or "ТП") if au else "?"
                        raw_t = c.text or ""
                        sn = raw_t[:280] + ("…" if len(raw_t) > 280 else "")
                        details_text += f"• {label}: {sn}\n"
                
                if ticket.photo_path:
                    details_text += f"📷 Фото: прикреплено\n"
                
                if ticket.resolved_at:
                    resolved_date = ticket.resolved_at.strftime('%d.%m.%Y %H:%M')
                    details_text += f"✅ Решена: {resolved_date}\n"
                
                # Создаем клавиатуру
                details_kb = [[CallbackButton(text='◀️ Назад к списку', payload='my_tickets')]]
                details_kb.append([CallbackButton(text='🏠 Главное меню', payload='back_to_main')])
                
                await safe_answer_ui(event, max_id,
                    details_text,
                    attachments=[keyboard_to_attachment(details_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'notifications':
            # Управление уведомлениями
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            db = next(get_db_session())
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                    return
                
                # Формируем сообщение с текущим статусом
                status_emoji = "✅" if user.notifications_enabled else "❌"
                status_text = "включены" if user.notifications_enabled else "выключены"
                
                message_text = f"🔔 Уведомления\n\n"
                message_text += f"Текущий статус: {status_emoji} Уведомления {status_text}\n\n"
                
                if user.notifications_enabled:
                    message_text += "Вы будете получать уведомления о:\n"
                    message_text += "• Изменении статуса заявки\n"
                    message_text += "• Назначении заявки на вас\n"
                    message_text += "• Комментариях к заявке\n"
                    message_text += "• Приближении дедлайна SLA\n\n"
                    if user.role == UserRole.SUPPORT:
                        n_new = "вкл" if getattr(user, "notify_new_tickets", True) else "выкл"
                        n_urg = "только срочные" if getattr(user, "notify_urgent_only", False) else "все новые"
                        message_text += f"Специалист ТП: push о новых заявках — {n_new}, режим — {n_urg}.\n\n"
                    message_text += "Нажмите кнопку ниже, чтобы отключить уведомления."
                else:
                    message_text += "Уведомления отключены. Вы не будете получать сообщения о изменениях в заявках.\n\n"
                    message_text += "Нажмите кнопку ниже, чтобы включить уведомления."
                
                # Создаем клавиатуру
                notifications_kb = []
                if user.notifications_enabled:
                    notifications_kb.append([CallbackButton(text='❌ Отключить уведомления', payload='notifications_off')])
                else:
                    notifications_kb.append([CallbackButton(text='✅ Включить уведомления', payload='notifications_on')])
                if user.role == UserRole.SUPPORT and user.notifications_enabled:
                    notifications_kb.append(
                        [CallbackButton(text="🔔 Переключить: новые заявки", payload="notif_support_new_toggle")]
                    )
                    notifications_kb.append(
                        [CallbackButton(text="🚨 Режим: все / только срочные", payload="notif_support_urgent_toggle")]
                    )
                notifications_kb.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
                
                await safe_answer_ui(event, max_id,
                    message_text,
                    attachments=[keyboard_to_attachment(notifications_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'notifications_on':
            # Включить уведомления
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            db = next(get_db_session())
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    user.notifications_enabled = True
                    db.commit()
                    logger.info(f"Уведомления включены для user_id {user_id}")
                    
                    role = get_user_role(max_id)
                    keyboard = get_main_menu_keyboard(role)
                    await safe_answer_ui(event, max_id,
                        "✅ Уведомления включены!\n\nВы будете получать уведомления о изменениях в ваших заявках.",
                        attachments=[keyboard_to_attachment(keyboard)]
                    )
                else:
                    await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
            finally:
                db.close()
        elif callback_data == 'notifications_off':
            # Отключить уведомления
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            db = next(get_db_session())
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    user.notifications_enabled = False
                    db.commit()
                    logger.info(f"Уведомления отключены для user_id {user_id}")
                    
                    role = get_user_role(max_id)
                    keyboard = get_main_menu_keyboard(role)
                    await safe_answer_ui(event, max_id,
                        "❌ Уведомления отключены!\n\nВы не будете получать уведомления о изменениях в заявках.",
                        attachments=[keyboard_to_attachment(keyboard)]
                    )
                else:
                    await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
            finally:
                db.close()
        elif callback_data == 'instructions':
            instructions_text = "📚 Инструкции\n\n"
            instructions_kb = []
            db = next(get_db_session())
            try:
                docs = db.query(InstructionDocument).order_by(InstructionDocument.id.desc()).all()
                if docs:
                    instructions_text += "📎 Документы от администратора:\n"
                    for d in docs:
                        label = d.title if len(d.title) <= 40 else d.title[:37] + "…"
                        instructions_kb.append(
                            [CallbackButton(text=f"📄 {label}", payload=f"instr_doc_{d.id}")]
                        )
                    instructions_text += "\n"
                instructions_text += "📖 Встроенная справка:\n"
            finally:
                db.close()
            instructions_kb.extend(
                [
                    [CallbackButton(text='1️⃣ Как создать заявку', payload='instruction_create')],
                    [CallbackButton(text='2️⃣ Отслеживание статуса', payload='instruction_status')],
                    [CallbackButton(text='3️⃣ Прикрепление фото', payload='instruction_photo')],
                    [CallbackButton(text='4️⃣ FAQ', payload='instruction_faq')],
                    [CallbackButton(text='5️⃣ Контакты', payload='instruction_contacts')],
                    [CallbackButton(text='◀️ Назад', payload='back_to_main')],
                ]
            )
            await safe_answer_ui(event, max_id,
                instructions_text + "\nВыберите пункт:",
                attachments=[keyboard_to_attachment(instructions_kb)],
            )
        elif callback_data.startswith('instr_doc_'):
            try:
                doc_id = int(callback_data.split('_')[-1])
            except ValueError:
                return
            db = next(get_db_session())
            try:
                d = db.query(InstructionDocument).filter(InstructionDocument.id == doc_id).first()
                if not d:
                    await safe_answer_ui(event, max_id,"Документ не найден.")
                    return
                path = INSTRUCTIONS_DIR / d.stored_filename
                if not path.is_file():
                    await safe_answer_ui(event, max_id,"Файл недоступен на сервере.")
                    return
                await safe_answer_ui(event, max_id,
                    text=f"📄 {d.title}",
                    attachments=[InputMedia(str(path))],
                )
                nav_kb = [
                    [CallbackButton(text='◀️ Назад к инструкциям', payload='instructions')],
                    [CallbackButton(text='🏠 Главное меню', payload='back_to_main')],
                ]
                await safe_answer_ui(event, max_id,
                    "Навигация:",
                    attachments=[keyboard_to_attachment(nav_kb)],
                )
            finally:
                db.close()
        elif callback_data == 'instruction_create':
            # Инструкция: Как создать заявку
            instruction_text = "📝 Как создать заявку\n\n"
            instruction_text += "1. Нажмите кнопку '📝 Создать заявку' в главном меню\n"
            instruction_text += "2. Выберите магазин из списка\n"
            instruction_text += "3. Выберите категорию проблемы\n"
            instruction_text += "4. Введите краткий заголовок заявки (например: 'Не работает касса')\n"
            instruction_text += "5. Опишите проблему подробно\n"
            instruction_text += "6. При необходимости прикрепите фото\n\n"
            instruction_text += "После создания заявки вы получите номер заявки и сможете отслеживать её статус в разделе 'Мои заявки'."
            
            instructions_kb = [
                [CallbackButton(text='◀️ Назад к инструкциям', payload='instructions')],
                [CallbackButton(text='🏠 Главное меню', payload='back_to_main')]
            ]
            
            await safe_answer_ui(event, max_id,
                instruction_text,
                attachments=[keyboard_to_attachment(instructions_kb)]
            )
        elif callback_data == 'instruction_status':
            # Инструкция: Отслеживание статуса
            instruction_text = "📊 Отслеживание статуса заявки\n\n"
            instruction_text += "Статусы заявок:\n\n"
            instruction_text += "🆕 Новая - заявка только что создана, ожидает обработки\n"
            instruction_text += "⚙️ В работе - заявка назначена специалисту и обрабатывается\n"
            instruction_text += "✅ Решена - проблема решена, заявка закрыта\n"
            instruction_text += "⏸️ Отложена - заявка временно отложена\n\n"
            instruction_text += "Для просмотра статуса:\n"
            instruction_text += "1. Откройте раздел '📋 Мои заявки'\n"
            instruction_text += "2. Выберите нужную заявку\n"
            instruction_text += "3. Просмотрите детали и текущий статус\n\n"
            instruction_text += "Вы будете получать уведомления при изменении статуса (если уведомления включены)."
            
            instructions_kb = [
                [CallbackButton(text='◀️ Назад к инструкциям', payload='instructions')],
                [CallbackButton(text='🏠 Главное меню', payload='back_to_main')]
            ]
            
            await safe_answer_ui(event, max_id,
                instruction_text,
                attachments=[keyboard_to_attachment(instructions_kb)]
            )
        elif callback_data == 'instruction_photo':
            # Инструкция: Прикрепление фото
            instruction_text = "📷 Как прикрепить фото к заявке\n\n"
            instruction_text += "1. При создании заявки, после ввода описания, вам будет предложено прикрепить фото\n"
            instruction_text += "2. Нажмите 'Да, прикрепить фото'\n"
            instruction_text += "3. Отправьте фото в чат (просто отправьте изображение как обычное сообщение)\n"
            instruction_text += "4. Фото будет автоматически прикреплено к заявке\n\n"
            instruction_text += "💡 Совет: Фото помогает специалистам быстрее понять проблему и решить её.\n"
            instruction_text += "Рекомендуется прикреплять фото при проблемах с оборудованием, визуальных дефектах и т.д."
            
            instructions_kb = [
                [CallbackButton(text='◀️ Назад к инструкциям', payload='instructions')],
                [CallbackButton(text='🏠 Главное меню', payload='back_to_main')]
            ]
            
            await safe_answer_ui(event, max_id,
                instruction_text,
                attachments=[keyboard_to_attachment(instructions_kb)]
            )
        elif callback_data == 'instruction_faq':
            # FAQ
            instruction_text = "❓ Часто задаваемые вопросы (FAQ)\n\n"
            instruction_text += "❓ Как долго обрабатывается заявка?\n"
            instruction_text += "⏱️ Время обработки зависит от категории и приоритета. Обычно заявки обрабатываются в течение 24 часов.\n\n"
            instruction_text += "❓ Можно ли отредактировать заявку после создания?\n"
            instruction_text += "📝 Нет, после создания заявку нельзя отредактировать. Если нужно что-то изменить, создайте новую заявку.\n\n"
            instruction_text += "❓ Как связаться со специалистом напрямую?\n"
            instruction_text += "💬 Все общение происходит через заявки. Специалист свяжется с вами при необходимости.\n\n"
            instruction_text += "❓ Что делать, если заявка не решается долго?\n"
            instruction_text += "⏰ Проверьте статус заявки. Если она в работе, значит специалист работает над проблемой. При необходимости создайте новую заявку с пометкой о срочности."
            
            instructions_kb = [
                [CallbackButton(text='◀️ Назад к инструкциям', payload='instructions')],
                [CallbackButton(text='🏠 Главное меню', payload='back_to_main')]
            ]
            
            await safe_answer_ui(event, max_id,
                instruction_text,
                attachments=[keyboard_to_attachment(instructions_kb)]
            )
        elif callback_data == 'instruction_contacts':
            # Контакты
            instruction_text = "📞 Контакты службы поддержки\n\n"
            instruction_text += "Если у вас возникли вопросы или проблемы с использованием бота:\n\n"
            instruction_text += "📧 Email: support@example.com\n"
            instruction_text += "📱 Телефон: +7 (XXX) XXX-XX-XX\n"
            instruction_text += "🕐 Время работы: Пн-Пт, 9:00 - 18:00\n\n"
            instruction_text += "💬 Также вы можете создать заявку через бота - это самый быстрый способ получить помощь!"
            
            instructions_kb = [
                [CallbackButton(text='◀️ Назад к инструкциям', payload='instructions')],
                [CallbackButton(text='🏠 Главное меню', payload='back_to_main')]
            ]
            
            await safe_answer_ui(event, max_id,
                instruction_text,
                attachments=[keyboard_to_attachment(instructions_kb)]
            )
        elif callback_data == 'help':
            await safe_answer_ui(event, max_id,
                "❓ Помощь\n\nЭто бот службы поддержки. Используйте меню для навигации.",
                attachments=[keyboard_to_attachment(get_back_button())]
            )
        elif callback_data == 'new_tickets':
            # Просмотр новых заявок для специалиста ТП
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'support' and role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только специалистам ТП")
                return
            
            db = next(get_db_session())
            try:
                # Срочные выше в списке, затем по дате
                tickets = db.query(Ticket).filter(
                    Ticket.status == TicketStatus.NEW
                ).order_by(Ticket.is_urgent.desc(), Ticket.created_at.desc()).limit(20).all()
                
                if not tickets:
                    await safe_answer_ui(event, max_id,
                        "🆕 Новые заявки\n\nНовых заявок нет.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Создаем клавиатуру со списком заявок
                tickets_kb = []
                for ticket in tickets:
                    # Получаем информацию о заявке
                    shop = db.query(Shop).filter(Shop.id == ticket.shop_id).first()
                    category = db.query(Category).filter(Category.id == ticket.category_id).first()
                    user = db.query(User).filter(User.id == ticket.user_id).first()
                    
                    shop_name = shop.name if shop else f"Магазин #{ticket.shop_id}"
                    priority_emoji = {
                        TicketPriority.LOW: '🟢',
                        TicketPriority.NORMAL: '🟡',
                        TicketPriority.HIGH: '🟠',
                        TicketPriority.URGENT: '🔴'
                    }.get(ticket.priority, '🟡')
                    
                    # Формируем текст кнопки
                    button_text = f"{priority_emoji} #{ticket.id} - {ticket.title[:22]}"
                    if ticket.is_urgent:
                        button_text = f"🚨 {button_text}"
                    if len(ticket.title) > 22:
                        button_text += "..."
                    
                    tickets_kb.append([CallbackButton(text=button_text, payload=f'support_ticket_{ticket.id}')])
                
                tickets_kb.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
                
                message_text = f"🆕 Новые заявки\n\nВсего новых заявок: {len(tickets)}\n\n"
                message_text += "Выберите заявку для просмотра и назначения:"
                
                await safe_answer_ui(event, max_id,
                    message_text,
                    attachments=[keyboard_to_attachment(tickets_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'in_progress_tickets':
            # Просмотр заявок в работе для специалиста ТП
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'support' and role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только специалистам ТП")
                return
            
            db = next(get_db_session())
            try:
                # Получаем заявки в работе, назначенные на этого специалиста
                tickets = db.query(Ticket).filter(
                    Ticket.status == TicketStatus.IN_PROGRESS,
                    Ticket.assigned_to == user_id
                ).order_by(Ticket.created_at.desc()).all()
                
                if not tickets:
                    await safe_answer_ui(event, max_id,
                        "⚙️ Заявки в работе\n\nУ вас нет заявок в работе.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Создаем клавиатуру со списком заявок
                tickets_kb = []
                for ticket in tickets:
                    shop = db.query(Shop).filter(Shop.id == ticket.shop_id).first()
                    shop_name = shop.name if shop else f"Магазин #{ticket.shop_id}"
                    
                    button_text = f"#{ticket.id} - {ticket.title[:30]}"
                    if len(ticket.title) > 30:
                        button_text += "..."
                    
                    tickets_kb.append([CallbackButton(text=button_text, payload=f'support_ticket_{ticket.id}')])
                
                tickets_kb.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
                
                message_text = f"⚙️ Заявки в работе\n\nВсего заявок в работе: {len(tickets)}\n\n"
                message_text += "Выберите заявку для управления:"
                
                await safe_answer_ui(event, max_id,
                    message_text,
                    attachments=[keyboard_to_attachment(tickets_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'tickets_by_shop':
            # Фильтрация заявок по магазинам
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'support' and role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только специалистам ТП")
                return
            
            db = next(get_db_session())
            try:
                # Получаем список магазинов
                shops = db.query(Shop).all()
                
                if not shops:
                    await safe_answer_ui(event, max_id,
                        "🏪 Заявки по магазинам\n\nМагазины не найдены.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Создаем клавиатуру с магазинами
                shops_kb = []
                for shop in shops:
                    # Подсчитываем количество активных заявок для этого магазина
                    active_tickets = db.query(Ticket).filter(
                        Ticket.shop_id == shop.id,
                        Ticket.status.in_([TicketStatus.NEW, TicketStatus.IN_PROGRESS])
                    ).count()
                    
                    button_text = f"{shop.name}"
                    if active_tickets > 0:
                        button_text += f" ({active_tickets})"
                    
                    shops_kb.append([CallbackButton(text=button_text, payload=f'support_shop_{shop.id}')])
                
                shops_kb.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
                
                await safe_answer_ui(event, max_id,
                    "🏪 Заявки по магазинам\n\nВыберите магазин для просмотра заявок:",
                    attachments=[keyboard_to_attachment(shops_kb)]
                )
            finally:
                db.close()
        elif callback_data.startswith('support_shop_'):
            # Просмотр заявок конкретного магазина
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            shop_id = int(callback_data.split('_')[2])
            
            db = next(get_db_session())
            try:
                shop = db.query(Shop).filter(Shop.id == shop_id).first()
                shop_name = shop.name if shop else f"Магазин #{shop_id}"
                
                # Получаем активные заявки для этого магазина
                tickets = db.query(Ticket).filter(
                    Ticket.shop_id == shop_id,
                    Ticket.status.in_([TicketStatus.NEW, TicketStatus.IN_PROGRESS])
                ).order_by(Ticket.created_at.desc()).limit(20).all()
                
                if not tickets:
                    await safe_answer_ui(event, max_id,
                        f"🏪 {shop_name}\n\nАктивных заявок нет.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Создаем клавиатуру со списком заявок
                tickets_kb = []
                for ticket in tickets:
                    status_emoji = '🆕' if ticket.status == TicketStatus.NEW else '⚙️'
                    button_text = f"{status_emoji} #{ticket.id} - {ticket.title[:28]}"
                    if len(ticket.title) > 28:
                        button_text += "..."
                    
                    tickets_kb.append([CallbackButton(text=button_text, payload=f'support_ticket_{ticket.id}')])
                
                tickets_kb.append([CallbackButton(text='◀️ Назад к магазинам', payload='tickets_by_shop')])
                tickets_kb.append([CallbackButton(text='🏠 Главное меню', payload='back_to_main')])
                
                message_text = f"🏪 {shop_name}\n\nАктивных заявок: {len(tickets)}\n\n"
                message_text += "Выберите заявку:"
                
                await safe_answer_ui(event, max_id,
                    message_text,
                    attachments=[keyboard_to_attachment(tickets_kb)]
                )
            finally:
                db.close()
        elif callback_data.startswith('support_ticket_'):
            # Просмотр и управление заявкой для специалиста
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            ticket_id = int(callback_data.split('_')[2])
            
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
                
                if not ticket:
                    await safe_answer_ui(event, max_id,
                        "Заявка не найдена.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Получаем дополнительную информацию
                shop = db.query(Shop).filter(Shop.id == ticket.shop_id).first()
                category = db.query(Category).filter(Category.id == ticket.category_id).first()
                user = db.query(User).filter(User.id == ticket.user_id).first()
                assigned_user = None
                if ticket.assigned_to:
                    assigned_user = db.query(User).filter(User.id == ticket.assigned_to).first()
                
                shop_name = shop.name if shop else f"Магазин #{ticket.shop_id}"
                category_name = category.name if category else f"Категория #{ticket.category_id}"
                user_name = user.first_name if user and user.first_name else f"Пользователь #{ticket.user_id}"
                
                # Формируем статус
                status_text = {
                    TicketStatus.NEW: '🆕 Новая',
                    TicketStatus.IN_PROGRESS: '⚙️ В работе',
                    TicketStatus.RESOLVED: '✅ Решена',
                    TicketStatus.POSTPONED: '⏸️ Отложена'
                }.get(ticket.status, ticket.status.value)
                
                # Формируем приоритет
                priority_text = {
                    TicketPriority.LOW: '🟢 Низкий',
                    TicketPriority.NORMAL: '🟡 Обычный',
                    TicketPriority.HIGH: '🟠 Высокий',
                    TicketPriority.URGENT: '🔴 Срочный'
                }.get(ticket.priority, ticket.priority.value)
                
                # Формируем дату
                created_date = ticket.created_at.strftime('%d.%m.%Y %H:%M') if ticket.created_at else 'Не указана'
                
                # Формируем сообщение
                details_text = f"📋 Заявка #{ticket.id}\n\n"
                details_text += f"📝 Заголовок: {ticket.title}\n"
                details_text += f"📄 Описание: {ticket.description}\n\n"
                details_text += f"👤 Автор: {user_name}\n"
                details_text += f"🏪 Магазин: {shop_name}\n"
                details_text += f"📂 Категория: {category_name}\n"
                details_text += f"📊 Статус: {status_text}\n"
                details_text += f"⚡ Приоритет: {priority_text}\n"
                details_text += f"📅 Создана: {created_date}\n"
                
                if ticket.assigned_to:
                    assigned_name = assigned_user.first_name if assigned_user and assigned_user.first_name else f"Специалист #{ticket.assigned_to}"
                    details_text += f"👨‍💼 Назначена на: {assigned_name}\n"
                else:
                    details_text += f"👨‍💼 Назначена на: не назначена\n"
                
                if ticket.photo_path:
                    details_text += f"📷 Фото: прикреплено\n"
                
                if ticket.sla_deadline:
                    sla_date = ticket.sla_deadline.strftime('%d.%m.%Y %H:%M')
                    details_text += f"⏱️ SLA дедлайн: {sla_date}\n"

                viewer_role = get_user_role(max_id)
                comments = (
                    db.query(TicketComment)
                    .filter(TicketComment.ticket_id == ticket.id)
                    .order_by(TicketComment.created_at.desc())
                    .limit(12)
                    .all()
                )
                if comments:
                    details_text += "\n💬 Комментарии:\n"
                    for c in reversed(comments):
                        au = db.query(User).filter(User.id == c.user_id).first()
                        if c.is_system:
                            label = "система"
                        else:
                            label = (au.first_name or au.username or f"user#{c.user_id}") if au else "?"
                        raw_t = c.text or ""
                        sn = raw_t[:280] + ("…" if len(raw_t) > 280 else "")
                        details_text += f"• {label}: {sn}\n"
                
                # Создаем клавиатуру управления заявкой
                manage_kb = []
                if viewer_role == "support":
                    manage_kb.append(
                        [CallbackButton(text="💬 Комментарий", payload=f"ticket_comment_{ticket.id}")]
                    )
                
                if ticket.status == TicketStatus.NEW:
                    # Если заявка новая, можно назначить на себя
                    if ticket.assigned_to != user_id:
                        manage_kb.append([CallbackButton(text='✅ Взять в работу', payload=f'assign_ticket_{ticket.id}')])
                
                if ticket.status == TicketStatus.IN_PROGRESS and ticket.assigned_to == user_id:
                    # Если заявка в работе и назначена на этого специалиста, можно изменить статус
                    manage_kb.append([CallbackButton(text='✅ Решить', payload=f'resolve_ticket_{ticket.id}')])
                    manage_kb.append([CallbackButton(text='⏸️ Отложить', payload=f'postpone_ticket_{ticket.id}')])
                
                if ticket.status == TicketStatus.POSTPONED and ticket.assigned_to == user_id:
                    # Если заявка отложена, можно вернуть в работу
                    manage_kb.append([CallbackButton(text='⚙️ Вернуть в работу', payload=f'reopen_ticket_{ticket.id}')])
                
                manage_kb.append([CallbackButton(text='◀️ Назад', payload='new_tickets')])
                manage_kb.append([CallbackButton(text='🏠 Главное меню', payload='back_to_main')])
                
                await safe_answer_ui(event, max_id,
                    details_text,
                    attachments=[keyboard_to_attachment(manage_kb)]
                )
            finally:
                db.close()
        elif callback_data.startswith('assign_ticket_'):
            # Назначение заявки на специалиста
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            ticket_id = int(callback_data.split('_')[2])
            
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
                
                if not ticket:
                    await safe_answer_ui(event, max_id,"Заявка не найдена")
                    return
                
                # Назначаем заявку на специалиста и меняем статус
                ticket.assigned_to = user_id
                ticket.status = TicketStatus.IN_PROGRESS
                db.add(
                    TicketComment(
                        ticket_id=ticket.id,
                        user_id=user_id,
                        text="Заявка взята в работу",
                        is_system=True,
                    )
                )
                db.commit()
                
                logger.info(f"Заявка #{ticket_id} назначена на user_id {user_id}")
                await notify_user_status_change(
                    bot,
                    db,
                    ticket,
                    f"Заявка #{ticket_id} взята в работу специалистом.",
                )
                
                role = get_user_role(max_id)
                keyboard = get_main_menu_keyboard(role)
                
                await safe_answer_ui(event, max_id,
                    f"✅ Заявка #{ticket_id} взята в работу!\n\nСтатус изменен на 'В работе'.",
                    attachments=[keyboard_to_attachment(keyboard)]
                )
            finally:
                db.close()
        elif callback_data.startswith('resolve_ticket_'):
            # Решение заявки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            ticket_id = int(callback_data.split('_')[2])
            
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
                
                if not ticket:
                    await safe_answer_ui(event, max_id,"Заявка не найдена")
                    return
                
                if ticket.assigned_to != user_id:
                    await safe_answer_ui(event, max_id,"Эта заявка назначена на другого специалиста")
                    return
                
                # Меняем статус на решена
                ticket.status = TicketStatus.RESOLVED
                ticket.resolved_at = datetime.now(timezone.utc)
                db.add(
                    TicketComment(
                        ticket_id=ticket.id,
                        user_id=user_id,
                        text="Заявка отмечена как решённая",
                        is_system=True,
                    )
                )
                db.commit()
                
                logger.info(f"Заявка #{ticket_id} решена специалистом user_id {user_id}")
                await notify_user_status_change(
                    bot,
                    db,
                    ticket,
                    f"Заявка #{ticket_id} решена.",
                )
                
                role = get_user_role(max_id)
                keyboard = get_main_menu_keyboard(role)
                
                await safe_answer_ui(event, max_id,
                    f"✅ Заявка #{ticket_id} отмечена как решенная!",
                    attachments=[keyboard_to_attachment(keyboard)]
                )
            finally:
                db.close()
        elif callback_data.startswith('postpone_ticket_'):
            # Отложение заявки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            ticket_id = int(callback_data.split('_')[2])
            
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
                
                if not ticket:
                    await safe_answer_ui(event, max_id,"Заявка не найдена")
                    return
                
                if ticket.assigned_to != user_id:
                    await safe_answer_ui(event, max_id,"Эта заявка назначена на другого специалиста")
                    return
                
                # Меняем статус на отложена
                ticket.status = TicketStatus.POSTPONED
                db.add(
                    TicketComment(
                        ticket_id=ticket.id,
                        user_id=user_id,
                        text="Заявка отложена",
                        is_system=True,
                    )
                )
                db.commit()
                
                logger.info(f"Заявка #{ticket_id} отложена специалистом user_id {user_id}")
                await notify_user_status_change(
                    bot,
                    db,
                    ticket,
                    f"Заявка #{ticket_id} отложена.",
                )
                
                role = get_user_role(max_id)
                keyboard = get_main_menu_keyboard(role)
                
                await safe_answer_ui(event, max_id,
                    f"⏸️ Заявка #{ticket_id} отложена.",
                    attachments=[keyboard_to_attachment(keyboard)]
                )
            finally:
                db.close()
        elif callback_data.startswith('reopen_ticket_'):
            # Возврат заявки в работу
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            ticket_id = int(callback_data.split('_')[2])
            
            db = next(get_db_session())
            try:
                ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
                
                if not ticket:
                    await safe_answer_ui(event, max_id,"Заявка не найдена")
                    return
                
                if ticket.assigned_to != user_id:
                    await safe_answer_ui(event, max_id,"Эта заявка назначена на другого специалиста")
                    return
                
                # Меняем статус на в работе
                ticket.status = TicketStatus.IN_PROGRESS
                db.add(
                    TicketComment(
                        ticket_id=ticket.id,
                        user_id=user_id,
                        text="Заявка снова в работе",
                        is_system=True,
                    )
                )
                db.commit()
                
                logger.info(f"Заявка #{ticket_id} возвращена в работу специалистом user_id {user_id}")
                await notify_user_status_change(
                    bot,
                    db,
                    ticket,
                    f"Заявка #{ticket_id} снова в работе.",
                )
                
                role = get_user_role(max_id)
                keyboard = get_main_menu_keyboard(role)
                
                await safe_answer_ui(event, max_id,
                    f"⚙️ Заявка #{ticket_id} возвращена в работу.",
                    attachments=[keyboard_to_attachment(keyboard)]
                )
            finally:
                db.close()
        elif callback_data == 'my_statistics':
            # Статистика специалиста
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'support' and role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только специалистам ТП")
                return
            
            db = next(get_db_session())
            try:
                # Получаем статистику специалиста
                total_tickets = db.query(Ticket).filter(Ticket.assigned_to == user_id).count()
                resolved_tickets = db.query(Ticket).filter(
                    Ticket.assigned_to == user_id,
                    Ticket.status == TicketStatus.RESOLVED
                ).count()
                in_progress_tickets = db.query(Ticket).filter(
                    Ticket.assigned_to == user_id,
                    Ticket.status == TicketStatus.IN_PROGRESS
                ).count()
                postponed_tickets = db.query(Ticket).filter(
                    Ticket.assigned_to == user_id,
                    Ticket.status == TicketStatus.POSTPONED
                ).count()
                
                # Вычисляем среднее время решения (для решенных заявок)
                resolved_tickets_with_time = db.query(Ticket).filter(
                    Ticket.assigned_to == user_id,
                    Ticket.status == TicketStatus.RESOLVED,
                    Ticket.resolved_at.isnot(None),
                    Ticket.created_at.isnot(None)
                ).all()
                
                avg_resolution_time = None
                if resolved_tickets_with_time:
                    total_seconds = 0
                    for ticket in resolved_tickets_with_time:
                        if ticket.resolved_at and ticket.created_at:
                            delta = ticket.resolved_at - ticket.created_at
                            total_seconds += delta.total_seconds()
                    avg_seconds = total_seconds / len(resolved_tickets_with_time)
                    avg_hours = avg_seconds / 3600
                    avg_resolution_time = f"{avg_hours:.1f} часов"
                
                # Формируем сообщение
                stats_text = f"📊 Моя статистика\n\n"
                stats_text += f"📋 Всего заявок: {total_tickets}\n"
                stats_text += f"✅ Решено: {resolved_tickets}\n"
                stats_text += f"⚙️ В работе: {in_progress_tickets}\n"
                stats_text += f"⏸️ Отложено: {postponed_tickets}\n"
                
                if resolved_tickets > 0:
                    resolution_rate = (resolved_tickets / total_tickets) * 100
                    stats_text += f"\n📈 Процент решенных: {resolution_rate:.1f}%\n"
                
                if avg_resolution_time:
                    stats_text += f"⏱️ Среднее время решения: {avg_resolution_time}\n"
                
                stats_kb = [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]
                
                await safe_answer_ui(event, max_id,
                    stats_text,
                    attachments=[keyboard_to_attachment(stats_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'general_statistics':
            # Общая статистика для директора
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только директору")
                return
            
            db = next(get_db_session())
            try:
                # Общая статистика по заявкам
                total_tickets = db.query(Ticket).count()
                new_tickets = db.query(Ticket).filter(Ticket.status == TicketStatus.NEW).count()
                in_progress_tickets = db.query(Ticket).filter(Ticket.status == TicketStatus.IN_PROGRESS).count()
                resolved_tickets = db.query(Ticket).filter(Ticket.status == TicketStatus.RESOLVED).count()
                postponed_tickets = db.query(Ticket).filter(Ticket.status == TicketStatus.POSTPONED).count()
                
                # Статистика по приоритетам
                urgent_tickets = db.query(Ticket).filter(Ticket.priority == TicketPriority.URGENT).count()
                high_tickets = db.query(Ticket).filter(Ticket.priority == TicketPriority.HIGH).count()
                normal_tickets = db.query(Ticket).filter(Ticket.priority == TicketPriority.NORMAL).count()
                low_tickets = db.query(Ticket).filter(Ticket.priority == TicketPriority.LOW).count()
                
                # Статистика по магазинам
                shops = db.query(Shop).all()
                shops_stats = []
                for shop in shops:
                    shop_tickets = db.query(Ticket).filter(Ticket.shop_id == shop.id).count()
                    shops_stats.append((shop.name, shop_tickets))
                shops_stats.sort(key=lambda x: x[1], reverse=True)
                
                # Статистика по категориям
                categories = db.query(Category).all()
                categories_stats = []
                for category in categories:
                    cat_tickets = db.query(Ticket).filter(Ticket.category_id == category.id).count()
                    categories_stats.append((category.name, cat_tickets))
                categories_stats.sort(key=lambda x: x[1], reverse=True)
                
                # Количество специалистов
                specialists_count = db.query(User).filter(User.role == UserRole.SUPPORT).count()
                
                # Формируем сообщение
                stats_text = "📊 Общая статистика\n\n"
                stats_text += "📋 По статусам:\n"
                stats_text += f"   🆕 Новые: {new_tickets}\n"
                stats_text += f"   ⚙️ В работе: {in_progress_tickets}\n"
                stats_text += f"   ✅ Решено: {resolved_tickets}\n"
                stats_text += f"   ⏸️ Отложено: {postponed_tickets}\n"
                stats_text += f"   📊 Всего: {total_tickets}\n\n"
                
                stats_text += "⚡ По приоритетам:\n"
                stats_text += f"   🔴 Срочные: {urgent_tickets}\n"
                stats_text += f"   🟠 Высокие: {high_tickets}\n"
                stats_text += f"   🟡 Обычные: {normal_tickets}\n"
                stats_text += f"   🟢 Низкие: {low_tickets}\n\n"
                
                if resolved_tickets > 0 and total_tickets > 0:
                    resolution_rate = (resolved_tickets / total_tickets) * 100
                    stats_text += f"📈 Процент решенных: {resolution_rate:.1f}%\n\n"
                
                stats_text += f"👥 Специалистов ТП: {specialists_count}\n\n"
                
                if shops_stats:
                    stats_text += "🏪 Топ-3 магазина по заявкам:\n"
                    for i, (shop_name, count) in enumerate(shops_stats[:3], 1):
                        stats_text += f"   {i}. {shop_name}: {count}\n"
                    stats_text += "\n"
                
                if categories_stats:
                    stats_text += "📂 Топ-3 категории:\n"
                    for i, (cat_name, count) in enumerate(categories_stats[:3], 1):
                        stats_text += f"   {i}. {cat_name}: {count}\n"
                
                stats_kb = [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]
                
                await safe_answer_ui(event, max_id,
                    stats_text,
                    attachments=[keyboard_to_attachment(stats_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'specialists_efficiency':
            # Эффективность специалистов
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только директору")
                return
            
            db = next(get_db_session())
            try:
                # Получаем всех специалистов
                specialists = db.query(User).filter(User.role == UserRole.SUPPORT).all()
                
                if not specialists:
                    await safe_answer_ui(event, max_id,
                        "👥 Эффективность специалистов\n\nСпециалисты не найдены.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Собираем статистику по каждому специалисту
                specialists_stats = []
                for specialist in specialists:
                    total = db.query(Ticket).filter(Ticket.assigned_to == specialist.id).count()
                    resolved = db.query(Ticket).filter(
                        Ticket.assigned_to == specialist.id,
                        Ticket.status == TicketStatus.RESOLVED
                    ).count()
                    in_progress = db.query(Ticket).filter(
                        Ticket.assigned_to == specialist.id,
                        Ticket.status == TicketStatus.IN_PROGRESS
                    ).count()
                    
                    # Среднее время решения
                    resolved_tickets_with_time = db.query(Ticket).filter(
                        Ticket.assigned_to == specialist.id,
                        Ticket.status == TicketStatus.RESOLVED,
                        Ticket.resolved_at.isnot(None),
                        Ticket.created_at.isnot(None)
                    ).all()
                    
                    avg_time = None
                    if resolved_tickets_with_time:
                        total_seconds = 0
                        for ticket in resolved_tickets_with_time:
                            if ticket.resolved_at and ticket.created_at:
                                delta = ticket.resolved_at - ticket.created_at
                                total_seconds += delta.total_seconds()
                        if len(resolved_tickets_with_time) > 0:
                            avg_hours = (total_seconds / len(resolved_tickets_with_time)) / 3600
                            avg_time = f"{avg_hours:.1f}ч"
                    
                    resolution_rate = (resolved / total * 100) if total > 0 else 0
                    
                    name = specialist.first_name or specialist.username or f"ID {specialist.id}"
                    specialists_stats.append({
                        'name': name,
                        'total': total,
                        'resolved': resolved,
                        'in_progress': in_progress,
                        'rate': resolution_rate,
                        'avg_time': avg_time
                    })
                
                # Сортируем по количеству решенных
                specialists_stats.sort(key=lambda x: x['resolved'], reverse=True)
                
                # Формируем сообщение
                stats_text = "👥 Эффективность специалистов\n\n"
                
                for i, spec in enumerate(specialists_stats, 1):
                    stats_text += f"{i}. {spec['name']}\n"
                    stats_text += f"   📊 Всего: {spec['total']}\n"
                    stats_text += f"   ✅ Решено: {spec['resolved']}\n"
                    stats_text += f"   ⚙️ В работе: {spec['in_progress']}\n"
                    stats_text += f"   📈 Процент решенных: {spec['rate']:.1f}%\n"
                    if spec['avg_time']:
                        stats_text += f"   ⏱️ Среднее время: {spec['avg_time']}\n"
                    stats_text += "\n"
                
                stats_kb = [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]
                
                await safe_answer_ui(event, max_id,
                    stats_text,
                    attachments=[keyboard_to_attachment(stats_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'sla_statistics':
            # Статистика SLA
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только директору")
                return
            
            db = next(get_db_session())
            try:
                # Получаем все заявки с SLA дедлайном
                all_tickets = db.query(Ticket).filter(Ticket.sla_deadline.isnot(None)).all()
                
                if not all_tickets:
                    await safe_answer_ui(event, max_id,
                        "⏱️ Статистика SLA\n\nЗаявок с установленным SLA дедлайном нет.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                now = datetime.now(timezone.utc)
                
                # Функция для нормализации datetime к timezone-aware
                def normalize_datetime(dt):
                    if dt is None:
                        return None
                    if dt.tzinfo is None:
                        # Если datetime без timezone, считаем его UTC
                        return dt.replace(tzinfo=timezone.utc)
                    return dt
                
                # Подсчитываем статистику
                total_with_sla = len(all_tickets)
                overdue = 0  # Просроченные
                on_time = 0  # В срок
                upcoming = 0  # Скоро дедлайн (менее 2 часов)
                
                for ticket in all_tickets:
                    if ticket.sla_deadline:
                        sla_deadline = normalize_datetime(ticket.sla_deadline)
                        if ticket.status == TicketStatus.RESOLVED:
                            # Проверяем, решена ли в срок
                            resolved_at = normalize_datetime(ticket.resolved_at) if ticket.resolved_at else None
                            if resolved_at and sla_deadline and resolved_at <= sla_deadline:
                                on_time += 1
                            else:
                                overdue += 1
                        else:
                            # Проверяем, просрочена ли
                            if sla_deadline and sla_deadline < now:
                                overdue += 1
                            else:
                                # Проверяем, скоро ли дедлайн
                                if sla_deadline:
                                    time_left = (sla_deadline - now).total_seconds() / 3600
                                    if time_left <= 2:
                                        upcoming += 1
                                    else:
                                        on_time += 1
                
                # Среднее время до дедлайна для активных заявок
                active_tickets = [t for t in all_tickets if t.status != TicketStatus.RESOLVED]
                avg_time_to_deadline = None
                if active_tickets:
                    total_hours = 0
                    count = 0
                    for ticket in active_tickets:
                        if ticket.sla_deadline:
                            sla_deadline = normalize_datetime(ticket.sla_deadline)
                            if sla_deadline:
                                time_left = (sla_deadline - now).total_seconds() / 3600
                                if time_left > 0:
                                    total_hours += time_left
                                    count += 1
                    if count > 0:
                        avg_time_to_deadline = total_hours / count
                
                # Формируем сообщение
                stats_text = "⏱️ Статистика SLA\n\n"
                stats_text += f"📊 Всего заявок с SLA: {total_with_sla}\n\n"
                stats_text += "📈 Соответствие SLA:\n"
                if total_with_sla > 0:
                    on_time_rate = (on_time / total_with_sla) * 100
                    overdue_rate = (overdue / total_with_sla) * 100
                    stats_text += f"   ✅ В срок: {on_time} ({on_time_rate:.1f}%)\n"
                    stats_text += f"   ❌ Просрочено: {overdue} ({overdue_rate:.1f}%)\n"
                    if upcoming > 0:
                        stats_text += f"   ⚠️ Скоро дедлайн (<2ч): {upcoming}\n"
                stats_text += "\n"
                
                if avg_time_to_deadline:
                    stats_text += f"⏰ Среднее время до дедлайна: {avg_time_to_deadline:.1f} часов\n\n"
                
                # Статистика по категориям
                categories = db.query(Category).all()
                if categories:
                    stats_text += "📂 SLA по категориям:\n"
                    for category in categories:
                        cat_tickets = [t for t in all_tickets if t.category_id == category.id]
                        if cat_tickets:
                            cat_overdue = 0
                            for t in cat_tickets:
                                if t.sla_deadline:
                                    sla_deadline = normalize_datetime(t.sla_deadline)
                                    if t.status != TicketStatus.RESOLVED:
                                        if sla_deadline and sla_deadline < now:
                                            cat_overdue += 1
                                    else:
                                        resolved_at = normalize_datetime(t.resolved_at) if t.resolved_at else None
                                        if resolved_at and sla_deadline and resolved_at > sla_deadline:
                                            cat_overdue += 1
                            cat_total = len(cat_tickets)
                            if cat_total > 0:
                                overdue_pct = (cat_overdue / cat_total) * 100
                                stats_text += f"   {category.name}: {cat_overdue}/{cat_total} просрочено ({overdue_pct:.1f}%)\n"
                
                stats_kb = [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]
                
                await safe_answer_ui(event, max_id,
                    stats_text,
                    attachments=[keyboard_to_attachment(stats_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'problem_points':
            # Проблемные точки
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Проверяем роль
            role = get_user_role(max_id)
            if role != 'director':
                await safe_answer_ui(event, max_id,"Эта функция доступна только директору")
                return
            
            db = next(get_db_session())
            try:
                now = datetime.now(timezone.utc)
                
                # Функция для нормализации datetime
                def normalize_datetime(dt):
                    if dt is None:
                        return None
                    if dt.tzinfo is None:
                        return dt.replace(tzinfo=timezone.utc)
                    return dt
                
                # Проблемные магазины (много нерешенных заявок)
                shops = db.query(Shop).all()
                problem_shops = []
                for shop in shops:
                    active_tickets = db.query(Ticket).filter(
                        Ticket.shop_id == shop.id,
                        Ticket.status.in_([TicketStatus.NEW, TicketStatus.IN_PROGRESS])
                    ).count()
                    # Проверяем просроченные вручную из-за проблем с timezone
                    all_shop_tickets = db.query(Ticket).filter(
                        Ticket.shop_id == shop.id,
                        Ticket.sla_deadline.isnot(None),
                        Ticket.status != TicketStatus.RESOLVED
                    ).all()
                    overdue_count = 0
                    for ticket in all_shop_tickets:
                        if ticket.sla_deadline:
                            sla_deadline = normalize_datetime(ticket.sla_deadline)
                            if sla_deadline and sla_deadline < now:
                                overdue_count += 1
                    if active_tickets > 0 or overdue_count > 0:
                        problem_shops.append({
                            'name': shop.name,
                            'active': active_tickets,
                            'overdue': overdue_count
                        })
                problem_shops.sort(key=lambda x: x['active'] + x['overdue'], reverse=True)
                
                # Проблемные категории (много нерешенных)
                categories = db.query(Category).all()
                problem_categories = []
                for category in categories:
                    active_tickets = db.query(Ticket).filter(
                        Ticket.category_id == category.id,
                        Ticket.status.in_([TicketStatus.NEW, TicketStatus.IN_PROGRESS])
                    ).count()
                    # Проверяем просроченные вручную
                    all_cat_tickets = db.query(Ticket).filter(
                        Ticket.category_id == category.id,
                        Ticket.sla_deadline.isnot(None),
                        Ticket.status != TicketStatus.RESOLVED
                    ).all()
                    overdue_count = 0
                    for ticket in all_cat_tickets:
                        if ticket.sla_deadline:
                            sla_deadline = normalize_datetime(ticket.sla_deadline)
                            if sla_deadline and sla_deadline < now:
                                overdue_count += 1
                    if active_tickets > 0 or overdue_count > 0:
                        problem_categories.append({
                            'name': category.name,
                            'active': active_tickets,
                            'overdue': overdue_count
                        })
                problem_categories.sort(key=lambda x: x['active'] + x['overdue'], reverse=True)
                
                # Заявки с просроченным SLA
                all_tickets_with_sla = db.query(Ticket).filter(
                    Ticket.sla_deadline.isnot(None),
                    Ticket.status != TicketStatus.RESOLVED
                ).all()
                overdue_tickets_list = []
                for ticket in all_tickets_with_sla:
                    if ticket.sla_deadline:
                        sla_deadline = normalize_datetime(ticket.sla_deadline)
                        if sla_deadline and sla_deadline < now:
                            overdue_tickets_list.append(ticket)
                overdue_tickets_list.sort(key=lambda t: normalize_datetime(t.sla_deadline) if t.sla_deadline else datetime.min.replace(tzinfo=timezone.utc))
                overdue_tickets_list = overdue_tickets_list[:10]
                
                # Формируем сообщение
                stats_text = "🔍 Проблемные точки\n\n"
                
                if problem_shops:
                    stats_text += "🏪 Проблемные магазины:\n"
                    for i, shop in enumerate(problem_shops[:5], 1):
                        stats_text += f"   {i}. {shop['name']}\n"
                        stats_text += f"      Активных: {shop['active']}, Просрочено: {shop['overdue']}\n"
                    stats_text += "\n"
                
                if problem_categories:
                    stats_text += "📂 Проблемные категории:\n"
                    for i, cat in enumerate(problem_categories[:5], 1):
                        stats_text += f"   {i}. {cat['name']}\n"
                        stats_text += f"      Активных: {cat['active']}, Просрочено: {cat['overdue']}\n"
                    stats_text += "\n"
                
                if overdue_tickets_list:
                    stats_text += "⏰ Просроченные заявки (топ-10):\n"
                    for ticket in overdue_tickets_list:
                        shop = db.query(Shop).filter(Shop.id == ticket.shop_id).first()
                        shop_name = shop.name if shop else f"Магазин #{ticket.shop_id}"
                        if ticket.sla_deadline:
                            sla_deadline = normalize_datetime(ticket.sla_deadline)
                            hours_overdue = (now - sla_deadline).total_seconds() / 3600 if sla_deadline else 0
                        else:
                            hours_overdue = 0
                        stats_text += f"   #{ticket.id} - {ticket.title[:30]}...\n"
                        stats_text += f"      {shop_name}, просрочено на {hours_overdue:.1f}ч\n"
                else:
                    stats_text += "✅ Просроченных заявок нет\n"
                
                stats_kb = [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]
                
                await safe_answer_ui(event, max_id,
                    stats_text,
                    attachments=[keyboard_to_attachment(stats_kb)]
                )
            finally:
                db.close()
        elif callback_data == 'period_report':
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            role = get_user_role(max_id)
            if role != "director":
                await safe_answer_ui(event, max_id,"Эта функция доступна только директору")
                return
            rep_kb = [
                [
                    CallbackButton(text="CSV", payload="dir_rep_csv"),
                    CallbackButton(text="Excel (.xlsx)", payload="dir_rep_xlsx"),
                ],
                [CallbackButton(text="CSV + Excel", payload="dir_rep_both")],
                [CallbackButton(text="◀️ Назад", payload="back_to_main")],
            ]
            await safe_answer_ui(
                event,
                max_id,
                "📄 Отчёт за период\n\n"
                "Выберите формат файла, затем введите две даты (начало и конец периода).",
                attachments=[keyboard_to_attachment(rep_kb)],
            )
        elif callback_data in ("dir_rep_csv", "dir_rep_xlsx", "dir_rep_both"):
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            if get_user_role(max_id) != "director":
                await safe_answer_ui(event, max_id,"Эта функция доступна только директору")
                return
            fmt = {"dir_rep_csv": "csv", "dir_rep_xlsx": "xlsx", "dir_rep_both": "both"}[callback_data]
            FSM.set_state(user_id, FSMState.DIRECTOR_REPORT_FROM.value, {"report_fmt": fmt})
            await safe_answer_ui(
                event,
                max_id,
                f"Формат: {fmt.upper() if fmt != 'both' else 'CSV + Excel'}\n\n"
                "Шаг 1/2: введите дату начала (ГГГГ-ММ-ДД).",
                attachments=[keyboard_to_attachment(get_back_button())],
            )
        elif callback_data.startswith('shop_'):
            # Обработка выбора магазина
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            shop_id = int(callback_data.split('_')[1])
            
            # Получаем название магазина
            db = next(get_db_session())
            try:
                shop = db.query(Shop).filter(Shop.id == shop_id).first()
                shop_name = shop.name if shop else f"Магазин #{shop_id}"
            finally:
                db.close()

            fsm_prev = FSM.get_data(user_id)
            if fsm_prev.get("urgent_ticket"):
                from utils.urgent_ticket import ensure_urgent_category

                db = next(get_db_session())
                try:
                    category_id, category_name = ensure_urgent_category(db)
                finally:
                    db.close()
                FSM.set_state(
                    user_id,
                    FSMState.ENTER_TITLE.value,
                    {
                        "shop_id": shop_id,
                        "shop_name": shop_name,
                        "category_id": category_id,
                        "category_name": category_name,
                        "urgent_ticket": True,
                    },
                )
                await safe_answer_ui(event, max_id,
                    "🚨 Срочная заявка\n\n"
                    f"✅ Магазин: {shop_name}\n"
                    f"✅ Категория: {category_name} (автоматически)\n"
                    "⚡ Приоритет: высокий\n"
                    "⏱️ Срок ответа (SLA): по настройкам срочных заявок\n\n"
                    "Шаг 2/4: введите заголовок заявки (кратко опишите проблему)"
                )
                return
            
            # Сохраняем выбранный магазин и его название в FSM
            FSM.set_state(user_id, FSMState.SELECT_CATEGORY.value, {'shop_id': shop_id, 'shop_name': shop_name})
            
            # Получаем список категорий
            db = next(get_db_session())
            try:
                categories = db.query(Category).all()
                if not categories:
                    await safe_answer_ui(event, max_id,
                        f"📝 Создание заявки\n\n✅ Магазин: {shop_name}\n\n⚠️ Категории не настроены. Обратитесь к администратору.",
                        attachments=[keyboard_to_attachment(get_back_button())]
                    )
                    return
                
                # Создаем клавиатуру с категориями
                categories_kb = []
                for category in categories:
                    categories_kb.append([CallbackButton(text=category.name, payload=f'category_{category.id}')])
                categories_kb.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
                
                await safe_answer_ui(event, max_id,
                    f"📝 Создание заявки\n\n✅ Магазин: {shop_name}\n\nШаг 2/5: Выберите категорию проблемы",
                    attachments=[keyboard_to_attachment(categories_kb)]
                )
            finally:
                db.close()
        elif callback_data.startswith('category_'):
            # Обработка выбора категории
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            category_id = int(callback_data.split('_')[1])
            logger.info(f"Выбрана категория {category_id} для user_id {user_id}")
            
            # Получаем название категории
            db = next(get_db_session())
            try:
                category = db.query(Category).filter(Category.id == category_id).first()
                category_name = category.name if category else f"Категория #{category_id}"
            finally:
                db.close()
            
            # Получаем данные из FSM
            fsm_data = FSM.get_data(user_id)
            logger.info(f"Текущие данные FSM перед сохранением категории: {fsm_data}")
            # Создаем новый словарь с обновленными данными
            fsm_data = fsm_data.copy()
            fsm_data['category_id'] = category_id
            fsm_data['category_name'] = category_name
            FSM.set_state(user_id, FSMState.ENTER_TITLE.value, fsm_data)
            logger.info(f"Категория сохранена, данные FSM: {FSM.get_data(user_id)}")
            
            await safe_answer_ui(event, max_id,
                f"📝 Создание заявки\n\n✅ Магазин: выбрано\n✅ Категория: {category_name}\n\nШаг 3/5: Введите заголовок заявки (краткое описание проблемы)"
            )
            logger.info(f"Сообщение с запросом заголовка отправлено")
        elif callback_data == 'add_photo_yes':
            # Пользователь хочет прикрепить фото - оставляем состояние ADD_PHOTO
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            # Убеждаемся, что состояние ADD_PHOTO установлено
            fsm_data = FSM.get_data(user_id)
            FSM.set_state(user_id, FSMState.ADD_PHOTO.value, fsm_data)
            _hdr = "🚨 Срочная заявка\n\n" if fsm_data.get("urgent_ticket") else "📝 Создание заявки\n\n"
            await safe_answer_ui(event, max_id,
                f"{_hdr}📷 Отправьте фото или нажмите «Пропустить»",
                attachments=[keyboard_to_attachment([
                    [CallbackButton(text='⏭️ Пропустить', payload='add_photo_no')],
                    [CallbackButton(text='◀️ Назад', payload='back_to_main')]
                ])]
            )
        elif callback_data == 'add_photo_no':
            # Пользователь пропустил фото — экран подтверждения
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            
            await show_ticket_confirmation(user_id, max_id, event)
        elif callback_data == 'ticket_confirm_submit':
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            if FSM.get_state(user_id) != FSMState.CONFIRM.value:
                await safe_answer_ui(event, max_id,"Сессия устарела. Начните создание заявки снова.")
                return
            await create_ticket_from_fsm(user_id, max_id, event)
        elif callback_data == 'ticket_confirm_cancel':
            user_id = get_user_id_by_max_id(max_id)
            if user_id:
                FSM.clear(user_id)
            role = get_user_role(max_id)
            await safe_answer_ui(event, max_id,
                "Создание заявки отменено.",
                attachments=[keyboard_to_attachment(get_main_menu_keyboard(role))],
            )
        elif callback_data.startswith("ticket_comment_"):
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                await safe_answer_ui(event, max_id,"Ошибка: пользователь не найден")
                return
            role = get_user_role(max_id)
            if role != "support":
                await safe_answer_ui(event, max_id,"Доступно только специалистам ТП.")
                return
            tid = int(callback_data.split("_")[2])
            FSM.set_state(user_id, FSMState.ENTER_TICKET_COMMENT.value, {"comment_ticket_id": tid})
            await safe_answer_ui(event, max_id,
                f"💬 Комментарий к заявке #{tid}\n\n"
                f"Введите текст (не более {MAX_MESSAGE_TEXT_LENGTH} символов).",
                attachments=[keyboard_to_attachment(get_back_button())],
            )
        elif callback_data == "notif_support_urgent_toggle":
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                return
            db = next(get_db_session())
            try:
                u = db.query(User).filter(User.id == user_id).first()
                if u and u.role == UserRole.SUPPORT:
                    u.notify_urgent_only = not bool(getattr(u, "notify_urgent_only", False))
                    db.commit()
                    mode = "только срочные заявки" if u.notify_urgent_only else "все новые заявки"
                    await safe_answer_ui(event, max_id,f"Режим уведомлений: {mode}.")
            finally:
                db.close()
        elif callback_data == "notif_support_new_toggle":
            user_id = get_user_id_by_max_id(max_id)
            if not user_id:
                return
            db = next(get_db_session())
            try:
                u = db.query(User).filter(User.id == user_id).first()
                if u and u.role == UserRole.SUPPORT:
                    u.notify_new_tickets = not bool(getattr(u, "notify_new_tickets", True))
                    db.commit()
                    st = "включены" if u.notify_new_tickets else "выключены"
                    await safe_answer_ui(event, max_id,f"Уведомления о новых заявках {st}.")
            finally:
                db.close()
        else:
            if callback_data.startswith("admin_"):
                logger.error(
                    "Callback %r попал в общий else — на сервере, скорее всего, старая bot.py "
                    "без делегирования в app.admin_panel. Залейте весь каталог max_support_bot.",
                    callback_data,
                )
                await safe_answer_ui(event, max_id,
                    "⚠️ На сервере запущена устаревшая версия кода (нет модулей app/admin_panel, "
                    "admin_documents_flow, admin_system_flow).\n\n"
                    "Обновите **все** файлы проекта и перезапустите бота.",
                    attachments=[keyboard_to_attachment(get_admin_menu_keyboard())],
                )
                return
            await safe_answer_ui(event, max_id,
                f"Функция '{callback_data}' будет реализована на следующем этапе.",
                attachments=[keyboard_to_attachment(get_back_button())],
            )
            
    except Exception as e:
        logger.error(f"Ошибка при обработке callback: {e}", exc_info=True)
        try:
            mid = max_user_id_from_message_callback(event)
            if mid:
                await safe_answer_ui(event, mid, "Произошла ошибка. Попробуйте позже.")
            elif getattr(event, "message", None) is not None:
                await event.message.answer("Произошла ошибка. Попробуйте позже.")
        except Exception:
            pass


async def main():
    """Главная функция запуска бота"""
    try:
        logger.info("Запуск бота...")
        logger.info("Сборка: max_support_bot admin-modules (app/admin_panel + documents + system)")

        if not (BOT_TOKEN or "").strip():
            logger.error(
                "Не задан BOT_TOKEN (или MAX_BOT_TOKEN). "
                "Создайте файл .env в каталоге max_support_bot и укажите токен."
            )
            raise SystemExit(1)
        
        # Инициализируем базу данных
        logger.info("Инициализация базы данных...")
        init_db()
        logger.info("База данных инициализирована")
        ensure_data_dirs()

        # Удаляем старые webhook подписки
        try:
            await bot.delete_webhook()
            logger.info("Старые webhook подписки удалены")
        except Exception as e:
            logger.warning(f"Не удалось удалить webhook: {e}")
        
        # Запускаем polling
        logger.info("Запуск polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise


if __name__ == '__main__':
    asyncio.run(main())
