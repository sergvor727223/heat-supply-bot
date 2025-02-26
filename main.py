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

from config import (
    TELEGRAM_TOKEN,
    OPENAI_API_KEY,
    WEBHOOK_URL,
    LOG_BOT_TOKEN,
    LOG_CHAT_ID,
    WEBHOOK_PATH,
    PORT
)

from system_prompt import SYSTEM_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

openai.api_key = OPENAI_API_KEY

bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ----- ПАМЯТЬ ДИАЛОГА (В ОПЕРАТИВКЕ) -----
# user_context[user_id] = [
#    {"role": "assistant"/"user"/"system", "content": "..."},
#    ...
# ]
user_context = {}

# ----- Пример локальной базы (тест) -----
DOCS_DB = {
    "ГОСТ 12.0.004-2015": {
        "title": "ГОСТ 12.0.004-2015 Организация обучения безопасности труда",
        "text": (
            "Этот стандарт устанавливает основные требования к обучению охране труда "
            "для работников различных отраслей. Здесь описаны методы обучения и требования к квалификации инструкторов."
        )
    },
    "Приказ Минтруда №59н": {
        "title": "Приказ Министерства труда и соцзащиты №59н",
        "text": (
            "В данном приказе регламентируются методики проверки знаний сотрудников по безопасности и охране труда. "
            "Описаны процедуры проведения инструктажей и обучения новых работников."
        )
    },
}

async def send_log_to_telegram(user_info: str, user_message: str, bot_response: str) -> None:
    from aiogram import Bot
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
    query_lower = query.lower()
    for doc_number, doc_data in DOCS_DB.items():
        if (query_lower in doc_data["text"].lower()) or (query_lower in doc_data["title"].lower()):
            snippet = doc_data["text"][:300] + "..."
            return (doc_number, doc_data["title"], snippet)
    return None

async def search_google(query: str, session: ClientSession):
    google_url = "https://www.google.com/search"
    params = {"q": query, "hl": "ru", "num": "5"}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
    }
    try:
        async with session.get(google_url, params=params, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                logger.warning(f"Google вернул статус {resp.status}")
                return None
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            
            # Ищем первый результат
            divs = soup.select("div.tF2Cxc, div.g")
            if not divs:
                return None
            first = divs[0]
            link_tag = first.select_one("a")
            snippet_tag = first.select_one(".VwiC3b") or first.select_one(".st")
            if not link_tag:
                return None
            title = link_tag.get_text(strip=True)
            link = link_tag.get("href", "")
            excerpt = snippet_tag.get_text(strip=True) if snippet_tag else "Описание отсутствует"
            return {"title": title, "link": link, "excerpt": excerpt}
    except Exception as e:
        logger.error(f"Ошибка при поиске в Google: {e}")
        return None

async def call_openai_chat(context_messages):
    """
    Вызывает OpenAI ChatCompletion с system_prompt + контекстом (context_messages).
    context_messages - это список словарей вида:
      [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": ...}, ...]
    """
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=context_messages,
            max_tokens=1000
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка при обращении к OpenAI: {e}")
        return "Извините, произошла ошибка при генерации ответа."

@router.message(CommandStart())
async def command_start(message: Message) -> None:
    welcome_text = (
        "Привет! Я Алина, консультант по охране труда. Задавай вопросы, и я постараюсь найти ответ "
        "в своей локальной базе или в интернете, обязательно укажу источник. "
        "Я не выдумываю информацию, а если чего-то нет, сообщу вам об этом."
    )
    await message.answer(welcome_text)

    user_id = message.from_user.id
    user_context[user_id] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]  # Начинаем контекст с system_prompt

    user_info = f"{message.from_user.full_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.full_name
    await send_log_to_telegram(user_info, "/start", welcome_text)

@router.message(F.text)
async def handle_query(message: Message) -> None:
    user_id = message.from_user.id
    user_text = message.text.strip()
    user_info = (f"{message.from_user.full_name} (@{message.from_user.username})"
                 if message.from_user.username else message.from_user.full_name)

    # Если контекста нет (бот перезагрузился), создаём заново
    if user_id not in user_context:
        user_context[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Сохраняем сообщение пользователя в контексте
    user_context[user_id].append({"role": "user", "content": user_text})

    # 1) Проверяем, не "судеб" + "практик" ли запрос
    if "судеб" in user_text.lower() and "практик" in user_text.lower():
        # Ищем в интернете (Google) по самой фразе пользователя
        async with ClientSession() as session:
            result = await search_google(user_text, session)
        if result:
            # Формируем ответ
            answer_text = (
                f"По вашему запросу о судебной практике:\n"
                f"Название: {result['title']}\n"
                f"Ссылка: {result['link']}\n"
                f"Описание: {result['excerpt']}"
            )
            # Добавим это как assistant в контекст
            user_context[user_id].append({"role": "assistant", "content": answer_text})
            await message.answer(answer_text)
            await send_log_to_telegram(user_info, user_text, answer_text)
            return
        else:
            # Ничего не найдено, попросим OpenAI помочь
            user_context[user_id].append({"role": "assistant", "content": "Поиск в интернете не дал результатов. Сейчас попробую обобщить информацию."})
            final_answer = await call_openai_chat(user_context[user_id])
            user_context[user_id].append({"role": "assistant", "content": final_answer})
            await message.answer(final_answer)
            await send_log_to_telegram(user_info, user_text, final_answer)
            return

    # 2) Поиск в локальной базе
    found_doc = find_in_local_docs(user_text)
    if found_doc:
        doc_num, doc_title, snippet = found_doc
        answer_text = (
            f"Найден документ в локальной базе:\n"
            f"Документ: {doc_title} ({doc_num})\n"
            f"Выдержка: {snippet}"
        )
        # Добавляем как assistant
        user_context[user_id].append({"role": "assistant", "content": answer_text})
        await message.answer(answer_text)
        await send_log_to_telegram(user_info, user_text, answer_text)
        return

    # 3) Если нет в базе, ищем в интернете (Google)
    async with ClientSession() as session:
        result = await search_google(user_text, session)

    if result:
        answer_text = (
            f"Найдена информация из интернета:\n"
            f"Название: {result['title']}\n"
            f"Ссылка: {result['link']}\n"
            f"Описание: {result['excerpt']}"
        )
        user_context[user_id].append({"role": "assistant", "content": answer_text})
        await message.answer(answer_text)
        await send_log_to_telegram(user_info, user_text, answer_text)
    else:
        # 4) Если даже в интернете нет, попробуем задать вопрос OpenAI на основе контекста
        user_context[user_id].append({"role": "assistant", "content": "В интернете не найдено точных результатов. Сейчас уточню у ИИ."})
        final_answer = await call_openai_chat(user_context[user_id])
        user_context[user_id].append({"role": "assistant", "content": final_answer})
        await message.answer(final_answer)
        await send_log_to_telegram(user_info, user_text, final_answer)

# -----------------------------------------------------------------------------
# Запуск (webhook, etc.)
# -----------------------------------------------------------------------------
async def on_startup(bot: Bot) -> None:
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        logger.info(f"Устанавливаю вебхук: {webhook_url}")
        await bot.set_webhook(webhook_url)

        from aiogram import Bot
        log_bot = Bot(token=LOG_BOT_TOKEN)
        try:
            await log_bot.send_message(
                LOG_CHAT_ID,
                f"🚀 Бот запущен\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
            f"🔴 Бот остановлен\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления при остановке: {e}")
    finally:
        await log_bot.session.close()
    await bot.session.close()

def main() -> None:
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    app.on_startup.append(lambda app: on_startup(bot))
    app.on_shutdown.append(lambda app: on_shutdown(bot))
    web.run_app(app, host="0.0.0.0", port=int(PORT))

if __name__ == "__main__":
    main()
