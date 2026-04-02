"""Модель заявки"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from models.database import Base
import enum


class TicketStatus(str, enum.Enum):
    NEW = 'new'
    IN_PROGRESS = 'in_progress'
    RESOLVED = 'resolved'
    POSTPONED = 'postponed'


class TicketPriority(str, enum.Enum):
    LOW = 'low'
    NORMAL = 'normal'
    HIGH = 'high'
    URGENT = 'urgent'


class Ticket(Base):
    __tablename__ = 'tickets'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    shop_id = Column(Integer, ForeignKey('shops.id'), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False)
    is_urgent = Column(Boolean, default=False)
    status = Column(Enum(TicketStatus), default=TicketStatus.NEW, nullable=False)
    priority = Column(Enum(TicketPriority), default=TicketPriority.NORMAL, nullable=False)
    assigned_to = Column(Integer, ForeignKey('users.id'), nullable=True)
    photo_path = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
    sla_deadline = Column(DateTime, nullable=True)

    photo_attachments = relationship(
        "TicketAttachment",
        back_populates="ticket",
        order_by="TicketAttachment.position",
        cascade="all, delete-orphan",
    )
