import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config import ADMIN_ID
from database import upsert_user, get_stats
from handlers.premium import cmd_premium

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user.id, user.username or "", user.first_name)

    is_admin = user.id == ADMIN_ID
    text = (
        "👋 **Привет!** Я умею скачивать контент с Pinterest и TikTok.\n\n"
        "📌 Просто отправь ссылку — и я пришлю файл **без сжатия**.\n\n"
        "Поддерживаемые ссылки:\n"
        "• `pinterest.com/pin/...` или `pin.it/...`\n"
        "• `tiktok.com/...` или `vm.tiktok.com/...`"
    )
    if is_admin:
        text += "\n\n👑 Ты админ. `/help` — все команды."

    keyboard = [
        [
            InlineKeyboardButton("💎 Premium", callback_data="btn_premium"),
            InlineKeyboardButton("❓ Помощь", callback_data="btn_help"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    is_admin = user.id == ADMIN_ID

    if is_admin:
        text = (
            "👑 **Админ-команды:**\n\n"
            "`/limit <МБ>` — дневной лимит скачиваний на юзера\n"
            "`/stats` — общая статистика бота\n"
            "`/users` — CSV всех пользователей\n"
            "`/sysinfo` — нагрузка сервера (RAM/CPU/диск)\n"
            "`/clean` — срочно удалить все локальные файлы\n"
            "`/block <id>` — заблокировать пользователя\n"
            "`/unblock <id>` — разблокировать\n"
            "`/banlist` — список заблокированных\n"
            "`/broadcast <текст>` — рассылка всем\n"
            "`/logs` — последние строки лога\n\n"
            "📌 Просто отправь ссылку — скачаю медиа."
        )
    else:
        text = (
            "📌 Просто отправь ссылку на Pinterest или TikTok —\n"
            "я скачаю контент в лучшем качестве и пришлю\n"
            "файлом без сжатия Telegram.\n\n"
            "Примеры:\n"
            "• `https://pin.it/xxxxx`\n"
            "• `https://www.tiktok.com/@user/video/xxxx`\n\n"
            "💎 **Premium** — безлимит, макс.качество, без очереди: /buy"
        )

    keyboard = [
        [
            InlineKeyboardButton("💎 Premium", callback_data="btn_premium"),
            InlineKeyboardButton("🏠 Главная", callback_data="btn_start"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "btn_premium":
        await cmd_premium(update, context)
    elif data == "btn_help":
        await cmd_help(update, context)
    elif data == "btn_start":
        await cmd_start(update, context)


def get_handlers():
    return [
        CommandHandler("start", cmd_start),
        CommandHandler("help", cmd_help),
        CallbackQueryHandler(button_callback, pattern=r"^btn_"),
    ]
