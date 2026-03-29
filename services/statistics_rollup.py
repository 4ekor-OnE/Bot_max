"""Заполнение таблицы statistics срезом по дню (UTC)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models.daily_statistics import DailyStatistics
from models.ticket import Ticket, TicketStatus


def rollup_day(db: Session, d: date) -> DailyStatistics:
    """Пересчитать агрегаты за календарный день d (UTC)."""
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    now = datetime.now(timezone.utc)

    q = db.query(Ticket).filter(Ticket.created_at >= start, Ticket.created_at < end)
    tickets = q.all()
    total = len(tickets)
    new_c = sum(1 for t in tickets if t.status == TicketStatus.NEW)
    resolved_c = sum(1 for t in tickets if t.status == TicketStatus.RESOLVED)
    urgent_c = sum(1 for t in tickets if t.is_urgent)
    overdue_c = 0
    for t in tickets:
        if t.sla_deadline and t.status not in (TicketStatus.RESOLVED,):
            dl = t.sla_deadline
            if getattr(dl, "tzinfo", None) is None:
                dl = dl.replace(tzinfo=timezone.utc)
            if now > dl:
                overdue_c += 1

    row = db.query(DailyStatistics).filter(DailyStatistics.stat_date == d).first()
    if not row:
        row = DailyStatistics(stat_date=d)
        db.add(row)
    row.total_tickets = total
    row.new_tickets = new_c
    row.resolved_tickets = resolved_c
    row.urgent_tickets = urgent_c
    row.overdue_tickets = overdue_c
    db.commit()
    db.refresh(row)
    return row
