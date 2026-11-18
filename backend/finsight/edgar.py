"""Şirket ve başvuru verisini ayrıştıran saf fonksiyonlar (FR-01, FR-02).

Ağ erişimi yoktur; tümü örnek JSON ile test edilebilir.
"""

from datetime import date

DEFAULT_FORMS = frozenset({"10-K", "10-Q", "8-K"})


def pad_cik(cik: str | int) -> str:
    """CIK'yi SEC API'lerinin istediği 10 haneli biçime getirir."""
    text = str(cik).strip()
    if text.upper().startswith("CIK"):
        text = text[3:]
    if not text.isdigit() or len(text) > 10:
        raise ValueError(f"Geçersiz CIK: {cik!r}")
    return text.zfill(10)


def build_ticker_map(company_tickers: dict) -> dict[str, dict]:
    """company_tickers.json -> {"AAPL": {"cik": "0000320193", "name": "Apple Inc."}}"""
    result: dict[str, dict] = {}
    for row in company_tickers.values():
        ticker = str(row["ticker"]).upper()
        result.setdefault(ticker, {"cik": pad_cik(row["cik_str"]), "name": row["title"]})
    return result


def resolve_company(query: str, ticker_map: dict[str, dict]) -> dict:
    """Ticker veya CIK ile şirketi bulur. Bulamazsa KeyError."""
    q = query.strip().upper()
    if q in ticker_map:
        return {"ticker": q, **ticker_map[q]}
    digits = q[3:] if q.startswith("CIK") else q
    if digits.isdigit():
        cik = pad_cik(digits)
        for ticker, info in ticker_map.items():
            if info["cik"] == cik:
                return {"ticker": ticker, **info}
        return {"ticker": None, "cik": cik, "name": None}
    raise KeyError(f"Şirket bulunamadı: {query}")


def _rows_from_arrays(arrays: dict) -> list[dict]:
    """SEC'in sütunlu dizilerini (aynı indeks = aynı başvuru) satırlara çevirir."""
    keys = [k for k, v in arrays.items() if isinstance(v, list)]
    if not keys:
        return []
    n = len(arrays["accessionNumber"])
    return [{k: arrays[k][i] if i < len(arrays[k]) else None for k in keys} for i in range(n)]


def parse_filings(arrays: dict, forms=DEFAULT_FORMS, since: date | None = None) -> list[dict]:
    """filings.recent (veya ek dosya) dizilerinden istenen formları süzer.

    Değişiklik başvuruları (ör. 10-K/A) dahil edilir ve is_amendment ile işaretlenir.
    """
    out = []
    for row in _rows_from_arrays(arrays):
        form = row.get("form") or ""
        base = form[:-2] if form.endswith("/A") else form
        if base not in forms:
            continue
        filing_date = date.fromisoformat(row["filingDate"])
        if since and filing_date < since:
            continue
        report = row.get("reportDate") or None
        out.append({
            "accession_no": row["accessionNumber"],
            "form": form,
            "base_form": base,
            "is_amendment": form.endswith("/A"),
            "filing_date": filing_date,
            "report_date": date.fromisoformat(report) if report else None,
            "primary_document": row.get("primaryDocument") or None,
            "items": row.get("items") or None,
            "is_xbrl": bool(row.get("isXBRL")),
        })
    return out


def older_filing_files(submissions: dict, since: date) -> list[str]:
    """filings.files içinden 'since' tarihine uzanan ek dosyaların adları."""
    names = []
    for f in submissions.get("filings", {}).get("files", []):
        if date.fromisoformat(f["filingTo"]) >= since:
            names.append(f["name"])
    return names


def document_url(cik: str | int, accession: str, primary_document: str) -> str:
    """Ana belge adresi: CIK baştaki sıfırlar olmadan, accession tiresiz."""
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(pad_cik(cik))}/{accession.replace('-', '')}/{primary_document}"
    )
