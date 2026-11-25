"""Ağdan veri toplama adımı; veritabanına dokunmaz (test edilebilirlik için ayrı)."""

from datetime import date

from .edgar import older_filing_files, parse_filings, resolve_company
from .sec_client import SecClient
from .xbrl import extract_all, extract_ttm


def company_tickers(cik: str, requested: str | None, subs: dict,
                    ticker_map: dict) -> list[str]:
    """Aynı CIK'e bağlı tüm ticker'lar (büyük harf, tekrarsız): istenen ticker önce, sonra
    submissions ``tickers`` alanı, sonra company_tickers listesinde aynı CIK'li olanlar
    (ör. GOOGL ve GOOG aynı şirkettir)."""
    out: list[str] = []
    candidates = [requested, *(subs.get("tickers") or []),
                  *(t for t, info in ticker_map.items() if info.get("cik") == cik)]
    for t in candidates:
        t = str(t or "").strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def collect(client: SecClient, query: str, ticker_map: dict, years: int = 3,
            today: date | None = None, *, fresh: bool = False) -> dict:
    """Bir şirketin başvuru listesini ve yıllık finansal kalemlerini toplar.

    years: başvuru listesinin kaç takvim yılı geriye gideceği. Finansal kalemler bir yıl fazla
    çekilir (``extract_all(cf, years=years + 1)``): ROA/ROE ortalamaları ve YoY için en eski
    dönemin bir önceki mali yıl sonu da gerekir (financial-analysis §1).

    Aşama 4: ``ttm`` anahtarı ``xbrl.extract_ttm`` çıktısıdır (son 10-K'dan sonra 10-Q varsa
    TTM noktaları; docs/asama4-sozlesme.md §2). fresh=True: submissions ve companyfacts
    önbellek okunmadan SEC'ten alınır (yenileme akışı).
    """
    company = resolve_company(query, ticker_map)
    cik = company["cik"]
    subs = client.submissions(cik, fresh=fresh)
    today = today or date.today()
    since = date(today.year - years, 1, 1)

    filings = parse_filings(subs["filings"]["recent"], since=since)
    for name in older_filing_files(subs, since):
        filings += parse_filings(client.submissions_file(name), since=since)

    companyfacts = client.companyfacts(cik, fresh=fresh)
    all_annual = extract_all(companyfacts, years=0)
    facts = {m: s[-(years + 1):] for m, s in all_annual.items()}  # = extract_all(years+1)
    ttm = extract_ttm(companyfacts, all_annual)
    primary = company["ticker"] or (subs.get("tickers") or [None])[0]
    return {
        "company": {
            "cik": cik,
            "ticker": primary,
            "tickers": company_tickers(cik, primary, subs, ticker_map),
            "name": subs.get("name") or company["name"],
            "sic": subs.get("sic"),
            "sic_description": subs.get("sicDescription") or None,
            "fiscal_year_end": subs.get("fiscalYearEnd"),
            "exchanges": [e for e in (subs.get("exchanges") or []) if e],
        },
        "filings": filings,
        "facts": facts,
        "ttm": ttm,
    }
