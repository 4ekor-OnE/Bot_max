"""Модель пользователя"""
from sqlalchemy import Column, Integer, String, DateTime, Enum, Boolean
from sqlalchemy.sql import func
from models.database import Base
import enum


class UserRole(str, enum.Enum):
    USER = 'user'
    SUPPORT = 'support'
    DIRECTOR = 'director'


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    max_id = Column(String(100), unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    role = Column(Enum(UserRole), default=UserRole.USER, nullable=False)
    notifications_enabled = Column(Boolean, default=True)
    # Специалисты: уведомления о новых заявках (общий канал)
    notify_new_tickets = Column(Boolean, default=True)
    # Только срочные заявки (если True — обычные новые не дублировать в push)
    notify_urgent_only = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    last_activity = Column(DateTime, server_default=func.now(), onupdate=func.now())
