from datetime import datetime, timedelta

from config import settings


class Game:
    id: int
    startTime:datetime
    currWord: str = ""
    lastWord: str = ""
    nextWord: str = ""
    language: str
    image: str 
    word_len: int

    def __init__(self, id: int, currWord: str = "", language: str = "en", image: str = "others.jpg"):
        if self.currWord != "":
            self.lastWord = self.currWord
        if self.nextWord != "":    
            self.currWord = self.nextWord
        else:        
            self.currWord = currWord     
        self.id = id    
        self.language = language
        self.image = image
        self.word_len = len(currWord)
        self.startTime = datetime.now()

    def getRunningTime(self) -> int:
        period:timedelta = datetime.now() - self.startTime
        return  round(period.total_seconds())   

    def getTimeLeft(self) -> int:
        period:timedelta = datetime.now() - self.startTime
        return settings.game_duration - round(period.total_seconds())   

