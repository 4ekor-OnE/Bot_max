"""Сохранение фото заявок для отображения в панели управления."""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from config import BOT_TOKEN, TICKET_MAX_PHOTOS, TICKET_PHOTO_MAX_BYTES, TICKET_PHOTOS_DIR
from services.instruction_files import download_url_to_file

logger = logging.getLogger(__name__)

_LOCAL_PREFIX = "local:"
_FNAME_RE = re.compile(r"^[a-f0-9]{32}\.(?:jpe?g|png|gif|webp|bin)$", re.IGNORECASE)
_MAX_API_BASE = "https://platform-api.max.ru"


def ensure_ticket_photos_dir() -> None:
    TICKET_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)


def is_local_photo_ref(value: str | None) -> bool:
    return bool(value and value.startswith(_LOCAL_PREFIX))


def local_photo_filename(value: str | None) -> str | None:
    if not value or not value.startswith(_LOCAL_PREFIX):
        return None
    return value[len(_LOCAL_PREFIX) :]


def is_safe_ticket_photo_filename(name: str) -> bool:
    return bool(name and _FNAME_RE.match(name))


def is_image_attachment(attachment: Any) -> bool:
    return _attachment_type_str(attachment) == "image"


def collect_image_attachments_from_message(message: Any) -> list:
    """Все вложения-изображения из тела сообщения (порядок сохраняется)."""
    out: list = []
    try:
        body = getattr(message, "body", None)
        attachments = getattr(body, "attachments", None) or []
        for attachment in attachments:
            if is_image_attachment(attachment):
                out.append(attachment)
    except Exception:
        logger.debug("collect_image_attachments_from_message: не удалось прочитать вложения", exc_info=True)
    return out


def expand_legacy_ticket_photo_path_field(value: str | None) -> list[str]:
    """
    Разбор поля tickets.photo_path, если вложений в ticket_attachments нет (или все отброшены):
    одна ссылка local:/https, JSON-массив путей, несколько строк или пути через запятую.
    """
    if not value:
        return []
    p = str(value).strip()
    if not p or p.lower() == "uploaded":
        return []
    if p.startswith("["):
        try:
            data = json.loads(p)
            if isinstance(data, list):
                out: list[str] = []
                for x in data:
                    s = str(x).strip() if x is not None else ""
                    if s and s.lower() != "uploaded":
                        out.append(s)
                return out[:TICKET_MAX_PHOTOS]
        except json.JSONDecodeError:
            pass
    if "\n" in p:
        parts = [
            ln.strip()
            for ln in p.splitlines()
            if ln.strip() and ln.strip().lower() != "uploaded"
        ]
        if len(parts) > 1:
            return parts[:TICKET_MAX_PHOTOS]
    if "," in p:
        parts = [x.strip() for x in p.split(",") if x.strip() and x.strip().lower() != "uploaded"]
        if len(parts) > 1 and all(
            x.startswith(("local:", "http://", "https://")) for x in parts
        ):
            return parts[:TICKET_MAX_PHOTOS]
    return [p]


def normalize_paths_from_attachment_raw_paths(raw_paths: list[str | None]) -> list[str]:
    """Пути из строк ticket_attachments.path (одна строка может быть JSON-массивом путей)."""
    out: list[str] = []
    for raw in raw_paths:
        s = str(raw).strip() if raw is not None else ""
        if not s or s.lower() == "uploaded":
            continue
        if s.startswith("["):
            out.extend(expand_legacy_ticket_photo_path_field(s))
        else:
            out.append(s)
        if len(out) >= TICKET_MAX_PHOTOS:
            break
    return out[:TICKET_MAX_PHOTOS]


def list_photo_paths_for_ticket(db, ticket) -> list[str]:
    """Пути фото заявки: ticket_attachments (по порядку), иначе разбор legacy tickets.photo_path."""
    from models.ticket_attachment import TicketAttachment

    tid = getattr(ticket, "id", None)
    if tid is None:
        return []
    rows = (
        db.query(TicketAttachment)
        .filter(TicketAttachment.ticket_id == tid)
        .order_by(TicketAttachment.position.asc(), TicketAttachment.id.asc())
        .all()
    )
    from_rows = normalize_paths_from_attachment_raw_paths([r.path for r in rows])
    if from_rows:
        return from_rows
    return expand_legacy_ticket_photo_path_field(getattr(ticket, "photo_path", None))


def fsm_normalize_photo_paths(fsm_data: dict) -> list[str]:
    """Список путей из FSM (photo_paths или старый photo_path), с лимитом."""
    raw = fsm_data.get("photo_paths")
    out: list[str] = []
    if isinstance(raw, list):
        for p in raw:
            s = str(p).strip() if p is not None else ""
            if s and s.lower() != "uploaded":
                out.append(s)
    else:
        legacy = fsm_data.get("photo_path")
        if legacy:
            s = str(legacy).strip()
            if s and s.lower() != "uploaded":
                out.append(s)
    return out[:TICKET_MAX_PHOTOS]


def _attachment_type_str(attachment: Any) -> str:
    t = getattr(attachment, "type", None)
    if t is None:
        return ""
    if isinstance(t, str):
        return t.lower()
    return str(getattr(t, "value", t)).lower()


def _payload_url_token(payload: Any) -> tuple[str | None, str | None]:
    """Достаёт url и token из payload (dict или объект maxapi)."""
    if payload is None:
        return None, None
    if isinstance(payload, dict):
        url = payload.get("url") or payload.get("link")
        token = payload.get("token") or payload.get("file_id")
        if not url and payload.get("photo_id") is not None:
            token = token or str(payload["photo_id"])
        return (_nonempty_str(url), _nonempty_str(token))
    url = getattr(payload, "url", None)
    token = getattr(payload, "token", None)
    if not token:
        pid = getattr(payload, "photo_id", None)
        if pid is not None:
            token = str(pid)
    return (_nonempty_str(url), _nonempty_str(token))


def _nonempty_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _guess_image_ext(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


async def _url_to_bytes(url: str) -> bytes | None:
    import tempfile
    import os

    fd, tmp_name = tempfile.mkstemp(suffix=".bin")
    os.close(fd)
    path = Path(tmp_name)
    try:
        await download_url_to_file(url, path)
        data = path.read_bytes()
        if len(data) > TICKET_PHOTO_MAX_BYTES:
            logger.warning("Фото заявки слишком большое после загрузки")
            return None
        return data
    except Exception:
        logger.exception("Не удалось скачать фото по URL")
        return None
    finally:
        path.unlink(missing_ok=True)


def _json_extract_url(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for key in ("url", "link", "downloadUrl", "download_url", "src"):
            u = _nonempty_str(obj.get(key))
            if u and (u.startswith("http://") or u.startswith("https://")):
                return u
        for v in obj.values():
            u = _json_extract_url(v)
            if u:
                return u
    elif isinstance(obj, list):
        for item in obj:
            u = _json_extract_url(item)
            if u:
                return u
    return None


async def _try_fetch_photo_by_token(token: str) -> bytes | None:
    headers: dict[str, str] = {}
    t = (BOT_TOKEN or "").strip()
    if t:
        headers["Authorization"] = t
    timeout = aiohttp.ClientTimeout(total=90)
    paths = (
        f"/photos/{token}",
        f"/images/{token}",
        f"/files/{token}",
    )
    async with aiohttp.ClientSession(timeout=timeout, base_url=_MAX_API_BASE, headers=headers) as session:
        for path in paths:
            try:
                async with session.get(path) as resp:
                    if resp.status != 200:
                        continue
                    ct = (resp.headers.get("Content-Type") or "").lower()
                    data = await resp.read()
                    if len(data) > TICKET_PHOTO_MAX_BYTES:
                        continue
                    if "application/json" in ct or (data[:1] == b"{" and data.strip().endswith(b"}")):
                        try:
                            j = json.loads(data.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            continue
                        nested_url = _json_extract_url(j)
                        if nested_url:
                            return await _url_to_bytes(nested_url)
                        continue
                    if ct.startswith("image/") or _guess_image_ext(data) != ".bin":
                        return data
            except Exception:
                logger.debug("Попытка %s для токена фото не удалась", path, exc_info=True)
    return None


def _save_ticket_photo_bytes(data: bytes) -> str | None:
    if len(data) > TICKET_PHOTO_MAX_BYTES:
        return None
    ensure_ticket_photos_dir()
    ext = _guess_image_ext(data)
    name = f"{uuid.uuid4().hex}{ext}"
    path = TICKET_PHOTOS_DIR / name
    path.write_bytes(data)
    return f"{_LOCAL_PREFIX}{name}"


async def persist_ticket_photo_from_attachment(attachment: Any) -> str | None:
    """
    Возвращает строку для ticket.photo_path: https URL или local:<filename>.
    """
    if _attachment_type_str(attachment) != "image":
        return None
    payload = getattr(attachment, "payload", None)
    url, token = _payload_url_token(payload)

    data: bytes | None = None
    if url and (url.startswith("http://") or url.startswith("https://")):
        data = await _url_to_bytes(url)

    if data is None and token:
        data = await _try_fetch_photo_by_token(token)

    if data is not None:
        saved = _save_ticket_photo_bytes(data)
        if saved:
            return saved

    if url and (url.startswith("http://") or url.startswith("https://")):
        return url

    return None


def media_attachments_for_ticket_photo(photo_path: str | None) -> list:
    """
    Вложения MAX API для фото заявки (local:…, https URL или пропуск для uploaded/пусто).
    Используется в send_message / safe_answer вместе с текстом.
    """
    if not photo_path:
        return []
    p = str(photo_path).strip()
    if not p or p == "uploaded":
        return []
    try:
        from maxapi.types.input_media import InputMedia
    except ImportError:
        return []
    if p.startswith("local:"):
        fname = local_photo_filename(p)
        if not fname or not is_safe_ticket_photo_filename(fname):
            return []
        path = TICKET_PHOTOS_DIR / fname
        if path.is_file():
            return [InputMedia(str(path))]
        return []
    if p.startswith("http://") or p.startswith("https://"):
        return [InputMedia(p)]
    return []


async def send_ticket_photo_to_max_user(bot, max_id: str, photo_path: str | None, caption: str = "") -> None:
    """Отдельное сообщение с фото (если есть файл/URL), после текста с клавиатурой."""
    att = media_attachments_for_ticket_photo(photo_path)
    if not att:
        return
    try:
        uid = int(str(max_id).strip())
    except (TypeError, ValueError):
        logger.warning("send_ticket_photo_to_max_user: некорректный max_id")
        return
    text = (caption or "").strip() or "📷 Фото к заявке"
    try:
        await bot.send_message(user_id=uid, text=text[:3900], attachments=att)
    except Exception as e:
        logger.warning("Не удалось отправить фото заявки user=%s: %s", uid, e)


async def send_all_ticket_photos_to_max_user(
    bot,
    max_id: str,
    paths: list[str] | None,
    caption_prefix: str = "",
) -> None:
    """Отправляет по одному сообщению на каждое фото (после текста карточки)."""
    clean = [p for p in (paths or []) if p and str(p).strip().lower() not in ("", "uploaded")]
    if not clean:
        return
    n = len(clean)
    base = (caption_prefix or "").strip() or "📷 Фото к заявке"
    for i, path in enumerate(clean, 1):
        cap = base if n == 1 else f"{base} ({i}/{n})"
        await send_ticket_photo_to_max_user(bot, max_id, path, cap)
