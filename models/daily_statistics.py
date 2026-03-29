"""Суточные агрегаты по заявкам (опционально к ТЗ; заполняется rollup-скриптом)."""
from sqlalchemy import Column, Date, DateTime, Integer
from sqlalchemy.sql import func

from models.database import Base


class DailyStatistics(Base):
    """Одна строка на календарный день (UTC): снимок счётчиков."""

    __tablename__ = "statistics"

    id = Column(Integer, primary_key=True)
    stat_date = Column(Date, nullable=False, unique=True)
    total_tickets = Column(Integer, nullable=False, default=0)
    new_tickets = Column(Integer, nullable=False, default=0)
    resolved_tickets = Column(Integer, nullable=False, default=0)
    urgent_tickets = Column(Integer, nullable=False, default=0)
    overdue_tickets = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
