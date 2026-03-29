"""Общие утилиты админ-панели (FSM admin_mode)."""
from maxapi.types.attachments.buttons import CallbackButton

from app.fsm import FSM


def shorten_text(s: str, n: int = 35) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def admin_kb_home() -> list:
    return [[CallbackButton(text="◀️ Админ-меню", payload="admin_home")]]


def admin_fsm_merge(user_id: int, **kwargs) -> None:
    data = FSM.get_data(user_id).copy()
    for k, v in kwargs.items():
        if v is None and k in data:
            del data[k]
        else:
            data[k] = v
    FSM.set_state(user_id, "admin_mode", data)


def admin_fsm_clear_step(user_id: int) -> None:
    admin_fsm_merge(user_id, admin_step=None)
