"""Модель магазина"""
from sqlalchemy import Column, Integer, String
from models.database import Base


class Shop(Base):
    __tablename__ = 'shops'

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
