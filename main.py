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
    "290": "ПП_290_минимум_услуг.docx",
    "пп 354": "ПП_354_комуслуги.docx",
    "354": "ПП_354_комуслуги.docx",
    "пп 808": "ПП_808_теплоснабжение.docx",
    "808": "ПП_808_теплоснабжение.docx"
}

DOCS_DB = {}

def load_documents_from_docs_folder(folder_path="docs"):
    for filename in os.listdir(folder_path):
        if filename.endswith(".docx"):
            with open(os.path.join(folder_path, filename), "rb") as f:
                doc = Document(f)
                text = "\n".join([p.text for p in doc.paragraphs])
                DOCS_DB[filename] = text

load_documents_from_docs_folder()

@router.message(CommandStart())
async def start_handler(msg: Message):
    user_context[msg.from_user.id] = {"document": None}
    await msg.answer("Привет! Я Алина, эксперт по теплоснабжению и юридическим вопросам. Задавай вопрос — я постараюсь найти точный ответ в нормативных документах.")

@router.message()
async def query_handler(msg: Message):
    user_id = msg.from_user.id
    user_data = user_context.get(user_id, {"document": None})
    text = msg.text.strip().lower()

    # Подтверждение документа
    if user_data.get("awaiting_confirmation"):
        if text in ["да", "да."]:
            user_data["document"] = user_data["pending_doc"]
            user_data.pop("awaiting_confirmation")
            user_data.pop("pending_doc")
            user_context[user_id] = user_data
            docname = user_data["document"]
            snippet = DOCS_DB[docname][:1000]
            await msg.answer(f"Вот выдержка из документа «{docname}»:\n\n{snippet}\n\nТеперь можете задать вопрос по этому документу.")
        else:
            await msg.answer("Хорошо, уточните название документа.")
        return

    # Если документ уже выбран — ищем по нему
    if user_data.get("document"):
        docname = user_data["document"]
        doc_text = DOCS_DB.get(docname, "")
        matches = [p for p in doc_text.split("\n") if text in p.lower()]
        if matches:
            await msg.answer("Вот что удалось найти:\n\n" + "\n\n".join(matches[:5]))
        else:
            await msg.answer("К сожалению, в этом документе ничего не нашлось. Уточните вопрос.")
        return

    # Ищем совпадение по псевдонимам или номеру
    for alias, doc_file in DOCUMENT_ALIASES.items():
        if alias in text:
            if doc_file in DOCS_DB:
                user_context[user_id] = {
                    "awaiting_confirmation": True,
                    "pending_doc": doc_file
                }
                await msg.answer(f"Вы имели в виду документ: «{doc_file}»? (да/нет)")
                return

    await msg.answer("Не удалось найти документ в локальной базе. Можете дать более точное название?")

async def main():
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=PORT)
    await site.start()
    logger.info(f"Your service is live ✨\nRunning on http://0.0.0.0:{PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
