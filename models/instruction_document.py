"""Документы инструкций (файлы для пользователей)."""
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from models.database import Base


class InstructionDocument(Base):
    __tablename__ = "instruction_documents"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    original_filename = Column(String(255))
    mime_type = Column(String(120))
    created_at = Column(DateTime, server_default=func.now())
    notes = Column(Text)
