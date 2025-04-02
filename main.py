
import os
import logging
import openai
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.utils.executor import start_webhook
from config import TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL
import difflib
from docx import Document

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)
openai.api_key = OPENAI_API_KEY

# Загрузка документов
DOCS_DIR = "docs"
documents = {}
for filename in os.listdir(DOCS_DIR):
    if filename.endswith(".docx"):
        filepath = os.path.join(DOCS_DIR, filename)
        try:
            doc = Document(filepath)
            full_text = "\n".join([para.text for para in doc.paragraphs])
            documents[filename] = full_text
            logging.info(f"Загружен документ: {filename}")
        except Exception as e:
            logging.error(f"Ошибка при загрузке {filename}: {e}")

# Контексты пользователей
user_context = {}

# Хелпер: найти наиболее подходящий документ по запросу
def find_best_match(query):
    names = list(documents.keys())
    matches = difflib.get_close_matches(query, names, n=1, cutoff=0.3)
    return matches[0] if matches else None

# Хелпер: вызвать ChatGPT
def ask_openai(prompt):
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": open("system_prompt.py", encoding="utf-8").read()
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    return response["choices"][0]["message"]["content"]

# Обработка команды /start
@dp.message_handler(commands=["start"])
async def cmd_start(message: Message):
    user_context[message.from_user.id] = {}
    await message.answer(
        "Привет! Я Сергей, эксперт по теплоснабжению и юридическим вопросам. Задавай вопрос — я постараюсь найти точный ответ в нормативных документах."
    )

# Обработка сообщений
@dp.message_handler()
async def handle_message(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    context = user_context.setdefault(user_id, {})

    # Если ожидаем подтверждение выбора документа
    if context.get("awaiting_confirmation") and context.get("suggested_doc"):
        if text.lower() in ["да", "да.", "подтверждаю"]:
            context["selected_document"] = context["suggested_doc"]
            context["awaiting_confirmation"] = False
            doc_text = documents[context["selected_document"]][:2000]
            await message.answer(f"Вот выдержка из документа «{context['selected_document']}»:\n\n{doc_text}")
            return
        else:
            await message.answer("Хорошо. Попробуйте указать другое название документа.")
            context["awaiting_confirmation"] = False
            return

    # Если документ уже выбран — обрабатываем запрос по содержанию
    if context.get("selected_document"):
        document_text = documents.get(context["selected_document"], "")
        prompt = f"""Документ:

{document_text[:3000]}


Вопрос пользователя: {text}

Ответ:"""
        response = ask_openai(prompt)
        await message.answer(response)
        return

    # Если нет выбранного документа — пытаемся найти его
    doc_match = find_best_match(text)
    if doc_match:
        context["suggested_doc"] = doc_match
        context["awaiting_confirmation"] = True
        await message.answer(f"Вы имели в виду документ: «{doc_match}»? (да/нет)")
    else:
        await message.answer("Не удалось найти документ в локальной базе. Можете дать более точное название?")

# Настройка вебхука
async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(dp):
    await bot.delete_webhook()

if __name__ == "__main__":
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
