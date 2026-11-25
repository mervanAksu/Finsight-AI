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

from datetime import date, timedelta

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


# =========================================================================== TTM (Aşama 4)
#
# Son on iki ay (trailing twelve months) — docs/asama4-sozlesme.md §2, FR-04/FR-06/FR-11.
#
#   A         : en son yıllık değer (10-K, 330–400 gün), dönem sonu E_A
#   E_Q       : E_A'dan sonra biten en son 10-Q dönem sonu
#   YTD_cur   : E_Q'da biten, mali yıl başında (E_A + 1 gün) başlayan süreli değer
#   YTD_prior : bir yıl önceki aynı YTD dönemi (bitiş ≈ E_Q − 1 yıl, başlangıç ≈ A'nın başı)
#   Süreli kalem: TTM = A + YTD_cur − YTD_prior
#   Anlık kalem : E_Q tarihli 10-Q bilanço değeri (önceki: E_Q − 1 yıl tarihli bilanço)
#   Önceki TTM  : A_önceki + YTD_prior − YTD_prior2 (büyüme için)
#
# Tüm tarih eşleşmeleri ±7 gün toleranslıdır (52–53 haftalık mali yıllar). Bir parça
# eksikse o kalem için TTM üretilmez (uydurma / oranlama yok); neden `reasons` içinde döner
# ve dinamik seri o kalem için yıllık değere düşer.

QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
PERIODIC_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS
BASIS_ANNUAL = "annual"
BASIS_TTM = "ttm"
TTM_TOLERANCE_DAYS = 7
# E_Q, E_A'dan en az bu kadar sonra olmalı (aynı dönemin tekrarı sayılmasın) ...
TTM_MIN_GAP_DAYS = 30
# ... ve en fazla bu kadar sonra (3. çeyrek ≈ 273 gün; daha uzağı mali yıl değişimi vb.).
TTM_MAX_GAP_DAYS = 300
DYNAMIC_BASE_METRICS = ("revenue", "net_income", "total_assets")
TTM_FLOW_BASE_METRICS = ("revenue", "net_income")
NOTE_NO_FLOW_TTM = ("Gelir ve net kâr için TTM hesaplanamadı; yalnızca bilanço kalemleri son "
                    "10-Q'ya geçtiği için dayanak yıllık kaldı")
ROLE_ANNUAL = "annual"
ROLE_YTD_CURRENT = "ytd_current"
ROLE_YTD_PRIOR = "ytd_prior"

REASON_TTM_NO_ANNUAL = "yıllık değer (A) yok"
REASON_TTM_NO_YTD_CUR = "mali yıl başından bugüne (YTD) değeri yok"
REASON_TTM_NO_YTD_PRIOR = "önceki yılın aynı YTD dönemi yok"
REASON_TTM_NO_BALANCE = "10-Q bilanço değeri yok"


def _iso_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _req_date(value: str) -> date:
    """Zorunlu tarih alanı (süzgeçten geçmiş kayıtlarda end/start/filed her zaman dolu)."""
    return date.fromisoformat(value)


def years_before(d: date, years: int = 1) -> date:
    """Aynı ay/gün, `years` yıl önce (29 Şubat → 28 Şubat)."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def _near(a: date | None, b: date | None, tolerance: int = TTM_TOLERANCE_DAYS) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= tolerance


def _periodic_facts(us_gaap: dict, tag: str, unit: str) -> list[dict]:
    """Etiketin 10-K/10-Q (ve /A) kayıtları; değeri sayı olmayanlar atlanır."""
    out = []
    for fact in us_gaap.get(tag, {}).get("units", {}).get(unit, []):
        if fact.get("form") not in PERIODIC_FORMS or not fact.get("end"):
            continue
        if not isinstance(fact.get("val"), (int, float)) or isinstance(fact.get("val"), bool):
            continue
        out.append(fact)
    return out


def _latest_filed(facts) -> dict | None:
    """Aynı dönemi raporlayan birden çok başvuru: en son `filed` (eşitlikte accession)."""
    facts = list(facts)
    if not facts:
        return None
    return max(facts, key=lambda f: (f.get("filed") or "", f.get("accn") or ""))


def _find_duration(facts: list[dict], start: date, end: date) -> dict | None:
    """Başlangıcı ≈ start, bitişi ≈ end olan süreli kayıt (±7 gün), en son başvurudan."""
    return _latest_filed(
        f for f in facts
        if f.get("start") and _near(_iso_date(f["start"]), start)
        and _near(_iso_date(f["end"]), end))


def _find_annual(facts: list[dict], end: date) -> dict | None:
    return _latest_filed(f for f in facts if f.get("form") in ANNUAL_FORMS
                         and _is_annual_duration(f) and _near(_iso_date(f["end"]), end))


def _find_instant(facts: list[dict], end: date) -> dict | None:
    return _latest_filed(f for f in facts
                         if not f.get("start") and _near(_iso_date(f["end"]), end))


def _original_value(facts: list[dict], chosen: dict):
    """Aynı dönem (±7 gün) için daha önce FARKLI değer raporlanmışsa ilk raporlanan değer.

    Yeniden düzenlenmiş karşılaştırma (restatement): en son başvurudaki değer kullanılır
    (yaygın LTM uygulaması), ama ilk değer izlenebilirlik için parçada gösterilir.
    """
    start, end = _iso_date(chosen.get("start")), _req_date(chosen["end"])
    same = [f for f in facts
            if _near(_req_date(f["end"]), end)
            and ((start is None and not f.get("start"))
                 or (start is not None and _near(_iso_date(f.get("start")), start)))
            and f.get("val") != chosen["val"]
            and (f.get("filed") or "") < (chosen.get("filed") or "")]
    if not same:
        return None
    return min(same, key=lambda f: (f.get("filed") or "", f.get("accn") or ""))["val"]


def _part(role: str, tag: str, fact: dict, facts: list[dict] | None = None) -> dict:
    original = _original_value(facts, fact) if facts else None
    return {"role": role, "accession_no": fact["accn"], "form": fact.get("form"), "tag": tag,
            "period_start": _iso_date(fact.get("start")), "period_end": _req_date(fact["end"]),
            "value": fact["val"], "restated": original is not None,
            "original_value": original}


def _ttm_point(metric: str, tag: str, a: dict, cur: dict, prior: dict, unit: str,
               facts: list[dict] | None = None) -> dict:
    """TTM = A + YTD_cur − YTD_prior; kaynak bilgisi YTD_cur'un başvurusundan."""
    value = a["val"] + cur["val"] - prior["val"]
    end = _req_date(cur["end"])
    return {
        "metric": metric, "tag": tag,
        "period_start": _req_date(prior["end"]) + timedelta(days=1),
        "period_end": end, "value": value, "unit": unit,
        "accession_no": cur["accn"], "form": cur["form"],
        "filed": _req_date(cur["filed"]), "fy": cur.get("fy"), "fp": cur.get("fp"),
        "basis": BASIS_TTM,
        "derived_from": [_part(ROLE_ANNUAL, tag, a, facts),
                         _part(ROLE_YTD_CURRENT, tag, cur, facts),
                         _part(ROLE_YTD_PRIOR, tag, prior, facts)],
    }


def _instant_point(metric: str, tag: str, fact: dict, unit: str) -> dict:
    return {
        "metric": metric, "tag": tag, "period_start": None,
        "period_end": _req_date(fact["end"]), "value": fact["val"], "unit": unit,
        "accession_no": fact["accn"], "form": fact["form"],
        "filed": _req_date(fact["filed"]), "fy": fact.get("fy"), "fp": fact.get("fp"),
        "basis": BASIS_TTM, "derived_from": None,
    }


def _ttm_duration_for_tag(metric: str, tag: str, facts: list[dict], e_a: date, e_q: date,
                          unit: str) -> tuple[dict | None, dict | None, str | None]:
    """(TTM noktası, önceki TTM noktası, neden). Tüm parçalar aynı etiketten alınır."""
    a = _find_annual(facts, e_a)
    if a is None:
        return None, None, REASON_TTM_NO_ANNUAL
    a_start = _req_date(a["start"])
    # YTD_cur: mali yıl başı (E_A + 1) → E_Q. 3 aylık çeyrek değeri (Q2/Q3'te başlangıcı
    # E_Q − 3 ay) bu süzgece takılır; Q1'de çeyrek = YTD olduğundan doğru değer seçilir.
    cur = _find_duration(facts, e_a + timedelta(days=1), e_q)
    if cur is None:
        return None, None, REASON_TTM_NO_YTD_CUR
    prior = _find_duration(facts, a_start, years_before(_req_date(cur["end"])))
    if prior is None:
        return None, None, REASON_TTM_NO_YTD_PRIOR
    point = _ttm_point(metric, tag, a, cur, prior, unit, facts)

    prev_point = None
    a_prior = _find_annual(facts, a_start - timedelta(days=1))
    if a_prior is not None:
        prior2 = _find_duration(facts, _req_date(a_prior["start"]),
                                years_before(_req_date(prior["end"])))
        if prior2 is not None:
            prev_point = _ttm_point(metric, tag, a_prior, prior, prior2, unit, facts)
    return point, prev_point, None


def _ttm_instant_for_tag(metric: str, tag: str, facts: list[dict], e_q: date,
                         unit: str) -> tuple[dict | None, dict | None, str | None]:
    """E_Q bilançosu ve E_Q − 1 yıl bilançosu — ikisi de AYNI (birincil) etiketten. Önceki
    yoksa None (oranlarda ortalama yerine dönem sonu değeri + not)."""
    latest = _find_instant([f for f in facts if f["form"] in QUARTERLY_FORMS], e_q)
    if latest is None:
        return None, None, REASON_TTM_NO_BALANCE
    prior = _find_instant(facts, years_before(_req_date(latest["end"])))
    return (_instant_point(metric, tag, latest, unit),
            _instant_point(metric, tag, prior, unit) if prior else None, None)


def annual_primary_tag(us_gaap: dict, tags: list[str], is_instant: bool,
                       unit: str = "USD") -> str | None:
    """Yıllık serinin birincil etiketi (financial-analysis §1: en son yıllık period_end)."""
    order = _tag_order([(t, _valid_annual_facts(us_gaap, t, is_instant, unit)) for t in tags])
    return order[0] if order else None


def reason_other_tag(primary: str, other: str) -> str:
    return (f"TTM parçaları birincil etiket ({primary}) altında eksik; {other} etiketine "
            "geçilmedi")


def _ttm_for_tag(metric: str, tag: str, is_instant: bool, facts: list[dict], e_a: date,
                e_q: date, unit: str) -> tuple[dict | None, dict | None, str | None]:
    if is_instant:
        return _ttm_instant_for_tag(metric, tag, facts, e_q, unit)
    return _ttm_duration_for_tag(metric, tag, facts, e_a, e_q, unit)


def _ordered_periodic(us_gaap: dict, tags: list[str], unit: str) -> list[tuple[str, list[dict]]]:
    """Etiket → 10-K/10-Q kayıtları; birincil etiket (en son dönem sonu) önce."""
    per_tag = [(tag, _periodic_facts(us_gaap, tag, unit)) for tag in tags]
    order = _tag_order(per_tag)
    facts_by_tag = dict(per_tag)
    return [(tag, facts_by_tag[tag]) for tag in order]


def latest_annual_end(annual: dict[str, list[dict]]) -> date | None:
    """E_A: temel kalemlerin (gelir, net kâr, toplam varlık) en son yıllık dönem sonu."""
    ends = [i["period_end"] for m in DYNAMIC_BASE_METRICS for i in (annual.get(m) or [])]
    return max(ends) if ends else None


def latest_quarter_end(companyfacts: dict, e_a: date | None,
                       unit: str = "USD") -> date | None:
    """E_Q: E_A'dan 30–300 gün sonra biten en son 10-Q (veya 10-Q/A) dönem sonu."""
    if e_a is None:
        return None
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    best = None
    for metric in DYNAMIC_BASE_METRICS:
        for tag in METRICS[metric][0]:
            for fact in _periodic_facts(us_gaap, tag, unit):
                if fact["form"] not in QUARTERLY_FORMS:
                    continue
                end = _req_date(fact["end"])
                if TTM_MIN_GAP_DAYS < (end - e_a).days <= TTM_MAX_GAP_DAYS and (
                        best is None or end > best):
                    best = end
    return best


ROLE_COMPONENT = "component"


def _component(item: dict) -> dict:
    """Türetilmiş TTM'nin bileşeni (NFR-06): bileşen TTM ise kendi parçaları da taşınır."""
    return {"role": ROLE_COMPONENT, "metric": item["metric"], "tag": item["tag"],
            "accession_no": item["accession_no"], "form": item["form"],
            "period_start": item.get("period_start"), "period_end": item["period_end"],
            "value": item["value"],
            "parts": [p for p in (item.get("derived_from") or []) if isinstance(p, dict)]}


def _derive_ttm_gross_profit(out: dict, reasons: dict) -> None:
    """GrossProfit TTM yoksa: gelir TTM − satış maliyeti TTM (aynı dönem, ikisi de TTM)."""
    if out.get("gross_profit"):
        return
    rev, cost = out.get("revenue") or [], out.get("cost_of_revenue") or []
    cost_by_end = {i["period_end"]: i for i in cost}
    derived = []
    for r in rev:
        c = cost_by_end.get(r["period_end"])
        if c is None:
            continue
        parts = [_component(r), _component(c)]
        derived.append({**r, "metric": "gross_profit", "tag": DERIVED_TAG,
                        "value": r["value"] - c["value"], "derived_from": parts})
    if derived:
        out["gross_profit"] = derived
        reasons.pop("gross_profit", None)


def _derive_ttm_total_liabilities(us_gaap: dict, out: dict, reasons: dict, unit: str) -> None:
    """Liabilities yoksa E_Q (ve E_Q − 1 yıl) için LiabilitiesAndStockholdersEquity − özkaynak."""
    if out.get("total_liabilities"):
        return
    equity_points = out.get("stockholders_equity") or []
    # Birincil etiket kuralı türetmede de geçerli: her iki bileşen yıllık birincil etiketinden.
    t_tag = annual_primary_tag(us_gaap, _LSE_TAGS, True, unit) or _LSE_TAGS[0]
    e_tag = annual_primary_tag(us_gaap, _TOTAL_EQUITY_TAGS, True, unit) \
        or _TOTAL_EQUITY_TAGS[-1]
    lse = _periodic_facts(us_gaap, t_tag, unit)
    total_eq = _periodic_facts(us_gaap, e_tag, unit)
    derived = []
    for ref in equity_points:
        end = ref["period_end"]
        t_fact, e_fact = _find_instant(lse, end), _find_instant(total_eq, end)
        if t_fact is None or e_fact is None:
            continue
        latest = max((t_fact, e_fact), key=lambda f: f["filed"])
        comps = [{"role": ROLE_COMPONENT, "metric": m, "tag": tg, "accession_no": f["accn"],
                  "form": f["form"], "period_start": None, "period_end": _req_date(f["end"]),
                  "value": f["val"]}
                 for m, tg, f in (("liabilities_and_equity", t_tag, t_fact),
                                  ("total_equity", e_tag, e_fact))]
        derived.append({
            "metric": "total_liabilities", "tag": DERIVED_TAG, "period_start": None,
            "period_end": _req_date(t_fact["end"]), "value": t_fact["val"] - e_fact["val"],
            "unit": unit, "accession_no": latest["accn"], "form": latest["form"],
            "filed": _req_date(latest["filed"]), "fy": latest.get("fy"),
            "fp": latest.get("fp"), "basis": BASIS_TTM, "derived_from": comps})
    if derived:
        out["total_liabilities"] = sorted(derived, key=lambda x: x["period_end"])
        reasons.pop("total_liabilities", None)


def extract_ttm(companyfacts: dict, annual: dict[str, list[dict]] | None = None,
                unit: str = "USD") -> dict:
    """companyfacts'ten TTM noktaları (docs/asama4-sozlesme.md §2).

    annual: ``extract_all(cf, years=0)`` çıktısı (verilmezse hesaplanır); E_A buradan alınır.
    Dönüş::

        {"annual_period": E_A | None, "as_of_period": E_Q | None,
         "facts": {metric: [önceki TTM noktası?, TTM noktası]},  # 0–2 nokta, eskiden yeniye
         "reasons": {metric: "TTM üretilmedi nedeni"},
         "reason": "hiç TTM yoksa genel neden" | None}

    Noktalar FactItem biçimindedir; ek olarak ``basis: "ttm"`` ve süreli kalemlerde
    ``derived_from: [{role, accession_no, form, tag, period_start, period_end, value}]``
    taşır (NFR-06). Anlık kalemlerde derived_from None'dur (doğrudan 10-Q değeri).
    """
    annual = annual if annual is not None else extract_all(companyfacts, years=0)
    e_a = latest_annual_end(annual)
    result: dict = {"annual_period": e_a, "as_of_period": None, "facts": {}, "reasons": {},
                    "reason": None}
    if e_a is None:
        result["reason"] = "yıllık veri yok"
        return result
    e_q = latest_quarter_end(companyfacts, e_a, unit)
    if e_q is None:
        result["reason"] = "son 10-K'dan sonra yayımlanmış 10-Q verisi yok"
        return result

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    out: dict[str, list[dict]] = {}
    reasons: dict[str, str] = {}
    for metric, (tags, is_instant) in METRICS.items():
        # TTM yalnızca yıllık serinin birincil etiketinden üretilir: A ile YTD parçaları aynı
        # tanımlı olmalı; başka etikete geçilmez (inceleme kararı).
        primary = annual_primary_tag(us_gaap, tags, is_instant, unit)
        if primary is None:
            continue  # yıllık değer de yok: neden yazılmaz
        point, prev, reason = _ttm_for_tag(metric, primary, is_instant,
                                           _periodic_facts(us_gaap, primary, unit), e_a, e_q,
                                           unit)
        if point is None:
            for other in tags:
                if other == primary:
                    continue
                alt, _, _ = _ttm_for_tag(metric, other, is_instant,
                                         _periodic_facts(us_gaap, other, unit), e_a, e_q, unit)
                if alt is not None:
                    reason = reason_other_tag(primary, other)
                    break
            reason = reason or "veri yok"
            reasons[metric] = reason if "birincil etiket" in reason \
                else f"{reason} (etiket {primary})"
            continue
        out[metric] = [p for p in (prev, point) if p is not None]
    _derive_ttm_gross_profit(out, reasons)
    _derive_ttm_total_liabilities(us_gaap, out, reasons, unit)

    if not out:
        result["reason"] = "TTM parçaları eksik"
        result["reasons"] = reasons
        return result
    result.update(as_of_period=e_q, facts=out, reasons=reasons)
    return result


def _basis(item: dict | None) -> str:
    return (item or {}).get("basis") or BASIS_ANNUAL


def _filing_ref(items: list) -> dict | None:
    """Noktaların kaynağı olan en son başvuru: {form, accession_no, filing_date}."""
    items = [i for i in items if i and i.get("accession_no")]
    if not items:
        return None
    latest = max(items, key=lambda i: (i.get("filed") or date.min, i["accession_no"]))
    return {"form": latest.get("form"), "accession_no": latest["accession_no"],
            "filing_date": latest.get("filed")}


def build_dynamic(annual: dict[str, list[dict]], ttm: dict[str, list[dict]] | None = None,
                  reasons: dict[str, str] | None = None) -> dict:
    """Yıllık seri + TTM noktalarından "dinamik" görünüm (skor ve son dönem oranları için).

    annual: extract_all çıktısı (yalnızca yıllık noktalar). ttm: extract_ttm(...)["facts"]
    veya veritabanından okunan basis="ttm" noktaları. E_A'dan en az 30 gün sonra bitmeyen
    (eskimiş) TTM noktaları yok sayılır. Dönüş::

        {"basis": "ttm" | "annual", "as_of_period": E_Q | E_A, "data_through": aynı,
         "annual_period": E_A, "as_of_filing": {form, accession_no, filing_date} | None,
         "latest": {metric: FactItem | None}, "prior": {metric: FactItem | None},
         "reasons": {metric: neden}}

    TTM'si olmayan kalemde latest/prior yıllık değerlerdir (basis "annual") ve neden
    ``reasons``'a yazılır (uydurma yapılmaz).
    """
    annual = annual or {}
    e_a = latest_annual_end(annual)
    reasons = dict(reasons or {})
    ttm_points = {m: [p for p in (s or []) if p.get("period_end") is not None]
                  for m, s in (ttm or {}).items()}
    ends = [p["period_end"] for s in ttm_points.values() for p in s
            if e_a is None or (p["period_end"] - e_a).days > TTM_MIN_GAP_DAYS]
    e_q = max(ends) if ends else None
    note = None
    # Dayanak ancak en az bir temel AKIŞ kalemi (gelir / net kâr) gerçekten TTM ise "ttm" olur;
    # yalnızca bilanço E_Q'ya geçtiyse skor ve oranlar yıllık E_A'da kalır (inceleme kararı).
    if e_q is not None and not any(_near(p["period_end"], e_q)
                                   for m in TTM_FLOW_BASE_METRICS
                                   for p in ttm_points.get(m) or []):
        note = NOTE_NO_FLOW_TTM
        e_q = None

    latest: dict[str, dict | None] = {}
    prior: dict[str, dict | None] = {}
    for metric in METRICS:
        series = sorted(annual.get(metric) or [], key=lambda i: i["period_end"])
        a_latest = series[-1] if series else None
        a_prior = None
        if a_latest is not None:
            cands = [i for i in series
                     if 330 <= (a_latest["period_end"] - i["period_end"]).days <= 400]
            a_prior = cands[-1] if cands else None
        points = ttm_points.get(metric) or []
        t_latest = next((p for p in points if e_q is not None and _near(p["period_end"], e_q)),
                        None)
        if t_latest is not None:
            t_prior = [p for p in points
                       if 330 <= (t_latest["period_end"] - p["period_end"]).days <= 400]
            latest[metric] = t_latest
            prior[metric] = t_prior[-1] if t_prior else None
            reasons.pop(metric, None)
        else:
            latest[metric], prior[metric] = a_latest, a_prior
            if e_q is None or a_latest is None:
                reasons.pop(metric, None)  # hiç veri yok: "yıllığa düşüldü" denmez
            else:
                why = reasons.get(metric) or "TTM parçaları eksik"
                reasons[metric] = f"TTM hesaplanamadı ({why}); yıllık değer kullanıldı"

    basis = BASIS_TTM if e_q is not None else BASIS_ANNUAL
    as_of = e_q if e_q is not None else e_a
    base_items = [item for m in DYNAMIC_BASE_METRICS
                  if (item := latest.get(m)) is not None and _near(item["period_end"], as_of)]
    if basis == BASIS_ANNUAL:
        reasons = {}
    return {"basis": basis, "as_of_period": as_of, "data_through": as_of,
            "annual_period": e_a, "as_of_filing": _filing_ref(base_items),
            "latest": latest, "prior": prior, "reasons": reasons, "note": note}


def dynamic_from_companyfacts(companyfacts: dict, annual: dict[str, list[dict]] | None = None
                              ) -> dict:
    """extract_ttm + build_dynamic kısayolu (saf; testler ve CLI için)."""
    annual = annual if annual is not None else extract_all(companyfacts, years=0)
    ttm = extract_ttm(companyfacts, annual)
    return build_dynamic(annual, ttm["facts"], ttm["reasons"])
