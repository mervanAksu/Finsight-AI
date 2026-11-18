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


# --- 8-K madde etiketleri ve şirket arama (Aşama 3) ----------------------------------------

# 8-K "items" alanındaki madde numaralarının Türkçe kısa açıklamaları (SEC Form 8-K
# talimatları, Item 1.01–9.01). Bilinmeyen madde "Madde X" olarak gösterilir.
ITEM_LABELS_8K: dict[str, str] = {
    "1.01": "Önemli sözleşme imzalanması",
    "1.02": "Önemli sözleşmenin feshi",
    "1.03": "İflas veya kayyum atanması",
    "1.04": "Maden güvenliği ihlalleri",
    "1.05": "Önemli siber güvenlik olayı",
    "2.01": "Varlık edinimi veya elden çıkarılması",
    "2.02": "Faaliyet sonuçları ve finansal durum",
    "2.03": "Doğrudan finansal yükümlülük oluşması",
    "2.04": "Finansal yükümlülüğü hızlandıran tetikleyici olaylar",
    "2.05": "Çıkış veya elden çıkarma faaliyetleri maliyetleri",
    "2.06": "Önemli değer düşüklüğü",
    "3.01": "Borsa kotundan çıkarma veya kotasyon kuralı ihlali",
    "3.02": "Kayıt dışı özsermaye satışları",
    "3.03": "Menkul kıymet sahiplerinin haklarında önemli değişiklik",
    "4.01": "Bağımsız denetçi değişikliği",
    "4.02": "Önceki finansal tablolara artık güvenilmemesi",
    "5.01": "Şirket kontrolünde değişiklik",
    "5.02": "Yönetici veya yönetim kurulu üyesi değişikliği",
    "5.03": "Esas sözleşme veya iç yönetmelik değişikliği; mali yıl değişikliği",
    "5.04": "Çalışan emeklilik planlarında işlem yasağı",
    "5.05": "Etik kurallarında değişiklik veya muafiyet",
    "5.06": "Paravan şirket statüsünde değişiklik",
    "5.07": "Hissedar oylamasına sunulan konular",
    "5.08": "Hissedar aday gösterme tarihleri",
    "6.01": "ABS bilgilendirme materyali",
    "6.02": "Hizmet sağlayıcı veya kayyum değişikliği",
    "6.03": "Kredi güçlendirmesinde değişiklik",
    "6.04": "Dağıtım yapılamaması",
    "6.05": "Menkul kıymet kanunu açıklamaları",
    "7.01": "Regulation FD açıklaması",
    "8.01": "Diğer olaylar",
    "9.01": "Finansal tablolar ve ekler",
}


def split_items(items: str | list[str] | None) -> list[str]:
    """SEC 'items' alanı ("2.02,9.01") → ["2.02", "9.01"]; boş/None → []."""
    if not items:
        return []
    parts = items if isinstance(items, list) else str(items).split(",")
    return [p.strip() for p in parts if p and p.strip()]


def item_label(item: str) -> str:
    """8-K madde numarasının Türkçe etiketi; bilinmiyorsa "Madde <no>"."""
    return ITEM_LABELS_8K.get(item.strip(), f"Madde {item.strip()}")


def search_companies(ticker_map: dict[str, dict], query: str, limit: int = 10) -> list[dict]:
    """build_ticker_map çıktısında ticker, ad veya CIK araması (FR-01).

    Sıralama: tam ticker eşleşmesi, CIK eşleşmesi, ticker öneki, ad öneki, ad içinde geçme;
    eşitlikte ticker alfabetik. Dönüş: [{"ticker", "cik", "name"}].
    """
    q = (query or "").strip().upper()
    if not q or limit <= 0:
        return []
    digits = q[3:] if q.startswith("CIK") else q
    cik_query = pad_cik(digits) if digits.isdigit() and len(digits) <= 10 else None

    ranked: list[tuple[int, str, dict]] = []
    for ticker, info in ticker_map.items():
        name = str(info.get("name") or "")
        upper_name = name.upper()
        if ticker == q:
            rank = 0
        elif cik_query is not None and info.get("cik") == cik_query:
            rank = 1
        elif ticker.startswith(q):
            rank = 2
        elif upper_name.startswith(q):
            rank = 3
        elif q in upper_name:
            rank = 4
        else:
            continue
        ranked.append((rank, ticker, {"ticker": ticker, "cik": info.get("cik"), "name": name}))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in ranked[:limit]]
