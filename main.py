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

DOCUMENT_ALIASES = {
    "жк рф": "Жилищный кодекс Российской Федерации",
    "жилищный кодекс": "Жилищный кодекс Российской Федерации",
    "жк": "Жилищный кодекс Российской Федерации",
    "пп 290": "Постановление Правительства РФ от 03.04.2013 № 290",
    "постановление 290": "Постановление Правительства РФ от 03.04.2013 № 290",
    "пп рф 290": "Постановление Правительства РФ от 03.04.2013 № 290",
    "290": "Постановление Правительства РФ от 03.04.2013 № 290",
    "постановление 354": "Постановление Правительства РФ от 06.05.2011 № 354",
    "пп 354": "Постановление Правительства РФ от 06.05.2011 № 354",
    "354": "Постановление Правительства РФ от 06.05.2011 № 354",
    "постановление 808": "Постановление Правительства РФ от 13.08.2006 № 808",
    "пп 808": "Постановление Правительства РФ от 13.08.2006 № 808",
    "808": "Постановление Правительства РФ от 13.08.2006 № 808",
    "о приборах учета": "Постановление Правительства РФ от 06.05.2011 № 354",
    "об оплате отопления": "Постановление Правительства РФ от 06.05.2011 № 354"
}

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


def normalize_query(query: str) -> str:
    q = query.lower()
    for alias, full_name in DOCUMENT_ALIASES.items():
        if alias in q:
            q = q.replace(alias, full_name)
    return q

def infer_document_name_from_context(text: str) -> str:
    if re.search(r'оплата.*отоплен', text, re.IGNORECASE):
        return "Постановление Правительства РФ от 06.05.2011 № 354"
    if re.search(r'прибор.*учета', text, re.IGNORECASE):
        return "Постановление Правительства РФ от 06.05.2011 № 354"
    if re.search(r'ответственность.*управляющей|жк', text, re.IGNORECASE):
        return "Жилищный кодекс Российской Федерации"
    return text

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
        "Привет! Я Алина, эксперт по теплоснабжению и юридическим вопросам в этой области. Задавай вопрос, и я постараюсь помочь — используя нормативные документы, технические правила и открытую информацию."
    )
    await message.answer(welcome_text)

    user_id = message.from_user.id
    user_context[user_id] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

@router.message(F.text)
async def handle_query(message: Message) -> None:
    user_id = message.from_user.id
    user_text = message.text.strip()
    normalized_text = normalize_query(user_text)
    inferred_text = infer_document_name_from_context(normalized_text)

    if user_id not in user_context:
        user_context[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    user_context[user_id].append({"role": "user", "content": inferred_text})

    if "судеб" in inferred_text and "практик" in inferred_text:
        async with ClientSession() as session:
            result = await search_google(inferred_text, session)
        if result:
            answer_text = (
                f"По вашему запросу о судебной практике:\n"
                f"Название: {result['title']}\n"
                f"Ссылка: {result['link']}\n"
                f"Описание: {result['excerpt']}"
            )
            user_context[user_id].append({"role": "assistant", "content": answer_text})
            await message.answer(answer_text)
            return

    found_doc = find_in_local_docs(inferred_text)
    if found_doc:
        doc_num, doc_title, snippet = found_doc
        answer_text = (
            f"Найден документ в локальной базе:\n"
            f"Документ: {doc_title} ({doc_num})\n"
            f"Выдержка: {snippet}"
        )
        user_context[user_id].append({"role": "assistant", "content": answer_text})
        await message.answer(answer_text)
        return

    async with ClientSession() as session:
        result = await search_google(inferred_text, session)

    if result:
        answer_text = (
            f"Найдена информация из открытых источников:\n"
            f"Название: {result['title']}\n"
            f"Ссылка: {result['link']}\n"
            f"Описание: {result['excerpt']}"
        )
        user_context[user_id].append({"role": "assistant", "content": answer_text})
        await message.answer(answer_text)
    else:
        user_context[user_id].append({"role": "assistant", "content": "В интернете не найдено точных результатов. Сейчас уточню у ИИ."})
        final_answer = await call_openai_chat(user_context[user_id])
        user_context[user_id].append({"role": "assistant", "content": final_answer})
        await message.answer(final_answer)

def main() -> None:
    load_documents_from_docs_folder("docs")
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    web.run_app(app, host="0.0.0.0", port=int(PORT))

if __name__ == "__main__":
    main()
