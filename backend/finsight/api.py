"""FastAPI uç noktaları (prototip). Çalıştırma: uvicorn finsight.api:app --reload"""

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select

from . import DISCLAIMER, __version__
from .db import init_db, make_engine, make_session_factory
from .models import Company, Filing, FinancialFact
from .xbrl import METRICS

app = FastAPI(title="FinSight AI", version=__version__,
              description="SEC raporlarından finansal veri prototipi. " + DISCLAIMER)

_engine = make_engine()
init_db(_engine)
SessionLocal = make_session_factory(_engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _company_or_404(session, ticker: str) -> Company:
    company = session.scalar(select(Company).where(Company.ticker == ticker.upper()))
    if company is None:
        raise HTTPException(404, f"{ticker.upper()} henüz işlenmedi. Önce: "
                                 f"python -m finsight.ingest {ticker.upper()}")
    return company


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/companies")
def list_companies(session=Depends(get_session)):
    rows = session.scalars(select(Company).order_by(Company.ticker)).all()
    return [{"ticker": c.ticker, "cik": c.cik, "name": c.name} for c in rows]


@app.get("/companies/{ticker}/filings")
def list_filings(ticker: str,
                 form: str | None = Query(None, description="10-K, 10-Q veya 8-K"),
                 limit: int = Query(50, ge=1, le=500),
                 session=Depends(get_session)):
    company = _company_or_404(session, ticker)
    stmt = select(Filing).where(Filing.company_id == company.id)
    if form:
        stmt = stmt.where(Filing.form.in_([form, f"{form}/A"]))
    rows = session.scalars(stmt.order_by(Filing.filing_date.desc()).limit(limit)).all()
    return {
        "company": {"ticker": company.ticker, "cik": company.cik, "name": company.name},
        "filings": [{
            "accession_no": f.accession_no, "form": f.form, "filing_date": f.filing_date,
            "report_date": f.report_date, "items": f.items, "url": f.url,
        } for f in rows],
        "disclaimer": DISCLAIMER,
    }


@app.get("/companies/{ticker}/facts")
def list_facts(ticker: str,
               metric: str | None = Query(None, description=", ".join(METRICS)),
               session=Depends(get_session)):
    company = _company_or_404(session, ticker)
    if metric and metric not in METRICS:
        raise HTTPException(400, f"Bilinmeyen kalem. Seçenekler: {', '.join(METRICS)}")
    stmt = select(FinancialFact).where(FinancialFact.company_id == company.id)
    if metric:
        stmt = stmt.where(FinancialFact.metric == metric)
    rows = session.scalars(stmt.order_by(FinancialFact.metric, FinancialFact.period_end)).all()
    return {
        "company": {"ticker": company.ticker, "cik": company.cik, "name": company.name},
        "facts": [{
            "metric": r.metric, "period_start": r.period_start, "period_end": r.period_end,
            "value": float(r.value), "unit": r.unit,
            # NFR-06: her değer kaynağına izlenebilir
            "source": {"xbrl_tag": f"us-gaap:{r.tag}", "form": r.form,
                       "accession_no": r.accession_no, "filed": r.filed},
        } for r in rows],
        "disclaimer": DISCLAIMER,
    }
