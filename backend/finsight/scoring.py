"""100 puanlık açıklanabilir finansal sağlık skoru (FR-11; NFR-06, NFR-12).

Kurallar .claude/skills/financial-analysis/SKILL.md §3 ile birebir aynıdır (sürüm v1-2026-11).
Skor bir özet göstergedir; derecelendirme veya yatırım tavsiyesi değildir. Ağırlıklar ve
eşikler doğrulanmış bir model değil, başlangıç önerisidir.

- Her bileşen 0–100; bileşen puanı, kullanılabilen göstergelerin ortalamasıdır.
- Toplam = Σ(ağırlık × puan) / Σ(kullanılabilen ağırlık); kullanılabilen ağırlık < 50 → None.
- Tüm göstergeler son mali yıl (ratios["periods"][-1]) içindir.
"""

from datetime import date

from .formatting import MISSING, fmt_decimal, fmt_percent, fmt_times, fmt_usd_short
from .ratios import (
    RATIO_DEFS,
    REASON_EQUITY,
    REASON_NET_INCOME,
    is_prior_fiscal_year,
    match_period,
)
from .xbrl import yoy_ratio

WEIGHTS_VERSION = "v1-2026-11"
MIN_AVAILABLE_WEIGHT = 50

_RATIO_LABELS = {d["key"]: d["label"] for d in RATIO_DEFS}

# Gösterge: (key, label, gösterim türü, a, b) — puan = lin(değer, a, b)
COMPONENTS: list[dict] = [
    {"key": "profitability", "label": "Kârlılık", "weight": 25, "indicators": [
        ("net_margin", _RATIO_LABELS["net_margin"], "percent", 0.0, 0.20),
        ("operating_margin", _RATIO_LABELS["operating_margin"], "percent", 0.0, 0.25),
        ("roa", _RATIO_LABELS["roa"], "percent", 0.0, 0.15),
        ("roe", _RATIO_LABELS["roe"], "percent", 0.0, 0.25),
    ]},
    {"key": "leverage", "label": "Kaldıraç ve borç", "weight": 20, "indicators": [
        ("debt_to_equity", _RATIO_LABELS["debt_to_equity"], "times", 3.0, 0.5),
        ("liabilities_to_assets", _RATIO_LABELS["liabilities_to_assets"], "percent", 0.9, 0.3),
    ]},
    {"key": "liquidity", "label": "Likidite", "weight": 15, "indicators": [
        ("current_ratio", _RATIO_LABELS["current_ratio"], "times", 0.5, 2.0),
    ]},
    {"key": "cash_quality", "label": "Nakit akışı kalitesi", "weight": 15, "indicators": [
        ("ocf_to_net_income", _RATIO_LABELS["ocf_to_net_income"], "times", 0.0, 1.2),
        ("fcf_margin", _RATIO_LABELS["fcf_margin"], "percent", -0.05, 0.15),
    ]},
    {"key": "growth", "label": "Büyüme", "weight": 10, "indicators": [
        ("revenue_yoy", "Gelir büyümesi (YoY)", "percent", -0.10, 0.15),
        ("net_income_yoy", "Net kâr büyümesi (YoY)", "percent", -0.20, 0.20),
    ]},
    {"key": "text_tone", "label": "Metin tonu", "weight": 10, "indicators": [
        ("mdna_net_tone", "MD&A net tonu", "decimal", -0.40, 0.20),
        ("risk_net_tone", "Risk bölümü net tonu", "decimal", -0.80, -0.20),
    ]},
    {"key": "risk_change", "label": "Risk değişimi", "weight": 5, "indicators": [
        ("risk_added", "Eklenen risk paragrafı", "count", 15, 0),
    ]},
]


def lin(x: float | None, a: float, b: float) -> float | None:
    """Doğrusal eşleme: x ≤ a → 0, x ≥ b → 100, arası doğrusal; a > b ise ters yön."""
    if x is None:
        return None
    t = (x - a) / (b - a)
    return 100.0 * min(1.0, max(0.0, t))


def band(total: float | None) -> str | None:
    """Gösterim bandı (derecelendirme değildir)."""
    if total is None:
        return None
    if total >= 75:
        return "Güçlü"
    if total >= 60:
        return "Dengeli"
    if total >= 45:
        return "İzlenmeli"
    return "Zayıf"


# ---- gösterim yardımcıları ----

def _fmt(kind: str, x, threshold: bool = False) -> str:
    if x is None:
        return MISSING
    if kind == "percent":
        return fmt_percent(x, 0 if threshold else 1)
    if kind == "times":
        return fmt_times(x, 1 if threshold else 2)
    if kind == "decimal":
        return fmt_decimal(x, 2)
    return f"{x:g}" if threshold else f"{x} paragraf"


def _pts(p: float) -> str:
    s = fmt_decimal(p, 1)
    return s[:-2] if s.endswith(",0") else s


def _threshold(kind: str, a, b) -> str:
    return f"{_fmt(kind, a, True)}–{_fmt(kind, b, True)}"


def _indicator(spec: tuple, value, points: float | None, reason: str | None = None,
               display: str | None = None) -> dict:
    key, label, kind, a, b = spec
    return {"key": key, "label": label, "value": value,
            "display": display if display is not None else _fmt(kind, value),
            "points": points, "reason": reason, "threshold": _threshold(kind, a, b)}


def _from_value(spec: tuple, value, reason: str | None = None) -> dict:
    _, _, _, a, b = spec
    if value is None:
        return _indicator(spec, None, None, reason or "veri yok")
    return _indicator(spec, value, lin(value, a, b))


def _describe(ind: dict) -> str:
    if ind["points"] is None:
        return f"{ind['label']} hesaplanamadı ({ind['reason']})"
    if ind["value"] is None:  # kural tabanlı puan (ör. net kâr ≤ 0)
        return f"{ind['label']}: {ind['reason']} → {_pts(ind['points'])} puan"
    return (f"{ind['label']} {ind['display']} (eşik {ind['threshold']} → "
            f"{_pts(ind['points'])} puan)")


# ---- veri erişimi ----

def _fact_at(facts: dict, metric: str, end: date | None) -> dict | None:
    """`end` dönemine ait kayıt (oranlarla aynı kural: ±7 gün toleransla en yakın)."""
    if end is None:
        return None
    return match_period({i["period_end"]: i for i in facts.get(metric) or []}, end)


def _fact_prior(facts: dict, metric: str, end: date | None) -> dict | None:
    if end is None:
        return None
    prior = [i for i in facts.get(metric) or [] if is_prior_fiscal_year(i["period_end"], end)]
    return max(prior, key=lambda i: i["period_end"]) if prior else None


def _latest_ratios(ratios: dict, as_of: date | None) -> dict[str, dict]:
    out = {}
    for r in (ratios or {}).get("ratios", []):
        for v in r.get("values", []):
            if v["period_end"] == as_of:
                out[r["key"]] = v
    return out


def _ratio_indicator(spec: tuple, latest: dict[str, dict]) -> dict:
    entry = latest.get(spec[0])
    if entry is None:
        return _from_value(spec, None, "veri yok")
    return _from_value(spec, entry["value"], entry["reason"])


# ---- bileşenler ----

def _growth_indicator(spec: tuple, facts: dict, metric: str, as_of: date | None) -> dict:
    cur = _fact_at(facts, metric, as_of)
    prev = _fact_prior(facts, metric, cur["period_end"]) if cur is not None else None
    if cur is None:
        return _from_value(spec, None, f"eksik veri: {metric}")
    if prev is None:
        return _from_value(spec, None, f"eksik veri: önceki mali yıl {metric}")
    if metric == "net_income" and prev["value"] <= 0:
        return _from_value(spec, None,
                           "önceki yıl net kârı sıfır veya negatif; büyüme anlamlı değil")
    change = yoy_ratio([prev, cur])[1]
    return _from_value(spec, change, None if change is not None else "payda sıfır")


def _build_component(comp: dict, latest: dict, facts: dict, signals: dict,
                     as_of: date | None) -> tuple[dict, float | None]:
    key, specs = comp["key"], comp["indicators"]
    override: float | None = None
    note: str | None = None

    if key in ("profitability", "liquidity"):
        indicators = [_ratio_indicator(s, latest) for s in specs]
    elif key == "leverage":
        indicators = [_ratio_indicator(s, latest) for s in specs]
        equity = _fact_at(facts, "stockholders_equity", as_of)
        de = latest.get("debt_to_equity") or {}
        if (equity is not None and equity["value"] <= 0) or de.get("reason") == REASON_EQUITY:
            override = 0.0
            shown = fmt_usd_short(equity["value"]) if equity is not None else MISSING
            note = f"özkaynak negatif veya sıfır ({shown}), kural gereği puan 0"
    elif key == "cash_quality":
        ocf_spec, fcf_spec = specs
        ni = _fact_at(facts, "net_income", as_of)
        ocf = _fact_at(facts, "operating_cash_flow", as_of)
        if ni is not None and ni["value"] <= 0 and ocf is not None:
            positive = ocf["value"] > 0
            reason = (REASON_NET_INCOME + "; faaliyetlerden nakit akışı "
                      + ("pozitif" if positive else "pozitif değil"))
            first = _indicator(ocf_spec, None, 50.0 if positive else 0.0, reason, MISSING)
        else:
            first = _ratio_indicator(ocf_spec, latest)
        indicators = [first, _ratio_indicator(fcf_spec, latest)]
    elif key == "growth":
        rev_spec, ni_spec = specs
        indicators = [_growth_indicator(rev_spec, facts, "revenue", as_of),
                      _growth_indicator(ni_spec, facts, "net_income", as_of)]
    elif key == "text_tone":
        mdna_spec, risk_spec = specs
        mdna, risk = signals.get("mdna_net_tone"), signals.get("risk_net_tone")
        if mdna is not None:
            indicators = [_from_value(mdna_spec, mdna)]
        elif risk is not None:
            indicators = [_from_value(risk_spec, risk)]
        else:
            indicators = [_from_value(mdna_spec, None, "MD&A tonu yok"),
                          _from_value(risk_spec, None, "risk bölümü tonu yok")]
    else:  # risk_change
        indicators = [_from_value(specs[0], signals.get("risk_added"),
                                  "karşılaştırılacak iki 10-K yok")]

    points = [i["points"] for i in indicators if i["points"] is not None]
    if override is not None:
        score = override
    elif points:
        score = sum(points) / len(points)
    else:
        score = None

    parts = [_describe(i) for i in indicators]
    if note:
        parts.append(note)
    if score is None:
        explanation = "Hesaplanamadı: " + "; ".join(parts) + "; bileşen skora katılmadı."
    else:
        explanation = "; ".join(parts) + f" → bileşen puanı {_pts(score)}."
        explanation = explanation[0].upper() + explanation[1:]

    for ind in indicators:
        if ind["points"] is not None:
            ind["points"] = round(ind["points"], 1)
    out = {"key": key, "label": comp["label"], "weight": comp["weight"],
           "score": round(score, 1) if score is not None else None,
           "available": score is not None, "indicators": indicators,
           "explanation": explanation}
    return out, score


def compute_score(ratios: dict, facts: dict[str, list[dict]],
                  text_signals: dict | None) -> dict:
    """Skoru hesaplar; çıktı API /score şeklidir (disclaimer hariç).

    ratios: ratios.compute_ratios çıktısı. facts: xbrl.extract_all çıktısı.
    text_signals: {"mdna_net_tone", "risk_net_tone", "risk_added"} (değerler None olabilir).
    Toplam 1 ondalığa yuvarlanır; bant yuvarlanmış toplamdan belirlenir.
    """
    signals = text_signals or {}
    periods = (ratios or {}).get("periods") or []
    as_of = periods[-1] if periods else None
    latest = _latest_ratios(ratios, as_of)
    facts = facts or {}

    components, weighted, used_weight = [], 0.0, 0
    for comp in COMPONENTS:
        out, raw = _build_component(comp, latest, facts, signals, as_of)
        components.append(out)
        if raw is not None:
            weighted += comp["weight"] * raw
            used_weight += comp["weight"]

    missing = [c["key"] for c in components if not c["available"]]
    total = round(weighted / used_weight, 1) if used_weight >= MIN_AVAILABLE_WEIGHT else None
    total_weight = sum(c["weight"] for c in COMPONENTS)
    missing_labels = ", ".join(c["label"] for c in components if not c["available"])

    if total is None:
        explanation = (
            f"Kullanılabilen bileşenlerin ağırlık toplamı {used_weight}/{total_weight}; "
            f"{MIN_AVAILABLE_WEIGHT}'nin altında olduğu için toplam skor üretilmedi."
        )
    else:
        explanation = (
            f"Toplam skor, kullanılabilen {len(components) - len(missing)} bileşenin "
            f"ağırlıklı ortalamasıdır (kullanılan ağırlık {used_weight}/{total_weight}"
            + (f"; eksik bileşenler: {missing_labels}; kalan ağırlıklar yeniden oranlandı"
               if missing else "")
            + ")."
        )
    explanation += (f" Ağırlıklar ve eşikler (sürüm {WEIGHTS_VERSION}) doğrulanmış bir model "
                    "değil, başlangıç önerisidir; bant gösterim içindir, derecelendirme "
                    "veya yatırım tavsiyesi değildir.")

    return {
        "total": total,
        "band": band(total),
        "as_of_period": as_of,
        "weights_version": WEIGHTS_VERSION,
        "components": components,
        "missing_components": missing,
        "explanation": explanation,
    }
