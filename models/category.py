"""Модель категории проблемы"""
from sqlalchemy import Column, Integer, String, Text
from models.database import Base


class Category(Base):
    __tablename__ = 'categories'

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    sla_hours = Column(Integer, default=24)  # SLA в часах
