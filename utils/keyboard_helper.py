"""Вспомогательные функции для работы с клавиатурами"""
from maxapi.types import Attachment
from maxapi.types.attachments.buttons import CallbackButton


def keyboard_to_attachment(keyboard):
    """Преобразует список кнопок в Attachment для maxapi"""
    # keyboard уже список CallbackButton в строках
    # Преобразуем в формат для Attachment
    buttons_list = []
    for row in keyboard:
        row_buttons = []
        for button in row:
            if isinstance(button, CallbackButton):
                row_buttons.append(button)
            elif isinstance(button, dict):
                text = button.get('text', '')
                callback_data = button.get('callback_data', '')
                row_buttons.append(CallbackButton(text=text, payload=callback_data))
        if row_buttons:
            buttons_list.append(row_buttons)
    
    # Если список пустой, добавляем кнопку "Назад" по умолчанию
    if not buttons_list:
        buttons_list = [[CallbackButton(text='◀️ Назад', payload='back_to_main')]]
    
    # Создаем Attachment с правильной структурой для maxapi
    # payload должен быть словарем с ключом 'buttons'
    return Attachment(
        type='inline_keyboard',
        payload={'buttons': buttons_list}
    )
