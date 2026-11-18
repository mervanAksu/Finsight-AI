"""Veritabanı bağlantısı. Aşama 2'de create_all yerine Alembic göçleri kullanılacak."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import get_database_url
from .models import Base


def make_engine(url: str | None = None):
    url = url or get_database_url()
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
