"""Настройка базы данных SQLite"""
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _migrate_sqlite() -> None:
    """Добавление столбцов в существующие таблицы SQLite без Alembic."""
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        names = {r[1] for r in rows}
        if "notify_new_tickets" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN notify_new_tickets BOOLEAN DEFAULT 1"))
        if "notify_urgent_only" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN notify_urgent_only BOOLEAN DEFAULT 0"))


def _backfill_ticket_attachments_from_legacy() -> None:
    """Копирует legacy tickets.photo_path в ticket_attachments, если строк ещё нет."""
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO ticket_attachments (ticket_id, path, position)
                    SELECT t.id, t.photo_path, 0
                    FROM tickets t
                    WHERE t.photo_path IS NOT NULL
                      AND TRIM(t.photo_path) != ''
                      AND LOWER(TRIM(t.photo_path)) != 'uploaded'
                      AND NOT EXISTS (
                        SELECT 1 FROM ticket_attachments ta WHERE ta.ticket_id = t.id
                      )
                    """
                )
            )
    except Exception:
        pass


def _migrate_postgresql_ticket_photo_path() -> None:
    """Расширение tickets.photo_path до TEXT (длинные URL CDN)."""
    url = (DATABASE_URL or "").lower()
    if "postgresql" not in url and "postgres" not in url:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tickets ALTER COLUMN photo_path TYPE TEXT"))
    except Exception:
        pass


def get_db_session():
    """Получить сессию базы данных"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Инициализация базы данных - создание всех таблиц"""
    # Регистрация моделей для metadata
    from models.instruction_document import InstructionDocument  # noqa: F401
    from models.system_settings import SystemSettings  # noqa: F401
    from models.user import User  # noqa: F401 — для FK ticket_comments
    from models.ticket import Ticket  # noqa: F401
    from models.ticket_attachment import TicketAttachment  # noqa: F401
    from models.ticket_comment import TicketComment  # noqa: F401
    from models.daily_statistics import DailyStatistics  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    _migrate_postgresql_ticket_photo_path()
    _backfill_ticket_attachments_from_legacy()

    db = SessionLocal()
    try:
        if db.query(SystemSettings).filter(SystemSettings.id == 1).first() is None:
            db.add(SystemSettings(id=1))
            db.commit()
    finally:
        db.close()
