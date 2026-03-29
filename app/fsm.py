"""Конечный автомат состояний (FSM) для создания заявок"""
from enum import Enum
from datetime import datetime, timedelta
from models.database import get_db_session
from models.session import Session


class FSMState(str, Enum):
    """Состояния FSM"""
    IDLE = 'idle'
    SELECT_SHOP = 'select_shop'
    SELECT_CATEGORY = 'select_category'
    ENTER_TITLE = 'enter_title'
    ENTER_DESCRIPTION = 'enter_description'
    ADD_PHOTO = 'add_photo'
    CONFIRM = 'confirm'
    ENTER_TICKET_COMMENT = 'enter_ticket_comment'
    DIRECTOR_REPORT_FROM = 'director_report_from'
    DIRECTOR_REPORT_TO = 'director_report_to'


class FSM:
    """Управление состоянием пользователя"""
    
    @staticmethod
    def get_state(user_id: int) -> str:
        """Получить текущее состояние пользователя"""
        db = next(get_db_session())
        try:
            session = db.query(Session).filter(Session.user_id == user_id).first()
            if session:
                return session.state
            return FSMState.IDLE.value
        finally:
            db.close()
    
    @staticmethod
    def set_state(user_id: int, state: str, data: dict = None):
        """Установить состояние пользователя"""
        db = next(get_db_session())
        try:
            session = db.query(Session).filter(Session.user_id == user_id).first()
            if not session:
                session = Session(
                    user_id=user_id,
                    state=state,
                    data=data or {},
                    expires_at=datetime.now() + timedelta(hours=24)
                )
                db.add(session)
            else:
                session.state = state
                if data is not None:
                    # Если data передан, обновляем данные
                    if session.data:
                        # Обновляем существующие данные, сохраняя все ключи
                        new_data = session.data.copy()
                        new_data.update(data)
                        session.data = new_data
                    else:
                        session.data = data.copy() if isinstance(data, dict) else data
                    # Помечаем поле как измененное для SQLAlchemy
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(session, 'data')
                session.updated_at = datetime.now()
            db.commit()
        except Exception as e:
            import logging
            logger = logging.getLogger('fsm')
            logger.error(f"Ошибка установки состояния: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()
    
    @staticmethod
    def get_data(user_id: int) -> dict:
        """Получить данные сессии"""
        db = next(get_db_session())
        try:
            session = db.query(Session).filter(Session.user_id == user_id).first()
            if session:
                return session.data or {}
            return {}
        finally:
            db.close()
    
    @staticmethod
    def clear(user_id: int):
        """Очистить состояние пользователя"""
        db = next(get_db_session())
        try:
            session = db.query(Session).filter(Session.user_id == user_id).first()
            if session:
                db.delete(session)
                db.commit()
        finally:
            db.close()
