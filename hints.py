from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from fastapi import Request
from openai import AsyncOpenAI
from game import Game
from schemas import HintCache, HintResponse
from config import settings, logger



# Отдаем подсказки игроку и фиксируем количество подсказок, которые он запросил
def manage_hint(gameid: int, userid: int, request: Request) -> HintResponse:  
    level: int = 0
    game: Game = request.app.state.game
    attempts = game.getUserAttempts(userid)

    if attempts < 1: 
        return HintResponse(result="NO", first_letter="", second_letter="", last_letter="", analogues=[], anagram="", ai="")

    hintResponse: HintResponse = get_hints_from_cache(request, attempts) 

    if attempts > 0 and attempts < 13:
        if attempts < 21 or len(hintResponse.analogues) > 1 or hintResponse.second_letter != "":
            if attempts == 1:
                level = 1
            else:    
                level = 1 + (attempts // 3) 
            game.addHints(userid, level)
            logger.info(f"ํПользователем {userid} в игре {gameid} запрошено {level} подсказок")  

    return hintResponse    


#  Работаем с кэшем подсказок
def get_hints_from_cache(request, level: int) -> HintResponse:
    first_letter: str = ""  
    second_letter: str = ""  
    last_letter: str = ""  
    analogues: list = []
    anagram: str = ""
    ai = ""
    result: str = "NO"
    hint_cache: HintCache = request.app.state.stored_hint 
    logger.info(hint_cache)

    if level > 0:
        first_letter = hint_cache.first_letter  
        logger.info(f"Первая подсказка '{first_letter}' получена из кэша")
        result = "YES"
    
    if level > 2:
        last_letter = hint_cache.last_letter
        logger.info(f"Вторая подсказка  '{last_letter}' получена из кэша")

    if level > 5: 
        analogues = hint_cache.analogues
        logger.info(f"Третья подсказка {str(analogues)} получена из кэша")
        if hint_cache.size > 3 and len(analogues) < 1:   
            second_letter = hint_cache.second_letter 
        if len(analogues) < 1 and second_letter == "": 
            ai = hint_cache.ai
    
    if level > 8:
        if ai == "": 
            ai = hint_cache.ai
        else: 
            anagram = hint_cache.anagram
        logger.info(f"Четвертая подсказка '{ai}' получена из кэша")

    if level > 11 and anagram == "":     
        anagram = hint_cache.anagram
        logger.info(f"ПЯТАЯ подсказка '{ai}' получена из кэша")

    return HintResponse(result=result, first_letter=first_letter, second_letter=second_letter, last_letter=last_letter, analogues=analogues, anagram=anagram, ai=ai)


#  Заполняем кэш подсказками в самом начале игры
async def fill_hints_cache(gameid: int, word: str, app_state, session_factory: async_sessionmaker):
    language = app_state.language
    hint_cache: HintCache = app_state.stored_hint
    hint_cache.gameid = gameid
    hint_cache.word = word
    hint_cache.first_letter = word[0].upper() 
    hint_cache.last_letter = word[-1].upper() 
    hint_cache.second_letter = word[1].upper()
    hint_cache.analogues = []
    hint_cache.ai = ""
    hint_cache.anagram = "".join(sorted(word))
    hint_cache.size = len(word)
    hint_cache.result = "YES"

    if language != "ru":
        async with session_factory() as session:         
            sql = text("""SELECT DISTINCT T.word FROM (
                SELECT w.word 
                FROM words w   
                WHERE w.language=:lang AND w.word != :word0 
                AND (w.embedding <=> (SELECT z.embedding FROM words z WHERE z.word=:word1 LIMIT 1)) < 0.40 AND w.word != 'word' 
                ORDER BY (w.embedding <=> (SELECT z.embedding FROM words z WHERE z.word=:word2 LIMIT 1)) 
                LIMIT 3     
            ) T;""")
            res = await session.execute(sql, {"lang": language, "word0": word, "word1": word, "word2": word})
            words_list = list(res.scalars().all())
            if words_list:
                hint_cache.analogues = words_list 

    if app_state.ai_enabled:    
        hint_cache.ai = await create_ai_description(word, language, app_state)   
    else:
        logger.warning("Модель ИИ НЕ инициализирована!")    


async def create_ai_description(word: str, language: str, app_state) -> str:
    llm = cast(AsyncOpenAI, app_state.llm)

    imperativ:str = "Ты - ведущий в игре 'Угадай слово'. Твоя задача: Дать краткое (1-2 предложения) описание слова для игроков, которое поможет им угадать это слово, НИ В КОЕМ СЛУЧАЕ не называя это загаданное слово или однокоренные с ним слова."
    prompt:str = f"Загаданное слово: {word}. Дай описание слова."
    if language == "en":
        imperativ = "You are the host of the game «Guess the Word». Your task: Give a short (1-2 sentences) description of the word to the players, which will help them guess it. Under NO CIRCUMSTANCES do you mention the hidden word or words with the same root as it."
        prompt = f"The word is «{word}» Describe the word."
    else:
        if language == "fr":
            imperativ = "Vous êtes l'animateur du jeu « Devinez le mot ». Votre mission : donner aux joueurs une brève description (1 à 2 phrases) du mot à deviner. Vous ne devez en aucun cas mentionner le mot caché ni aucun mot ayant la même racine."
            prompt = f"Le mot est « {word} ». Décrivez ce mot."

    response = await llm.chat.completions.create(
        model="GLM-5.2", 
        temperature=0.7,
        max_tokens=500,
        extra_body={"thinking": {"type": "disabled"}}, 
        messages=[
            {
                "role": "system",
                "content": (imperativ)
            },
            {"role": "user", "content": prompt}
        ]
    )
    logger.warning(f"обращение к АПИ модели GLM-5.2 !!!  Язык: {language}")
    content = response.choices[0].message.content
       
    if content is None:
        return "😔"
    else: 
        return content.strip().replace(word, "*" * len(word))
        
