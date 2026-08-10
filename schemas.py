import re
from typing import Annotated

from annotated_types import Gt
import bleach
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, field_validator
from datetime import date

from enum import Enum


clean_before = BeforeValidator(lambda v: bleach.clean(str(v or ''), strip=True).strip())
clean_before_bi = BeforeValidator(lambda v: bleach.clean(str(v or ''), tags=['b', 'i'], strip=True).strip())

def validate_not_past(v: date) -> date:
    if v < date.today():
        raise ValueError('date_in_past')
    return v

FutureDate = Annotated[date, AfterValidator(validate_not_past)]

class Language(str, Enum):
    EN = "en"
    FR = "fr"
    RU = "ru"

class User(BaseModel):
    username: Annotated[str, clean_before, Field(min_length=3, max_length=20)]   
    password: Annotated[str, AfterValidator(str.strip), Field(min_length=6, max_length=20)]    
    lang: Annotated[Language, Field(description="Язык интерфейса и секрктного слова игры")]      

class NewUser(BaseModel):
    username: Annotated[str, clean_before, Field(min_length=3, max_length=20)]    
    secret: Annotated[str, AfterValidator(str.strip), Field(min_length=2, max_length=100)]   
    password1: Annotated[str, AfterValidator(str.strip), Field(min_length=6, max_length=20)]     
    password2: Annotated[str, AfterValidator(str.strip), Field(min_length=6, max_length=20)]     
    
class UserInfo(BaseModel):
    userid: Annotated[int, Gt(0)]
    username: Annotated[str, clean_before, Field(min_length=2, max_length=20)] 
    lang: Annotated[str, Field(min_length=2, max_length=2)]

class WordsDataInfo(BaseModel):
    filename: Annotated[str,  Field(min_length=3, max_length=20)]
    lang: Annotated[str, Field(min_length=2, max_length=2)]

class GuessRequest(BaseModel):
    gameid: Annotated[int, Field(description="current game id")] 
    word: Annotated[str, clean_before, Field(min_length=1, max_length=40, description="Слово-догадка")]

    @field_validator("word")
    @classmethod
    def clean_and_validate_word(cls, v: str) -> str:
        # Дополнительный шаг: оставляем только буквы разных алфавитов и дефисы (актуально для фр. и рус.)
        # Убираем знаки препинания, цифры, скобки, эмодзи
        cleaned_word = re.sub(r"[^\w\s-]", "", v, flags=re.UNICODE)
        
        # Переводим в нижний регистр прямо на этапе валидации Pydantic
        final_word = cleaned_word.strip().lower()
        
        if not final_word:
            raise ValueError("Слово должно содержать только буквы")
            
        return final_word    
    
class GuessResponse(BaseModel):
    status: str = Field(..., description="Статус текущей игры") 
    word:str = Field(..., description="Загаданное слово")
    similarity: float = Field(..., description="Процент сходства (0.0 - 100.0)")
    is_correct: bool = Field(..., description="Угадано ли слово на 100%")
    attempts: int = Field(..., description="Текущее количество попыток игрока")   
    reason: str = Field(..., description="Причина отказа")  

class GameStartResponse(BaseModel):
    message: str = Field(..., description="Статус начала игры")
    status: str = Field("active", description="Статус сессии")   

class HintResponse(BaseModel):
    result: Annotated[str, Field(..., description="kind")]
    first_letter: Annotated[str, Field(min_length=0, max_length=1, description="Первая буква загаданного слова")]   
    second_letter: Annotated[str, Field(min_length=0, max_length=1, description="Вторая буква загаданного слова")]   
    last_letter: Annotated[str, Field(min_length=0, max_length=1, description="Первая буква загаданного слова")]   
    analogues: Annotated[list, Field(..., description="Список из трех слов - аналогов")] 
    ai: Annotated[str, Field(min_length=0, max_length=2000, description="Описание слова, сформулированное ИИ")]  
    anagram: Annotated[str, Field(min_length=0, max_length=20, description="Anagram")]

class HintCache(HintResponse):
    word: Annotated[str, Field(min_length=0, max_length=20, description="Загаданное слово")]   
    gameid: Annotated[int, Field(description="current game id")]   
    size: int



class WordRequest(BaseModel):
    word: str
    language: str