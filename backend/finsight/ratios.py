"""Finansal oranlar (FR-06, FR-07; NFR-06 izlenebilirlik).

Kurallar .claude/skills/financial-analysis/SKILL.md §2 ile birebir aynıdır:
- Değerler ham oran olarak döner (0.253 = %25,3); yuvarlama yapılmaz.
- Dönemler revenue ∪ net_income ∪ total_assets mali yıl sonlarının birleşimidir; birbirine
  30 günden yakın tarihler aynı dönemdir (en son tarih tutulur).
- Akış ve bilanço kalemleri period_end ±7 gün toleransla (en yakın) eşleşir (52–53 haftalık
  mali yıl, gelir 28 Aralık / bilanço 31 Aralık gibi farklar).
- ROA/ROE paydası ortalamadır: (dönem sonu + bir önceki mali yıl sonu) / 2. Önceki mali yıl
  sonu (period_end farkı 330–400 gün) yoksa dönem sonu değeri kullanılır ve note yazılır.
  ROE'de önceki dönem özkaynağı ≤ 0 ise de dönem sonu değeri kullanılır (ayrı note).
- Hesaplanamayan oran: value=None, status="missing", reason dolu. Eksik veri 0 sayılmaz.

Girdi: xbrl.extract_all çıktısı. Ortalamaya yetmesi için entegrasyon katmanı
``extract_all(cf, years=4)`` çağırır; bu modül dönemleri kendisi seçer (``periods``).
"""

from datetime import date

NOTE_PERIOD_END = "ortalama yerine dönem sonu değeri"
NOTE_PRIOR_EQUITY = "önceki dönem özkaynağı negatif; ortalama yerine dönem sonu değeri"
MATCH_TOLERANCE_DAYS = 7  # akış/bilanço kalemi eşleştirme toleransı
SAME_PERIOD_DAYS = 30  # bundan yakın dönem sonları aynı dönemdir
PERIOD_BASE_METRICS = ("revenue", "net_income", "total_assets")
REASON_ZERO = "payda sıfır"
REASON_EQUITY = "özkaynak negatif veya sıfır; oran anlamlı değil"
REASON_NET_INCOME = "net kâr sıfır veya negatif; oran anlamlı değil"
# Gelir 0 ise genel kural ("payda sıfır") geçerlidir; negatif gelir için bu metin.
REASON_REVENUE = "gelir negatif; marj oranı anlamlı değil"

RATIO_DEFS: list[dict] = [
    {"key": "gross_margin", "label": "Brüt kâr marjı",
     "formula": "Brüt kâr / Gelir", "unit": "percent", "higher_is_better": True},
    {"key": "operating_margin", "label": "Faaliyet kâr marjı",
     "formula": "Faaliyet kârı / Gelir", "unit": "percent", "higher_is_better": True},
    {"key": "net_margin", "label": "Net kâr marjı",
     "formula": "Net kâr / Gelir", "unit": "percent", "higher_is_better": True},
    {"key": "roa", "label": "Aktif kârlılığı (ROA)",
     "formula": "Net kâr / Ortalama toplam varlıklar", "unit": "percent",
     "higher_is_better": True},
    {"key": "roe", "label": "Özkaynak kârlılığı (ROE)",
     "formula": "Net kâr / Ortalama özkaynak", "unit": "percent", "higher_is_better": True},
    {"key": "current_ratio", "label": "Cari oran",
     "formula": "Dönen varlıklar / Kısa vadeli yükümlülükler", "unit": "times",
     "higher_is_better": True},
    {"key": "debt_to_equity", "label": "Borç / özkaynak",
     "formula": "Toplam yükümlülükler / Özkaynak", "unit": "times",
     "higher_is_better": False},
    {"key": "liabilities_to_assets", "label": "Yükümlülük / varlık",
     "formula": "Toplam yükümlülükler / Toplam varlıklar", "unit": "percent",
     "higher_is_better": False},
    {"key": "ocf_to_net_income", "label": "Nakit akışı kalitesi",
     "formula": "Faaliyetlerden nakit akışı / Net kâr", "unit": "times",
     "higher_is_better": True},
    {"key": "fcf_margin", "label": "Serbest nakit akışı marjı",
     "formula": "(Faaliyetlerden nakit akışı − Yatırım harcamaları) / Gelir",
     "unit": "percent", "higher_is_better": True},
]

# key -> (pay kalemleri, payda kalemi, paydada ortalama mı, payda kuralı)
# Pay kalemleri birden fazlaysa ilki eksi diğerleri (fcf: ocf − capex).
_SPECS: dict[str, tuple[list[str], str, bool, str]] = {
    "gross_margin": (["gross_profit"], "revenue", False, "revenue"),
    "operating_margin": (["operating_income"], "revenue", False, "revenue"),
    "net_margin": (["net_income"], "revenue", False, "revenue"),
    "roa": (["net_income"], "total_assets", True, "plain"),
    "roe": (["net_income"], "stockholders_equity", True, "equity"),
    "current_ratio": (["current_assets"], "current_liabilities", False, "plain"),
    "debt_to_equity": (["total_liabilities"], "stockholders_equity", False, "equity"),
    "liabilities_to_assets": (["total_liabilities"], "total_assets", False, "plain"),
    "ocf_to_net_income": (["operating_cash_flow"], "net_income", False, "net_income"),
    "fcf_margin": (["operating_cash_flow", "capex"], "revenue", False, "revenue"),
}


def is_prior_fiscal_year(prev_end: date, cur_end: date) -> bool:
    """prev_end, cur_end'den bir önceki mali yıl sonu mu (330–400 gün önce)?"""
    return 330 <= (cur_end - prev_end).days <= 400


def _index(facts: dict[str, list[dict]]) -> dict[str, dict[date, dict]]:
    return {m: {i["period_end"]: i for i in (series or [])} for m, series in facts.items()}


def match_period(by_end: dict[date, dict], end: date,
                 tolerance_days: int = MATCH_TOLERANCE_DAYS) -> dict | None:
    """`end` ile aynı dönemdeki kayıt: tam eşleşme, yoksa ±`tolerance_days` içindeki en yakın."""
    if end in by_end:
        return by_end[end]
    near = [(abs((e - end).days), -e.toordinal(), e) for e in by_end
            if abs((e - end).days) <= tolerance_days]
    return by_end[min(near)[2]] if near else None


def _prior(by_end: dict[date, dict], cur_end: date) -> dict | None:
    candidates = [i for end, i in by_end.items() if is_prior_fiscal_year(end, cur_end)]
    return max(candidates, key=lambda i: i["period_end"]) if candidates else None


def select_periods(facts: dict[str, list[dict]], periods: int = 3) -> list[date]:
    """Son `periods` mali yıl sonu, eskiden yeniye.

    Aday tarihler revenue ∪ net_income ∪ total_assets dönem sonlarıdır (gelir etiketi listede
    olmayan bir etikete geçse bile dönemler eskide takılı kalmaz). Birbirine 30 günden yakın
    tarihler (ör. gelir 2024-12-28, bilanço 2024-12-31) tek dönem sayılır; en son tarih tutulur.
    """
    ends = sorted({i["period_end"] for m in PERIOD_BASE_METRICS for i in (facts.get(m) or [])})
    merged: list[date] = []
    for end in ends:
        if merged and (end - merged[-1]).days < SAME_PERIOD_DAYS:
            merged[-1] = end  # aynı dönem, farklı tarih → en son tarih
        else:
            merged.append(end)
    return merged[-periods:] if periods else merged


def _input(item: dict) -> dict:
    return {"metric": item["metric"], "value": float(item["value"]),
            "accession_no": item["accession_no"]}


def _entry(end: date, value=None, reason=None, note=None, inputs=None) -> dict:
    return {"period_end": end, "value": value, "status": "ok" if reason is None else "missing",
            "reason": reason, "note": note, "inputs": inputs or []}


def _compute_one(key: str, end: date, idx: dict[str, dict[date, dict]]) -> dict:
    num_metrics, den_metric, use_avg, rule = _SPECS[key]
    num_items = [match_period(idx.get(m, {}), end) for m in num_metrics]
    den_item = match_period(idx.get(den_metric, {}), end)
    prev_item = (_prior(idx.get(den_metric, {}), den_item["period_end"])
                 if use_avg and den_item is not None else None)

    found = [i for i in [*num_items, den_item, prev_item] if i is not None]
    inputs = [_input(i) for i in found]
    missing = [m for m, i in zip([*num_metrics, den_metric], [*num_items, den_item]) if i is None]
    present = [i for i in num_items if i is not None]
    if missing or den_item is None or len(present) != len(num_items):
        return _entry(end, reason="eksik veri: " + ", ".join(missing), inputs=inputs)

    numerator = float(present[0]["value"]) - sum(float(i["value"]) for i in present[1:])
    cur_den = float(den_item["value"])
    note = None
    if use_avg and prev_item is not None:
        prev_den = float(prev_item["value"])
        if rule == "equity" and prev_den <= 0 < cur_den:
            denominator, note = cur_den, NOTE_PRIOR_EQUITY
        else:
            denominator = (cur_den + prev_den) / 2
    else:
        denominator = cur_den
        if use_avg:
            note = NOTE_PERIOD_END

    if rule == "equity" and (cur_den <= 0 or denominator <= 0):
        return _entry(end, reason=REASON_EQUITY, inputs=inputs)
    if rule == "net_income" and cur_den <= 0:
        return _entry(end, reason=REASON_NET_INCOME, inputs=inputs)
    if denominator == 0:
        return _entry(end, reason=REASON_ZERO, inputs=inputs)
    if rule == "revenue" and cur_den < 0:
        return _entry(end, reason=REASON_REVENUE, inputs=inputs)
    return _entry(end, value=numerator / denominator, note=note, inputs=inputs)


def compute_ratios(facts: dict[str, list[dict]], periods: int = 3) -> dict:
    """RATIO_DEFS'teki 10 oranı son `periods` mali yıl için hesaplar.

    Çıktı: {"periods": [date], "ratios": [{key, label, formula, unit, higher_is_better,
    "values": [{period_end, value, status, reason, note, inputs}]}]} — API /ratios şeklinin
    company/disclaimer hariç hali. `values` her dönem için bir öğe, `periods` sırasıyla.
    """
    idx = _index(facts)
    ends = select_periods(facts, periods)
    ratios = []
    for d in RATIO_DEFS:
        ratios.append({**d, "values": [_compute_one(d["key"], end, idx) for end in ends]})
    return {"periods": ends, "ratios": ratios}
