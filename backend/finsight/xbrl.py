"""companyfacts verisinden yıllık finansal kalemleri çıkarır (FR-04, FR-05).

Bilinen zorluklar ve uygulanan kurallar:
1. Aynı değer sonraki raporlarda karşılaştırma sütunu olarak tekrar görünür.
   -> Aynı dönem (start, end) için en son 'filed' tarihli kayıt seçilir.
2. 'fy' alanı değerin değil, onu raporlayan başvurunun mali yılıdır.
   -> Dönem, 'end' (ve 'start') tarihleriyle belirlenir, 'fy' ile değil.
3. 10-K içinde çeyreklik değerler de bulunabilir.
   -> Süreli kalemlerde yalnızca 330-400 günlük dönemler alınır.
4. Şirketler aynı kavram için farklı etiket kullanabilir veya etiket değiştirebilir.
   -> Öncelik sıralı yedek etiket listesi; aynı dönemde öncelikli etiket kazanır.
"""

from datetime import date

ANNUAL_FORMS = {"10-K", "10-K/A"}

# metric -> (öncelik sıralı us-gaap etiketleri, anlık (bilanço) kalemi mi)
METRICS: dict[str, tuple[list[str], bool]] = {
    "revenue": ([
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], False),
    "net_income": (["NetIncomeLoss"], False),
    "operating_cash_flow": (["NetCashProvidedByUsedInOperatingActivities"], False),
    "total_assets": (["Assets"], True),
    "total_liabilities": (["Liabilities"], True),
    "stockholders_equity": ([
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], True),
}


def _is_annual_duration(fact: dict) -> bool:
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return False
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return 330 <= days <= 400


def extract_annual(companyfacts: dict, metric: str, years: int = 3, unit: str = "USD") -> list[dict]:
    """Bir kalemin son 'years' mali yılına ait değerleri, eskiden yeniye sıralı döndürür."""
    if metric not in METRICS:
        raise KeyError(f"Bilinmeyen kalem: {metric}")
    tags, is_instant = METRICS[metric]
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})

    chosen: dict[tuple, dict] = {}  # (start, end) -> seçilen kayıt
    for priority, tag in enumerate(tags):
        for fact in us_gaap.get(tag, {}).get("units", {}).get(unit, []):
            if fact.get("form") not in ANNUAL_FORMS:
                continue
            if is_instant:
                if fact.get("start"):
                    continue
            elif not _is_annual_duration(fact):
                continue
            key = (fact.get("start"), fact["end"])
            candidate = {
                "metric": metric,
                "tag": tag,
                "priority": priority,
                "period_start": date.fromisoformat(fact["start"]) if fact.get("start") else None,
                "period_end": date.fromisoformat(fact["end"]),
                "value": fact["val"],
                "unit": unit,
                "accession_no": fact["accn"],
                "form": fact["form"],
                "filed": date.fromisoformat(fact["filed"]),
                "fy": fact.get("fy"),
                "fp": fact.get("fp"),
            }
            current = chosen.get(key)
            if current is None or (priority, -candidate["filed"].toordinal()) < (
                current["priority"], -current["filed"].toordinal()
            ):
                chosen[key] = candidate

    # Aynı bitiş tarihine farklı başlangıçlı kayıt düşerse en son raporlananı tut.
    by_end: dict[date, dict] = {}
    for item in chosen.values():
        prev = by_end.get(item["period_end"])
        if prev is None or (item["priority"], -item["filed"].toordinal()) < (
            prev["priority"], -prev["filed"].toordinal()
        ):
            by_end[item["period_end"]] = item

    series = sorted(by_end.values(), key=lambda x: x["period_end"])
    for item in series:
        item.pop("priority")
    return series[-years:] if years else series


def extract_all(companyfacts: dict, years: int = 3) -> dict[str, list[dict]]:
    return {m: extract_annual(companyfacts, m, years) for m in METRICS}


def yoy_changes(series: list[dict]) -> list[float | None]:
    """Yıllık yüzde değişim. İlk yıl ve sıfır/negatif tabanlı yıllar için None."""
    changes: list[float | None] = [None]
    for prev, cur in zip(series, series[1:]):
        base = prev["value"]
        changes.append(None if not base or base < 0 else round((cur["value"] - base) / base * 100, 2))
    return changes
