"""Загрузка и проверка файлов инструкций."""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import aiohttp

from config import BOT_TOKEN, INSTRUCTION_MAX_BYTES, INSTRUCTIONS_DIR

logger = logging.getLogger(__name__)

ALLOWED_INSTRUCTION_EXTENSIONS = {".txt", ".pdf", ".docx"}


def ensure_instruction_dirs() -> None:
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)


def safe_extension(filename: str | None) -> str | None:
    if not filename:
        return None
    lower = filename.lower()
    for ext in ALLOWED_INSTRUCTION_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    m = re.search(r"(\.[a-z0-9]{2,8})$", lower)
    if m and m.group(1) in ALLOWED_INSTRUCTION_EXTENSIONS:
        return m.group(1)
    return None


async def download_url_to_file(url: str, dest: Path) -> None:
    headers = {}
    token = (BOT_TOKEN or "").strip()
    if token:
        headers["Authorization"] = token
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            total = 0
            chunks: list[bytes] = []
            async for chunk in resp.content.iter_chunked(65536):
                total += len(chunk)
                if total > INSTRUCTION_MAX_BYTES:
                    raise ValueError("Файл слишком большой")
                chunks.append(chunk)
    dest.write_bytes(b"".join(chunks))


def new_stored_filename(ext: str) -> str:
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"{uuid.uuid4().hex}{ext}"


def save_instruction_disk(data: bytes, original_name: str | None) -> str:
    """Сохраняет файл на диск. Возвращает stored_filename."""
    if len(data) > INSTRUCTION_MAX_BYTES:
        raise ValueError("Файл слишком большой")
    ext = safe_extension(original_name)
    if not ext or ext not in ALLOWED_INSTRUCTION_EXTENSIONS:
        raise ValueError("Допустимы только .txt, .pdf, .docx")
    ensure_instruction_dirs()
    stored = new_stored_filename(ext)
    path = INSTRUCTIONS_DIR / stored
    path.write_bytes(data)
    return stored
