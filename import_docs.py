#!/usr/bin/env python3

import os
import sys
import logging

import psycopg2
from psycopg2 import sql

# Для чтения docx
import docx  # pip install python-docx

# Для чтения PDF
import pdfplumber  # pip install pdfplumber

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# 1. Получаем строку подключения к базе из переменной окружения.
#    На Render это будет DATABASE_URL (например, "postgres://user:pass@host:port/dbname").
# ------------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("Не установлена переменная окружения DATABASE_URL.")
    sys.exit(1)

# ------------------------------------------------------------------------------
# 2. Подключаемся к базе
# ------------------------------------------------------------------------------
try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cursor = conn.cursor()
except Exception as e:
    logger.error(f"Ошибка подключения к базе: {e}")
    sys.exit(1)

# ------------------------------------------------------------------------------
# 3. Создаём таблицу (documents), если она ещё не создана.
#    В таблице поля: id (SERIAL, PRIMARY KEY), doc_number, title, content.
# ------------------------------------------------------------------------------
CREATE_TABLE_QUERY = """
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    doc_number TEXT,
    title TEXT,
    content TEXT
);
"""
cursor.execute(CREATE_TABLE_QUERY)

# ------------------------------------------------------------------------------
# 4. Функции для извлечения текста из DOCX и PDF
# ------------------------------------------------------------------------------
def extract_text_from_docx(filepath):
    """
    Извлечение текста из docx через python-docx.
    Возвращает строку текста.
    """
    doc = docx.Document(filepath)
    paragraphs = [p.text for p in doc.paragraphs]
    full_text = "\n".join(paragraphs)
    return full_text

def extract_text_from_pdf(filepath):
    """
    Извлечение текста из PDF через pdfplumber.
    Возвращает строку текста.
    """
    with pdfplumber.open(filepath) as pdf:
        all_text = []
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                all_text.append(page_text)
        return "\n".join(all_text)

# ------------------------------------------------------------------------------
# 5. Функция для записи документа в базу.
# ------------------------------------------------------------------------------
INSERT_QUERY = """
INSERT INTO documents (doc_number, title, content)
VALUES (%s, %s, %s)
"""

def insert_document(doc_number, title, content):
    cursor.execute(INSERT_QUERY, (doc_number, title, content))

# ------------------------------------------------------------------------------
# 6. Обходим все файлы в папке docs/, извлекаем текст и записываем в базу
# ------------------------------------------------------------------------------
DOCS_FOLDER = "docs"
if not os.path.isdir(DOCS_FOLDER):
    logger.error(f"Папка '{DOCS_FOLDER}' не найдена. Создайте её и положите туда docx/pdf файлы.")
    sys.exit(1)

files = os.listdir(DOCS_FOLDER)
if not files:
    logger.warning("Папка docs пуста. Нет файлов для импорта.")
    sys.exit(0)

for filename in files:
    filepath = os.path.join(DOCS_FOLDER, filename)

    # Пропускаем папки или другие форматы
    if not os.path.isfile(filepath):
        continue

    # Простейшая логика: по расширению определяем формат
    ext = filename.lower().rsplit(".", 1)[-1]

    doc_number = filename  # На старте пусть будет просто имя файла
    title = f"Документ: {filename}"

    try:
        if ext == "docx":
            text_content = extract_text_from_docx(filepath)
        elif ext == "pdf":
            text_content = extract_text_from_pdf(filepath)
        else:
            logger.info(f"Файл '{filename}' не docx и не pdf, пропускаем.")
            continue

        # Сохраняем в базу
        insert_document(doc_number, title, text_content[:200000])  
        # [:200000] — если боитесь очень длинных файлов, можно обрезать.

        logger.info(f"Файл '{filename}' успешно импортирован в базу.")
    except Exception as e:
        logger.error(f"Ошибка обработки файла '{filename}': {e}")

logger.info("Импорт завершён.")

# ------------------------------------------------------------------------------
# 7. Закрываем соединение
# ------------------------------------------------------------------------------
cursor.close()
conn.close()
