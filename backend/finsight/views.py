"""API yanıtlarını düz sözlüklerden kuran SAF katman (docs/api-contract.md).

fastapi/sqlalchemy içe aktarılmaz; ``repository.py`` veritabanı satırlarını düz sözlüklere
çevirir, bu modül sözleşmedeki JSON şekillerini üretir. Bütün çıktılar ``json_safe`` ile
JSON'a hazır hale getirilir: tarih → ISO 8601, numpy/Decimal → float/int, NaN → None.

Girdi sözlükleri:
- company: ``{ticker, cik, name, sic, sic_description, fiscal_year_end, exchanges,
  analysis_updated_at}``
- filing: ``{accession_no, form, filing_date, report_date, url, items, analyzed}``
- facts: ``dict[metric, list[FactItem]]`` (bkz. docs/modul-arayuzleri.md)
- score: ``scoring.compute_score`` çıktısı (veya onun JSON'dan okunmuş hali)
- sentiment bölümleri: ``{filing, item_code, title, char_start, model, sentences:
  [{sentence, label, score}]}``
- risk: ``{old_filing, new_filing, counts, changes}``
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Sequence
from typing import Any
from datetime import date, datetime
from decimal import Decimal

import numpy as np

from . import DISCLAIMER
from .analyze import latest_annual_summaries
from .edgar import item_label, split_items
from .ratios import RATIO_DEFS
from .scoring import WEIGHTS_VERSION
from .sentiment import summarize
from .xbrl import DERIVED_TAG, METRIC_LABELS, METRICS, yoy_ratio

KEY_METRICS = ("revenue", "net_income", "operating_cash_flow", "total_assets",
               "total_liabilities", "stockholders_equity")
LATEST_FILINGS = 5
EXPORT_COLUMNS = ("ticker", "cik", "section", "key", "label", "period_start", "period_end",
                  "value", "unit", "status", "xbrl_tag", "form", "accession_no", "filed")
SCORE_NOT_COMPUTED = ("Skor henüz hesaplanmadı: şirket için analiz çalıştırılmadı veya "
                      "yeterli finansal veri yok.")
EMPTY_RISK_COUNTS = None  # karşılaştırma yoksa sayılar uydurulmaz (0 ≠ eksik veri)


# --------------------------------------------------------------------------- JSON güvenliği


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:  # veritabanında UTC saklanır
        return value.replace(microsecond=0).isoformat() + "Z"
    return value.isoformat(timespec="seconds")


def json_safe(obj) -> Any:
    """JSON'a güvenli dönüşüm: date→ISO, numpy→float/int, Decimal→float, NaN/inf→None."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating, Decimal)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, datetime):
        return _iso_datetime(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, np.ndarray):
        return [json_safe(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_safe(x) for x in obj]
    return str(obj)


def _num(value) -> float | None:
    """Sayısal değer → float (None, NaN → None)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _iso(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _iso_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------------------- ortak nesneler


def company_view(company: dict) -> dict:
    exchanges = company.get("exchanges") or []
    return {
        "ticker": company.get("ticker"),
        "cik": company.get("cik"),
        "name": company.get("name"),
        "sic": company.get("sic"),
        "sic_description": company.get("sic_description"),
        "fiscal_year_end": company.get("fiscal_year_end"),
        "exchanges": [str(e) for e in exchanges],
    }


def filing_ref(filing: dict | None) -> dict | None:
    if not filing:
        return None
    return {
        "accession_no": filing.get("accession_no"),
        "form": filing.get("form"),
        "filing_date": _iso(filing.get("filing_date")),
        "report_date": _iso(filing.get("report_date")),
        "url": filing.get("url"),
    }


def source_view(item: dict) -> dict:
    """Bir FactItem'in kaynağı (NFR-06). Türetilmiş değerde xbrl_tag "derived"."""
    tag = item.get("tag")
    derived = tag == DERIVED_TAG
    return {
        "xbrl_tag": DERIVED_TAG if derived else f"us-gaap:{tag}",
        "form": item.get("form"),
        "accession_no": item.get("accession_no"),
        "filed": _iso(item.get("filed")),
        "derived_from": list(item.get("derived_from") or []) if derived else None,
    }


# --------------------------------------------------------------------------- finansal


def _fact_values(series: Sequence[dict]) -> list[dict]:
    ordered = sorted(series, key=lambda i: i["period_end"])
    changes = yoy_ratio(ordered)
    return [{
        "period_start": _iso(item.get("period_start")),
        "period_end": _iso(item["period_end"]),
        "value": _num(item.get("value")),
        "yoy": _num(change),
        "source": source_view(item),
    } for item, change in zip(ordered, changes)]


def facts_view(company: dict, facts: dict[str, list[dict]], metric: str | None = None) -> dict:
    """GET /facts: kalemler METRICS sırasıyla, değerler eskiden yeniye, yoy ham oran."""
    keys = [metric] if metric else list(METRICS)
    metrics = []
    for key in keys:
        if key not in METRICS:
            continue
        metrics.append({
            "metric": key,
            "label": METRIC_LABELS.get(key, key),
            "kind": "instant" if METRICS[key][1] else "duration",
            "values": _fact_values(facts.get(key) or []),
        })
    return json_safe({"company": company_view(company), "metrics": metrics,
                      "disclaimer": DISCLAIMER})


def ratios_view(company: dict, ratios: dict) -> dict:
    """GET /ratios: compute_ratios çıktısı + company + disclaimer."""
    return json_safe({
        "company": company_view(company),
        "periods": [_iso(p) for p in ratios.get("periods") or []],
        "ratios": ratios.get("ratios") or [],
        "disclaimer": DISCLAIMER,
    })


def score_brief(score: dict | None) -> dict | None:
    if not score:
        return None
    return {"total": _num(score.get("total")), "band": score.get("band")}


def score_view(score: dict | None) -> dict:
    """GET /score: skor yoksa 200 ile total=None, components=[]."""
    if not score:
        return {"total": None, "band": None, "as_of_period": None,
                "weights_version": WEIGHTS_VERSION, "components": [],
                "missing_components": [], "explanation": SCORE_NOT_COMPUTED,
                "disclaimer": DISCLAIMER}
    out = {
        "total": score.get("total"),
        "band": score.get("band"),
        "as_of_period": _iso(score.get("as_of_period")),
        "weights_version": score.get("weights_version") or WEIGHTS_VERSION,
        "components": score.get("components") or [],
        "missing_components": score.get("missing_components") or [],
        "explanation": score.get("explanation"),
        "disclaimer": DISCLAIMER,
    }
    return json_safe(out)


def key_metrics(facts: dict[str, list[dict]]) -> list[dict]:
    """Genel bakıştaki ana kalemler (sözleşmedeki sıra), son dönem değeri ve YoY."""
    out = []
    for key in KEY_METRICS:
        series = sorted(facts.get(key) or [], key=lambda i: i["period_end"])
        entry = {"metric": key, "label": METRIC_LABELS.get(key, key), "value": None,
                 "period_end": None, "yoy": None, "source": None, "reason": "veri yok"}
        if series:
            latest = series[-1]
            entry.update({
                "value": _num(latest.get("value")),
                "period_end": _iso(latest["period_end"]),
                "yoy": _num(yoy_ratio(series)[-1]),
                "source": source_view(latest),
                "reason": None,
            })
        out.append(entry)
    return out


# --------------------------------------------------------------------------- metin analizi


def _section_model(section: dict) -> str | None:
    return section.get("model")


def _sorted_sections(sections: Sequence[dict]) -> list[dict]:
    def key(s: dict):
        f = s.get("filing") or {}
        return (_iso(f.get("filing_date")) or "", str(f.get("accession_no") or ""),
                int(s.get("char_start") or 0))
    return sorted(sections, key=key)


def current_sentiment_model(sections: Sequence[dict], default: str | None = None) -> str | None:
    """En yeni başvurunun bölümlerindeki model adı (yoksa default)."""
    ordered = _sorted_sections(sections)
    for s in reversed(ordered):
        if _section_model(s):
            return _section_model(s)
    return default


def sentiment_sections(sections: Sequence[dict], model: str | None = None) -> list[dict]:
    """Bölüm başına özet (summarize) — yalnızca `model` ile üretilmiş bölümler (verilmişse)."""
    out = []
    for s in _sorted_sections(sections):
        if model is not None and _section_model(s) not in (None, model):
            continue
        sentences = s.get("sentences") or []
        texts = [x["sentence"] for x in sentences]
        preds = [{"label": x["label"], "score": float(x["score"])} for x in sentences]
        out.append({
            "filing": filing_ref(s.get("filing")),
            "item_code": s.get("item_code"),
            "title": s.get("title"),
            **summarize(texts, preds),
        })
    return out


def sentiment_view(sections: Sequence[dict], default_model: str | None = None) -> dict:
    """GET /sentiment: bölümler başvuru tarihine göre eskiden yeniye."""
    model = current_sentiment_model(sections, default_model)
    return json_safe({"model": model, "sections": sentiment_sections(sections, model),
                      "disclaimer": DISCLAIMER})


def sentiment_summary_view(sections: Sequence[dict]) -> dict | None:
    """Genel bakış: son 10-K'nın MD&A ve risk bölümü net tonu (yoksa None)."""
    model = current_sentiment_model(sections)
    summarized = sentiment_sections(sections, model)
    # latest_annual_summaries filing sözlüğünde form/filing_date/accession_no bekler.
    filing, by_code = latest_annual_summaries(summarized)
    if filing is None:
        return None
    mdna, risk = by_code.get("7"), by_code.get("1A")
    return {
        "filing": filing,
        "mdna_net_tone": _num(mdna.get("net_tone")) if mdna else None,
        "risk_net_tone": _num(risk.get("net_tone")) if risk else None,
        "model": model,
    }


def risk_counts(risk: dict | None) -> dict | None:
    if not risk or not risk.get("counts"):
        return EMPTY_RISK_COUNTS
    c = risk["counts"]
    return {k: int(c.get(k) or 0) for k in ("added", "removed", "modified", "unchanged")}


_CHANGE_ORDER = {"added": 0, "modified": 1, "removed": 2}


def risk_view(risk: dict | None) -> dict:
    """GET /risk-changes: iki 10-K yoksa old_filing/new_filing None, counts None, changes []."""
    if not risk:
        return {"old_filing": None, "new_filing": None, "counts": EMPTY_RISK_COUNTS,
                "changes": [], "disclaimer": DISCLAIMER}
    changes = [c for c in risk.get("changes") or [] if c.get("change_type") in _CHANGE_ORDER]
    # Kararlı sıralama: tür sırası (added → modified → removed), tür içinde kayıt sırası.
    changes = sorted(changes, key=lambda c: _CHANGE_ORDER[c["change_type"]])
    return json_safe({
        "old_filing": filing_ref(risk.get("old_filing")),
        "new_filing": filing_ref(risk.get("new_filing")),
        "counts": risk_counts(risk),
        "changes": [{"change_type": c["change_type"], "old_text": c.get("old_text"),
                     "new_text": c.get("new_text"), "similarity": _num(c.get("similarity"))}
                    for c in changes],
        "disclaimer": DISCLAIMER,
    })


# --------------------------------------------------------------------------- sayfalar


def overview_view(company: dict, facts: dict[str, list[dict]], score: dict | None,
                  filings: Sequence[dict], sentiment: Sequence[dict],
                  risk: dict | None) -> dict:
    """GET /overview. `filings` yeniden eskiye sıralı olmalıdır (ilk 5'i gösterilir)."""
    score_part = None
    if score:
        score_part = {"total": _num(score.get("total")), "band": score.get("band"),
                      "as_of_period": _iso(score.get("as_of_period"))}
    return json_safe({
        "company": company_view(company),
        "score": score_part,
        "key_metrics": key_metrics(facts),
        "latest_filings": [filing_ref(f) for f in list(filings)[:LATEST_FILINGS]],
        "sentiment_summary": sentiment_summary_view(sentiment),
        "risk_summary": risk_counts(risk),
        "analysis_updated_at": _iso(company.get("analysis_updated_at")),
        "disclaimer": DISCLAIMER,
    })


def filing_view(filing: dict) -> dict:
    items = split_items(filing.get("items"))
    return {**(filing_ref(filing) or {}), "items": items,
            "item_labels": [item_label(i) for i in items],
            "analyzed": bool(filing.get("analyzed"))}


def filings_view(company: dict, filings: Sequence[dict]) -> dict:
    """GET /filings: `filings` yeniden eskiye sıralı verilir; items listeye çevrilir."""
    return json_safe({"company": company_view(company),
                      "filings": [filing_view(f) for f in filings],
                      "disclaimer": DISCLAIMER})


def company_list_item(company: dict, analyzed: bool, last_filing_date,
                      score: dict | None) -> dict:
    """GET /companies öğesi."""
    return json_safe({
        "ticker": company.get("ticker"), "cik": company.get("cik"), "name": company.get("name"),
        "sic_description": company.get("sic_description"), "analyzed": bool(analyzed),
        "last_filing_date": _iso(last_filing_date), "score": score_brief(score),
    })


def job_view(job: dict) -> dict:
    """GET /jobs/{id}. `warnings`: işi durdurmayan adım hataları (sözleşmeye ek alan)."""
    return json_safe({
        "id": job.get("id"), "ticker": job.get("ticker"), "status": job.get("status"),
        "step": job.get("step"), "progress": float(job.get("progress") or 0.0),
        "error": job.get("error"), "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"), "request_count": int(job.get("request_count") or 0),
        "warnings": list(job.get("warnings") or []),
    })


def _latest_ratio_values(ratios: dict) -> tuple[str | None, dict[str, float | None]]:
    periods = ratios.get("periods") or []
    last = periods[-1] if periods else None
    values: dict[str, float | None] = {}
    for r in ratios.get("ratios") or []:
        match = [v for v in r.get("values") or [] if v.get("period_end") == last]
        values[r["key"]] = _num(match[0].get("value")) if match else None
    return _iso(last), values


def compare_view(entries: Sequence[dict]) -> dict:
    """GET /compare. entries: [{"company", "ratios" (compute_ratios), "score"}] istek sırasıyla."""
    companies = []
    for e in entries:
        period_end, values = _latest_ratio_values(e.get("ratios") or {})
        company = e["company"]
        companies.append({"ticker": company.get("ticker"), "name": company.get("name"),
                          "period_end": period_end, "score": score_brief(e.get("score")),
                          "ratios": values})
    meta = [{k: d[k] for k in ("key", "label", "unit", "higher_is_better")} for d in RATIO_DEFS]
    return json_safe({"ratio_meta": meta, "companies": companies, "disclaimer": DISCLAIMER})


# --------------------------------------------------------------------------- CSV dışa aktarım


def export_rows(company: dict, facts: dict[str, list[dict]], ratios: dict,
                score: dict | None) -> list[dict]:
    """FR-16: kalemler, oranlar ve skor satırları (EXPORT_COLUMNS anahtarlarıyla).

    Eksik değer boş hücre olarak yazılır (0 değil); status "missing".
    """
    base = {"ticker": company.get("ticker"), "cik": company.get("cik")}
    rows: list[dict] = []
    for metric in METRICS:
        for item in sorted(facts.get(metric) or [], key=lambda i: i["period_end"]):
            src = source_view(item)
            rows.append({**base, "section": "fact", "key": metric,
                         "label": METRIC_LABELS.get(metric, metric),
                         "period_start": _iso(item.get("period_start")),
                         "period_end": _iso(item["period_end"]),
                         "value": _num(item.get("value")), "unit": item.get("unit") or "USD",
                         "status": "ok", "xbrl_tag": src["xbrl_tag"], "form": src["form"],
                         "accession_no": src["accession_no"], "filed": src["filed"]})
    for r in (ratios or {}).get("ratios") or []:
        for v in r.get("values") or []:
            accessions = sorted({i["accession_no"] for i in v.get("inputs") or []
                                 if i.get("accession_no")})
            rows.append({**base, "section": "ratio", "key": r["key"], "label": r["label"],
                         "period_start": None, "period_end": _iso(v.get("period_end")),
                         "value": _num(v.get("value")), "unit": r.get("unit"),
                         "status": v.get("status") or ("ok" if v.get("value") is not None
                                                       else "missing"),
                         "xbrl_tag": None, "form": None,
                         "accession_no": ";".join(accessions) or None, "filed": None})
    if score:
        as_of = _iso(score.get("as_of_period"))
        total = _num(score.get("total"))
        rows.append({**base, "section": "score", "key": "total", "label": "Toplam skor",
                     "period_start": None, "period_end": as_of, "value": total,
                     "unit": "points", "status": "ok" if total is not None else "missing",
                     "xbrl_tag": None, "form": None, "accession_no": None, "filed": None})
        for comp in score.get("components") or []:
            value = _num(comp.get("score"))
            rows.append({**base, "section": "score", "key": comp.get("key"),
                         "label": comp.get("label"), "period_start": None, "period_end": as_of,
                         "value": value, "unit": "points",
                         "status": "ok" if value is not None else "missing",
                         "xbrl_tag": None, "form": None, "accession_no": None, "filed": None})
    return rows


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    return str(value)


def export_csv(rows: Sequence[dict]) -> str:
    """CSV metni, UTF-8 BOM ile başlar (Excel'in Türkçe karakterleri doğru açması için)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_csv_cell(row.get(col)) for col in EXPORT_COLUMNS])
    return "﻿" + buf.getvalue()
