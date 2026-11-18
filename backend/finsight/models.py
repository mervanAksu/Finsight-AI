"""Prototip tabloları: company, filing, financial_fact (tasarım Bölüm 10'un alt kümesi)."""

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Numeric, String,
                        UniqueConstraint, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
