import os
import logging

from telegram.ext import ApplicationBuilder

from config import BOT_TOKEN, DOWNLOAD_DIR
from database import init_db
from handlers import start, media, admin, premium

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    for handler in start.get_handlers():
        app.add_handler(handler)
    for handler in media.get_handlers():
        app.add_handler(handler)
    for handler in admin.get_handlers():
        app.add_handler(handler)
    for handler in premium.get_handlers():
        app.add_handler(handler)

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
