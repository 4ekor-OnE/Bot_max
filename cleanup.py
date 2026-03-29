#!/usr/bin/env python3
"""
Плановая очистка данных (cron / ручной запуск).
Использует те же правила, что и админ-панель «Очистка данных».
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from config import ensure_data_dirs
from models.database import SessionLocal, init_db
from services.cleanup_service import (
    cleanup_resolved_tickets_older_than,
    cleanup_sessions,
    cleanup_temp_files,
)
from utils.settings_service import get_system_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Очистка старых заявок, сессий и temp-файлов")
    parser.add_argument("--tickets", action="store_true", help="Удалить старые решённые заявки")
    parser.add_argument("--sessions", action="store_true", help="Очистить сессии FSM")
    parser.add_argument("--temp", action="store_true", help="Удалить старые временные файлы")
    parser.add_argument("--all", action="store_true", help="Все операции")
    parser.add_argument(
        "--stats-day",
        metavar="YYYY-MM-DD",
        help="Пересчитать агрегат в таблице statistics за указанный день (UTC)",
    )
    parser.add_argument(
        "--stats-yesterday",
        action="store_true",
        help="Пересчитать statistics за вчера (UTC) — удобно для cron",
    )
    args = parser.parse_args()

    stats_only = bool(args.stats_day or args.stats_yesterday)
    if not (args.tickets or args.sessions or args.temp or args.all or stats_only):
        parser.print_help()
        return 1

    init_db()
    ensure_data_dirs()

    db = SessionLocal()
    try:
        if stats_only:
            from services.statistics_rollup import rollup_day

            if args.stats_yesterday:
                d = date.today() - timedelta(days=1)
            else:
                d = date.fromisoformat(args.stats_day)
            row = rollup_day(db, d)
            print(
                f"statistics {row.stat_date}: total={row.total_tickets} new={row.new_tickets} "
                f"resolved={row.resolved_tickets} urgent={row.urgent_tickets} overdue={row.overdue_tickets}"
            )

        if not (args.all or args.tickets or args.sessions or args.temp):
            return 0

        s = get_system_settings(db)
        if args.all or args.tickets:
            n = cleanup_resolved_tickets_older_than(db, s.retention_resolved_tickets_days)
            print(f"Удалено решённых заявок: {n}")
        if args.all or args.sessions:
            n = cleanup_sessions(db, s.retention_sessions_days)
            print(f"Удалено сессий: {n}")
        if args.all or args.temp:
            n = cleanup_temp_files(s.retention_temp_files_days)
            print(f"Удалено временных файлов: {n}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
