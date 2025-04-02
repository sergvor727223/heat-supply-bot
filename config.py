import os
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Если вы используете python-dotenv для локальной разработки:
# from dotenv import load_dotenv
# load_dotenv()  # Тогда переменные окружения можно хранить в .env

# Список обязательных переменных окружения:
REQUIRED_ENV_VARS = [
    "TELEGRAM_TOKEN",
    "OPENAI_API_KEY",
    "WEBHOOK_URL",
    "LOG_BOT_TOKEN",
    "LOG_CHAT_ID"
]

# Считываем переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip('/')
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")
PORT = os.getenv("PORT", "10000")

# Проверяем, не пропущены ли какие-то обязательные переменные
missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    logger.error(f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}")
    sys.exit(1)

# Формируем путь для вебхука (если нужно). 
# Если Telegram шлёт запросы на /bot<Токен>, используем тот же путь
WEBHOOK_PATH = "/webhook"

# Можно добавить любые дополнительные настройки
# Например, URL-адрес для поиска по ConsultantPlus, и т. д.
CONSULTANTPLUS_BASE_URL = "https://www.consultant.ru/search/"

# Логируем, что конфиг успешно загружен
logger.info("config.py загружен успешно!")
