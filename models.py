from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from sqlalchemy import DateTime, func


class Base(DeclarativeBase):
    pass

class UserOrm(Base):
    __tablename__ = "users"
    userid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(20), index=True, unique=True)
    password: Mapped[str] = mapped_column(String(60))
    rate: Mapped[int] = mapped_column(Integer, nullable=False, default=0) 
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now(),
        index=True
    )   


class GameOrm(Base):
    __tablename__ = "games"   
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  
    secret_word_id: Mapped[int] = mapped_column(Integer, ForeignKey('words.id', ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(2))
    started: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), index=True) 
    finished: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=True)


class PlayerOrm(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    userid: Mapped[int] = mapped_column(ForeignKey('users.userid', ondelete="CASCADE"), index=True) 
    gameid: Mapped[int] = mapped_column(ForeignKey('games.id', ondelete="CASCADE"), index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), index=True) 
    finished: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=True)    

    __table_args__ = (
        UniqueConstraint('gameid', 'userid', name='uq_game_user'),
    )
    

class SessionOrm(Base):
    __tablename__ = "sessions" 
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)      
    playerid: Mapped[int] = mapped_column(ForeignKey('players.id', ondelete="CASCADE"), index=True) 
    word: Mapped[str] = mapped_column(String(50))
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class WordOrm(Base):
    __tablename__ = "words"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)   
    word: Mapped[str] = mapped_column(String(50))
    # Задаем векторную колонку. Размерность 384 — для модели MiniLM-L12-v2
    embedding: Mapped[list] = mapped_column(Vector(384), nullable=False)   
    # метаданные для фильтрации (язык)
    language: Mapped[str] = mapped_column(String(2))

    __table_args__ = (
        Index(
            'idx_words_embedding_cosine', 
            embedding,                   
            postgresql_using='hnsw',      
            postgresql_ops={'embedding': 'vector_cosine_ops'},
            postgresql_with={'m': 16, 'ef_construction': 64}
        ),
    )


class WinnerOrm(Base):
    __tablename__ = "winners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True) 
    userid: Mapped[int] = mapped_column(ForeignKey('users.userid', ondelete="CASCADE"), index=True) 
    gameid: Mapped[int] = mapped_column(ForeignKey('games.id', ondelete="CASCADE"), index=True)
    scores: Mapped[int] = mapped_column(nullable=False, default=0) 

    
class DescriptOrm(Base):
    __tablename__ = "descriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)   
    word: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(2000))