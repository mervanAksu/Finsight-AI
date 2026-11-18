"""Komut satırı veri çekme ve analiz aracı.

Kullanım:
    python -m finsight.ingest AAPL MSFT --years 3 --csv cikti.csv
    python -m finsight.ingest MSFT --analyze

Her şirket için SEC'ten şunları çeker ve veritabanına yazar:
- şirket bilgisi ve 10-K / 10-Q / 8-K başvuru listesi (submissions)
- son N+1 mali yılın temel kalemleri (companyfacts; ortalama oranlar için bir yıl fazla)
--analyze ile ayrıca son iki 10-K ve son 10-Q indirilip bölüm, duygu, gömme, risk farkı ve
skor üretilir (jobs.run_analysis_for_company).
"""

import argparse
import csv
import logging
import sys
import time

from . import DISCLAIMER
from .config import ConfigError, get_cache_dir, get_max_rps, get_user_agent
from .db import init_db, make_engine, make_session_factory
from .edgar import build_ticker_map
from .models import Company
from .pipeline import collect
from .rate_limiter import RateLimiter
from .repository import save_collected
from .sec_client import FileCache, SecClient
from .xbrl import yoy_changes

log = logging.getLogger("finsight.ingest")


def save(session, data: dict) -> Company:
    """Tekrar çalıştırmada mükerrer kayıt oluşturmadan yazar (upsert).

    derived_from, sic_description ve exchanges da saklanır (repository.save_collected).
    """
    return save_collected(session, data)


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


def _model_kind(name: str, fallback: str) -> str:
    return "yedek model" if name == fallback or name.endswith("-lexicon") else "tam model"


def print_analysis(summary: dict) -> None:
    """--analyze özeti: kullanılan modeller, bölümler, risk farkı, skor."""
    from .embeddings import HASHING_NAME
    from .sentiment import LEXICON_NAME

    sm, em = summary["sentiment_model"], summary["embedding_model"]
    llm = summary.get("llm")
    print("\nAnaliz")
    print(f"  Duygu modeli : {sm} ({_model_kind(sm, LEXICON_NAME)})")
    print(f"  Gömme modeli : {em} ({_model_kind(em, HASHING_NAME)})")
    print("  LLM          : " + (f"{llm['provider']} / {llm['model']}" if llm
                                 else "yok (soru-cevap çıkarımsal modda çalışır)"))
    statuses = {"analyzed": "analiz edildi", "skipped": "önceden analiz edilmiş, atlandı",
                "error": "HATA"}
    for f in summary["filings"]:
        filed = f["filing_date"].isoformat() if hasattr(f["filing_date"], "isoformat") \
            else f["filing_date"]
        detail = ""
        if f["status"] == "analyzed":
            detail = (f" — bölümler: {', '.join(f['sections']) or 'yok'}, "
                      f"{f['sentences']} cümle, {f['chunks']} parça")
        elif f["status"] == "error":
            detail = f" — {f.get('error')}"
        print(f"  {f['form']:<7} {filed} {f['accession_no']}: {statuses[f['status']]}{detail}")
    n_sections = sum(len(f["sections"]) for f in summary["filings"])
    print(f"  Toplam bölüm : {n_sections}")
    risk = summary.get("risk")
    if risk:
        print(f"  Risk farkı   : {risk['added']} eklenen, {risk['removed']} çıkarılan, "
              f"{risk['modified']} değişen, {risk['unchanged']} aynı paragraf")
    else:
        print("  Risk farkı   : yapılamadı (iki 10-K'nın Item 1A bölümü gerekli)")
    score = summary.get("score") or {}
    if score.get("total") is not None:
        print(f"  Skor         : {score['total']:.1f} / 100 ({score['band']}, "
              f"dönem sonu {score['as_of_period']}) — derecelendirme değildir")
    else:
        print("  Skor         : hesaplanamadı (yeterli veri yok)")
    for w in summary.get("warnings") or []:
        print(f"  Uyarı: {w}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEC'ten şirket verisi çeker.")
    parser.add_argument("companies", nargs="+", help="Ticker veya CIK (ör. AAPL 320193)")
    parser.add_argument("--years", type=int, default=3,
                        help="Başvuru listesi için yıl (finansal kalemler bir yıl fazla)")
    parser.add_argument("--csv", help="Sonuçları CSV olarak da kaydet")
    parser.add_argument("--no-db", action="store_true", help="Veritabanına yazma")
    parser.add_argument("--analyze", action="store_true",
                        help="10-K/10-Q metinlerini indir; duygu, risk farkı, gömme ve skor üret")
    parser.add_argument("--force", action="store_true",
                        help="--analyze ile: önceden analiz edilmiş başvuruları yeniden işle")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.analyze and args.no_db:
        print("--analyze veritabanı gerektirir; --no-db ile birlikte kullanılamaz.",
              file=sys.stderr)
        return 2

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
        company = save(session, data) if session is not None else None
        print_summary(data)
        results.append(data)
        if args.analyze and session is not None and company is not None:
            from .jobs import run_analysis_for_company

            def progress(step: str, _frac: float) -> None:
                print(f"  … {step}")

            try:
                summary = run_analysis_for_company(session, client, company.id,
                                                   progress=progress, force=args.force)
            except Exception as e:  # analiz hatası diğer şirketleri durdurmasın
                session.rollback()
                log.error("%s analiz edilemedi: %s", query, e)
                failed.append(query)
                continue
            print_analysis(summary)

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
