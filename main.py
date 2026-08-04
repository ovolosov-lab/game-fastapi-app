import asyncio
import json
from urllib.parse import quote, unquote
import os
from typing import Annotated
from fastembed import TextEmbedding
# from llama_cpp import Llama
import numpy as np
from sqlalchemy.sql import func
from contextlib import asynccontextmanager
import uvicorn
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from fastapi import Cookie, FastAPI, Form, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

from config import BASE_DIR, ERROR_MESSAGES_EN, ERROR_MESSAGES_RU, MODEL_PATH, settings, logger
from database import SessionDep, check_the_game_duration, check_the_player_involved, check_user, create_new_game, engine, create_all_tables, db_connection_check, get_hint, get_players_stats, get_the_game_statistic, user_exists, the_game_state_update, new_session
from schemas import GuessRequest, GuessResponse, HintCache, NewUser, User, UserInfo, WordsDataInfo
from services import AsyncPeriodicTask, create_new_user, get_err_message, load_internationalization_data
from tokens import create_access_token, get_current_user
from words import SUCCESS_THRESHOLD_PERCENT, detect_language, fill_words_list, generate_embedding, unload_words_list


templates: Jinja2Templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# Lifespan function to perform startup and shutdown tasks, such as checking the database connection, 
# creating tables, starting background tasks for checking users activity and 
# closing the database connection pool at shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_connection_check()
    await create_all_tables()

    app.state.process_lock = asyncio.Lock()
    app.state.stored_hint = HintCache(result="NO", word="", first_letter="", second_letter="", last_letter="", analogues=[], ai="", anagram="", gameid=0, size=0)     

    periodic_task = AsyncPeriodicTask(interval=settings.check_interval, task_func=lambda: check_the_game_duration(new_session, app.state))
    periodic_task.start()    

    logger.info("Загрузка ML моделей...")
    app.state.embedder = TextEmbedding(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    try:
        # app.state.llm = Llama(model_path=str(MODEL_PATH), n_ctx=1024, n_threads=4)
        app.state.llm = AsyncOpenAI(
            base_url = settings.ai_api_path, 
            api_key = settings.ai_api_key 
        )    
        logger.success(f"Инициализация клиента для ML моделей завершена!  {settings.ai_api_path}")
        app.state.ai_enabled = True 
    except Exception as e:
        app.state.ai_enabled = False
        logger.exception("Ошибка загрузки модели")
    

    await create_new_game(new_session, app.state, True)

    yield

    await periodic_task.stop()
    await engine.dispose()

    if app.state.ai_enabled:
        app.state.llm.close()

app = FastAPI(lifespan=lifespan)


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint to verify that the application is running and can connect to the database
@app.get("/health")
async def health_check() -> JSONResponse:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse(content={"status": "ok"})   
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service Unavailable")
    

# Get the authorization page with the form for entering username and password, and also with the flash message if there is an error during the previous authorization attempt 
@app.get("/", tags=["Game", "auth"], summary="Game auth page") 
async def auth_page(request: Request, flash_msg: str | None = Cookie(None), i18n_data: dict = Depends(lambda: load_internationalization_data(settings.language))):
    decoded_msg = unquote(flash_msg) if flash_msg else None
    data: dict = {"flash_msg": decoded_msg} if decoded_msg else {}
    response = templates.TemplateResponse(request, "index.html", {"request": request, **i18n_data, **data})
    if flash_msg:
        response.delete_cookie(key="flash_msg")
    return response


# Get the registration page
@app.get("/users/reg", tags=["Game", "new user"], summary="Game new user registration page")
async def regstration_page(request: Request, i18n_data: dict = Depends(lambda: load_internationalization_data(settings.language))):
    return templates.TemplateResponse(request, "reg.html", {"request": request, **i18n_data})


# User authorization and token generation, setting http-only cookie with the token and redirecting to the main page if authorization is successful, otherwise - redirecting to the auth page with error message in cookie
@app.post("/users/auth",  tags=["Game", "user authorization"], summary="๊")
async def user_auth(user: Annotated[User, Form()], session: SessionDep) -> RedirectResponse:
    userid: int = await check_user(user.username, user.password, session) 
    if (userid > 0):
        token: str = create_access_token(data={"username": user.username, "userid": str(userid), "language": user.lang.value})
        # set http-only token in cookie and redirect to main page
        response = RedirectResponse(url="/game/home/", status_code=303)
        response.set_cookie(key="access_token", value=token, httponly=True)
        logger.success(f'Пользователь "{user.username}" успешно авторизовался в программе. Язык {user.lang.value}')
        return response
    else:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=quote(get_err_message("authorization_error","ошибка авторизации пользователя", user.lang.value)), httponly=True)
        return response


# Create a new user with the registration form data, checking the password confirmation and the uniqueness of the username, 
# setting error message in cookie and redirecting to the auth page if something is wrong, otherwise - creating a new user, his token and redirecting to the main page
@app.post("/users/add",  tags=["Game", "new user"], summary="Add a new user")
async def add_user(new_user: Annotated[NewUser, Form()], session: SessionDep) -> RedirectResponse:
    if (new_user.password1 == new_user.password2):
        if (await user_exists(new_user.username, session)):
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(key="flash_msg", value=quote(get_err_message("username_taken", "Это имя пользователя уже занято", settings.language)), httponly=True)
            return response
        else:
            if new_user.secret == settings.friend_reference:  
                return await create_new_user(new_user, session)
            else:  # введенное секретное слово не совпадает с правильным из настроек 
                response = RedirectResponse(url="/", status_code=303)
                response.set_cookie(key="flash_msg", value=quote(get_err_message("secret_word", "Секретное слово неверное", settings.language)), httponly=True)
                return response
    else:
        response = RedirectResponse(url="/game/home/", status_code=303)
        return response


# Get the Game main page (a single-page application that receives all the data for a given application via asynchronous requests to the FastApi backend and updates the page dynamically without reloading) 
@app.get("/game/home/", tags=["Game", "home page"], summary="Welcome to the Game home page")
async def home_page(session: SessionDep, request: Request, current_user: UserInfo = Depends(get_current_user)):
    i18n_data: dict = load_internationalization_data(current_user.lang)
    player_data: dict = await check_the_player_involved(current_user.userid, 0, current_user.lang, False, session)
    data = {"username": current_user.username, "joined_the_game": "True" if player_data["result"] == True else "False", "language": current_user.lang}
    return templates.TemplateResponse(request, "game.html", {"request": request, **data, **i18n_data})


@app.post("/words/add",  tags=["Game", "word list"], summary="Initial formation of the word list")
async def initial_fill_words_list(dataInfo: WordsDataInfo, session: SessionDep, request: Request, current_user: UserInfo = Depends(get_current_user)) -> RedirectResponse:
    if current_user.username == "admin":
        await fill_words_list(dataInfo.filename, dataInfo.lang, request.app.state.embedder, session)
    response = RedirectResponse(url="/game/home/", status_code=303)    
    return response


@app.post("/words/unload",  tags=["Game", "word list, unload"], summary="Unload the word list")
async def unload_words(dataInfo: WordsDataInfo, session: SessionDep, request: Request, current_user: UserInfo = Depends(get_current_user)) -> RedirectResponse:
    if current_user.username == "admin":
        await unload_words_list(dataInfo.filename, dataInfo.lang, session)
    response = RedirectResponse(url="/game/home/", status_code=303)    
    return response


@app.post("/game/guess/", tags=["Game", "game session", "guess"], summary="Player makes his guess")
async def make_guess(
    payload: GuessRequest, 
    request: Request, 
    session: SessionDep,
    current_user: UserInfo = Depends(get_current_user) 
):
    if payload.gameid == 0:
        return GuessResponse(status = "CLOSED", word = "", similarity = 0, is_correct = False, attempts = 0)
       
    similarity_percent:float = 0.0
    is_correct:bool = False
    attempts:int = 0

    player_data:dict = await check_the_player_involved(current_user.userid, payload.gameid, current_user.lang, False, session)
    if player_data["result"]:
        # Кодируем guessed word игрока
        guessing_lang: str = detect_language(payload.word)
        user_embedd_vec = generate_embedding(payload.word, guessing_lang, request.app.state.embedder)  

        # Получаем ИД игрока, кол-во попыток. Считаем расстояние в БД и переводим в проценты схожести
        sql_query = text("""
            SELECT g.id AS game_id, w.embedding, w.word as secret_word, w.language
            FROM games g JOIN words w ON w.id = g.secret_word_id
            WHERE g.id=:gameid AND g.finished IS NULL
            LIMIT 1
        """)  
        result = await session.execute(sql_query, {"gameid": payload.gameid}) 
        game_data = result.fetchone()
        if not game_data:
            return GuessResponse(status = "CLOSED", word = "", similarity = 0, is_correct = False, attempts = 0)

        if (game_data.secret_word == payload.word):
            similarity_percent = 100.0
            is_correct = True
        else:    
            db_embedding = game_data.embedding
            if isinstance(db_embedding, str):
                word_vec_list = json.loads(db_embedding)
            else:
                word_vec_list = db_embedding
            word_vec = np.asarray(word_vec_list, dtype=np.float64)

            dot_product = np.dot(user_embedd_vec, word_vec)
            norm_user = np.linalg.norm(user_embedd_vec)
            norm_word = np.linalg.norm(word_vec)
            if norm_user == 0 or norm_word == 0:
                cos_distance = 1.0  # Слова абсолютно не похожи
            else:
                cos_distance = float(1.0 - (dot_product / (norm_user * norm_word)))

            if cos_distance is None:
                raise HTTPException(status_code=404, detail=f"Secret word {game_data.secret_word} was not found.")

            similarity_percent = max(0.0, min(100.0, (1.0 - cos_distance) * 100.0))
            success_treshold: float = SUCCESS_THRESHOLD_PERCENT
            if guessing_lang == game_data.language: 
                success_treshold = 98.0

            is_correct = similarity_percent >= success_treshold
            if is_correct:
                similarity_percent = 100

        # Обновляем состояние игры
        await the_game_state_update(current_user.userid, game_data.game_id, player_data["player_id"], payload.word, game_data.secret_word, similarity_percent, request.app.state, session)

        attempts = player_data["attempts"] + 1

        return GuessResponse(status = "OK", word = payload.word, similarity = round(similarity_percent, 0), is_correct = is_correct, attempts = attempts)
    else:
        return GuessResponse(status = "CLOSED", word = "", similarity = 0, is_correct = False, attempts = 0)


@app.get("/game/stats/", tags=["Game", "game session", "stats"], summary="Data for the current game statistic")
async def get_game_status(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    status:str = "active"
    participants:int = 0
    total_attempts:int = 0
    remaining_time:int = 0
    secret_word:str = ""
    game_stats = await get_the_game_statistic(0, session)
    players_rates = await get_players_stats(session)
    
    
    if not game_stats:
        status = "waiting" 
        game_id = 0
    else:
        game_id = game_stats.game_id
        participants = game_stats.total_participants
        total_attempts = game_stats.total_attempts
        secret_word = game_stats.last_word

        if game_stats.win:
            status = "finished"
            remaining_time = 0
        else:     
            remaining_time = settings.game_duration - game_stats.seconds_passed
            
        logger.info(f"{settings.game_duration} remaining game time (sec) = {remaining_time}")

        if remaining_time < 0:
            status = "waiting" 
            remaining_time = 0   
    
    return {
        "status": status,
        "game_id": game_id,
        "participants": participants,
        "total_attempts": total_attempts,
        "seconds_left": remaining_time, 
        "last_secret_word": secret_word, 
        "remaining_time": remaining_time,
        "players": list(players_rates)
    }


@app.post("/game/join/", tags=["Game", "game session", "join"], summary="Join the game")
async def join_the_game(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    player_data:dict = await check_the_player_involved(current_user.userid, 0, current_user.lang, True, session)
    if player_data["result"]:
        game_stats = await get_the_game_statistic(current_user.userid, session)
        
        if not game_stats:
            return {"status": "waiting", "message": get_err_message("game_beginning", "he new game is about to start...", current_user.lang)}
        
        remaining_time = settings.game_duration - game_stats.seconds_passed 
            
        logger.info(f"{settings.game_duration} remaining game time (sec) = {remaining_time}")

        if remaining_time < 11: 
            return {"status": "waiting", "game_id": 0, "message": get_err_message("game_beginning", "he new game is about to start...", current_user.lang) }

        return {
            "status": "active",
            "game_id": game_stats.game_id,
            "word_len": game_stats.word_len,
            "participants": game_stats.total_participants,
            "total_attempts": game_stats.total_attempts,
            "started": game_stats.started,
            "seconds_left": remaining_time,
            "language": game_stats.language
        } 
    else:
        return {"status": "waiting", "message": get_err_message("game_beginning", "he new game is about to start...", current_user.lang)}



@app.get("/game/help/{game_id}", tags=["Game", "game session, hinsts", "hint"], summary="Get the Hint")
async def get_the_help(request: Request, game_id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):  
    return await get_hint(game_id, current_user.userid, current_user.lang, session, request)     


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(conn: ConnectionAbortedError, exc: HTTPException):
    if exc.status_code == 401:
        logger.info(exc.detail if exc.detail else "Error occurred during user authentication. Redirecting to '/'")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=quote(exc.detail if exc.detail else "Please login first"), httponly=True)
        return response 


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse | RedirectResponse:
    readable_errors = []
    
    for error in exc.errors():
        err_type = error['type']
        err_field = None
        template = ""

        if settings.language == "ru":
            template = ERROR_MESSAGES_RU.get(err_type, error['msg'] if error['msg'] else " Ошибка валидации данных")
        else:
            template = ERROR_MESSAGES_EN.get(err_type, error['msg'] if error['msg'] else "Validation error")

        if err_type.startswith("value_error") or err_type.startswith("type_error"):
                err_field = ",".join(str(loc) for loc in error['loc'])
        err_field = "" if err_field is None else err_field.replace("body,","").replace("query,","").replace("path,","").replace("header,","").replace("cookie,","")

        readable_errors.append(template + (f" ({err_field})" if err_field != ""  else ""))

    final_msg = " | ".join(readable_errors)

    accept_header = request.headers.get("accept", "")
    
    if "application/json" in accept_header:
        return JSONResponse(
            status_code=422,
            content={"result": "error", "details": final_msg}
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="flash_msg", value=quote(final_msg), httponly=True)  
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Произошла непредвиденная ошибка!") 
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )


if __name__ == "__main__":    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)