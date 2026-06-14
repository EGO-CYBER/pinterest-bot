import asyncio
import os
import re
import logging
from typing import Optional

from telegram import Update, InputFile
from telegram.ext import MessageHandler, filters, ContextTypes

from config import (
    ADMIN_ID, DOWNLOAD_DIR, AUTO_DELETE_SECONDS,
    FREE_DAILY_FILES, FREE_DAILY_MB, FREE_MAX_HEIGHT,
)
from database import (
    upsert_user, log_download, get_user_daily_size,
    get_user_daily_files, is_user_blocked, get_recent_download,
    is_premium, is_downloading, set_downloading, get_total_downloads,
)
from downloaders.pinterest import download_pinterest
from downloaders.tiktok import is_tiktok_url, download_tiktok

logger = logging.getLogger(__name__)

PINTEREST_REGEX = re.compile(
    r"(?:https?://)?(?:[^/]+\.)?(?:pinterest\.\w+|pin\.it)/",
    re.IGNORECASE,
)

_downloading_lock: dict[int, asyncio.Lock] = {}


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _downloading_lock:
        _downloading_lock[user_id] = asyncio.Lock()
    return _downloading_lock[user_id]


def _disk_free_mb() -> int:
    try:
        s = os.statvfs(DOWNLOAD_DIR)
        free = s.f_frsize * s.f_bavail
        return free // (1024 * 1024)
    except Exception:
        return 999


def _emergency_cleanup(force: bool = False) -> int:
    removed = 0
    free_mb = _disk_free_mb()
    if force or free_mb < 500:
        for f in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                    removed += 1
                except Exception:
                    pass
        if removed:
            logger.warning("⚠️ Only %d MB free - cleaned %d files", free_mb, removed)
    return removed


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text.strip()

    upsert_user(user.id, user.username or "", user.first_name)

    is_pin = bool(PINTEREST_REGEX.search(text))
    is_tik = is_tiktok_url(text)

    if not is_pin and not is_tik:
        return

    # blocked?
    if is_user_blocked(user.id):
        await update.message.reply_text("🚫 Ты заблокирован.")
        return

    # premium check
    prem = is_premium(user.id) or user.id == ADMIN_ID

    # QUEUE — только для free
    if not prem:
        if is_downloading(user.id):
            await update.message.reply_text(
                "⏳ Дождись завершения текущей загрузки.\n"
                "💎 Premium — без очереди: /buy"
            )
            return
        set_downloading(user.id, True)

    # daily limits (free only)
    if not prem:
        daily_files = get_user_daily_files(user.id)
        if daily_files >= FREE_DAILY_FILES:
            set_downloading(user.id, False)
            await update.message.reply_text(
                f"⚠️ Дневной лимит ({FREE_DAILY_FILES} файлов) исчерпан.\n"
                "Завтра сбросится.\n"
                "💎 Premium без лимитов: /buy"
            )
            return

        daily_size = get_user_daily_size(user.id)
        daily_mb_limit = FREE_DAILY_MB
        daily_bytes_limit = daily_mb_limit * 1024 * 1024

        if daily_size >= daily_bytes_limit:
            set_downloading(user.id, False)
            await update.message.reply_text(
                f"⚠️ Дневной лимит ({FREE_DAILY_MB} МБ) исчерпан.\n"
                "Завтра сбросится.\n"
                "💎 Premium без лимитов: /buy"
            )
            return

    # duplicate check (5 min)
    dup = get_recent_download(text, 300)
    if dup and os.path.isfile(dup["filepath"]):
        try:
            with open(dup["filepath"], "rb") as f:
                await update.message.reply_document(document=InputFile(f, filename=dup["filename"]))
            _safe_delete(dup["filepath"])
        except Exception:
            await update.message.reply_text("❌ Ошибка при отправке повтора")
        if not prem:
            set_downloading(user.id, False)
        return

    # emergency cleanup
    _emergency_cleanup()

    status = await update.message.reply_text("⏬ Скачиваю...")

    result: Optional[dict] = None
    source_name = ""

    try:
        quality = None if prem else 720
        if is_pin:
            result = await download_pinterest(text, max_height=quality)
            source_name = "🖼 Pinterest"
        elif is_tik:
            result = await download_tiktok(text, max_height=quality)
            source_name = "🎵 TikTok"
    except Exception:
        logger.exception("Download error")
        await status.edit_text("❌ Ошибка при скачивании")
        if not prem:
            set_downloading(user.id, False)
        return

    if result is None:
        await status.edit_text(
            "❌ Не удалось скачать.\n"
            "• Приватный/удалённый пост\n"
            "• Платформа недоступна\n"
            "• Неподдерживаемый формат"
        )
        if not prem:
            set_downloading(user.id, False)
        return

    filepath = result["filepath"]
    filename = result["filename"]
    filesize = result["filesize"]
    file_type = result["file_type"]

    # final size check (free)
    if not prem:
        if daily_size + filesize > FREE_DAILY_MB * 1024 * 1024:
            _safe_delete(filepath)
            await status.edit_text("⚠️ Файл превышает оставшийся лимит MB.")
            set_downloading(user.id, False)
            return

    # 50MB Telegram limit
    if filesize > 50 * 1024 * 1024:
        _safe_delete(filepath)
        await status.edit_text("❌ Файл >50 МБ, Telegram не разрешает.")
        if not prem:
            set_downloading(user.id, False)
        return

    if not os.path.isfile(filepath) or os.path.getsize(filepath) == 0:
        _safe_delete(filepath)
        await status.edit_text("❌ Файл повреждён")
        if not prem:
            set_downloading(user.id, False)
        return

    # counter-based filename
    total = get_total_downloads()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    prefix = "video" if file_type == "video" else "image"
    short_name = f"{prefix}_{total + 1}.{ext}"

    # rename to short name
    new_path = os.path.join(os.path.dirname(filepath), short_name)
    try:
        os.rename(filepath, new_path)
        filepath, filename = new_path, short_name
    except Exception:
        pass

    # --- SEND ---
    kval = "оригинал" if prem else f"до 720p"

    if file_type == "video":
        # 1 — sendVideo (preview with player)
        await status.edit_text(f"{source_name} | отправляю превью...")
        try:
            with open(filepath, "rb") as f:
                await update.message.reply_video(
                    video=InputFile(f, filename=filename),
                    caption=f"🎬 {filename}",
                    supports_streaming=True,
                )
        except Exception:
            logger.exception("sendVideo failed, falling back to document")

        # 2 — sendDocument (original file, no compression)
        await status.edit_text(f"{source_name} | отправляю файл...")
        try:
            with open(filepath, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=filename),
                    caption=f"📁 {filename} ({kval})",
                )
        except Exception:
            logger.exception("sendDocument failed")
            await status.edit_text("❌ Ошибка при отправке файла")
            _safe_delete(filepath)
            if not prem:
                set_downloading(user.id, False)
            return
    else:
        # photo — just document (no compression)
        await status.edit_text(f"{source_name} | отправляю...")
        try:
            with open(filepath, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=filename),
                    caption=f"📁 {filename} ({kval})",
                )
        except Exception:
            logger.exception("Upload failed")
            await status.edit_text("❌ Ошибка при отправке")
            _safe_delete(filepath)
            if not prem:
                set_downloading(user.id, False)
            return

    await status.delete()

    log_download(user.id, text, file_type, filesize, filepath, filename)

    asyncio.create_task(_delayed_delete(filepath, AUTO_DELETE_SECONDS))

    if not prem:
        set_downloading(user.id, False)


async def _delayed_delete(filepath: str, delay: int) -> None:
    await asyncio.sleep(delay)
    _safe_delete(filepath)


def _safe_delete(filepath: str) -> None:
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
    except Exception:
        pass


def get_handlers():
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link),
    ]
