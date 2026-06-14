import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file")

ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
if not ADMIN_ID:
    raise ValueError("ADMIN_ID not found in .env file")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if os.getenv("AMVERA") == "1":
    DATA_DIR = "/data"
else:
    DATA_DIR = os.path.join(BASE_DIR, "data")

DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
DB_PATH = os.path.join(DATA_DIR, "bot.db")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

AUTO_DELETE_SECONDS = 150
MAX_FILE_SIZE = 50 * 1024 * 1024

# FREE limits
FREE_DAILY_FILES = 10
FREE_DAILY_MB = 150
FREE_MAX_HEIGHT = 720
FREE_MAX_WIDTH = 1280

# Premium prices (Telegram Stars)
PREMIUM_DAILY_STARS = 5
PREMIUM_MONTHLY_STARS = 25
