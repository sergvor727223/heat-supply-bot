import logging
import asyncio
from datetime import datetime
import os

import openai
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web, ClientSession
from bs4 import BeautifulSoup
import re
from docx import Document

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

user_context = {}
user_pending_confirmation = {}
user_last_document = {}  # Документ, по которому был предыдущий вопрос

DOCS_DB = {}


def load_documents_from_docs_folder(folder_path="docs"):
    for filename in os.listdir(folder_path):
        if filename.endswith(".docx"):
            try:
                doc = Document(os.path.join(folder_path, filename))
                full_text = "\n".join([p.text for p in doc.paragraphs])
                DOCS_DB[filename] = {
                    "title": filename,
                    "text": full_text
                }
                logger.info(f"Загружен документ: {filename}")
            except Exception as e:
                logger.warning(f"Ошибка при чтении {filename}: {e}")


def search_doc_by_number_or_name(query: str):
    query = query.lower()
    for filename, data in DOCS_DB.items():
        title_lower = data['title'].lower()
        if query in title_lower:
            return filename, data['title'], data['text'][:300] + "..."
        match = re.search(r'\d{3,4}', query)
        if match and match.group() in title_lower:
            return filename, data['title'], data['text'][:300] + "..."
    return None


def search_answer_in_documents(query: str):
    query = query.lower()
    for filename, data in DOCS_DB.items():
        if query in data['text'].lower():
            snippet_start = data['text'].lower().find(query)
            snippet = data['text'][snippet_start:snippet_start + 500] + "..."
            return data['title'], snippet
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


@router.message(CommandStart())
async def command_start(message: Message) -> None:
    welcome_text = (
        "Привет! Я Алина, эксперт по теплоснабжению и юридическим вопросам в этой области. Задавай вопрос, и я постараюсь помочь — используя нормативные документы, технические правила и открытую информацию."
    )
    await message.answer(welcome_text)
    user_id = message.from_user.id
    user_context[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]


@router.message(F.text)
async def handle_query(message: Message) -> None:
    user_id = message.from_user.id
    user_text = message.text.strip().lower()

    # Обработка подтверждения
    if user_id in user_pending_confirmation:
        confirmed_title = user_pending_confirmation[user_id]
        if "да" in user_text:
            matched_doc = DOCS_DB.get(confirmed_title)
            if matched_doc:
                user_last_document[user_id] = confirmed_title
                snippet = matched_doc['text'][:500] + "..."
                await message.answer(f"Вот выдержка из документа «{matched_doc['title']}»:\n{snippet}")
            else:
                await message.answer("Документ больше не найден. Пожалуйста, уточните название.")
            del user_pending_confirmation[user_id]
            return
        elif "нет" in user_text:
            await message.answer("Попробуйте уточнить название документа или его номер.")
            del user_pending_confirmation[user_id]
            return

    # Поиск документа по названию или номеру
    local_result = search_doc_by_number_or_name(user_text)
    if local_result:
        doc_filename, doc_title, snippet = local_result
        user_pending_confirmation[user_id] = doc_filename
        await message.answer(f"Вы имели в виду документ: «{doc_title}»? (да/нет)")
        return

    # Попытка найти ответ по содержанию документов
    answer_result = search_answer_in_documents(user_text)
    if answer_result:
        doc_title, snippet = answer_result
        await message.answer(f"Вот, что удалось найти в документе «{doc_title}»:\n{snippet}")
        return

    # Просим уточнить запрос
    await message.answer("Не удалось найти документ в локальной базе. Можете дать более точное название?")

    # Поиск в интернете
    async with ClientSession() as session:
        result = await search_google(user_text, session)

    if result:
        await message.answer(
            f"Вот, что удалось найти в открытых источниках:\nНазвание: {result['title']}\nСсылка: {result['link']}\nОписание: {result['excerpt']}"
        )
    else:
        await message.answer("Ничего не удалось найти в интернете. Пожалуйста, уточните запрос.")


def main() -> None:
    load_documents_from_docs_folder("docs")
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    web.run_app(app, host="0.0.0.0", port=int(PORT))


if __name__ == "__main__":
    main()
