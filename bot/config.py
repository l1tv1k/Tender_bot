import os
from dotenv import load_dotenv

load_dotenv()


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def optional_env_int(name: str) -> int:
    try:
        value = int(os.getenv(name, "0"))
        return value if value > 0 else 0
    except ValueError:
        return 0


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", 0))
TELEGRAM_POLLING_TIMEOUT = env_int("TELEGRAM_POLLING_TIMEOUT", 2)
TELEGRAM_API_TIMEOUT = env_int("TELEGRAM_API_TIMEOUT", 8)
TELEGRAM_DOCUMENT_TIMEOUT = env_int("TELEGRAM_DOCUMENT_TIMEOUT", 90)
TELEGRAM_DOCUMENT_TRANSPORT = os.getenv("TELEGRAM_DOCUMENT_TRANSPORT", "mtproto").casefold()
TELEGRAM_API_ID = optional_env_int("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
MTPROTO_SESSION_PATH = os.getenv("MTPROTO_SESSION_PATH", "/data/mtproto/tender_bot")
MTPROTO_CONNECT_TIMEOUT = env_int("MTPROTO_CONNECT_TIMEOUT", 20)
MTPROTO_USE_OBFUSCATED = env_bool("MTPROTO_USE_OBFUSCATED", True)
TELEGRAM_EDIT_RETRY_ATTEMPTS = env_int("TELEGRAM_EDIT_RETRY_ATTEMPTS", 3)
TELEGRAM_RETRY_BASE_DELAY = env_int("TELEGRAM_RETRY_BASE_DELAY", 1)
DB_POOL_MIN_SIZE = env_int("DB_POOL_MIN_SIZE", 1)
DB_POOL_MAX_SIZE = max(DB_POOL_MIN_SIZE, env_int("DB_POOL_MAX_SIZE", 5))
DB_CONFIG = {
    "user": os.getenv("DB_USER", "tender_user"),
    "password": os.getenv("DB_PASS", "tender_password"),
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "tender_bot_db")
}

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
