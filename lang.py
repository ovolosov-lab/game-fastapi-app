from config import PROMPT_TEMPLATES, logger
from langdetect import detect, LangDetectException

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
    # logger.info(f"Игрок ввел слово на языке [{detected_lang}]. Сформирован промпт: {prompt}")
    return prompt

