"""Flask панель управления: тот же пароль, что /admin в боте."""
from __future__ import annotations

import secrets
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import func

from config import TICKET_PHOTOS_DIR, ensure_data_dirs, verify_admin_password
from services.ticket_photos import (
    expand_legacy_ticket_photo_path_field,
    is_safe_ticket_photo_filename,
    list_photo_paths_for_ticket,
    normalize_paths_from_attachment_raw_paths,
)
from models.category import Category
from models.database import SessionLocal, init_db
from models.shop import Shop
from models.ticket import Ticket, TicketStatus
from models.ticket_attachment import TicketAttachment
from models.ticket_comment import TicketComment
from models.user import User, UserRole

def create_app() -> Flask:
    import os
    from pathlib import Path

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
    )
    app.config["SECRET_KEY"] = os.getenv(
        "WEB_ADMIN_SECRET_KEY",
        "change-me-set-WEB_ADMIN_SECRET_KEY-in-production",
    )

    @app.context_processor
    def _ctx():
        return {"csrf_token": session.get("csrf_token", "")}

    def login_required(f):
        @wraps(f)
        def w(*args, **kwargs):
            if not session.get("web_admin"):
                return redirect(url_for("login", next=request.path))
            return f(*args, **kwargs)

        return w

    def check_csrf():
        if request.form.get("csrf_token") != session.get("csrf_token"):
            abort(400)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("web_admin"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            check_csrf() if session.get("csrf_token") else None
            pw = (request.form.get("password") or "").strip()
            if verify_admin_password(pw):
                session["web_admin"] = True
                session["csrf_token"] = secrets.token_hex(32)
                session.permanent = True
                flash("Вход выполнен.", "success")
                nxt = request.args.get("next") or url_for("dashboard")
                if not nxt.startswith("/"):
                    nxt = url_for("dashboard")
                return redirect(nxt)
            flash("Неверный пароль.", "danger")
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Вы вышли.", "info")
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        db = SessionLocal()
        try:
            n_users = db.query(func.count(User.id)).scalar() or 0
            n_shops = db.query(func.count(Shop.id)).scalar() or 0
            n_cats = db.query(func.count(Category.id)).scalar() or 0
            n_tickets = db.query(func.count(Ticket.id)).scalar() or 0
            rows = (
                db.query(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status).all()
            )
            ticket_by_status = [(s.value if hasattr(s, "value") else str(s), n) for s, n in rows]
        finally:
            db.close()
        return render_template(
            "dashboard.html",
            n_users=n_users,
            n_shops=n_shops,
            n_cats=n_cats,
            n_tickets=n_tickets,
            ticket_by_status=ticket_by_status,
        )

    @app.get("/users")
    @login_required
    def users_list():
        db = SessionLocal()
        try:
            users = db.query(User).order_by(User.id).all()
        finally:
            db.close()
        return render_template("users.html", users=users)

    @app.post("/users/<int:uid>/role")
    @login_required
    def user_set_role(uid: int):
        check_csrf()
        role_s = (request.form.get("role") or "").strip()
        if role_s not in ("user", "support", "director"):
            flash("Недопустимая роль.", "danger")
            return redirect(url_for("users_list"))
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == uid).first()
            if not u:
                flash("Пользователь не найден.", "danger")
            else:
                u.role = UserRole(role_s)
                db.commit()
                flash("Роль обновлена.", "success")
        finally:
            db.close()
        return redirect(url_for("users_list"))

    @app.get("/shops")
    @login_required
    def shops_list():
        db = SessionLocal()
        try:
            shops_raw = db.query(Shop).order_by(Shop.id).all()
            shops = []
            for s in shops_raw:
                cnt = db.query(func.count(Ticket.id)).filter(Ticket.shop_id == s.id).scalar() or 0
                shops.append(type("Row", (), {"id": s.id, "name": s.name, "ticket_count": cnt})())
        finally:
            db.close()
        return render_template("shops.html", shops=shops)

    @app.post("/shops/add")
    @login_required
    def shop_add():
        check_csrf()
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Пустое название.", "danger")
            return redirect(url_for("shops_list"))
        db = SessionLocal()
        try:
            if db.query(Shop).filter(Shop.name == name).first():
                flash("Магазин с таким именем уже есть.", "danger")
            else:
                db.add(Shop(name=name))
                db.commit()
                flash("Магазин добавлен.", "success")
        finally:
            db.close()
        return redirect(url_for("shops_list"))

    @app.post("/shops/<int:sid>/rename")
    @login_required
    def shop_rename(sid: int):
        check_csrf()
        name = (request.form.get("name") or "").strip()
        db = SessionLocal()
        try:
            s = db.query(Shop).filter(Shop.id == sid).first()
            if not s:
                flash("Не найден.", "danger")
            elif db.query(Shop).filter(Shop.name == name, Shop.id != sid).first():
                flash("Имя занято.", "danger")
            else:
                s.name = name
                db.commit()
                flash("Сохранено.", "success")
        finally:
            db.close()
        return redirect(url_for("shops_list"))

    @app.post("/shops/<int:sid>/delete")
    @login_required
    def shop_delete(sid: int):
        check_csrf()
        db = SessionLocal()
        try:
            s = db.query(Shop).filter(Shop.id == sid).first()
            if not s:
                flash("Не найден.", "danger")
            elif (db.query(func.count(Ticket.id)).filter(Ticket.shop_id == sid).scalar() or 0) > 0:
                flash("Нельзя удалить: есть заявки.", "danger")
            else:
                db.delete(s)
                db.commit()
                flash("Удалён.", "success")
        finally:
            db.close()
        return redirect(url_for("shops_list"))

    @app.get("/categories")
    @login_required
    def categories_list():
        db = SessionLocal()
        try:
            cats = db.query(Category).order_by(Category.id).all()
            categories = []
            for c in cats:
                cnt = (
                    db.query(func.count(Ticket.id)).filter(Ticket.category_id == c.id).scalar() or 0
                )
                categories.append(
                    type("Row", (), {"id": c.id, "name": c.name, "description": c.description, "sla_hours": c.sla_hours, "ticket_count": cnt})()
                )
        finally:
            db.close()
        return render_template("categories.html", categories=categories)

    @app.post("/categories/add")
    @login_required
    def category_add():
        check_csrf()
        name = (request.form.get("name") or "").strip()
        desc = (request.form.get("description") or "").strip()
        if desc == "-":
            desc = ""
        try:
            sla = int(request.form.get("sla_hours") or "24")
        except ValueError:
            sla = 24
        if sla < 1:
            sla = 24
        if not name:
            flash("Нужно название.", "danger")
            return redirect(url_for("categories_list"))
        db = SessionLocal()
        try:
            db.add(Category(name=name, description=desc, sla_hours=sla))
            db.commit()
            flash("Категория создана.", "success")
        finally:
            db.close()
        return redirect(url_for("categories_list"))

    @app.post("/categories/<int:cid>/update")
    @login_required
    def category_update(cid: int):
        check_csrf()
        name = (request.form.get("name") or "").strip()
        desc = (request.form.get("description") or "").strip()
        if desc == "-":
            desc = ""
        try:
            sla = int(request.form.get("sla_hours") or "24")
        except ValueError:
            sla = 24
        db = SessionLocal()
        try:
            c = db.query(Category).filter(Category.id == cid).first()
            if not c:
                flash("Не найдена.", "danger")
            else:
                c.name = name or c.name
                c.description = desc
                c.sla_hours = max(1, sla)
                db.commit()
                flash("Сохранено.", "success")
        finally:
            db.close()
        return redirect(url_for("categories_list"))

    @app.post("/categories/<int:cid>/delete")
    @login_required
    def category_delete(cid: int):
        check_csrf()
        db = SessionLocal()
        try:
            c = db.query(Category).filter(Category.id == cid).first()
            if not c:
                flash("Не найдена.", "danger")
            elif (db.query(func.count(Ticket.id)).filter(Ticket.category_id == cid).scalar() or 0) > 0:
                flash("Нельзя удалить: есть заявки.", "danger")
            else:
                db.delete(c)
                db.commit()
                flash("Удалена.", "success")
        finally:
            db.close()
        return redirect(url_for("categories_list"))

    @app.get("/tickets")
    @login_required
    def tickets_list():
        st_f = (request.args.get("status") or "").strip()
        shop_f = request.args.get("shop_id") or ""
        db = SessionLocal()
        try:
            q = db.query(Ticket).order_by(Ticket.id.desc())
            if st_f in ("new", "in_progress", "resolved", "postponed"):
                q = q.filter(Ticket.status == TicketStatus(st_f))
            if shop_f.isdigit():
                q = q.filter(Ticket.shop_id == int(shop_f))
            raw = q.limit(200).all()
            shop_map = {s.id: s.name for s in db.query(Shop).all()}
            ids = [t.id for t in raw]
            att_by_tid: dict[int, list] = {}
            if ids:
                for att in (
                    db.query(TicketAttachment)
                    .filter(TicketAttachment.ticket_id.in_(ids))
                    .order_by(
                        TicketAttachment.ticket_id,
                        TicketAttachment.position.asc(),
                        TicketAttachment.id.asc(),
                    )
                    .all()
                ):
                    att_by_tid.setdefault(att.ticket_id, []).append(att.path)
            tickets = []
            for t in raw:
                row_paths = normalize_paths_from_attachment_raw_paths(
                    att_by_tid.get(t.id, [])
                )
                if not row_paths:
                    row_paths = expand_legacy_ticket_photo_path_field(t.photo_path)
                tickets.append(
                    type(
                        "T",
                        (),
                        {
                            "id": t.id,
                            "status": t.status,
                            "title": t.title,
                            "created_at": t.created_at,
                            "shop_name": shop_map.get(t.shop_id, str(t.shop_id)),
                            "photo_count": len(row_paths),
                        },
                    )()
                )
            shops = db.query(Shop).order_by(Shop.id).all()
        finally:
            db.close()
        filters = {"status": st_f, "shop_id": int(shop_f) if shop_f.isdigit() else None}
        statuses = [s.value for s in TicketStatus]
        return render_template(
            "tickets.html",
            tickets=tickets,
            shops=shops,
            filters=filters,
            statuses=statuses,
        )

    @app.route("/tickets/<int:tid>", methods=["GET", "POST"])
    @login_required
    def ticket_detail(tid: int):
        if request.method == "POST":
            check_csrf()
            new_st = (request.form.get("status") or "").strip()
            if new_st not in ("new", "in_progress", "resolved", "postponed"):
                flash("Неверный статус.", "danger")
                return redirect(url_for("ticket_detail", tid=tid))
            db = SessionLocal()
            try:
                from datetime import datetime, timezone

                t = db.query(Ticket).filter(Ticket.id == tid).first()
                if not t:
                    flash("Не найдена.", "danger")
                else:
                    t.status = TicketStatus(new_st)
                    if new_st == "resolved":
                        t.resolved_at = datetime.now(timezone.utc)
                    db.commit()
                    flash("Статус обновлён.", "success")
            finally:
                db.close()
            return redirect(url_for("ticket_detail", tid=tid))

        db = SessionLocal()
        try:
            ticket = db.query(Ticket).filter(Ticket.id == tid).first()
            if not ticket:
                flash("Заявка не найдена.", "danger")
                return redirect(url_for("tickets_list"))
            shop = db.query(Shop).filter(Shop.id == ticket.shop_id).first()
            category = db.query(Category).filter(Category.id == ticket.category_id).first()
            comments = (
                db.query(TicketComment)
                .filter(TicketComment.ticket_id == tid)
                .order_by(TicketComment.created_at.desc())
                .limit(50)
                .all()
            )
            photo_paths = list_photo_paths_for_ticket(db, ticket)
        finally:
            db.close()
        statuses = [s.value for s in TicketStatus]
        return render_template(
            "ticket_detail.html",
            ticket=ticket,
            shop=shop,
            category=category,
            comments=comments,
            photo_paths=photo_paths,
            statuses=statuses,
        )

    @app.get("/ticket-photos/<filename>")
    @login_required
    def ticket_photo_file(filename: str):
        if not is_safe_ticket_photo_filename(filename):
            abort(404)
        ensure_data_dirs()
        try:
            base = TICKET_PHOTOS_DIR.resolve()
            path = (TICKET_PHOTOS_DIR / filename).resolve()
        except OSError:
            abort(404)
        if path.parent != base or not path.is_file():
            abort(404)
        return send_from_directory(
            base,
            filename,
            max_age=86400,
        )

    return app


def run():
    import os

    from config import WEB_ADMIN_HOST, WEB_ADMIN_PORT

    ensure_data_dirs()
    init_db()
    app = create_app()
    app.run(
        host=WEB_ADMIN_HOST,
        port=WEB_ADMIN_PORT,
        debug=os.getenv("WEB_ADMIN_DEBUG", "").lower() in ("1", "true", "yes"),
    )
