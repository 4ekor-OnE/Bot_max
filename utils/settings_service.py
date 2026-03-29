"""Доступ к строке настроек system_settings (id=1)."""
from sqlalchemy.orm import Session

from models.system_settings import SystemSettings


def get_system_settings(db: Session) -> SystemSettings:
    row = db.query(SystemSettings).filter(SystemSettings.id == 1).first()
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row
