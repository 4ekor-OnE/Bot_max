"""Очистка старых данных (заявки, сессии, временные файлы)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from config import TEMP_FILES_DIR
from models.session import Session as UserSession
from models.ticket import Ticket, TicketStatus

logger = logging.getLogger(__name__)


def cleanup_resolved_tickets_older_than(db: Session, days: int) -> int:
    """Удаляет решённые заявки с resolved_at старше ``days`` дней."""
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(Ticket).filter(
        Ticket.status == TicketStatus.RESOLVED,
        Ticket.resolved_at.isnot(None),
        Ticket.resolved_at < cutoff,
    )
    n = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    logger.info("Удалено решённых заявок старше %s дн.: %s", days, n)
    return n


def cleanup_sessions(db: Session, days: int) -> int:
    """Удаляет истёкшие сессии и сессии без активности дольше ``days`` дней."""
    now = datetime.utcnow()
    idle_cut = now - timedelta(days=max(days, 1))
    q = db.query(UserSession).filter(
        or_(
            and_(UserSession.expires_at.is_not(None), UserSession.expires_at < now),
            UserSession.updated_at < idle_cut,
        )
    )
    n = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    logger.info("Удалено сессий: %s", n)
    return n


def cleanup_temp_files(days: int) -> int:
    """Удаляет файлы в TEMP_FILES_DIR старше ``days`` дней (по mtime)."""
    if days <= 0:
        return 0
    TEMP_FILES_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - days * 86400
    removed = 0
    for p in TEMP_FILES_DIR.iterdir():
        if not p.is_file():
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError as e:
            logger.warning("Не удалось обработать %s: %s", p, e)
    logger.info("Удалено временных файлов: %s", removed)
    return removed
