"""Komut satırı veri çekme aracı.

Kullanım:
    python -m finsight.ingest AAPL MSFT --years 3 --csv cikti.csv

Her şirket için SEC'ten şunları çeker ve veritabanına yazar:
- şirket bilgisi ve 10-K / 10-Q / 8-K başvuru listesi (submissions)
- son N mali yılın temel kalemleri (companyfacts)
"""

import argparse
import csv
import logging
import sys
import time

from sqlalchemy import select

from . import DISCLAIMER
from .config import ConfigError, get_cache_dir, get_max_rps, get_user_agent
from .db import init_db, make_engine, make_session_factory
from .edgar import build_ticker_map, document_url
from .models import Company, Filing, FinancialFact
from .pipeline import collect
from .rate_limiter import RateLimiter
from .sec_client import FileCache, SecClient
from .xbrl import yoy_changes

log = logging.getLogger("finsight.ingest")


def save(session, data: dict) -> Company:
    """Tekrar çalıştırmada mükerrer kayıt oluşturmadan yazar (upsert)."""
    info = data["company"]
    company = session.scalar(select(Company).where(Company.cik == info["cik"]))
    if company is None:
        company = Company(cik=info["cik"])
        session.add(company)
    for key in ("ticker", "name", "sic", "fiscal_year_end"):
        setattr(company, key, info[key])
    session.flush()

    for f in data["filings"]:
        row = session.scalar(select(Filing).where(Filing.accession_no == f["accession_no"]))
        if row is None:
            row = Filing(accession_no=f["accession_no"], company_id=company.id)
            session.add(row)
        row.form = f["form"]
        row.is_amendment = f["is_amendment"]
        row.filing_date = f["filing_date"]
        row.report_date = f["report_date"]
        row.primary_document = f["primary_document"]
        row.items = f["items"]
        row.is_xbrl = f["is_xbrl"]
        if f["primary_document"]:
            row.url = document_url(info["cik"], f["accession_no"], f["primary_document"])

    for metric, series in data["facts"].items():
        for item in series:
            row = session.scalar(select(FinancialFact).where(
                FinancialFact.company_id == company.id,
                FinancialFact.metric == metric,
                FinancialFact.period_end == item["period_end"],
            ))
            if row is None:
                row = FinancialFact(company_id=company.id, metric=metric,
                                    period_end=item["period_end"])
                session.add(row)
            for key in ("tag", "unit", "value", "period_start", "fy", "fp", "form",
                        "accession_no", "filed"):
                setattr(row, key, item[key])
    session.commit()
    return company


def print_summary(data: dict) -> None:
    c = data["company"]
    counts = {}
    for f in data["filings"]:
        counts[f["base_form"]] = counts.get(f["base_form"], 0) + 1
    print(f"\n{c['name']} ({c['ticker']}, CIK {c['cik']})")
    print("Başvurular: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print(f"{'Kalem':<22}{'Dönem sonu':<12}{'Değer (USD)':>20}{'YoY %':>10}  Kaynak")
    for metric, series in data["facts"].items():
        if not series:
            print(f"{metric:<22}{'-':<12}{'veri yok':>20}")
            continue
        for item, change in zip(series, yoy_changes(series)):
            ch = f"{change:.2f}" if change is not None else "-"
            print(f"{metric:<22}{item['period_end'].isoformat():<12}"
                  f"{item['value']:>20,.0f}{ch:>10}  {item['form']} {item['accession_no']}")


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "cik", "metric", "period_start", "period_end", "value",
                         "unit", "xbrl_tag", "form", "accession_no", "filed"])
        for data in rows:
            c = data["company"]
            for metric, series in data["facts"].items():
                for i in series:
                    writer.writerow([c["ticker"], c["cik"], metric, i["period_start"],
                                     i["period_end"], i["value"], i["unit"], i["tag"],
                                     i["form"], i["accession_no"], i["filed"]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEC'ten şirket verisi çeker.")
    parser.add_argument("companies", nargs="+", help="Ticker veya CIK (ör. AAPL 320193)")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--csv", help="Sonuçları CSV olarak da kaydet")
    parser.add_argument("--no-db", action="store_true", help="Veritabanına yazma")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        client = SecClient(
            get_user_agent(),
            limiter=RateLimiter(get_max_rps()),
            cache=FileCache(get_cache_dir()),
        )
    except ConfigError as e:
        print(f"Yapılandırma hatası: {e}", file=sys.stderr)
        return 2

    session = None
    if not args.no_db:
        engine = make_engine()
        init_db(engine)
        session = make_session_factory(engine)()

    started = time.monotonic()
    ticker_map = build_ticker_map(client.company_tickers())
    results, failed = [], []
    for query in args.companies:
        try:
            data = collect(client, query, ticker_map, args.years)
        except Exception as e:  # bir şirketin hatası diğerlerini durdurmasın
            log.error("%s işlenemedi: %s", query, e)
            failed.append(query)
            continue
        if session is not None:
            save(session, data)
        print_summary(data)
        results.append(data)

    if args.csv and results:
        write_csv(args.csv, results)
        print(f"\nCSV kaydedildi: {args.csv}")
    elapsed = time.monotonic() - started
    print(f"\nSEC istek sayısı: {client.request_count}, süre: {elapsed:.1f} sn "
          f"(önbellekten gelenler sayılmaz)")
    print(DISCLAIMER)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
