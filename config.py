import os
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Список обязательных переменных окружения:
REQUIRED_ENV_VARS = [
    "TELEGRAM_TOKEN",
    "OPENAI_API_KEY",
    "LOG_BOT_TOKEN",
    "LOG_CHAT_ID"
]

# Считываем переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")

# Проверка наличия переменных
missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    logger.error(f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}")
    sys.exit(1)

logger.info("config.py загружен успешно!")
