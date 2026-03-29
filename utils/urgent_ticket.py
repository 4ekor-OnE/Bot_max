"""Срочные заявки: фиксированная категория и создание записи в справочнике при необходимости."""

from __future__ import annotations

URGENT_CATEGORY_NAME = "Касса и оплата"


def ensure_urgent_category(db) -> tuple[int, str]:
    """
    Возвращает (id, name) категории для срочных заявок.
    Если категории с таким именем нет — создаёт (SLA часов из system_settings).
    """
    from models.category import Category
    from utils.settings_service import get_system_settings

    settings = get_system_settings(db)
    cat = db.query(Category).filter(Category.name == URGENT_CATEGORY_NAME).first()
    if cat:
        return cat.id, cat.name
    cat = Category(
        name=URGENT_CATEGORY_NAME,
        description="Автоматически для срочных заявок (касса и оплата)",
        sla_hours=settings.urgent_sla_hours,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat.id, cat.name
