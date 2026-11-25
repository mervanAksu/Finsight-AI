"""Komut satırı veri çekme ve analiz aracı.

Kullanım:
    python -m finsight.ingest AAPL MSFT --years 3 --csv cikti.csv
    python -m finsight.ingest MSFT --analyze
    python -m finsight.ingest MSFT --analyze --force --no-sentence-cache   # model hızını ölç

Her şirket için SEC'ten şunları çeker ve veritabanına yazar:
- şirket bilgisi ve 10-K / 10-Q / 8-K başvuru listesi (submissions)
- son N+1 mali yılın temel kalemleri (companyfacts; ortalama oranlar için bir yıl fazla)
--analyze ile ayrıca son iki 10-K ve son 10-Q indirilip bölüm, duygu, gömme, risk farkı ve
skor üretilir (jobs.run_analysis_for_company). Özet, adım başına süreleri ve duygu analizi
hızını (cümle/sn) gösterir; darboğaz bu satırlardan okunur (README "Performans").
"""

import argparse
import csv
import logging
import sys
import time

from . import DISCLAIMER, jobs
from .config import (ConfigError, get_cache_dir, get_sec_json_ttl,
                     get_user_agent)
from .db import init_db, make_engine, make_session_factory
from .edgar import build_ticker_map
from .models import Company
from .pipeline import collect
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
    print_ttm(data.get("ttm"))


def print_ttm(ttm: dict | None) -> None:
    """Son 10-K'dan sonraki 10-Q'ya dayanan TTM noktaları (docs/asama4-sozlesme.md §2)."""
    if not ttm:
        return
    if ttm.get("as_of_period") is None:
        print(f"TTM: yok — {ttm.get('reason') or 'veri yok'} (skor yıllık veriye dayanır)")
        return
    print(f"TTM (son on iki ay, {ttm['as_of_period'].isoformat()} itibarıyla; "
          f"son yıllık dönem {ttm['annual_period'].isoformat()}):")
    for metric, points in ttm["facts"].items():
        p = points[-1]
        parts = " ".join(f"{d['role']}={d['value']:,.0f}" for d in p.get("derived_from") or []
                         if isinstance(d, dict) and "role" in d and "metric" not in d)
        print(f"  {metric:<22}{p['value']:>20,.0f}  {p['form']} {p['accession_no']}"
              + (f"  [{parts}]" if parts else ""))
    for metric, reason in sorted((ttm.get("reasons") or {}).items()):
        print(f"  {metric:<22}TTM yok: {reason}")


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
    device = summary.get("sentiment_device")
    if device:
        gpu = "GPU kullanılıyor" if "cuda" in device.lower() or "mps" in device.lower() \
            else "GPU kullanılmıyor"
        print(f"  Duygu cihazı : {device} ({gpu})")
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
        basis = "son on iki ay (TTM)" if score.get("basis") == "ttm" else "yıllık"
        print(f"  Skor         : {score['total']:.1f} / 100 ({score['band']}, "
              f"dönem sonu {score['as_of_period']}, dayanak {basis}) — derecelendirme değildir")
        filing = score.get("as_of_filing")
        if filing:
            print(f"  Dayandığı başvuru: {filing.get('form')} {filing.get('accession_no')} "
                  f"({filing.get('filing_date')})")
    else:
        print("  Skor         : hesaplanamadı veya güncellenmedi (uyarılara bakın)")
    for note in summary.get("notes") or []:
        print(f"  Not: {note}")
    for w in summary.get("warnings") or []:
        print(f"  Uyarı: {w}")
    print_timings(summary)


def _fmt_rate(value) -> str:
    return f"{value:.1f} cümle/sn" if value else "- cümle/sn"


def print_timings(summary: dict) -> None:
    """Adım başına süre + duygu hızı satırı (darboğazın kanıtı)."""
    from .analyze import TIMING_STEPS

    timings = summary.get("timings") or {}
    total = float(summary.get("total_seconds") or 0.0)
    if not timings:
        return
    print("  Adım süreleri:")
    longest = max(timings, key=lambda k: timings[k]) if timings else None
    for key, label in TIMING_STEPS:
        seconds = float(timings.get(key, 0.0))
        share = f"{100 * seconds / total:5.1f}%" if total > 0 else "    -"
        mark = "  <- en uzun" if key == longest and seconds > 0 else ""
        print(f"    {label:<26}{seconds:9.1f} sn {share}{mark}")
    print(f"    {'Toplam (analiz)':<26}{total:9.1f} sn")
    st = summary.get("sentiment_stats") or {}
    if st:
        print(f"  Duygu        : {st.get('sentences', 0)} cümle, {st.get('seconds', 0):.1f} sn, "
              f"{_fmt_rate(st.get('sentences_per_second'))}")
        cache = "açık" if st.get("cache_enabled") else "kapalı (--no-sentence-cache)"
        print(f"                 modele giden {st.get('predicted', 0)}, önbellekten "
              f"{st.get('cache_hits', 0)}, yinelenen {st.get('duplicates', 0)}; "
              f"model hızı {_fmt_rate(st.get('model_sentences_per_second'))}; "
              f"cümle önbelleği {cache}"
              + (f" ({st.get('cache_preloaded', 0)} kayıtlı cümle)"
                 if st.get("cache_enabled") else ""))
    for f in summary.get("filings") or []:
        fs = f.get("sentiment")
        if fs:
            print(f"    {f['form']:<7} {f['accession_no']}: {fs['sentences']} cümle, "
                  f"modele {fs['predicted']}, önbellek {fs['cache_hits']}, "
                  f"{fs['seconds']:.1f} sn")


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
    parser.add_argument("--no-sentence-cache", action="store_true",
                        help="--analyze ile: önceki cümle duygu sonuçlarını kullanma "
                             "(modelin gerçek hızını ölçmek için)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.analyze and args.no_db:
        print("--analyze veritabanı gerektirir; --no-db ile birlikte kullanılamaz.",
              file=sys.stderr)
        return 2

    try:
        client = SecClient(
            get_user_agent(),
            # NFR-02: API süreci, refresh CLI ve bu CLI aynı (dosya tabanlı) hız bütçesini paylaşır.
            limiter=jobs.shared_limiter(),
            cache=FileCache(get_cache_dir(), json_ttl_seconds=get_sec_json_ttl()),
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
            company = save(session, data) if session is not None else None
        except Exception as e:  # bir şirketin hatası (ağ veya veritabanı) diğerlerini durdurmasın
            if session is not None:
                session.rollback()
            log.error("%s işlenemedi: %s", query, e)
            failed.append(query)
            continue
        print_summary(data)
        results.append(data)
        if args.analyze and session is not None and company is not None:
            from .jobs import run_analysis_for_company

            def progress(step: str, _frac: float) -> None:
                print(f"  … {step}")

            try:
                summary = run_analysis_for_company(session, client, company.id,
                                                   progress=progress, force=args.force,
                                                   sentence_cache=not args.no_sentence_cache)
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
