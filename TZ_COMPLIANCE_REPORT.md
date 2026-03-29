# Отчёт о соответствии ТЗ (актуально)

## Реализовано в коде

| Блок ТЗ | Реализация |
|--------|------------|
| Роли user / support / director, `/start`, FSM заявок | `bot.py`, `app/fsm.py` |
| Подтверждение перед созданием заявки | `FSMState.CONFIRM`, кнопки в `bot.py` |
| Срочная заявка, SLA из настроек | `utils/urgent_ticket.py`, `system_settings` |
| Комментарии, системные комментарии, уведомления | `models/ticket_comment.py`, `services/notification_service.py` |
| Уведомления специалистам (новые / только срочные) | `notify_specialist_*`, поля `User` |
| Уведомление о комментариях участникам | `notify_ticket_comment_participants` |
| Назначение из админки + уведомление исполнителю | `app/admin_tickets_admin.py`, `notify_specialist_assigned` |
| Директор: отчёт за период **CSV и/или Excel** | `deliver_director_period_reports`, `openpyxl` |
| Админ `/admin <пароль>`, меню, пользователи, справочники | `app/admin_panel.py`, `config.verify_admin_password` |
| Админ: заявки с **фильтрами** (статус, магазин, исполнитель, даты) | `app/admin_tickets_admin.py` |
| Документы инструкций (БД + файлы), раздел пользователя | `app/admin_documents_flow.py`, `bot.py` → instructions |
| Настройки SLA / хранения, очистка | `app/admin_system_flow.py`, `cleanup.py` |
| Таблица **statistics** (суточные агрегаты) | `models/daily_statistics.py`, `cleanup.py --stats-day` |
| Установщик `.env` + проверка токена | `installer.py` |
| **Веб-админка Flask** (пароль как у `/admin`, те же модели БД) | `web_admin/`, запуск `python -m web_admin` |
| Ответы при удалённом сообщении с клавиатурой | `utils/safe_reply.py` |

## Не в этой репозиторной поставке

- **Flask (или иной) HTTPS webhook для приёма событий MAX** — отдельный endpoint и домен; бот по-прежнему **long polling**. Веб-админка Flask — это **панель в браузере**, не webhook.
- Кэш с TTL 5 мин и лимит 30 RPS — при необходимости на стороне прокси/обёртки.

## Быстрая приёмка

1. `installer.py` или ручной `.env`, `pip install -r requirements.txt`, `python bot.py`.
2. `/start`, `/admin <пароль>`, раздел «Все заявки» — фильтры и пагинация.
3. Назначение специалиста из карточки заявки в админке.
4. Директор: отчёт — выбор CSV / Excel, две даты.
5. `cleanup.py --stats-yesterday` после появления заявок за вчера.
