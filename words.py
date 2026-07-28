import asyncio
import os
from fastapi import HTTPException
import numpy as np
from fastapi.concurrency import run_in_threadpool
from fastembed import TextEmbedding
from sqlalchemy import text
from database import SessionDep, insert_words2db
from config import PROMPT_TEMPLATES, logger, settings
from langdetect import detect, LangDetectException
#import nltk
#from nltk.corpus import wordnet
#from nltk.corpus.reader.wordnet import Synset


WORD_LISTS_DIR: str = "data"
BATCH_SIZE: int = 256 
SUCCESS_THRESHOLD_PERCENT: float = 86.0

#nltk.download('wordnet')

#def is_noun(word: str) -> bool:
#    synsets = wordnet.synsets(word)
#    
#    if synsets:
#        if synsets[0].pos() == 'n':  # type: ignore
#            return True         
#    return False

def detect_language(single_word: str) -> str:
    if any(c in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя' for c in single_word):
        return "ru"
    else:
        try:
            lang = detect(single_word)
            if lang in ["en", "fr"]:
                return lang
            else:
                return "en"
        except LangDetectException:
            return "en"     


def create_word_prompt(single_word: str, detected_lang: str) -> str:
    if any(c in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя' for c in single_word):
        detected_lang = "ru"
    else:
        # 2. Если латиница — выбираем между en и fr
        try:
            lang = detect(single_word)
            if lang in ["en", "fr"]:
                detected_lang = lang
        except LangDetectException:
            pass     

    template: str = PROMPT_TEMPLATES.get(detected_lang, "This word is {word}.")
    prompt: str = template.format(word=single_word) 
    logger.info(f"Игрок ввел слово на языке [{detected_lang}]. Сформирован промпт: {prompt}")
    return prompt


def generate_embedding(single_word: str, language: str, embedder: TextEmbedding) -> np.ndarray:
    prompt = create_word_prompt(single_word, language)      
    emb = next(iter(embedder.embed([prompt])))  
    
    try:
        # Превращаем в массив, вытягиваем в одну строку (.ravel()) 
        # и приводим к типу float64 (или float32 для экономии памяти)
        return np.asarray(emb).ravel().astype(np.float64)
    except Exception:
        # Резервный вариант, если прямой перевод в np.asarray вызвал сбой
        if hasattr(emb, "tolist"):
            out = emb.tolist()
            if isinstance(out, list) and len(out) == 1 and isinstance(out[0], list):
                out = out[0]
            return np.array(out, dtype=np.float64)
        else:
            return np.array([float(x) for x in emb], dtype=np.float64)


def read_file_sync(path) -> list[str]:
    lines: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            clean: str = line.strip().lower()
            if clean and len(clean) <= 11:
                lines.append(clean)       
#                if is_noun(clean):
#                    lines.append(clean)       
    return lines    


async def fill_words_list(fileName: str, lang: str, embedder: TextEmbedding, session: SessionDep):
    file_path = os.path.join(WORD_LISTS_DIR, fileName)
    row_words: list[str] = [] 
    template = PROMPT_TEMPLATES.get(lang, "{word}")

    try:
        row_words: list[str] = await run_in_threadpool(read_file_sync, file_path)
    except OSError as e:
        logger.error(f"File {fileName} read error: {e}")
        raise HTTPException(status_code=500, detail=f"При чтении файла {fileName} возникла ошибка")       

    seen: set = set()
    unique_words: list[str] = []
    for w in row_words:
        if w not in seen and w.strip():
            seen.add(w)
            unique_words.append(w)

    total_words: int = len(unique_words)        
    # превращаем слова в короткие предложения 
    unique_phrases = [template.format(word=w) for w in unique_words]

    logger.info(f"Генерация эмбеддингов для {total_words} уникальных слов на языке {lang}")

    # Разбиваем на батчи и обрабатываем короткими батчами для защиты от OOM и рассинхронизации
    for i in range(0, total_words, BATCH_SIZE):
        batch_frazes = unique_phrases[i:i + BATCH_SIZE]
        batch_words = unique_words[i:i + BATCH_SIZE]
        embeddings_iterator = embedder.embed(batch_frazes)
        batch_embeddings = [list(vec) for vec in embeddings_iterator]

        if len(batch_words) != len(batch_embeddings):
            logger.error(f"Mismatch error at batch {i}. Words: {len(batch_words)}, Embeddings: {len(batch_embeddings)}")
            raise HTTPException(status_code=500, detail="Ошибка генерации векторов: несовпадение размерности")

        await insert_words2db(batch_words, batch_embeddings, lang, session)
        
        logger.info(f"Успешно обработано и сохранено: {min(i + BATCH_SIZE, total_words)}/{total_words}")
        await asyncio.sleep(0.01)

    return True

async def unload_words_list(fileName: str, lang: str, session: SessionDep):
    file_path = os.path.join(WORD_LISTS_DIR, fileName)

    sql = text("SELECT w.word FROM words w WHERE w.language = :lang ORDER BY w.word")
    result = await session.execute(sql, {"lang": lang})

    file_content = "\n".join([row["word"] for row in result.mappings()])
    logger.info(f"Выгрузка данных ({file_content[:21]}) в файл {file_path}")

    if file_content:
        await asyncio.to_thread(save_file, file_content, file_path, lang)

# Синхронная функция записи в файл
def save_file(content: str, filename: str, lang: str):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    logger.success(f"Выгрузка списка слов для языка '{lang}' завершена!")

