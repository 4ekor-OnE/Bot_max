"""Подтверждение нажатия inline-кнопки в MAX без повторной отправки вложений."""

from maxapi.types.updates.message_callback import MessageCallback

# Невидимый символ: иначе maxapi при пустом answer() пересылает keyboard из исходного
# сообщения, и в JSON попадает buttons=null → 400 proto.payload.
_INVISIBLE_ACK = "\u2060"


async def acknowledge_callback(event: MessageCallback) -> None:
    b = event.bot
    if b is None:
        return
    await b.send_callback(
        callback_id=str(event.callback.callback_id),
        message=None,
        notification=_INVISIBLE_ACK,
    )
