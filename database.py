import asyncio
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastembed import TextEmbedding
import numpy as np
from openai import AsyncOpenAI
from sqlalchemy import URL, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import Annotated, Any, Dict, cast
# from llama_cpp import Llama
from lang import create_word_prompt
from models import Base, CategoryOrm, WordOrm
from schemas import HintCache, HintResponse
from config import settings, logger


DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.db_user,
    password=settings.db_password,
    host=settings.db_host,
    port=int(settings.db_port),
    database=settings.db_name,
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=settings.pool_size, max_overflow=settings.max_overflow)
new_session = async_sessionmaker(engine, expire_on_commit=False)

async def get_session():
    async with new_session() as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# ---------------------------------------------------------------------------------------------------------------------------

async def db_connection_check() -> None:
    """Database connection check at application startup. If the connection fails, the application will not start."""
    retries: int = 5
    while retries > 0:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.success("Database connection check successful")
            return
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            retries -= 1
            if retries > 0:
                logger.info(f"Retrying database connection check... ({5 - retries}/5)")
                await asyncio.sleep(5)  # Wait 5 seconds before the next attempt

    raise RuntimeError("Failed to connect to the database. Application startup aborted.")


async def user_exists(username: str, session: SessionDep) -> bool:
    sql = text("SELECT userid FROM users WHERE username = :uname LIMIT 1")
    result = await session.execute(sql, {"uname": username}) 
    row = result.first()
    if row:
        return True
    else:
        return False   


async def check_user(username: str, password: str, session: SessionDep, justFind: bool=False) -> int:
    sql = text("SELECT userid, password FROM users WHERE username = :uname LIMIT 1")
    result = await session.execute(sql, {"uname": username}) 
    row = result.first()
    if row:
        #if verify_password(password, row.password):
        if justFind or (password == row.password):
            return row.userid
        else:
            return 0
    else:
        return 0
    

async def create_all_tables() -> None:
    """ DB: Create pgvector extension, if not exists. 
        Create all tables if they do not exist yet. 
        This function should be called just once at the start of the application. """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

        if settings.in_development and settings.force_recreate_db:
            await conn.run_sync(Base.metadata.drop_all)
            logger.success("All previous database tables have been dropped.")
        
        await conn.run_sync(Base.metadata.create_all)
        logger.success("Database tables were created successfully")       

        await conn.execute(text("ALTER TABLE words ADD COLUMN IF NOT EXISTS cat_id integer;")) 
        

async def db_add_record(session: AsyncSession, model_instance: Base, log_label: str = "Record") -> dict:
    try:
        session.add(model_instance)
        await session.commit()
        logger.success(f"{log_label} successfully added")
        return {"result": "ok"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to add {log_label.lower()}: {e}")
        return {"result": "error"}
    

async def background_checks(session_factory: async_sessionmaker) -> None:
    logger.info("Activity check started")
    async with session_factory() as session:
        sql = text("SELECT 1")  # здесь всего-лишь заглушка сейчас, для данного проекта
        await session.execute(sql)
        # await session.commit()
        logger.success("Activity check completed successfully")


# сохранение в БД (таблица words) списка слов и их эмбеддингов
async def insert_words2db(words: list[str], embeddings_list: list[list[float]], categories:list[int], lang: str, session: AsyncSession) -> None:
    # Проверяем, какие слова из текущего БАТЧА уже есть в БД
    stmt = select(WordOrm.word).where(WordOrm.language == lang, WordOrm.word.in_(words))
    result = await session.execute(stmt)
    
    existing_words = set(result.scalars().all())

    # Формируем список словарей для слов НЕ найденных в таблице words:
    new_data = [{"word": w, "embedding": e, "language": lang, "cat_id": c} for w, e, c in zip(words, embeddings_list, categories) if w not in existing_words]

    # Если все слова из этого батча уже есть в базе, просто выходим
    if not new_data:
        return

    # bulk-вставка
    await session.execute(insert(WordOrm), new_data)
    await session.commit()


async def find_the_running_game(session: AsyncSession) -> dict:
    result = await session.execute(text("SELECT g.id, EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER AS game_time FROM games g WHERE finished IS NULL LIMIT 1")) 
    row = result.first()
    if row:
        return {"id": row.id, "game_time": row.game_time} 
    else:
        return {"id": 0, "game_time": 0}  
    

async def check_the_player_involved(userid: int, gameid: int, language: str, session: SessionDep) -> dict:
    """Ensure a player record exists for the given userid in the current running game.
    Returns True if the user already has a player record for the running game.
    Returns False if there is no running game (the game has been just cosed / another user won the game)).
    """
    if gameid == 0:
        logger.info(f"This player (userid={userid}) is NOT involved!  GAME ID == 0")
        return {"result": False, "reason": "game_id == 0"}

    player_id:int = 0
    # Check if the player already exists for this game (and the game has not been finished yet)
    sql = text("""
        SELECT p.id, p.finished, p.attempts, p.hints, g.finished AS game_finished, 
        EXISTS(SELECT 1 FROM players pp WHERE pp.gameid = :gameid1 AND pp.finished IS NOT NULL AND pp.id <> p.id) AS another_won 
        FROM players p INNER JOIN games g ON g.id=p.gameid 
        WHERE p.userid = :userid AND p.gameid = :gameid2 LIMIT 1
    """)
    res = await session.execute(sql, {"gameid1": gameid, "userid": userid, "gameid2": gameid})
    row = res.first()
    if row:
        if row.another_won:
            logger.warning(f"UNABLE GUESS the word for the game {gameid}! Another player won the game")
            return {"result": False, "reason": "ANOTHER_PLAYER_WON"}
        else:
            if row.finished or row.game_finished:
                logger.warning(f"UNABLE GUESS the word for the FINISHED game {gameid}!")
                return {"result": False, "reason": "GAME_FINISHED"}
            else:
                return {"result": True, "player_id": row.id, "attempts": row.attempts, "hints": row.hints}
    else:
        logger.error(f" UNABLE GUESS the word: User {userid} is NOT involved into the game {gameid} ")
        return {"result": False, "reason": "GAME_NOT_FOUND / PLAYER_NOT_JOINED"}


async def join_the_player(userid: int, language: str, session: SessionDep) -> dict:
    player_id:int = 0
    game_id:int = 0
    time_left:int = settings.game_duration
    try:
        game_dict = await find_the_running_game(session)
        game_id = game_dict["id"] 
        if game_id == 0:
            logger.error(f"No running game found when trying to involve user {userid} in a game")
            return {"result": False, "reason": "GAME_NOT_FOUND"}
        else:
            time_left = settings.game_duration - game_dict["game_time"]
            if time_left < 11:
                logger.warning(f"Current game is about to exceed its time. Cannot join {userid} to this game")
                return {"result": False, "reason": "GAME_ABOUT_TO_EXCEED"}

        sql = text("""
            SELECT p.id, p.finished, p.hints, p.attempts, 
            EXISTS(SELECT 1 FROM players pp WHERE pp.gameid = :gameid1 AND pp.finished IS NOT NULL AND pp.id <> p.id) AS another_won 
            FROM players p WHERE p.userid = :userid AND p.gameid = :gameid2 LIMIT 1
        """)
        res = await session.execute(sql, {"gameid1": game_id, "userid": userid, "gameid2": game_id})
        row = res.first()
        if row:
            if not row.finished:
                if not row.another_won:
                    return {"result": True, "player_id": row.id, "time_left": time_left, "attempts": row.attempts, "hints": row.hints}
                else:
                    logger.warning(f"Unable to join the game {game_id}! Another player won the game")
                    return {"result": False, "reason": "ANOTHER_PLAYER_WON"}
            else:
                logger.error(f"Unable to join the FINISHED game {game_id}!")
                return {"result": False, "reason": "GAME_FINISHED"}
        else:
            logger.info(f"User {userid} is NOT joined to game {game_id} - START joining!")
            #  Only HE FIRST player joined the game can define the next game language (next secret word language) 
            sql = text("SELECT count(*) AS cnt FROM players WHERE gameid = :gameid")
            result = await session.execute(sql, {"gameid": game_id})
            row = result.first()
            if row:
                if row.cnt == 0:
                    settings.language = language  # language in settings define the secret word language

            # Try to insert new player record (to involve user in the game)
            try:
                sql = text("INSERT INTO players (userid, gameid, attempts, hints) VALUES (:userid, :gameid, 0, 0) RETURNING id")
                res = await session.execute(sql, {"userid": userid, "gameid": game_id})
                await session.commit()    
                row = res.first()
                if row:
                    player_id = row.id
                    logger.success(f"User {userid} was added as a player to game {game_id}")
                    return {"result": True, "player_id": player_id, "time_left": time_left, "attempts": 0, "honts": 0}
                else:
                    logger.error(f"AN ANEXPECTED ERROR occured when trying to involve user {userid} in a game {game_id}")
                    return {"result": False, "reason": "ERROR"}  
            except IntegrityError as e:
                await session.rollback()
                logger.info(f"Попытка userid={userid} подключиться в игру {game_id} второй раз !")
                return {"result": False}    
    except Exception as e:
        await session.rollback()
        logger.error(f"FAILED to add user {userid} to the running game: {e}")
        raise HTTPException(status_code=500, detail="Internal error while adding player to the game")


async def the_game_state_update(userid: int, game_id: int, player_id: int, word: str, secret_word:str, similarity_percent: float, session: SessionDep) -> bool:
    enddate:datetime|None = None
    try:
        # Если игрок угадал слово - он победитель и игра завершена!
        if similarity_percent > 99.0:
            word_length: int = (len(secret_word) - 3) * 10
            sql_query = text("""
                INSERT INTO winners(userid, gameid, scores) 
                SELECT :userid, :gameid, (100 + (:word_length - COALESCE((SELECT p.hints FROM players p WHERE p.id = :playerid LIMIT 1),0)*10));
            """)
            await session.execute(sql_query, {"userid": userid, "playerid": player_id, "gameid": game_id, "word_length": word_length})
            enddate = datetime.now()
            logger.info(f"записываются в БД данные победителя {userid} - он победил в игре {game_id}, угадал слово {secret_word}")

        # записываем результаты очередного хода игрока
        logger.info(f"Записываются в БД результаты очередного хода игрока {userid} в игре {game_id}, он ввел слово {word} для слова {secret_word}. similarity = {similarity_percent}")
        if enddate:
            sql_query = text("UPDATE players SET attempts=attempts+1, finished=LOCALTIMESTAMP WHERE id=:player_id")
        else:    
            sql_query = text("UPDATE players SET attempts=attempts+1 WHERE id=:player_id")
        await session.execute(sql_query, {"player_id": player_id})

        if similarity_percent <= 99.0:
            sql_query = text("INSERT INTO sessions(playerid, word, similarity_score) SELECT :playerid, :word, :similarity_score")
            await session.execute(sql_query, {"playerid": player_id, "word": word, "similarity_score": similarity_percent})
        
        await session.commit()
        logger.success("Данные очередного хода успешно записаны в БД")
        return True
    except Exception as e:
        await session.rollback() 
        logger.exception("Ошибка записи в БД данных очередного хода")
        raise HTTPException(status_code=500, detail="Ошибка записи в БД данных очередного хода")


async def create_new_game(session_factory: async_sessionmaker, app_state, force: bool) -> bool:
    language: str = settings.language
    app_state.language = language
    game_lock = app_state.process_lock
    new_game_id:int|None = None
    async with game_lock:       
        async with session_factory() as session: 
            try:
                logger.info("Начало создания новой игры ...")
                # Выбираем случайное слово 
                select_word_query = text("""
                    SELECT w.id, w.word FROM words w 
                    WHERE (w.language = :lang) AND (w.id NOT IN (SELECT g.secret_word_id FROM games g ORDER BY g.id DESC LIMIT 10)) AND (LENGTH(w.word) < :max_len) AND (LENGTH(w.word) > :min_len)
                    ORDER BY random() LIMIT 1
                """)
                word_res = await session.execute(select_word_query, {"lang": language, "max_len": settings.max_word_len, "min_len": (3 if language == 'ru' else 2)})
                word_data = word_res.fetchone()
                if not word_data:
                    logger.error(f"Словарь слов на языке '{language}' не найден в базе данных. Заполните таблицу words для '{language}'")
                    if language != 'en':
                        settings.language = 'en'
                    return False
            
                logger.success(f"Выбрано новое секретное слово для угадывания: {word_data.word}")

                # Закрываем старую игру
                if force:
                    finish_query = text("UPDATE games SET finished=LOCALTIMESTAMP WHERE finished IS NULL  RETURNING id")
                else:    
                    finish_query = text("UPDATE games SET finished=LOCALTIMESTAMP WHERE finished IS NULL AND EXTRACT(EPOCH FROM (LOCALTIMESTAMP - started))::INTEGER > 10  RETURNING id")
                result = await session.execute(finish_query)
                old_game_id = result.scalar()
                if old_game_id or force:
                    finish_query = text("DELETE FROM players WHERE gameid < :gameid - 1")
                    await session.execute(finish_query, {"gameid": old_game_id})
                    logger.info(f"Текущая (старая) игра {old_game_id} завершена!")
                    # Фиксируем НОВУЮ игру в таблице games
                    insert_game_query = text("INSERT INTO games (secret_word_id, language) VALUES (:word_id, :lang)  RETURNING id")
                    result = await session.execute(insert_game_query, {"word_id": word_data.id, "lang": language})
                    new_game_id = result.scalar()
                await session.commit() 

                if new_game_id:
                    logger.success(f"ํНовая игра {new_game_id} создана!")        
                    # await fill_hints_cache(new_game_id, word_data.word, language, session, app_state)
                    return True
                else:
                    logger.warning(f"Нельзя завершить игру {old_game_id}, которая только-что началась!")
                    return False
            except Exception as e:
                await session.rollback() 
                logger.exception("Ошибка при создании новой игры!")
                return False


async def get_the_game_statistic(userid, session: SessionDep):    
    if userid == 0:    
        status_query = text("""
            SELECT 
                g.id AS game_id,
                g.started,  
                COUNT(DISTINCT p.userid) AS total_participants,
                COALESCE(SUM(p.attempts), 0) AS total_attempts,
                COALESCE((SELECT ww.word FROM words ww INNER JOIN games gg ON ww.id=gg.secret_word_id WHERE gg.finished IS NOT NULL ORDER BY gg.id DESC LIMIT 1), '') as last_word, 
                MIN(EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER) AS seconds_passed,
                EXISTS(SELECT 1 FROM winners w WHERE w.gameid=g.id) AS win
            FROM games g LEFT JOIN players p ON p.gameid = g.id
            WHERE g.id = (SELECT MAX(z.id) FROM games z)
            GROUP BY g.id
        """)    
        res = await session.execute(status_query)
    else:    
        status_query = text("""
            SELECT 
                g.id AS game_id,
                g.started,  
                1 AS total_participants,
                COALESCE(p.attempts, 0) AS total_attempts,
                w.word AS secret_word, 
                LENGTH(w.word) as word_len, 
                w.language, 
                EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER AS seconds_passed, 
                COALESCE((SELECT ww.word FROM words ww INNER JOIN games gg ON ww.id=gg.secret_word_id WHERE gg.id=(g.id - 1) LIMIT 1), '') as last_word, 
                COALESCE(c.image, 'others.jpg') AS image                                  
            FROM games g INNER JOIN words w ON g.secret_word_id=w.id INNER JOIN categories c ON w.cat_id=c.id
            LEFT JOIN players p ON p.gameid = g.id AND p.userid=:userid
            WHERE g.finished IS NULL
            LIMIT 1
        """)    
        res = await session.execute(status_query, {"userid": userid})
    return res.fetchone()


async def check_the_game_duration(session_factory: async_sessionmaker, app_state):  
    logger.info(f"Проверка игры на превышение {settings.game_duration}")
    found: bool = False   
    async with session_factory() as session:
        query = text("""
            SELECT g.id FROM games g 
            WHERE g.finished IS NULL AND (((EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER) >= :seconds) OR EXISTS(SELECT 1 FROM winners w WHERE w.gameid=g.id));
        """)
        result = await session.execute(query, {"seconds": (settings.game_duration - 10)})
        row = result.first()
        if row:
            found = True       

    if found:
        logger.info("Найдена 'устаревшая' игра !!!")
        await create_new_game(session_factory, app_state, False)  


async def manage_hint(gameid: int, userid: int, language:str, session: SessionDep, request: Request) -> HintResponse:  
    sql = text("SELECT count(*) as cnt FROM sessions s INNER JOIN players p ON p.id=s.playerid INNER JOIN games g ON g.id=p.gameid WHERE p.userid = :userid AND p.gameid = :gameid AND g.finished IS NULL")
    res = await session.execute(sql, {"userid": userid, "gameid": gameid})
    row = res.first()
    if not row:
        return HintResponse(result="NO", first_letter="", second_letter="", last_letter="", analogues=[], anagram="", ai="")
    if row.cnt < 5: 
        return HintResponse(result="NO", first_letter="", second_letter="", last_letter="", analogues=[], anagram="", ai="")

    attempts: int = row.cnt 
    hintResponse: HintResponse = get_hints_from_cache(request, attempts) 

    if attempts > 4 and attempts < 26:
        if attempts < 21 or len(hintResponse.analogues) > 1 or hintResponse.second_letter != "":
            sql = text("UPDATE players SET hints=:hints WHERE userid = :userid AND gameid = :gameid AND hints < :hints;")
            level : int = attempts // 5 
            await session.execute(sql, {"userid": userid, "gameid": gameid, "hints": level})
            await session.commit() 
            logger.info(f"ํПользователем {userid} в игре {gameid} запрошено {level} подсказок")  

    return hintResponse     


async def get_players_stats (session: SessionDep):
    sql = text("""
        SELECT u.username, COALESCE(SUM(w.scores),0) as rate, 
        SUM(CASE WHEN w.id = (SELECT MAX(z.id) FROM winners z) THEN w.scores ELSE 0 END) AS last_winner 
        FROM users u LEFT OUTER JOIN winners w ON u.userid=w.userid 
        GROUP BY u.username
        ORDER BY COALESCE(SUM(w.scores),0) DESC;      
    """)
    res = await session.execute(sql)
    return res.mappings().all()


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

    if level > 4:
        first_letter = hint_cache.first_letter  
        logger.info(f"Первая подсказка '{first_letter}' получена из кэша")
        result = "YES"
    
    if level > 9:
        last_letter = hint_cache.last_letter
        logger.info(f"Вторая подсказка  '{last_letter}' получена из кэша")

    if level > 14: 
        analogues = hint_cache.analogues
        logger.info(f"Третья подсказка {str(analogues)} получена из кэша")
        if hint_cache.size > 3 and len(analogues) < 1:   
            second_letter = hint_cache.second_letter 
        if len(analogues) < 1 and second_letter == "": 
            ai = hint_cache.ai
    
    if level > 19:
        if ai == "": 
            ai = hint_cache.ai
        else: 
            anagram = hint_cache.anagram
        logger.info(f"Четвертая подсказка '{ai}' получена из кэша")

    if level > 24 and anagram == "":     
        anagram = hint_cache.anagram
        logger.info(f"ПЯТАЯ подсказка '{ai}' получена из кэша")

    return HintResponse(result=result, first_letter=first_letter, second_letter=second_letter, last_letter=last_letter, analogues=analogues, anagram=anagram, ai=ai)



async def fill_hints_cache(gameid: int, word: str, session: SessionDep, app_state):
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
        return content.strip()
        


async def delete_user(userName: str, session: SessionDep) -> bool:
    """Delete a user by username from the database."""
    try:
        await session.execute(
            text("DELETE FROM users WHERE username = :username"),
            {"username": userName},
        )
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logger.error(f"Error deleting user {userName}: {e}")
        return False


async def add_categories(session: SessionDep, embedder: TextEmbedding):
    category_centroids = {}
    ANCHOR_DATA = {
        "1": ["animal", "prey", "dog", "cat", "bird", "fish", "horse", "elephant", "lion", "tiger", "bear", "wolf", "fox", "rabbit", "mouse", "monkey", "eagle", "owl", "shark", "whale", "snake", "frog", "bee", "spider", "deer", "cow", "sheep", "pig", "chicken", "duck", "paw", "tail"],
        "2": ["herb", "plant", "tree", "flower", "grass", "mushroom", "leaf", "root", "branch", "forest", "oak", "pine", "rose", "tulip", "daisy", "sunflower", "cactus", "bush", "fern", "moss", "bark", "seed", "sprout", "weed", "wood", "ivy", "palm"],
        "3": ["meal", "bread", "meat", "milk", "bakon", "water", "apple", "banana", "tomato", "potato", "cheese", "butter", "soup", "salad", "sugar", "salt", "pepper", "coffee", "tea", "juice", "wine", "beer", "rice", "pasta", "egg", "fish", "cake", "chocolate", "fruit", "vegetable", "onion", "garlic", "whiskey"],
        "4": ["man", "woman", "child", "baby", "father", "mother", "brother", "sister", "family", "head", "face", "eye", "ear", "nose", "mouth", "hair", "hand", "arm", "leg", "foot", "heart", "blood", "brain", "body", "soul", "friend", "person", "people", "joy", "fear", "vein", "disease"],
        "5": ["doctor", "teacher", "engineer", "driver", "writer", "actor", "artist", "singer", "chef", "baker", "builder", "lawyer", "police", "soldier", "nurse", "pilot", "farmer", "sport", "football", "chess", "hobby", "work", "job", "career", "manager", "clerk", "worker", "scientist", "judge"],
        "6": ["jacket", "coat", "shirt", "trousers", "jeans", "dress", "skirt", "sweater", "boots", "shoes", "socks", "hat", "cap", "gloves", "scarf", "tie", "belt", "bag", "backpack", "wallet", "ring", "necklace", "watch", "glasses", "suit", "uniform", "pocket", "button"],
        "7": ["table", "chair", "bed", "sofa", "wardrobe", "door", "window", "wall", "floor", "roof", "house", "room", "kitchen", "plate", "cup", "spoon", "fork", "knife", "fridge", "lamp", "mirror", "towel", "pillow", "blanket", "curtain", "carpet", "clock", "key", "soap", "lock"],
        "8": ["hammer", "saw", "screwdriver", "pliers", "wrench", "drill", "axe", "shovel", "nail", "screw", "phone", "computer", "laptop", "camera", "battery", "wire", "device", "gadget", "weapon", "gun", "sword", "scissors", "tape", "ladder", "instrument", "tool", "engine", "robot"],
        "9": ["car", "bus", "train", "plane", "ship", "boat", "bicycle", "motorcycle", "truck", "wheel", "engine", "road", "street", "track", "station", "airport", "port", "driver", "pilot", "passenger", "flight", "trip", "journey", "traffic", "bridge", "tunnel"],
        "10": ["city", "town", "village", "country", "world", "earth", "sun", "moon", "star", "sky", "sea", "ocean", "river", "lake", "mountain", "hill", "valley", "desert", "island", "beach", "stone", "sand", "wind", "rain", "snow", "cloud", "weather", "map", "continent", "shore"],
        "11": ["book", "music", "song", "guitar", "piano", "picture", "painting", "movie", "film", "theatre", "dance", "game", "history", "science", "math", "physics", "language", "word", "letter", "holiday", "party", "museum", "poem", "story", "statue", "school", "university"],
        "12": ["time", "year", "month", "week", "day", "hour", "minute", "second", "number", "money", "price", "law", "rule", "order", "peace", "war", "life", "death", "truth", "lie", "love", "hate", "mind", "thought", "idea", "reason", "fact", "secret", "future", "past", "rage", "sadness", "policy", "authority", "power", "guilt", "government", ""]
    }    
    result = await session.execute(text("SELECT COUNT(*) AS cnt FROM categories")) 
    row = result.first()
    if row and row.cnt == 0:
        await db_add_record(session, CategoryOrm(id=1, ru="animals", en='animals', fr="animals", image='animqals.jpg'), "Category animals")
        await db_add_record(session, CategoryOrm(id=2, ru="plants", en="plants", fr="plants", image='plants.jpg'), "Category plants")
        await db_add_record(session, CategoryOrm(id=3, ru="meal", en="meal", fr="meal", image='meal.jpg'), "Category meal")
        await db_add_record(session, CategoryOrm(id=4, ru="human", en="human", fr="human", image='human.jpg'), "Category human")
        await db_add_record(session, CategoryOrm(id=5, ru="activity", en="activity", fr="activity", image='activity.jpg'), "Category activity")
        await db_add_record(session, CategoryOrm(id=6, ru="dress", en="dress", fr="dress", image='dress.jpg'), "Category dress")
        await db_add_record(session, CategoryOrm(id=7, ru="home", en="home", fr="home", image='home.jpg'), "Category home")
        await db_add_record(session, CategoryOrm(id=8, ru="technics", en="technics", fr="technics", image='technics.jpg'), "Category technics")
        await db_add_record(session, CategoryOrm(id=9, ru="transport", en="transport", fr="transport", image='transport.jpg'), "Category transport")
        await db_add_record(session, CategoryOrm(id=10, ru="geography", en="geography", fr="geography", image='geography.jpg'), "Category geography")
        await db_add_record(session, CategoryOrm(id=11, ru="culture", en="culture", fr="culture", image='culture.jpg'), "Category culture")
        await db_add_record(session, CategoryOrm(id=12, ru="concepts", en="concepts", fr="concepts", image='concepts.jpg'), "Category concepts")
        await db_add_record(session, CategoryOrm(id=13, ru="others", en="others", fr="others", image='others.jpg'), "Category others")

    for cat_name, anchors in ANCHOR_DATA.items():
        # fastembed принимает список строк и возвращает генератор векторов
        # Превращаем его в массив numpy: матрица размера [N, 384]
        templated_anchors = [f"This word is {word}" for word in anchors]
        anchor_embeddings = np.array(list(embedder.embed(templated_anchors)))
        # Усредняем векторы по строкам (dim=0 в torch — это axis=0 в numpy)
        centroid_vector = np.mean(anchor_embeddings, axis=0)        
        # Нормализуем вектор (делим на его L2-норму) для корректного подсчета сходства
        centroid_vector = centroid_vector / np.linalg.norm(centroid_vector)        
        category_centroids[cat_name] = centroid_vector

    try:
        res = await session.execute(text("SELECT id, word, language FROM words"))  
        if res:
            words_to_update = res.mappings().all()
            logger.info(f"Найдено {len(words_to_update)} слов для разметки.")
            for row in words_to_update:
                category, score = predict_category(embedder, row.word, row.language, category_centroids)
                if score < 0.50:
                    category = "13"
                logger.info(f"Слово {row.word}.  Язык {row.language}.  Категория {category}  - {score}")
                #await session.execute(text("UPDATE words SET cat_id = :category WHERE id = :id"),{"category": int(category), "id": row.id})
                #await session.commit()
            
            logger.success("Разметка таблицы слов категориями успешно завершена!")
    except Exception as e:
        # await session.rollback()
        logger.error(f"Ошибка разметки таблицы слов категориями")



def predict_category(embedder: TextEmbedding, secret_word: str, language: str, category_centroids):
    if language == "ru":
        language = "en"
    prompt = create_word_prompt(secret_word, language)   
    # logger.info(f"Создан промпт для языка {language} : '{prompt}'")   
    word_embedding = next(iter(embedder.embed([prompt])))  
    # Нормализуем вектор слова
    word_embedding = word_embedding / np.linalg.norm(word_embedding)
    best_category = "13"
    best_score = -1.0
    
    # Сравниваем с центроидами
    for cat_name, centroid in category_centroids.items():
        # Так как оба вектора нормализованы, их скалярное произведение (dot product)
        # математически равно косинусному сходству (cos_sim)
        score = np.dot(word_embedding, centroid)
        
        if score > best_score:
            best_score = score
            best_category = cat_name
            
    if best_score < 0.50:
        return "13", best_score
        
    return best_category, best_score
         


          

