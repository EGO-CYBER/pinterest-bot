import os
import logging
from datetime import datetime

from telegram import Update, LabeledPrice
from telegram.ext import (
    CommandHandler,
    PreCheckoutQueryHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import PREMIUM_DAILY_STARS, PREMIUM_MONTHLY_STARS, ASSETS_DIR
from database import is_premium, set_premium, get_premium_info, upsert_user

logger = logging.getLogger(__name__)

CURRENCY = "XTR"


async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user.id, user.username or "", user.first_name)
    prem = is_premium(user.id)

    # Try sending banner image if exists
    banner = os.path.join(ASSETS_DIR, "premium_banner.jpg")
    if os.path.isfile(banner):
        with open(banner, "rb") as f:
            await update.message.reply_photo(photo=f)

    if prem:
        info = get_premium_info(user.id)
        expires = ""
        if info and info.get("expires_at"):
            try:
                dt = datetime.fromisoformat(info["expires_at"])
                expires = f"до {dt.strftime('%d.%m.%Y %H:%M')}"
            except Exception:
                expires = ""
        text = (
            "💎 **У тебя Premium!**\n\n"
            f"• Безлимит файлов и МБ\n"
            f"• Максимальное качество\n"
            f"• Без очереди\n"
        )
        if expires:
            text += f"• Действует {expires}"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    text = (
        "💎 **Premium-возможности:**\n\n"
        "┌─────────────────────────────┐\n"
        "│ 🆓 **Free**          💎 **Premium** │\n"
        "├─────────────────────────────┤\n"
        "│ 10 файлов/день       ♾ безлим  │\n"
        "│ 150 MB/день          ♾ безлим  │\n"
        "│ до 720p              максимум  │\n"
        "│ есть очередь         без очереди│\n"
        "└─────────────────────────────┘\n\n"
        "⭐ **5** — безлимит на 24 часа\n"
        "⭐ **25** — Premium на месяц\n\n"
        "Нажми кнопку ниже, чтобы купить 👇"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
    await _show_buy_buttons(update, context)


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_buy_buttons(update, context)


async def _show_buy_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [
            InlineKeyboardButton(
                f"⭐ {PREMIUM_DAILY_STARS} — безлимит 24ч",
                callback_data=f"buy_daily_{PREMIUM_DAILY_STARS}",
            ),
        ],
        [
            InlineKeyboardButton(
                f"⭐ {PREMIUM_MONTHLY_STARS} — месяц Premium",
                callback_data=f"buy_monthly_{PREMIUM_MONTHLY_STARS}",
            ),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "💎 Выбери тариф:",
        reply_markup=reply_markup,
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("buy_"):
        return

    parts = data.split("_")
    plan = parts[1]  # daily / monthly
    stars = int(parts[2])

    user_id = update.effective_user.id
    prices = [LabeledPrice(label="Premium", amount=stars)]

    if plan == "daily":
        title = "Безлимит на 24 часа"
        desc = "Все возможности Premium на 24 часа"
    else:
        title = "Premium на месяц"
        desc = "Все возможности Premium на 30 дней"

    # sendInvoice params
    payload = f"{user_id}_{plan}_{stars}"

    await context.bot.send_invoice(
        chat_id=user_id,
        title=title,
        description=desc,
        payload=payload,
        provider_token="",  # empty = Telegram Stars
        currency=CURRENCY,
        prices=prices,
        need_email=False,
        need_name=False,
        need_phone_number=False,
        is_flexible=False,
    )


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload
    parts = payload.split("_")
    if len(parts) != 3:
        await query.answer(ok=False, error_message="Ошибка платежа")
        return
    user_id_str, plan, stars_str = parts
    user_id = int(user_id_str)
    stars = int(stars_str)

    if query.from_user.id != user_id:
        await query.answer(ok=False, error_message="Не твой платёж")
        return

    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    payment = message.successful_payment
    payload = payment.invoice_payload
    charge_id = payment.telegram_payment_charge_id
    stars = payment.total_amount

    parts = payload.split("_")
    plan = parts[1]

    if plan == "daily":
        days = 1
        plan_type = "daily"
        text = (
            "✅ **Premium активирован на 24 часа!**\n"
            "Теперь всё без ограничений. 🎉"
        )
    else:
        days = 30
        plan_type = "monthly"
        text = (
            "✅ **Premium активирован на месяц!**\n"
            "Спасибо за поддержку! 💎"
        )

    set_premium(user.id, plan_type, days, stars, charge_id)
    await message.reply_text(text, parse_mode="Markdown")


def get_handlers():
    return [
        CommandHandler("premium", cmd_premium),
        CommandHandler("buy", cmd_buy),
        PreCheckoutQueryHandler(pre_checkout),
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment),
        CallbackQueryHandler(button_callback, pattern=r"^buy_"),
    ]
