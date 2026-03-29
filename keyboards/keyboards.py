"""Генерация inline-клавиатур"""
from maxapi.types.attachments.buttons import CallbackButton


def get_main_menu_keyboard(role='user'):
    """Главное меню в зависимости от роли"""
    if role == 'user':
        keyboard = [
            [CallbackButton(text='📝 Создать заявку', payload='create_ticket')],
            [CallbackButton(text='🚨 Срочная заявка', payload='create_urgent_ticket')],
            [CallbackButton(text='📋 Мои заявки', payload='my_tickets')],
            [CallbackButton(text='🔔 Уведомления', payload='notifications')],
            [CallbackButton(text='📚 Инструкции', payload='instructions')],
            [CallbackButton(text='❓ Помощь', payload='help')]
        ]
    elif role == 'support':
        keyboard = [
            [CallbackButton(text='🆕 Новые заявки', payload='new_tickets')],
            [CallbackButton(text='⚙️ В работе', payload='in_progress_tickets')],
            [CallbackButton(text='🏪 По магазинам', payload='tickets_by_shop')],
            [CallbackButton(text='📊 Моя статистика', payload='my_statistics')],
            [CallbackButton(text='🔔 Уведомления', payload='notifications')]
        ]
    elif role == 'director':
        keyboard = [
            [CallbackButton(text='📊 Общая статистика', payload='general_statistics')],
            [CallbackButton(text='👥 Эффективность специалистов', payload='specialists_efficiency')],
            [CallbackButton(text='⏱️ Время решения (SLA)', payload='sla_statistics')],
            [CallbackButton(text='🔍 Проблемные точки', payload='problem_points')],
            [CallbackButton(text='📄 Отчет за период', payload='period_report')]
        ]
    else:
        # По умолчанию меню пользователя
        keyboard = [
            [CallbackButton(text='📝 Создать заявку', payload='create_ticket')],
            [CallbackButton(text='📋 Мои заявки', payload='my_tickets')],
            [CallbackButton(text='❓ Помощь', payload='help')]
        ]
    
    return keyboard


def get_back_button():
    """Кнопка 'Назад'"""
    return [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]


def get_ticket_filters_keyboard(active_filter=None):
    """Клавиатура фильтров заявок по статусу"""
    filters = [
        ('all', '📋 Все', 'filter_tickets_all'),
        ('new', '🆕 Новые', 'filter_tickets_new'),
        ('in_progress', '⚙️ В работе', 'filter_tickets_in_progress'),
        ('resolved', '✅ Решенные', 'filter_tickets_resolved'),
        ('postponed', '⏸️ Отложенные', 'filter_tickets_postponed')
    ]
    
    keyboard = []
    for filter_value, text, callback in filters:
        # Если фильтр активен, добавляем галочку
        if active_filter == filter_value:
            text = f"✓ {text}"
        keyboard.append([CallbackButton(text=text, payload=callback)])
    
    keyboard.append([CallbackButton(text='◀️ Назад', payload='back_to_main')])
    
    return keyboard


def get_admin_menu_keyboard():
    """Главное меню администратора"""
    keyboard = [
        [CallbackButton(text='👥 Пользователи', payload='admin_users')],
        [CallbackButton(text='🏪 Магазины', payload='admin_shops')],
        [CallbackButton(text='📂 Категории', payload='admin_categories')],
        [CallbackButton(text='📄 Документы инструкций', payload='admin_documents')],
        [CallbackButton(text='📋 Все заявки', payload='admin_tickets')],
        [CallbackButton(text='⚙️ Настройки', payload='admin_settings')],
        [CallbackButton(text='🧹 Очистка данных', payload='admin_cleanup')],
        [CallbackButton(text='🚪 Выйти из админки', payload='admin_exit')]
    ]
    return keyboard
