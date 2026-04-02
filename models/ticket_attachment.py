"""Вложения-фото к заявке (несколько на одну заявку)."""
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from models.database import Base


class TicketAttachment(Base):
    __tablename__ = "ticket_attachments"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, default=0)

    ticket = relationship("Ticket", back_populates="photo_attachments")
