"""Veritabanı bağlantısı ve küçük şema yükseltici.

Şema yönetimi: ``init_db`` önce ``create_all`` ile eksik tabloları oluşturur, ardından mevcut
tablolarda modelde olup veritabanında olmayan NULL olabilir sütunları
``ALTER TABLE ... ADD COLUMN`` ile ekler (önceki prototipten kalan ``finsight.db`` dosyaları
bozulmasın diye). Alembic geçişi Aşama 4'e bırakılmıştır (docs/entegrasyon-plani.md).
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_database_url
from .models import Base

log = logging.getLogger(__name__)

_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:", "sqlite+pysqlite://",
                "sqlite+pysqlite:///:memory:"}


def _sqlite_pragmas(wal: bool):
    """SQLite bağlantı ayarları: yabancı anahtar zorlaması, kilit bekleme süresi ve dosya
    veritabanında WAL (arka plan işi yazarken API okuyabilsin)."""

    def on_connect(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            if wal:
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return on_connect


def make_engine(url: str | None = None) -> Engine:
    """SQLAlchemy motoru. SQLite'ta iş parçacıkları arası kullanım açılır; bellek içi
    veritabanında tek bağlantı (StaticPool) paylaşılır ki arka plan işi aynı veriyi görsün."""
    url = url or get_database_url()
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        in_memory = url in _MEMORY_URLS or ":memory:" in url
        if in_memory:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        event.listen(engine, "connect", _sqlite_pragmas(wal=not in_memory))
        return engine
    return create_engine(url, pool_pre_ping=True)


def upgrade_schema(engine: Engine) -> list[str]:
    """Mevcut tablolarda eksik NULL olabilir sütunları ekler; eklenenleri 'tablo.sütun' döndürür.

    NOT NULL olup eksik olan sütunlar eklenmez (varsayılan değer gerektirir); uyarı yazılır.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    preparer = engine.dialect.identifier_preparer
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable or column.primary_key:
                log.warning("Eksik NOT NULL sütun eklenemedi: %s.%s (elle geçiş gerekir)",
                            table.name, column.name)
                continue
            col_type = column.type.compile(dialect=engine.dialect)
            ddl = (f"ALTER TABLE {preparer.quote(table.name)} "
                   f"ADD COLUMN {preparer.quote(column.name)} {col_type}")
            with engine.begin() as conn:
                conn.execute(text(ddl))
            added.append(f"{table.name}.{column.name}")
            log.info("Şema yükseltildi: %s.%s eklendi", table.name, column.name)
    return added


def init_db(engine: Engine) -> list[str]:
    """Tabloları oluşturur ve eski şemayı yükseltir; eklenen sütunları döndürür."""
    Base.metadata.create_all(engine)
    return upgrade_schema(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
