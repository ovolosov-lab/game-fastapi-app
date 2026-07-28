import asyncio
from typing import Callable
from functools import cache
import json
import os
from typing_extensions import Annotated
from fastapi.responses import RedirectResponse
from fastapi import File, Form, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy import TextClause, delete, text
from sqlalchemy import select, cast
from models import UserOrm
from database import SessionDep, check_user, db_add_record, user_exists
from config import BASE_DIR, ERROR_MESSAGES_EN, ERROR_MESSAGES_RU, settings, logger
from schemas import NewUser
from tokens import create_access_token, get_current_user


class AsyncPeriodicTask:
    def __init__(self, interval: int, task_func: Callable):
        self.interval: int = interval
        self.task_func: Callable = task_func
        self._task: asyncio.Task | None = None
        self._is_running: bool = False  # флаг - запущена-ли задача

    async def _run(self):
        while self._is_running:
            try:
                await self.task_func()
            except Exception as e:
                logger.error(f"Error in background task: {e}")
            await asyncio.sleep(self.interval)          # спим заданный интервал до след. запуска задачи

    def start(self):
        if not self._is_running:
            self._is_running = True
            # Создаем задачу в текущем Event Loop
            self._task = asyncio.create_task(self._run())
            logger.info(f"Background task started (interval {self.interval}s)")

    async def stop(self):
        if self._is_running:
            self._is_running = False
            if self._task:
                self._task.cancel()  # Прерываем asyncio.sleep
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("Background task stopped.")


async def create_new_user(new_user: Annotated[NewUser, Form()], session: SessionDep) -> RedirectResponse:
    newUserOrm = UserOrm(username=new_user.username, password=new_user.password1)
    session.add(newUserOrm)
    await session.commit() 
 
    userid: int = await check_user(new_user.username, new_user.password1, session) 
    token: str = create_access_token(data={"username": new_user.username, "userid": str(userid)})
    response: RedirectResponse = RedirectResponse(url="/game/home", status_code=303)
    response.set_cookie(key="access_token", value=token, httponly=True)
    return response


class ProtectedStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def get_response(self, path: str, scope) -> Response:
        try:
            current_user = await get_current_user(Request(scope))
            if current_user.userid > 0:
                return await super().get_response(path, scope)
            else:
                logger.warning(f"Attempting to access the file page without authorization")
                raise HTTPException(status_code=401, detail="Authorization error")  
        except:    
            raise HTTPException(status_code=401, detail="Authorization error")  
        

@cache
def load_internationalization_data(language: str) -> dict:
    i18n_file = os.path.join(BASE_DIR, f"locales/i18n_{language}.json")
    try:
        with open(i18n_file, "r", encoding="utf-8") as f:
            i18n_data = json.load(f)
        return i18n_data
    except OSError as e:
        logger.error(f"i18n file read error: {e}")
        raise HTTPException(status_code=500, detail="Error occurred while loading internationalization data")


def get_err_message(key: str, default: str, lang:str) -> str:
    if default == "": 
        default = key
    return ERROR_MESSAGES_EN.get(key, default) if lang == "en" else ERROR_MESSAGES_RU.get(key, default) 

