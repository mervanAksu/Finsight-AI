"""Ağdan veri toplama adımı; veritabanına dokunmaz (test edilebilirlik için ayrı)."""

from datetime import date

from .edgar import older_filing_files, parse_filings, resolve_company
from .sec_client import SecClient
from .xbrl import extract_all


def collect(client: SecClient, query: str, ticker_map: dict, years: int = 3,
            today: date | None = None) -> dict:
    company = resolve_company(query, ticker_map)
    cik = company["cik"]
    subs = client.submissions(cik)
    today = today or date.today()
    since = date(today.year - years, 1, 1)

    filings = parse_filings(subs["filings"]["recent"], since=since)
    for name in older_filing_files(subs, since):
        filings += parse_filings(client.submissions_file(name), since=since)

    facts = extract_all(client.companyfacts(cik), years=years)
    return {
        "company": {
            "cik": cik,
            "ticker": company["ticker"] or (subs.get("tickers") or [None])[0],
            "name": subs.get("name") or company["name"],
            "sic": subs.get("sic"),
            "fiscal_year_end": subs.get("fiscalYearEnd"),
        },
        "filings": filings,
        "facts": facts,
    }
