"""Глобальные настройки (одна строка id=1)."""
from sqlalchemy import Column, Integer

from models.database import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)
    urgent_sla_hours = Column(Integer, nullable=False, default=2)
    normal_default_sla_hours = Column(Integer, nullable=False, default=24)
    retention_resolved_tickets_days = Column(Integer, nullable=False, default=365)
    retention_sessions_days = Column(Integer, nullable=False, default=7)
    retention_logs_days = Column(Integer, nullable=False, default=90)
    retention_temp_files_days = Column(Integer, nullable=False, default=7)
