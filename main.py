import logging
import asyncio
from datetime import datetime

import openai
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web, ClientSession
from bs4 import BeautifulSoup

# Импортируем настройки из config.py
from config import (
    TELEGRAM_TOKEN,
    OPENAI_API_KEY,
    WEBHOOK_URL,
    LOG_BOT_TOKEN,
    LOG_CHAT_ID,
    WEBHOOK_PATH,
    PORT
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Указываем ключ для OpenAI
openai.api_key = OPENAI_API_KEY

# -----------------------------------------------------------------------------
# 1. Инициализация бота и диспетчера
# -----------------------------------------------------------------------------
bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# -----------------------------------------------------------------------------
# 2. Пример БАЗЫ ЗНАНИЙ (локальный словарь DOCS_DB)
# -----------------------------------------------------------------------------
DOCS_DB = {
    "ГОСТ 12.0.004-2015": {
        "title": "ГОСТ 12.0.004-2015 Организация обучения безопасности труда",
        "text": (
            "Этот стандарт устанавливает основные требования к обучению охране труда "
            "для работников различных отраслей... (примерное содержание)..."
        )
    },
    "Приказ Минтруда №59н": {
        "title": "Приказ Министерства труда и соцзащиты №59н",
        "text": (
            "В данном приказе регламентируются методики проверки знаний сотрудников "
            "по безопасности и охране труда..."
        )
    },
    # Добавляйте другие документы при необходимости
}

# -----------------------------------------------------------------------------
# 3. Вспомогательные функции
# -----------------------------------------------------------------------------

async def send_log_to_telegram(user_info: str, user_message: str, bot_response: str) -> None:
    """
    Отправка лога в отдельный бот (LogBot).
    """
    from aiogram import Bot  # Локальный импорт, чтобы не пересекался с основным bot
    log_message = (
        f"👤 Пользователь: {user_info}\n"
        f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"📥 Запрос:\n{user_message}\n\n"
        f"📤 Ответ:\n{bot_response}"
    )
    log_bot = Bot(token=LOG_BOT_TOKEN)
    try:
        await log_bot.send_message(LOG_CHAT_ID, log_message)
        logger.info(f"Лог отправлен: {user_info}")
    except Exception as e:
        logger.error(f"Ошибка при отправке лога: {e}")
    finally:
        await log_bot.session.close()


def find_in_local_docs(query: str):
    """
    Простой поиск по локальным документам. Возвращает (doc_number, title, snippet) или None.
    """
    query_lower = query.lower()
    for doc_number, doc_data in DOCS_DB.items():
        full_text_lower = doc_data["text"].lower()
        title_lower = doc_data["title"].lower()
        # Примитивная логика: если query входит в текст или в title
        if query_lower in full_text_lower or query_lower in title_lower:
            snippet = doc_data["text"][:300] + "..."
            return (doc_number, doc_data["title"], snippet)
    return None


async def search_consultantplus(query: str, session: ClientSession):
    """
    Пример прямого парсинга сайта consultant.ru/search/?query=...
    Может потребовать авторизацию или нарушать TOS.
    """
    base_url = "https://www.consultant.ru/search/"
    params = {"query": query}
    try:
        async with session.get(base_url, params=params) as resp:
            if resp.status != 200:
                logger.warning(f"ConsultantPlus вернул статус {resp.status}")
                return None

            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")

            # Ищем результаты: <div class="search-card"> ...
            results = soup.find_all("div", class_="search-card")
            if not results:
                return None
            
            first_result = results[0]
            title_el = first_result.find("a", class_="search-card__link")
            excerpt_el = first_result.find("div", class_="search-card__anons")

            if not title_el or not excerpt_el:
                return None

            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            excerpt = excerpt_el.get_text(strip=True)

            # Если ссылка относительная, дополняем домен
            if link.startswith("/"):
                link = "https://www.consultant.ru" + link

            return {
                "title": title,
                "link": link,
                "excerpt": excerpt
            }
    except Exception as e:
        logger.error(f"Ошибка при поиске на consultantplus: {e}")
        return None


async def search_google_for_ot(query: str, session: ClientSession):
    """
    Условная функция поиска в Google (site:consultant.ru).
    Для реального проекта нужно использовать Google Custom Search API
    или иное разрешённое решение. Здесь - пример парсинга (может не работать).
    """
    google_url = "https://www.google.com/search"
    params = {
        "q": f"{query} site:consultant.ru",
        "hl": "ru"
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        )
    }
    try:
        async with session.get(google_url, params=params, headers=headers) as resp:
            if resp.status != 200:
                logger.warning(f"Google вернул статус {resp.status}")
                return None

            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            divs = soup.select("div.tF2Cxc")
            if not divs:
                return None
            first = divs[0]
            link_tag = first.select_one("a")
            snippet_tag = first.select_one(".VwiC3b")
            if not link_tag or not snippet_tag:
                return None
            title = link_tag.get_text(strip=True)
            link = link_tag.get("href", "")
            excerpt = snippet_tag.get_text(strip=True)
            return {
                "title": title,
                "link": link,
                "excerpt": excerpt
            }
    except Exception as e:
        logger.error(f"Ошибка при поиске в Google: {e}")
        return None

# -----------------------------------------------------------------------------
# 4. Хендлеры
# -----------------------------------------------------------------------------

@router.message(CommandStart())
async def command_start(message: Message) -> None:
    """
    /start
    """
    welcome_text = (
        "Привет! Я бот-консультант по охране труда. "
        "Задайте свой вопрос, и я поищу ответ в своей локальной базе. "
        "Если не найду, обращусь к КонсультантПлюс. "
        "Всегда указываю номер и название документа или ссылку на источник!"
    )
    await message.answer(welcome_text)
    user_info = (f"{message.from_user.full_name} (@{message.from_user.username})"
                 if message.from_user.username else message.from_user.full_name)
    await send_log_to_telegram(user_info, "/start", welcome_text)

@router.message(F.text)
async def handle_query(message: Message) -> None:
    """
    Основной обработчик: ищем в базе -> если не нашли, идём на consultant.ru
    """
    user_text = message.text.strip()
    user_info = (f"{message.from_user.full_name} (@{message.from_user.username})"
                 if message.from_user.username else message.from_user.full_name)

    # 1) поиск по локальной базе
    found_doc = find_in_local_docs(user_text)
    if found_doc:
        doc_num, doc_title, snippet = found_doc
        response_text = (
            f"Нашёл ответ в локальной базе:\n\n"
            f"Документ: {doc_title} ({doc_num})\n\n"
            f"{snippet}"
        )
        await message.answer(response_text)
        await send_log_to_telegram(user_info, user_text, response_text)
        return

    # 2) если не нашли -> сообщаем и идём на КонсультантПлюс
    no_local_text = (
        "В моей локальной базе знаний ответа нет. Сейчас попробую найти информацию "
        "на сайте КонсультантПлюс."
    )
    await message.answer(no_local_text)
    await send_log_to_telegram(user_info, user_text, no_local_text)

    # 2.1) спрашиваем, нужна ли судебная практика:
    question_text = (
        "Нужна ли по вашему запросу судебная практика? "
        "Напишите 'да, судебная практика' или 'нет, достаточно'."
    )
    await message.answer(question_text)


@router.message(F.text.lower().contains("да, судебная практика"))
async def handle_judicial_yes(message: Message):
    """
    Пользователь хочет судебную практику, ищем напрямую через consultantplus
    """
    user_text = message.text.strip()
    user_info = (f"{message.from_user.full_name} (@{message.from_user.username})"
                 if message.from_user.username else message.from_user.full_name)

    note_text = "Ищу судебную практику по охране труда на consultant.ru ..."
    await message.answer(note_text)
    await send_log_to_telegram(user_info, user_text, note_text)

    try:
        async with ClientSession() as session:
            result = await search_consultantplus("судебная практика по охране труда", session)

        if not result:
            not_found_text = (
                "К сожалению, не удалось получить результаты с consultant.ru "
                "(возможно, нужна авторизация или сайт недоступен)."
            )
            await message.answer(not_found_text)
            await send_log_to_telegram(user_info, user_text, not_found_text)
            return

        response_text = (
            f"Вот что удалось найти:\n\n"
            f"Название: {result['title']}\n"
            f"Ссылка: {result['link']}\n\n"
            f"{result['excerpt']}"
        )
        await message.answer(response_text)
        await send_log_to_telegram(user_info, user_text, response_text)

    except Exception as e:
        error_message = f"Произошла ошибка при поиске судебной практики: {e}"
        await message.answer(error_message)
        logger.error(error_message)
        await send_log_to_telegram(user_info, user_text, error_message)


@router.message(F.text.lower().contains("нет, достаточно"))
async def handle_judicial_no(message: Message):
    """
    Пользователь не хочет судебную практику, используем Google (site:consultant.ru)
    """
    user_text = message.text.strip()
    user_info = (f"{message.from_user.full_name} (@{message.from_user.username})"
                 if message.from_user.username else message.from_user.full_name)

    note_text = "Окей, без судебной практики. Ищу в ConsultantPlus через Google..."
    await message.answer(note_text)
    await send_log_to_telegram(user_info, user_text, note_text)

    try:
        async with ClientSession() as session:
            # site:consultant.ru
            result = await search_google_for_ot("охрана труда", session)

        if not result:
            not_found_text = (
                "Не удалось найти результаты по вашему запросу. "
                "Вероятно, Google не дал ответ или блокирует парсинг."
            )
            await message.answer(not_found_text)
            await send_log_to_telegram(user_info, user_text, not_found_text)
            return

        response_text = (
            f"Вот что удалось найти:\n\n"
            f"Название: {result['title']}\n"
            f"Ссылка: {result['link']}\n\n"
            f"{result['excerpt']}"
        )
        await message.answer(response_text)
        await send_log_to_telegram(user_info, user_text, response_text)

    except Exception as e:
        error_message = f"Ошибка при поиске в интернете: {e}"
        await message.answer(error_message)
        logger.error(error_message)
        await send_log_to_telegram(user_info, user_text, error_message)


# -----------------------------------------------------------------------------
# 5. Жизненный цикл приложения (webhook, запуск)
# -----------------------------------------------------------------------------

async def on_startup(bot: Bot) -> None:
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        logger.info(f"Устанавливаю вебхук: {webhook_url}")
        await bot.set_webhook(webhook_url)
        
        # Отправляем лог о старте
        from aiogram import Bot
        log_bot = Bot(token=LOG_BOT_TOKEN)
        try:
            await log_bot.send_message(
                LOG_CHAT_ID,
                f"🚀 Бот по охране труда запущен (с реальным поиском)\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления при старте: {e}")
        finally:
            await log_bot.session.close()

async def on_shutdown(bot: Bot) -> None:
    logger.info("Бот остановлен")
    from aiogram import Bot
    log_bot = Bot(token=LOG_BOT_TOKEN)
    try:
        await log_bot.send_message(
            LOG_CHAT_ID,
            f"🔴 Бот по охране труда остановлен\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления при остановке: {e}")
    finally:
        await log_bot.session.close()

    await bot.session.close()

def main() -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get("/", lambda request: web.Response(text="OK"))

    app.on_startup.append(lambda app: on_startup(bot))
    app.on_shutdown.append(lambda app: on_shutdown(bot))

    port = int(PORT)
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
