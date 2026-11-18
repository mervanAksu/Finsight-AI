"""Ağdan veri toplama adımı; veritabanına dokunmaz (test edilebilirlik için ayrı)."""

from datetime import date

from .edgar import older_filing_files, parse_filings, resolve_company
from .sec_client import SecClient
from .xbrl import extract_all


def collect(client: SecClient, query: str, ticker_map: dict, years: int = 3,
            today: date | None = None) -> dict:
    """Bir şirketin başvuru listesini ve yıllık finansal kalemlerini toplar.

    years: başvuru listesinin kaç takvim yılı geriye gideceği. Finansal kalemler bir yıl fazla
    çekilir (``extract_all(cf, years=years + 1)``): ROA/ROE ortalamaları ve YoY için en eski
    dönemin bir önceki mali yıl sonu da gerekir (financial-analysis §1).
    """
    company = resolve_company(query, ticker_map)
    cik = company["cik"]
    subs = client.submissions(cik)
    today = today or date.today()
    since = date(today.year - years, 1, 1)

    filings = parse_filings(subs["filings"]["recent"], since=since)
    for name in older_filing_files(subs, since):
        filings += parse_filings(client.submissions_file(name), since=since)

    facts = extract_all(client.companyfacts(cik), years=years + 1)
    return {
        "company": {
            "cik": cik,
            "ticker": company["ticker"] or (subs.get("tickers") or [None])[0],
            "name": subs.get("name") or company["name"],
            "sic": subs.get("sic"),
            "sic_description": subs.get("sicDescription") or None,
            "fiscal_year_end": subs.get("fiscalYearEnd"),
            "exchanges": [e for e in (subs.get("exchanges") or []) if e],
        },
        "filings": filings,
        "facts": facts,
    }
