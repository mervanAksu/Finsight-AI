"""Veritabanı tabloları (SQLAlchemy 2.0 tipli ORM).

Aşama 1 tabloları: company, filing, financial_fact (Aşama 3'te NULL olabilir sütunlar eklendi).
Aşama 3 tabloları: docs/entegrasyon-plani.md "Veritabanı" bölümü.

- JSON alanlar Text sütununda ``json.dumps(..., ensure_ascii=False)`` olarak saklanır.
- Gömme vektörleri LargeBinary (float32 ham bayt, ``embeddings.to_bytes``).
- Alt kayıtlar ON DELETE CASCADE ile bağlıdır; ``repository`` yeniden analizde eski
  bölüm/parça/gömme/duygu kayıtlarını açık DELETE ifadeleriyle siler (SQLite'ta yabancı anahtar
  zorlaması kapalı olsa bile tutarlı kalsın diye).
- Zaman damgaları UTC olarak, saat dilimi bilgisi olmadan saklanır.
"""

from datetime import date, datetime, timezone

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
                        LargeBinary, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Saat dilimsiz UTC zaman (veritabanında tutarlı saklama için)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- Aşama 1 tabloları


class Company(Base):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    ticker: Mapped[str | None] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    sic: Mapped[str | None] = mapped_column(String(8))
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                 onupdate=func.now())
    # Aşama 3
    sic_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exchanges: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON liste
    analysis_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    filings: Mapped[list["Filing"]] = relationship(back_populates="company")


class Filing(Base):
    __tablename__ = "filing"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), index=True)
    accession_no: Mapped[str] = mapped_column(String(25), unique=True)
    form: Mapped[str] = mapped_column(String(12), index=True)
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    report_date: Mapped[date | None] = mapped_column(Date)
    primary_document: Mapped[str | None] = mapped_column(String(255))
    items: Mapped[str | None] = mapped_column(String(255))
    is_xbrl: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str | None] = mapped_column(String(512))
    # Aşama 3: NULL eski kayıtlarda "analiz edilmedi" demektir.
    analyzed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)

    company: Mapped[Company] = relationship(back_populates="filings")


class FinancialFact(Base):
    __tablename__ = "financial_fact"
    __table_args__ = (
        UniqueConstraint("company_id", "metric", "period_end", name="uq_fact_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    tag: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Numeric(24, 2))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    fy: Mapped[int | None] = mapped_column(BigInteger)
    fp: Mapped[str | None] = mapped_column(String(4))
    form: Mapped[str] = mapped_column(String(12))
    accession_no: Mapped[str] = mapped_column(String(25))
    filed: Mapped[date] = mapped_column(Date)
    # Aşama 3: türetilmiş değerlerde kullanılan etiketler (JSON liste), aksi NULL.
    derived_from: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------- Aşama 3 tabloları


class FilingDocument(Base):
    """İndirilen ana belge (10-K/10-Q HTML) bilgisi."""

    __tablename__ = "filing_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filing.id", ondelete="CASCADE"),
                                           unique=True)
    local_path: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FilingSection(Base):
    """Rapor bölümü (10-K: 1A, 7; 10-Q: 1A, 2)."""

    __tablename__ = "filing_section"
    __table_args__ = (
        UniqueConstraint("filing_id", "item_code", name="uq_section_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filing.id", ondelete="CASCADE"),
                                           index=True)
    item_code: Mapped[str] = mapped_column(String(8))
    title: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)


class TextChunk(Base):
    """Soru-cevap (RAG) için bölüm parçası."""

    __tablename__ = "text_chunk"
    __table_args__ = (
        UniqueConstraint("section_id", "chunk_index", name="uq_chunk_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("filing_section.id", ondelete="CASCADE"),
                                            index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int | None] = mapped_column(Integer)


class ChunkEmbedding(Base):
    """Parçanın gömme vektörü (float32 ham bayt)."""

    __tablename__ = "chunk_embedding"

    chunk_id: Mapped[int] = mapped_column(ForeignKey("text_chunk.id", ondelete="CASCADE"),
                                          primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), index=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class SentimentResult(Base):
    """Cümle düzeyinde duygu sonucu."""

    __tablename__ = "sentiment_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("filing_section.id", ondelete="CASCADE"),
                                            index=True)
    sentence_index: Mapped[int] = mapped_column(Integer)
    sentence: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str] = mapped_column(String(255))


class RiskComparison(Base):
    """İki 10-K'nın risk bölümü karşılaştırması (FR-09)."""

    __tablename__ = "risk_comparison"
    __table_args__ = (
        Index("ix_risk_comparison_company_created", "company_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"),
                                            index=True)
    old_filing_id: Mapped[int | None] = mapped_column(
        ForeignKey("filing.id", ondelete="SET NULL"), nullable=True)
    new_filing_id: Mapped[int | None] = mapped_column(
        ForeignKey("filing.id", ondelete="SET NULL"), nullable=True)
    added: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    modified: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    changes: Mapped[list["RiskChange"]] = relationship(
        back_populates="comparison", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RiskChange.position")


class RiskChange(Base):
    """Eklenen / çıkarılan / değişen risk paragrafı."""

    __tablename__ = "risk_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("risk_comparison.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # compare_risk_sections sırası
    change_type: Mapped[str] = mapped_column(String(16))
    old_text: Mapped[str | None] = mapped_column(Text)
    new_text: Mapped[str | None] = mapped_column(Text)
    similarity: Mapped[float | None] = mapped_column(Float)

    comparison: Mapped[RiskComparison] = relationship(back_populates="changes")


class CompanyScore(Base):
    """Skor anlık görüntüsü; payload = compute_score çıktısı (JSON)."""

    __tablename__ = "company_score"
    __table_args__ = (
        Index("ix_company_score_company_created", "company_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"),
                                            index=True)
    as_of_period: Mapped[date | None] = mapped_column(Date)
    total: Mapped[float | None] = mapped_column(Float)
    band: Mapped[str | None] = mapped_column(String(32))
    weights_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QALog(Base):
    """Soru-cevap kaydı (NFR-07 izlenebilirlik)."""

    __tablename__ = "qa_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"),
                                            index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(255))
    source_chunk_ids: Mapped[str | None] = mapped_column(Text)  # JSON liste
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IngestionJob(Base):
    """Arka plan veri çekme + analiz işi (FR-15)."""

    __tablename__ = "ingestion_job"
    __table_args__ = (
        Index("ix_ingestion_job_ticker_status", "ticker", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    step: Mapped[str | None] = mapped_column(String(255))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    analyze: Mapped[bool] = mapped_column(Boolean, default=True)
    warnings: Mapped[str | None] = mapped_column(Text)  # JSON liste: işi durdurmayan hatalar
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Her ilerleme yazımında güncellenir; uzun süre değişmeyen aktif iş ölü sayılır.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
