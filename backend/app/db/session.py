from functools import lru_cache

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Session:
    return Session(get_engine())
