import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = str(os.getenv("TELEGRAM_BOT_TOKEN"))
PROXY_URL = str(os.getenv("TELEGRAM_PROXY_URL"))
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", 0))
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

DB_CONFIG = {
    "user": os.getenv("DB_USER", "tender_user"),
    "password": os.getenv("DB_PASS", "tender_password"),
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "tender_bot_db")
}

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")