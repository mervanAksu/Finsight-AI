"""companyfacts verisinden yıllık finansal kalemleri çıkarır (FR-04, FR-05).

Bilinen zorluklar ve uygulanan kurallar:
1. Aynı değer sonraki raporlarda karşılaştırma sütunu olarak tekrar görünür.
   -> Aynı dönem (start, end) için en son 'filed' tarihli kayıt seçilir.
2. 'fy' alanı değerin değil, onu raporlayan başvurunun mali yılıdır.
   -> Dönem, 'end' (ve 'start') tarihleriyle belirlenir, 'fy' ile değil.
3. 10-K içinde çeyreklik değerler de bulunabilir.
   -> Süreli kalemlerde yalnızca 330-400 günlük dönemler alınır.
4. Şirketler aynı kavram için farklı etiket kullanabilir veya etiket değiştirebilir.
   -> "Birincil etiket" = adaylar arasında en son period_end'e sahip etiket (eşitlikte öncelik
      sırası). Önce birincil etiketin tüm dönemleri alınır; birincilde olmayan dönemler için
      öncelik sırasıyla diğer etiketlere düşülür. Böylece etiket geçişinde yeniden düzenlenmiş
      karşılaştırma değeri (aynı tanım) eski etiketteki farklı tanımlı değere tercih edilir.
5. Bazı kalemler doğrudan raporlanmayabilir (brüt kâr, toplam yükümlülükler).
   -> extract_all, doğrudan değeri olmayan dönemler için skill'deki türetme kurallarını
      uygular; türetilen kayıtlar tag="derived" ve derived_from ile işaretlenir (NFR-06).

Hesap kuralları için tek doğruluk kaynağı: .claude/skills/financial-analysis/SKILL.md
"""

from datetime import date

ANNUAL_FORMS = {"10-K", "10-K/A"}

# metric -> (öncelik sıralı us-gaap etiketleri, anlık (bilanço) kalemi mi)
# Sıra ve etiketler financial-analysis skill tablosuyla aynıdır.
METRICS: dict[str, tuple[list[str], bool]] = {
    "revenue": ([
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ], False),
    "cost_of_revenue": ([
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ], False),
    "gross_profit": (["GrossProfit"], False),
    "operating_income": (["OperatingIncomeLoss"], False),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], False),
    "operating_cash_flow": ([
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ], False),
    "capex": ([
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ], False),
    "total_assets": (["Assets"], True),
    "current_assets": (["AssetsCurrent"], True),
    "current_liabilities": (["LiabilitiesCurrent"], True),
    "total_liabilities": (["Liabilities"], True),
    "stockholders_equity": ([
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], True),
}

METRIC_LABELS: dict[str, str] = {
    "revenue": "Gelir",
    "cost_of_revenue": "Satışların maliyeti",
    "gross_profit": "Brüt kâr",
    "operating_income": "Faaliyet kârı",
    "net_income": "Net kâr",
    "operating_cash_flow": "Faaliyetlerden nakit akışı",
    "capex": "Yatırım harcamaları (CAPEX)",
    "total_assets": "Toplam varlıklar",
    "current_assets": "Dönen varlıklar",
    "current_liabilities": "Kısa vadeli yükümlülükler",
    "total_liabilities": "Toplam yükümlülükler",
    "stockholders_equity": "Özkaynak",
}

DERIVED_TAG = "derived"

# Yalnızca türetmede yardımcı olarak okunan etiketler (METRICS'te kalem değildir).
_LSE_TAGS = ["LiabilitiesAndStockholdersEquity"]
# Toplam yükümlülük türetmesinde özkaynak: azınlık payları dahil olan öncelikli.
_TOTAL_EQUITY_TAGS = [
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "StockholdersEquity",
]


def _is_annual_duration(fact: dict) -> bool:
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return False
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return 330 <= days <= 400


def _valid_annual_facts(us_gaap: dict, tag: str, is_instant: bool, unit: str) -> list[dict]:
    out = []
    for fact in us_gaap.get(tag, {}).get("units", {}).get(unit, []):
        if fact.get("form") not in ANNUAL_FORMS:
            continue
        if is_instant:
            if fact.get("start"):
                continue
        elif not _is_annual_duration(fact):
            continue
        out.append(fact)
    return out


def _tag_order(per_tag: list[tuple[str, list[dict]]]) -> list[str]:
    """Birincil etiket (en son period_end; eşitlikte öncelik sırası) önce, sonra öncelik sırası."""
    present = [(tag, max(date.fromisoformat(f["end"]) for f in facts))
               for tag, facts in per_tag if facts]
    if not present:
        return []
    latest = max(end for _, end in present)
    primary = next(tag for tag, end in present if end == latest)
    return [primary] + [tag for tag, _ in present if tag != primary]


def _extract_tags(companyfacts: dict, metric: str, tags: list[str], is_instant: bool,
                  unit: str = "USD") -> list[dict]:
    """Verilen öncelik sıralı etiketlerden tüm yıllık dönemleri, eskiden yeniye döndürür.

    Etiket sırası `_tag_order` ile belirlenir (birincil etiket önce); aynı dönemde sırası
    önde olan etiket, aynı etikette en son `filed` kazanır.
    """
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    per_tag = [(tag, _valid_annual_facts(us_gaap, tag, is_instant, unit)) for tag in tags]
    rank = {tag: i for i, tag in enumerate(_tag_order(per_tag))}

    chosen: dict[tuple, dict] = {}  # (start, end) -> seçilen kayıt
    for tag, facts in per_tag:
        for fact in facts:
            key = (fact.get("start"), fact["end"])
            candidate = {
                "metric": metric,
                "tag": tag,
                "priority": rank[tag],
                "period_start": date.fromisoformat(fact["start"]) if fact.get("start") else None,
                "period_end": date.fromisoformat(fact["end"]),
                "value": fact["val"],
                "unit": unit,
                "accession_no": fact["accn"],
                "form": fact["form"],
                "filed": date.fromisoformat(fact["filed"]),
                "fy": fact.get("fy"),
                "fp": fact.get("fp"),
                "derived_from": None,
            }
            current = chosen.get(key)
            if current is None or (rank[tag], -candidate["filed"].toordinal()) < (
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
    return series


def extract_annual(companyfacts: dict, metric: str, years: int = 3, unit: str = "USD") -> list[dict]:
    """Bir kalemin son 'years' mali yılına ait değerleri, eskiden yeniye sıralı döndürür.

    Yalnızca doğrudan raporlanan etiketleri okur; türetme yapmaz (bkz. extract_all).
    years=0 tüm dönemleri döndürür.
    """
    if metric not in METRICS:
        raise KeyError(f"Bilinmeyen kalem: {metric}")
    tags, is_instant = METRICS[metric]
    series = _extract_tags(companyfacts, metric, tags, is_instant, unit)
    return series[-years:] if years else series


def _derived_item(metric: str, period_start: date | None, period_end: date, value,
                  sources: list[dict]) -> dict:
    """Türetilmiş FactItem; kaynak bilgisi kullanılan en son başvurudan alınır."""
    latest = max(sources, key=lambda s: s["filed"])  # eşitlikte ilk kaynak
    return {
        "metric": metric,
        "tag": DERIVED_TAG,
        "period_start": period_start,
        "period_end": period_end,
        "value": value,
        "unit": sources[0]["unit"],
        "accession_no": latest["accession_no"],
        "form": latest["form"],
        "filed": latest["filed"],
        "fy": latest["fy"],
        "fp": latest["fp"],
        "derived_from": [s["tag"] for s in sources],
    }


def _derive_gross_profit(direct: list[dict], revenue: list[dict],
                         cost: list[dict]) -> list[dict]:
    """GrossProfit olmayan dönemlerde: revenue − cost_of_revenue (aynı period_end)."""
    have = {i["period_end"] for i in direct}
    cost_by_end = {i["period_end"]: i for i in cost}
    out = list(direct)
    for rev in revenue:
        end = rev["period_end"]
        cogs = cost_by_end.get(end)
        if end in have or cogs is None:
            continue
        out.append(_derived_item("gross_profit", rev["period_start"], end,
                                 rev["value"] - cogs["value"], [rev, cogs]))
    return sorted(out, key=lambda x: x["period_end"])


def _derive_total_liabilities(direct: list[dict], lse: list[dict],
                              equity: list[dict]) -> list[dict]:
    """Liabilities olmayan dönemlerde: LiabilitiesAndStockholdersEquity − toplam özkaynak."""
    have = {i["period_end"] for i in direct}
    eq_by_end = {i["period_end"]: i for i in equity}
    out = list(direct)
    for total in lse:
        end = total["period_end"]
        eq = eq_by_end.get(end)
        if end in have or eq is None:
            continue
        out.append(_derived_item("total_liabilities", None, end,
                                 total["value"] - eq["value"], [total, eq]))
    return sorted(out, key=lambda x: x["period_end"])


def extract_all(companyfacts: dict, years: int = 3) -> dict[str, list[dict]]:
    """Tüm kalemleri (METRICS sırasıyla) türetme kurallarını uygulayarak döndürür.

    - gross_profit: GrossProfit yoksa aynı dönem için revenue − cost_of_revenue.
    - total_liabilities: Liabilities yoksa aynı period_end için
      LiabilitiesAndStockholdersEquity − (StockholdersEquityIncludingPortionAttributable
      ToNoncontrollingInterest, yoksa StockholdersEquity).
    Türetme yalnızca doğrudan değeri olmayan dönemler için yapılır; ardından her kalemin son
    'years' dönemi alınır (years=0 → tümü).

    Not (ortalama hesapları): ROA/ROE ortalaması bir önceki mali yıl sonunu gerektirir.
    Entegrasyon katmanı bu yüzden bir yıl fazla çeker ve dönem seçimini oranlara bırakır:
    ``facts = extract_all(cf, years=4)`` ardından ``ratios.compute_ratios(facts, periods=3)``.
    """
    full = {m: extract_annual(companyfacts, m, years=0) for m in METRICS}
    full["gross_profit"] = _derive_gross_profit(
        full["gross_profit"], full["revenue"], full["cost_of_revenue"])
    lse = _extract_tags(companyfacts, "total_liabilities", _LSE_TAGS, True)
    equity = _extract_tags(companyfacts, "total_liabilities", _TOTAL_EQUITY_TAGS, True)
    full["total_liabilities"] = _derive_total_liabilities(
        full["total_liabilities"], lse, equity)
    return {m: (s[-years:] if years else s) for m, s in full.items()}


def _is_consecutive_year(prev: dict, cur: dict) -> bool:
    """İki öğe ardışık mali yıl mı? period_end yoksa (eski çağrılar) kontrol yapılmaz.

    ratios.is_prior_fiscal_year ile aynı kural (330–400 gün); döngüsel import olmasın diye
    burada tekrarlanır.
    """
    p, c = prev.get("period_end"), cur.get("period_end")
    if p is None or c is None:
        return True
    return 330 <= (c - p).days <= 400


def yoy_changes(series: list[dict]) -> list[float | None]:
    """Yıllık yüzde değişim. İlk yıl, sıfır/negatif tabanlı yıllar ve ardışık olmayan
    dönemler (eksik yıl, mali yıl sonu değişimi) için None."""
    changes: list[float | None] = [None]
    for prev, cur in zip(series, series[1:]):
        base = prev["value"]
        if not base or base < 0 or not _is_consecutive_year(prev, cur):
            changes.append(None)
        else:
            changes.append(round((cur["value"] - base) / base * 100, 2))
    return changes


def yoy_ratio(series: list[dict]) -> list[float | None]:
    """Ardışık öğeler arası değişim, ham oran: (cur − prev) / |prev|.

    İlk öğe, prev 0 veya None, cur None ve iki öğe arası bir mali yıl (330–400 gün) değilse
    None. Yuvarlama yapılmaz.
    (yoy_changes'tan farkı: yüzde değil oran döndürür, negatif tabanda mutlak değerle böler.)
    """
    changes: list[float | None] = [None]
    for prev, cur in zip(series, series[1:]):
        base, value = prev.get("value"), cur.get("value")
        if base is None or value is None or base == 0 or not _is_consecutive_year(prev, cur):
            changes.append(None)
        else:
            changes.append((float(value) - float(base)) / abs(float(base)))
    return changes
