"""Veritabanı bağlantısı ve küçük şema yükseltici.

Şema yönetimi: ``init_db`` önce ``create_all`` ile eksik tabloları oluşturur, ardından mevcut
tablolarda modelde olup veritabanında olmayan sütunları ``ALTER TABLE ... ADD COLUMN`` ile
ekler (önceki prototipten kalan ``finsight.db`` dosyaları bozulmasın diye): NULL olabilir
sütunlar doğrudan, skaler Python varsayılanı olan NOT NULL sütunlar ``NOT NULL DEFAULT <değer>``
ile (mevcut satırlar bu değeri alır). Alembic geçişi Aşama 4'e bırakılmıştır
(docs/entegrasyon-plani.md).
"""

from __future__ import annotations

import logging

from sqlalchemy import Column, create_engine, event, inspect, literal, text
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


def _scalar_default_sql(column: Column, engine: Engine) -> str | None:
    """Sütunun skaler Python varsayılanını SQL sabiti olarak derler (ör. ``1``, ``'queued'``,
    ``0.0``); varsayılan yoksa veya çağrılabilirse (``utcnow``) None."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = getattr(default, "arg", None)
    if value is None:
        return None
    compiled = literal(value, type_=column.type).compile(
        dialect=engine.dialect, compile_kwargs={"literal_binds": True})
    return str(compiled)


def upgrade_schema(engine: Engine) -> list[str]:
    """Mevcut tablolarda eksik sütunları ekler; eklenenleri 'tablo.sütun' döndürür.

    - NULL olabilir sütun: ``ADD COLUMN ad tip``.
    - NOT NULL + skaler Python varsayılanı (ör. ``default=0``, ``default="queued"``):
      ``ADD COLUMN ad tip NOT NULL DEFAULT <sabit>`` (O3).
    - NOT NULL ve varsayılansız (veya çağrılabilir varsayılanlı) sütun eklenemez; uyarı yazılır.
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
            col_type = column.type.compile(dialect=engine.dialect)
            suffix = ""
            if column.primary_key:
                log.warning("Eksik birincil anahtar sütunu eklenemedi: %s.%s (elle geçiş gerekir)",
                            table.name, column.name)
                continue
            if not column.nullable:
                default_sql = _scalar_default_sql(column, engine)
                if default_sql is None:
                    log.warning("Eksik NOT NULL sütun eklenemedi: %s.%s (varsayılan değer yok; "
                                "elle geçiş gerekir)", table.name, column.name)
                    continue
                suffix = f" NOT NULL DEFAULT {default_sql}"
            ddl = (f"ALTER TABLE {preparer.quote(table.name)} "
                   f"ADD COLUMN {preparer.quote(column.name)} {col_type}{suffix}")
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
