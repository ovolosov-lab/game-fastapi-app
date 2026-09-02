import asyncio
from datetime import datetime

from fastapi import Depends, HTTPException
from sqlalchemy import URL, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import Annotated
# from llama_cpp import Llama
from game import Game
from models import Base, WordOrm
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

background_tasks_pool = set()
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
    

def check_the_player_involved(userid: int, gameid: int, game: Game) -> dict:
    """Ensure a player record exists for the given userid in the current running game.
    Returns True if the user already has a player record for the running game.
    Returns False if there is no running game (the game has been just cosed / another user won the game)).
    """
    if gameid == 0:
        logger.info(f"This player (userid={userid}) is NOT involved!  GAME ID == 0")
        return {"result": False, "reason": "game_id == 0"}
    else:    
        if game.id == 0:
            if game.anotherPlayerWon(gameid, userid):
                logger.warning(f"UNABLE GUESS the word for the game {gameid}! Another player won the game")
                return {"result": False, "reason": "ANOTHER_PLAYER_WON"}
            else:
                if game.prev_id > 0:
                    logger.warning(f"UNABLE GUESS the word for the FINISHED game {gameid}!")
                    return {"result": False, "reason": "GAME_FINISHED"}
                else:
                    logger.warning(f"UNABLE GUESS the word - game WAS NOT STARTED!")
                    return {"result": False, "reason": "GAME_NOT_STARTED"}
        else:
            if not game.thisGameExists(gameid):        
                logger.warning(f"UNABLE GUESS the word - game {gameid} NOT EXISTS!")
                return {"result": False, "reason": "GAME_NOT_EXISTS"}
            else:
                if game.playerExists(userid):
                    return {"result": True, "attempts": game.getUserAttempts(userid), "hints": game.hintsCount(userid)}
                else:
                    logger.error(f" UNABLE GUESS the word: User {userid} is NOT involved into the game {gameid} ")
                    return {"result": False, "reason": "PLAYER_NOT_JOINED"}
    player_id:int = 0
    # Check if the player already exists for this game (and the game has not been finished yet)
    #sql = text("""
    #    SELECT p.id, p.finished, p.attempts, p.hints, g.finished AS game_finished, 
    #    EXISTS(SELECT 1 FROM players pp WHERE pp.gameid = :gameid1 AND pp.finished IS NOT NULL AND pp.id <> p.id) AS another_won 
    #    FROM players p INNER JOIN games g ON g.id=p.gameid 
    #    WHERE p.userid = :userid AND p.gameid = :gameid2 LIMIT 1
    #""")
    #res = await session.execute(sql, {"gameid1": gameid, "userid": userid, "gameid2": gameid})
    #row = res.first()


async def join_the_player(game: Game, userid: int, language: str, session: SessionDep) -> dict:
    player_id:int = 0
    game_id:int = 0
    time_left:int = settings.game_duration
    try:
        # game_dict = await find_the_running_game(session)
        game_id = game.id 
        if game_id == 0:
            logger.error(f"No running game found when trying to involve user {userid} in a game")
            return {"result": False, "reason": "GAME_NOT_FOUND"}
        else:
            time_left = game.getTimeLeft()
            if time_left < 11:
                logger.warning(f"Current game is about to exceed its time. Cannot join {userid} to this game")
                return {"result": False, "reason": "GAME_ABOUT_TO_EXCEED"}

        sql = text("""
            SELECT p.id, p.finished, 
            EXISTS(SELECT 1 FROM players pp WHERE pp.gameid = :gameid1 AND pp.finished IS NOT NULL AND pp.id <> p.id) AS another_won 
            FROM players p WHERE p.userid = :userid AND p.gameid = :gameid2 LIMIT 1
        """)
        res = await session.execute(sql, {"gameid1": game_id, "userid": userid, "gameid2": game_id})
        row = res.first()
        if row:
            if not row.finished:
                if not row.another_won:
                    if not game.playerExists(userid):
                        game.addPlayer(userid)
                    return {"result": True, "player_id": row.id, "time_left": time_left, "attempts": game.getUserAttempts(userid), "hints": game.hintsCount(userid)}
                else:
                    logger.warning(f"Unable to join the game {game_id}! Another player won the game")
                    return {"result": False, "reason": "ANOTHER_PLAYER_WON"}
            else:
                logger.error(f"Unable to join the FINISHED game {game_id}!")
                return {"result": False, "reason": "GAME_FINISHED"}
        else:
            logger.info(f"User {userid} is NOT joined to game {game_id} - START joining!")
            #  Only THE FIRST player joined the game can define the next game language (next secret word language) 
            # sql = text("SELECT count(*) AS cnt FROM players WHERE gameid = :gameid")
            # result = await session.execute(sql, {"gameid": game_id})
            # row = result.first()
            if game.participants() == 0:
                settings.language = language  # language in settings define the secret word language

            # Try to insert new player record (to involve user in the game)
            game.addPlayer(userid)
            try:
                sql = text("INSERT INTO players (userid, gameid, attempts, hints) VALUES (:userid, :gameid, 0, 0) RETURNING id")
                res = await session.execute(sql, {"userid": userid, "gameid": game_id})
                await session.commit()    
                row = res.first()
                if row:
                    logger.success(f"User {userid} was added as a player to game {game_id}")
                    return {"result": True, "player_id": player_id, "time_left": time_left, "attempts": 0, "hints": 0}
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


async def the_game_state_update(userid: int, game_id: int, word: str, secret_word:str, similarity_percent: float, session: SessionDep, game: Game) -> bool:
    enddate:datetime|None = None
    try:
        game.addUAttempt(userid)

        # Если игрок угадал слово - он победитель и игра завершена!
        if similarity_percent > 99.0:
            game.finish(userid)
            word_length: int = (len(secret_word) - 3) * 10
            sql_query = text("""
                INSERT INTO winners(userid, gameid, scores) SELECT :userid, :gameid, (100 + (:word_length - :hints * 10));
            """)
            await session.execute(sql_query, {"userid": userid, "gameid": game_id, "word_length": word_length, "hints": game.hintsCount(userid)})
            enddate = datetime.now()
            logger.info(f"записываются в БД данные победителя {userid} - он победил в игре {game_id}, угадал слово {secret_word}")

        # записываем результаты очередного хода игрока
        logger.info(f"Записываются в БД результаты очередного хода игрока {userid} в игре {game_id}, он ввел слово {word} для слова {secret_word}. similarity = {similarity_percent}")

        if enddate:
            sql_query = text("UPDATE players SET attempts=attempts+1, finished=LOCALTIMESTAMP WHERE userid=:userid AND gameid=:gameid")
        else:    
            sql_query = text("UPDATE players SET attempts=attempts+1 WHERE userid=:userid AND gameid=:gameid")
        await session.execute(sql_query, {"userid": userid, "gameid": game_id})

        #if similarity_percent <= 99.0:
        #    sql_query = text("INSERT INTO sessions(playerid, word, similarity_score) SELECT :playerid, :word, :similarity_score")
        #    await session.execute(sql_query, {"playerid": player_id, "word": word, "similarity_score": similarity_percent})
        
        await session.commit()
        logger.success("Данные очередного хода успешно записаны в БД")
        return True
    except Exception as e:
        await session.rollback() 
        logger.exception("Ошибка записи в БД данных очередного хода")
        raise HTTPException(status_code=500, detail="Ошибка записи в БД данных очередного хода")


async def create_new_game(session_factory: async_sessionmaker, app_state, force: bool) -> bool:
    ret_result: bool = False 
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
                    SELECT w.id, w.word, (SELECT c.image FROM categories c WHERE c.id=w.cat_id LIMIT 1) AS image FROM words w
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
                logger.info(f"Текущая (старая) игра {old_game_id} завершена!")

                if old_game_id or force:
                    # Фиксируем НОВУЮ игру в таблице games
                    insert_game_query = text("INSERT INTO games (secret_word_id, language) VALUES (:word_id, :lang)  RETURNING id")
                    result = await session.execute(insert_game_query, {"word_id": word_data.id, "lang": language})
                    new_game_id = result.scalar()
                    app_state.game.start(new_game_id, word_data.word, language, word_data.image)
                await session.commit() 

                if new_game_id:
                    logger.success(f"ํНовая игра {new_game_id} создана!")        
                    ret_result = True
                else:
                    logger.warning(f"Нельзя завершить игру {old_game_id}, которая только-что началась!")
                    ret_result = False
            except Exception as e:
                await session.rollback() 
                logger.exception("Ошибка при создании новой игры!")
                return False
    # Создаем асинхронную задачу удаления старых игроков
    task = asyncio.create_task(erase_old_players(old_game_id))
    background_tasks_pool.add(task)    
    task.add_done_callback(background_tasks_pool.discard)
            
    return ret_result 


async def erase_old_players(old_game_id: int) -> bool:
    async with new_session() as session: 
        try:
            await session.execute(text("DELETE FROM players WHERE gameid < :gameid - 1"), {"gameid": old_game_id})
            await session.commit()
            return True    
        except Exception as e:
            logger.exception("Ошибка удалении старых игроков!")
            return False


# async def get_the_game_statistic(userid, session: SessionDep):    
#     if userid == 0:    
#         status_query = text("""
#             SELECT 
#                 g.id AS game_id,
#                 g.started,  
#                 COUNT(DISTINCT p.userid) AS total_participants,
#                 COALESCE(SUM(p.attempts), 0) AS total_attempts,
#                 COALESCE((SELECT ww.word FROM words ww INNER JOIN games gg ON ww.id=gg.secret_word_id WHERE gg.finished IS NOT NULL ORDER BY gg.id DESC LIMIT 1), '') as last_word, 
#                 MIN(EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER) AS seconds_passed,
#                 EXISTS(SELECT 1 FROM winners w WHERE w.gameid=g.id) AS win
#             FROM games g LEFT JOIN players p ON p.gameid = g.id
#             WHERE g.id = (SELECT MAX(z.id) FROM games z)
#             GROUP BY g.id
#         """)    
#         res = await session.execute(status_query)
#     else:    
#         status_query = text("""
#             SELECT 
#                 g.id AS game_id,
#                 g.started,  
#                 1 AS total_participants,
#                 COALESCE(p.attempts, 0) AS total_attempts,
#                 w.word AS secret_word, 
#                 LENGTH(w.word) as word_len, 
#                 w.language, 
#                 EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER AS seconds_passed, 
#                 COALESCE((SELECT ww.word FROM words ww INNER JOIN games gg ON ww.id=gg.secret_word_id WHERE gg.id=(g.id - 1) LIMIT 1), '') as last_word, 
#                 COALESCE(c.image, 'others.jpg') AS image                                  
#             FROM games g INNER JOIN words w ON g.secret_word_id=w.id INNER JOIN categories c ON w.cat_id=c.id
#             LEFT JOIN players p ON p.gameid = g.id AND p.userid=:userid
#             WHERE g.finished IS NULL
#             LIMIT 1
#         """)    
#         res = await session.execute(status_query, {"userid": userid})
#     return res.fetchone()


async def check_the_game_duration(session_factory: async_sessionmaker, app_state):  
    currentGame: Game = app_state.game
    logger.info(f"Ищем просроченную игру")
    # found: bool = False   
    # async with session_factory() as session:
    #         query = text("""
    #             SELECT g.id FROM games g 
    #             WHERE g.finished IS NULL AND (((EXTRACT(EPOCH FROM (LOCALTIMESTAMP - g.started))::INTEGER) >= :seconds) OR EXISTS(SELECT 1 FROM winners w WHERE w.gameid=g.id));
    #         """)
    #         result = await session.execute(query, {"seconds": (settings.game_duration - 10)})
    #         row = result.first()
    #         if row:
    #             found = True      
    if currentGame.getTimeLeft() < 10:
        logger.success(f"Найдена 'устаревшая' игра !!!  {settings.game_duration}")
        await create_new_game(session_factory, app_state, False)  


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

         


          

