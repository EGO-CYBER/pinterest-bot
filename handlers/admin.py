import os
import io
import csv
import logging

from telegram import Update, LabeledPrice, InlineKeyboardButton
from telegram.ext import CommandHandler, ContextTypes, filters

from config import ADMIN_ID, DOWNLOAD_DIR
from database import (
    get_stats, get_all_users, block_user, unblock_user,
    get_blocked_users, set_user_limit,
)
from system_info import get_system_info
from handlers.media import _emergency_cleanup

logger = logging.getLogger(__name__)


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("🚫 Нет доступа.")
            return
        return await func(update, context)
    return wrapper


@_admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_stats()
    text = (
        "📊 **Статистика бота**\n\n"
        f"👥 Всего пользователей: `{s['total_users']}`\n"
        f"📊 Всего скачиваний: `{s['total_downloads']}`\n"
        f"💾 Всего скачано: `{s['total_size_mb']} МБ`\n"
        f"🎥 Видео: `{s['videos']}`\n"
        f"🖼 Фото: `{s['photos']}`\n"
        f"📅 Активных сегодня: `{s['today_users']}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@_admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = get_all_users()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Username", "Name", "First seen", "Last seen", "Downloads", "Size (MB)", "Blocked"])
    for u in users:
        writer.writerow([
            u["user_id"], u["username"], u["first_name"],
            u["first_seen"], u["last_seen"],
            u["total_downloads"],
            round((u["total_file_size"] or 0) / 1024 / 1024, 2),
            "Yes" if u["is_blocked"] else "No",
        ])
    csv_bytes = output.getvalue().encode("utf-8-sig")
    await update.message.reply_document(
        document=io.BytesIO(csv_bytes),
        filename="users.csv",
    )


@_admin_only
async def cmd_sysinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_system_info()
    text = (
        "🖥 **Система**\n\n"
        f"🏠 Хост: `{s['hostname']}`\n"
        f"⚙️ Процессор: `{s['cpu_model']}`\n"
        f"🧠 CPU: `{s['cpu_percent']}%` ({s['cpu_count']} ядер)\n"
        f"💾 RAM: `{s['ram_used_gb']}/{s['ram_total_gb']} GB` ({s['ram_percent']}%)\n"
        f"📀 Диск: `{s['disk_used_gb']}/{s['disk_total_gb']} GB` ({s['disk_percent']}%)\n"
        f"🔓 Свободно: `{s['disk_free_gb']} GB`\n"
        f"⏱ Uptime: `{s['uptime']}`\n"
        f"🐍 Python: `{s['python_version']}`\n"
        f"💿 ОС: `{s['os']}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@_admin_only
async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: `/limit <МБ>`", parse_mode="Markdown")
        return
    mb = int(context.args[0])
    set_user_limit(mb)
    await update.message.reply_text(f"✅ Дневной лимит изменён на `{mb} МБ`", parse_mode="Markdown")


@_admin_only
async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    count = _emergency_cleanup(force=True)
    await update.message.reply_text(f"🧹 Удалено `{count}` файлов", parse_mode="Markdown")


@_admin_only
async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Использование: `/block <user_id>`", parse_mode="Markdown")
        return
    uid = int(context.args[0])
    if uid == ADMIN_ID:
        await update.message.reply_text("❌ Нельзя заблокировать админа.")
        return
    block_user(uid)
    await update.message.reply_text(f"🚫 Пользователь `{uid}` заблокирован", parse_mode="Markdown")


@_admin_only
async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Использование: `/unblock <user_id>`", parse_mode="Markdown")
        return
    uid = int(context.args[0])
    unblock_user(uid)
    await update.message.reply_text(f"✅ Пользователь `{uid}` разблокирован", parse_mode="Markdown")


@_admin_only
async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    banned = get_blocked_users()
    if not banned:
        await update.message.reply_text("✅ Нет заблокированных пользователей.")
        return
    lines = ["🚫 **Заблокированные:**"]
    for u in banned:
        name = u["first_name"] or "—"
        username = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"• `{u['user_id']}` | {name} | {username}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@_admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: `/broadcast <текст>`", parse_mode="Markdown")
        return
    text = " ".join(context.args)
    users = get_all_users()
    sent = 0
    failed = 0
    for u in users:
        if u["is_blocked"]:
            continue
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"📣 Рассылка завершена.\n✅ Отправлено: `{sent}`\n❌ Ошибок: `{failed}`",
        parse_mode="Markdown",
    )


@_admin_only
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_file = None
    for f in os.listdir("."):
        if f.endswith(".log"):
            log_file = f
            break
    if log_file:
        try:
            with open(log_file, "rb") as f:
                data = f.read()[-4096:]
            await update.message.reply_document(
                document=io.BytesIO(data),
                filename="bot.log",
            )
        except Exception:
            await update.message.reply_text("❌ Не удалось прочитать лог.")
    else:
        await update.message.reply_text("ℹ️ Лог-файл не найден.")


@_admin_only
async def cmd_test_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from config import PREMIUM_DAILY_STARS, PREMIUM_MONTHLY_STARS
    user_id = update.effective_user.id
    text = (
        "🧪 **Тестовый платёж (Stars)**\n\n"
        "Нажми кнопку ниже — Telegram покажет диалог оплаты.\n"
        "Средства **не снимутся** (нужно подтвердить)."
    )
    keyboard = [
        [InlineKeyboardButton(f"⭐ {PREMIUM_DAILY_STARS} — 24ч (тест)", callback_data="buy_daily_1")],
        [InlineKeyboardButton(f"⭐ {PREMIUM_MONTHLY_STARS} — месяц (тест)", callback_data="buy_monthly_1")],
    ]
    from telegram import InlineKeyboardMarkup
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


def get_handlers():
    return [
        CommandHandler("stats", cmd_stats, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("users", cmd_users, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("sysinfo", cmd_sysinfo, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("limit", cmd_limit, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("clean", cmd_clean, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("block", cmd_block, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("unblock", cmd_unblock, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("banlist", cmd_banlist, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("broadcast", cmd_broadcast, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("logs", cmd_logs, filters=filters.User(user_id=ADMIN_ID)),
        CommandHandler("test_buy", cmd_test_buy, filters=filters.User(user_id=ADMIN_ID)),
    ]
