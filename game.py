from datetime import datetime, timedelta

from config import settings


class Game:
    """ Хранит в памяти текущую игру """

    def __init__(self):
        self.id: int = 0
        self.prev_id: int = 0
        self.prev_winner:int = 0 
        self.winner: int = 0
        self.startTime:datetime = datetime.now()
        self.currWord: str = ""
        self.lastWord: str = ""
        self.language: str
        self.image: str = ""
        self.word_len: int = 0 
        self.players: dict[int, int] = {}
        self.hints: dict[int, int] = {}

    def start(self, id: int, currWord: str = "", language: str = "en", image: str = "others.jpg"):
        """ Creates NEW Game """
        self.prev_id = self.id
        self.id = id
        if self.currWord != "":
            self.lastWord = self.currWord
        self.currWord = currWord     
        self.language = language
        self.image = image
        self.word_len = len(currWord)
        self.prev_winner = self.winner
        self.winner = 0
        self.startTime = datetime.now()
        self.players = {}
        self.hints = {}

    def finish(self, winner:int) -> int:
        """ Finishes current Game """
        self.prev_id = self.id
        self.id = 0
        self.winner = winner
        return self.prev_id

    def getRunningTime(self) -> int:
        """ Возвращает промежуток времени в секундах, в течение которого текущая игра уже существует """
        if self.id > 0:
            period:timedelta = datetime.now() - self.startTime
            return round(period.total_seconds())   
        else:
            return 0

    def getTimeLeft(self) -> int:
        """ Возвращает промежуток времени в секундах до расчетного момента завершения игры (сколько осталось до момента, когда игру нужно принудительно завершить) """
        if self.id > 0:
            period:timedelta = datetime.now() - self.startTime
            return settings.game_duration - round(period.total_seconds())   
        else:
            return 0

    def thisGameExists(self, game_id: int) -> bool:
        return True if self.id > 0 and self.id == game_id else False

    def anotherPlayerWon(self, game_id: int, user_id: int) -> bool:
        return True if self.id == 0 and self.prev_id == game_id and self.winner != user_id else False

    def isFinished(self) -> bool:
        return True if self.id == 0 else False    

    def addPlayer(self, user_id: int):
        self.players[user_id] = 0 

    def addUAttempt(self, user_id: int):
        self.players[user_id] = self.players.get(user_id, 0) + 1    

    def getUserAttempts(self, user_id: int) -> int:
        return self.players.get(user_id, 0)

    def playerExists(self, user_id: int) -> bool:
        return True if self.id > 0 and user_id in self.players else False

    def participants(self) -> int:
        return len(self.players)

    def totalAttempts(self) -> int:
        return sum(self.players.values())
           
    def addHints(self, user_id: int, cnt: int):
        self.hints[user_id] = cnt 

    def hintsCount(self, user_id: int) -> int:
        return self.hints.get(user_id, 0)
