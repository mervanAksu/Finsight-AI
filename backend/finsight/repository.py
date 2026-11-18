"""Veritabanı erişim katmanı: satırları düz sözlüklere çevirir, analiz sonuçlarını yazar.

``views.py`` bu modülün döndürdüğü düz sözlüklerle çalışır (docs/entegrasyon-plani.md).
Yazan fonksiyonlar işlemlerini kendileri ``commit`` eder; hata durumunda çağıran
``session.rollback()`` yapmalıdır. Okuyan fonksiyonlar yazmaz.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .edgar import document_url, pad_cik
from .embeddings import from_bytes, to_bytes
from .models import (ChunkEmbedding, Company, CompanyScore, Filing, FilingDocument,
                     FilingSection, FinancialFact, IngestionJob, QALog, RiskChange,
                     RiskComparison, SentimentResult, TextChunk, utcnow)
from .scoring import WEIGHTS_VERSION
from .sentiment import LABELS, normalize_sentence
from .text_extract import SECTION_TITLES
from .views import json_safe

ACTIVE_JOB_STATUSES = ("queued", "running")
# Bu süre boyunca ilerleme yazmayan "çalışıyor" işi ölü sayılır (süreç yeniden başlatıldı vb.).
STALE_JOB_AFTER = timedelta(minutes=30)
STALE_JOB_ERROR = "İş yarıda kaldı (sunucu yeniden başlatılmış olabilir); tekrar başlatın."


def dumps(obj) -> str:
    """JSON metni (Türkçe karakterler korunur; tarih/numpy güvenli)."""
    return json.dumps(json_safe(obj), ensure_ascii=False)


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- şirket


def company_dict(row: Company) -> dict:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "cik": row.cik,
        "name": row.name,
        "sic": row.sic,
        "sic_description": row.sic_description,
        "fiscal_year_end": row.fiscal_year_end,
        "exchanges": loads(row.exchanges, []) or [],
        "tickers": company_tickers(row),
        "analysis_updated_at": row.analysis_updated_at,
    }


def company_tickers(row: Company) -> list[str]:
    """Şirketin bilinen tüm ticker'ları (birincil ``ticker`` önce, büyük harf, tekrarsız)."""
    out: list[str] = []
    for t in [row.ticker, *(loads(row.tickers, []) or [])]:
        t = str(t or "").strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def get_company_row(session: Session, ticker: str) -> Company | None:
    """Ticker'a göre şirket (K3): önce birincil ``Company.ticker``, yoksa ``tickers`` JSON
    listesinde ``"T"`` geçen şirket (ör. GOOGL kaydı GOOG ile de bulunur). Birden fazla
    eşleşmede en son güncellenen seçilir."""
    t = ticker.strip().upper()
    if not t:
        return None
    order = (Company.updated_at.desc(), Company.id.desc())
    row = session.scalar(select(Company).where(func.upper(Company.ticker) == t)
                         .order_by(*order).limit(1))
    if row is not None:
        return row
    # JSON metninde tırnaklı tam eşleşme ('%"GOOG"%' — "GOOGL" ile eşleşmez); autoescape
    # ile % ve _ karakterleri joker sayılmaz. save_collected listeyi büyük harfle yazar.
    candidates = session.scalars(
        select(Company).where(Company.tickers.contains(f'"{t}"', autoescape=True))
        .order_by(*order)).all()
    return next((c for c in candidates if t in company_tickers(c)), None)


def load_company(session: Session, ticker: str) -> dict | None:
    row = get_company_row(session, ticker)
    return company_dict(row) if row is not None else None


def load_company_by_id(session: Session, company_id: int) -> dict | None:
    row = session.get(Company, company_id)
    return company_dict(row) if row is not None else None


def list_companies(session: Session) -> list[dict]:
    """GET /companies girdisi: [{"company", "analyzed", "last_filing_date", "score"}]."""
    companies = session.scalars(select(Company).order_by(Company.ticker, Company.id)).all()
    last_dates = dict(session.execute(
        select(Filing.company_id, func.max(Filing.filing_date)).group_by(Filing.company_id)
    ).all())
    analyzed_ids = set(session.scalars(
        select(Filing.company_id).where(Filing.analyzed.is_(True)).distinct()).all())
    out = []
    for c in companies:
        out.append({
            "company": company_dict(c),
            "analyzed": c.id in analyzed_ids,
            "last_filing_date": last_dates.get(c.id),
            "score": load_latest_score(session, c.id),
        })
    return out


def is_company_analyzed(session: Session, company_id: int) -> bool:
    """Şirketin en az bir başvurusunda metin analizi yapılmış mı."""
    return session.scalar(select(Filing.id).where(
        Filing.company_id == company_id, Filing.analyzed.is_(True)).limit(1)) is not None


def all_tickers(session: Session) -> set[str]:
    """İşlenmiş şirketlerin tüm ticker'ları (aynı CIK'in diğer ticker'ları dahil)."""
    out: set[str] = set()
    for row in session.scalars(select(Company)).all():
        out.update(company_tickers(row))
    return out


def search_local(session: Session, query: str, limit: int = 10) -> list[dict]:
    """Yalnızca veritabanındaki şirketlerde ticker / ad / CIK araması."""
    q = query.strip()
    if not q:
        return []
    like = f"%{q.upper()}%"
    conditions = [func.upper(Company.ticker).like(like), func.upper(Company.name).like(like),
                  Company.tickers.like(like)]
    digits = q[3:] if q.upper().startswith("CIK") else q
    if digits.isdigit() and len(digits) <= 10:
        conditions.append(Company.cik == pad_cik(digits))
    rows = session.scalars(select(Company).where(or_(*conditions))
                           .order_by(Company.ticker).limit(limit)).all()
    exact = q.upper()
    rows = sorted(rows, key=lambda c: (0 if (c.ticker or "").upper() == exact else 1,
                                        c.ticker or ""))
    return [{"ticker": c.ticker, "cik": c.cik, "name": c.name} for c in rows]


def touch_analysis(session: Session, company_id: int) -> None:
    company = session.get(Company, company_id)
    if company is not None:
        company.analysis_updated_at = utcnow()
        session.commit()


# --------------------------------------------------------------------------- SEC verisi yazma


def save_collected(session: Session, data: dict) -> Company:
    """pipeline.collect çıktısını yazar; tekrar çalıştırmada mükerrer kayıt oluşturmaz (upsert).

    Kalemlerde derived_from (JSON) ve şirkette sic_description / exchanges saklanır.
    """
    info = data["company"]
    company = session.scalar(select(Company).where(Company.cik == info["cik"]))
    if company is None:
        company = Company(cik=info["cik"])
        session.add(company)
    # K3: birincil ticker yalnızca boşsa (veya SEC'in güncel listesinde artık yoksa — ticker
    # değişikliği) yazılır; istenen ticker ve aynı CIK'in diğer ticker'ları ``tickers``'a eklenir.
    requested = str(info.get("ticker") or "").strip().upper() or None
    sec_tickers = [str(t).strip().upper() for t in (info.get("tickers") or []) if t]
    current = (company.ticker or "").strip().upper()
    if not current or (sec_tickers and current not in sec_tickers):
        company.ticker = requested
    merged: list[str] = []
    for t in [company.ticker, requested, *sec_tickers, *(loads(company.tickers, []) or [])]:
        t = str(t or "").strip().upper()
        if t and t not in merged:
            merged.append(t)
    company.tickers = dumps(merged) if merged else None
    for key in ("name", "sic", "fiscal_year_end"):
        setattr(company, key, info.get(key))
    if "sic_description" in info:
        company.sic_description = info["sic_description"]
    if "exchanges" in info:
        company.exchanges = dumps(list(info["exchanges"] or []))
    session.flush()

    filings = data.get("filings") or []
    accessions = [f["accession_no"] for f in filings]
    existing: dict[str, Filing] = {}
    for start in range(0, len(accessions), 500):  # IN listesi sınırı için parçalı sorgu
        batch = accessions[start:start + 500]
        for row in session.scalars(select(Filing).where(Filing.accession_no.in_(batch))):
            existing[row.accession_no] = row
    for f in filings:
        row = existing.get(f["accession_no"])
        if row is None:
            row = Filing(accession_no=f["accession_no"], company_id=company.id, analyzed=False)
            session.add(row)
            existing[f["accession_no"]] = row
        row.company_id = company.id
        row.form = f["form"]
        row.is_amendment = bool(f.get("is_amendment"))
        row.filing_date = f["filing_date"]
        row.report_date = f.get("report_date")
        row.primary_document = f.get("primary_document")
        row.items = f.get("items")
        row.is_xbrl = bool(f.get("is_xbrl"))
        if f.get("primary_document"):
            row.url = document_url(info["cik"], f["accession_no"], f["primary_document"])

    facts_by_key = {(r.metric, r.period_end): r for r in session.scalars(
        select(FinancialFact).where(FinancialFact.company_id == company.id))}
    for metric, series in (data.get("facts") or {}).items():
        for item in series:
            row = facts_by_key.get((metric, item["period_end"]))
            if row is None:
                row = FinancialFact(company_id=company.id, metric=metric,
                                    period_end=item["period_end"])
                session.add(row)
                facts_by_key[(metric, item["period_end"])] = row
            for key in ("tag", "unit", "value", "period_start", "fy", "fp", "form",
                        "accession_no", "filed"):
                setattr(row, key, item[key])
            derived = item.get("derived_from")
            row.derived_from = dumps(list(derived)) if derived else None
    session.commit()
    return company


# --------------------------------------------------------------------------- kalemler, başvurular


def fact_dict(row: FinancialFact) -> dict:
    """FactItem (docs/modul-arayuzleri.md); value float."""
    return {
        "metric": row.metric,
        "tag": row.tag,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "value": float(row.value) if row.value is not None else None,
        "unit": row.unit,
        "accession_no": row.accession_no,
        "form": row.form,
        "filed": row.filed,
        "fy": row.fy,
        "fp": row.fp,
        "derived_from": loads(row.derived_from, None),
    }


def load_facts(session: Session, company_id: int,
               metric: str | None = None) -> dict[str, list[dict]]:
    """dict[metric, list[FactItem]] — her seri period_end'e göre eskiden yeniye."""
    stmt = select(FinancialFact).where(FinancialFact.company_id == company_id)
    if metric:
        stmt = stmt.where(FinancialFact.metric == metric)
    out: dict[str, list[dict]] = {}
    for row in session.scalars(stmt.order_by(FinancialFact.metric, FinancialFact.period_end)):
        out.setdefault(row.metric, []).append(fact_dict(row))
    return out


def filing_dict(row: Filing) -> dict:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "accession_no": row.accession_no,
        "form": row.form,
        "is_amendment": bool(row.is_amendment),
        "filing_date": row.filing_date,
        "report_date": row.report_date,
        "primary_document": row.primary_document,
        "items": row.items,
        "url": row.url,
        "analyzed": bool(row.analyzed),
    }


def load_filings(session: Session, company_id: int, form: str | None = None,
                 limit: int | None = None) -> list[dict]:
    """Başvurular yeniden eskiye. form verilirse değişiklik başvurusu (…/A) da dahildir."""
    stmt = select(Filing).where(Filing.company_id == company_id)
    if form:
        base = form.strip().upper()
        base = base[:-2] if base.endswith("/A") else base
        stmt = stmt.where(Filing.form.in_([base, f"{base}/A"]))
    stmt = stmt.order_by(Filing.filing_date.desc(), Filing.accession_no.desc())
    if limit:
        stmt = stmt.limit(limit)
    return [filing_dict(r) for r in session.scalars(stmt)]


def get_filing(session: Session, filing_id: int | None) -> dict | None:
    if filing_id is None:
        return None
    row = session.get(Filing, filing_id)
    return filing_dict(row) if row is not None else None


# --------------------------------------------------------------------------- belge analizi


def _section_ids(filing_id: int):
    return select(FilingSection.id).where(FilingSection.filing_id == filing_id)


def filing_analysis_models(session: Session, filing_id: int) -> tuple[set[str], set[str]]:
    """Bir başvurunun kayıtlı duygu ve gömme model adları."""
    sentiment = set(session.scalars(
        select(SentimentResult.model_name)
        .where(SentimentResult.section_id.in_(_section_ids(filing_id))).distinct()).all())
    chunk_ids = select(TextChunk.id).where(TextChunk.section_id.in_(_section_ids(filing_id)))
    embedding = set(session.scalars(
        select(ChunkEmbedding.model_name)
        .where(ChunkEmbedding.chunk_id.in_(chunk_ids)).distinct()).all())
    return sentiment, embedding


def is_filing_analyzed(session: Session, filing_id: int, sentiment_model: str,
                       embedding_model: str) -> bool:
    """Başvuru aynı duygu ve gömme modeliyle analiz edildiyse True (yeniden analiz atlanır).

    Bölüm bulunamayan (kayıt üretmeyen) analiz edilmiş başvurular da atlanır: bölüm ayıklama
    modelden bağımsızdır.
    """
    row = session.get(Filing, filing_id)
    if row is None or not row.analyzed:
        return False
    sentiment, embedding = filing_analysis_models(session, filing_id)
    return sentiment <= {sentiment_model} and embedding <= {embedding_model}


def delete_filing_analysis(session: Session, filing_id: int) -> None:
    """Başvurunun bölüm / parça / gömme / duygu kayıtlarını siler (commit etmez).

    Yabancı anahtar zorlamasından bağımsız olsun diye alt tablolardan başlayarak açık DELETE.
    synchronize_session="fetch": silinen satırların nesneleri oturumdan da çıkarılır; aksi
    halde aynı oturumda yeniden yazarken (SQLite kimlikleri yeniden kullanabilir) eski nesne
    ile yeni nesne aynı kimliğe çakışır.
    """
    opts = {"synchronize_session": "fetch"}
    section_ids = _section_ids(filing_id)
    chunk_ids = select(TextChunk.id).where(TextChunk.section_id.in_(section_ids))
    session.execute(delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)),
                    execution_options=opts)
    session.execute(delete(TextChunk).where(TextChunk.section_id.in_(section_ids)),
                    execution_options=opts)
    session.execute(delete(SentimentResult).where(SentimentResult.section_id.in_(section_ids)),
                    execution_options=opts)
    session.execute(delete(FilingSection).where(FilingSection.filing_id == filing_id),
                    execution_options=opts)


def save_analysis_document(session: Session, filing_id: int, analysis: dict,
                           document: dict | None = None) -> dict:
    """analyze.analyze_document çıktısını yazar; eski analiz kayıtları önce silinir.

    document: ``{"local_path", "sha256", "size_bytes"}`` (filing_document upsert).
    Dönüş: ``{"sections": [kodlar], "sentences": n, "chunks": n}``.
    """
    filing = session.get(Filing, filing_id)
    if filing is None:
        raise ValueError(f"Başvuru bulunamadı: {filing_id}")
    delete_filing_analysis(session, filing_id)

    sentiment_model = analysis["sentiment_model"]
    embedding_model = analysis["embedding_model"]
    dim = int(analysis["embedding_dim"])
    n_sentences = n_chunks = 0
    codes: list[str] = []
    for sec in analysis.get("sections") or []:
        section = FilingSection(
            filing_id=filing_id, item_code=sec["item_code"], title=sec.get("title"),
            text=sec["text"], char_start=sec.get("char_start"), char_end=sec.get("char_end"),
            word_count=sec.get("word_count"))
        session.add(section)
        session.flush()  # section.id
        codes.append(sec["item_code"])
        session.add_all([
            SentimentResult(section_id=section.id, sentence_index=int(s["index"]),
                            sentence=s["sentence"], label=s["label"], score=float(s["score"]),
                            model_name=sentiment_model)
            for s in sec.get("sentences") or []])
        n_sentences += len(sec.get("sentences") or [])
        pairs = []
        for ch in sec.get("chunks") or []:
            chunk = TextChunk(section_id=section.id, chunk_index=int(ch["index"]),
                              text=ch["text"], word_count=ch.get("word_count"))
            session.add(chunk)
            pairs.append((chunk, ch))
        session.flush()  # chunk.id
        session.add_all([
            ChunkEmbedding(chunk_id=chunk.id, model_name=embedding_model, dim=dim,
                           vector=to_bytes(ch["embedding"]))
            for chunk, ch in pairs])
        n_chunks += len(pairs)

    if document is not None:
        doc = session.scalar(select(FilingDocument).where(FilingDocument.filing_id == filing_id))
        if doc is None:
            doc = FilingDocument(filing_id=filing_id)
            session.add(doc)
        doc.local_path = document.get("local_path")
        doc.sha256 = document.get("sha256")
        doc.size_bytes = document.get("size_bytes")
        doc.downloaded_at = utcnow()
    filing.analyzed = True
    session.commit()
    return {"sections": codes, "sentences": n_sentences, "chunks": n_chunks}


def load_section_text(session: Session, filing_id: int, item_code: str) -> str | None:
    return session.scalar(select(FilingSection.text).where(
        FilingSection.filing_id == filing_id, FilingSection.item_code == item_code))


def load_sentiment_sections(session: Session, company_id: int) -> list[dict]:
    """Analiz edilmiş başvuruların bölümleri ve cümle duyguları (views.sentiment_view girdisi).

    Öğe: ``{"filing", "item_code", "title", "char_start", "model", "sentences":
    [{"index", "sentence", "label", "score"}]}``; başvuru tarihine göre eskiden yeniye.
    """
    rows = session.execute(
        select(FilingSection, Filing).join(Filing, Filing.id == FilingSection.filing_id)
        .where(Filing.company_id == company_id, Filing.analyzed.is_(True))
        .order_by(Filing.filing_date, Filing.accession_no, FilingSection.char_start)
    ).all()
    if not rows:
        return []
    by_section: dict[int, list[SentimentResult]] = {}
    section_ids = [section.id for section, _ in rows]
    for start in range(0, len(section_ids), 500):
        batch = section_ids[start:start + 500]
        for r in session.scalars(select(SentimentResult)
                                 .where(SentimentResult.section_id.in_(batch))
                                 .order_by(SentimentResult.section_id,
                                           SentimentResult.sentence_index)):
            by_section.setdefault(r.section_id, []).append(r)
    out = []
    for section, filing in rows:
        results = by_section.get(section.id, [])
        out.append({
            "filing": filing_dict(filing),
            "item_code": section.item_code,
            "title": section.title or SECTION_TITLES.get(section.item_code, section.item_code),
            "char_start": section.char_start or 0,
            "model": results[0].model_name if results else None,
            "sentences": [{"index": r.sentence_index, "sentence": r.sentence, "label": r.label,
                           "score": float(r.score)} for r in results],
        })
    return out


def load_sentence_predictions(session: Session, company_id: int,
                              model_name: str) -> dict[str, dict]:
    """Şirketin `model_name` ile kaydedilmiş cümle duyguları: normalize metin → {label, score}.

    analyze.predict_with_cache için önbellek: aynı model + aynı metin = aynı sonuç, bu yüzden
    yeni (veya --force ile yeniden) analiz edilen başvurularda tekrar eden cümleler modele
    gönderilmez. Yalnızca bu modelin satırları okunur; farklı model adları karışmaz.
    """
    stmt = (select(SentimentResult.sentence, SentimentResult.label, SentimentResult.score)
            .join(FilingSection, FilingSection.id == SentimentResult.section_id)
            .join(Filing, Filing.id == FilingSection.filing_id)
            .where(Filing.company_id == company_id, SentimentResult.model_name == model_name)
            .order_by(SentimentResult.id))
    out: dict[str, dict] = {}
    for sentence, label, score in session.execute(stmt):
        key = normalize_sentence(sentence or "")
        if key and key not in out and label in LABELS and score is not None:
            out[key] = {"label": label, "score": float(score)}
    return out


def load_company_sentences(session: Session, company_id: int) -> list[str]:
    """Şirketin kayıtlı tüm (farklı) duygu cümleleri, kayıt sırasıyla (bench_sentiment)."""
    stmt = (select(SentimentResult.sentence)
            .join(FilingSection, FilingSection.id == SentimentResult.section_id)
            .join(Filing, Filing.id == FilingSection.filing_id)
            .where(Filing.company_id == company_id)
            .order_by(SentimentResult.id))
    seen: set[str] = set()
    out: list[str] = []
    for sentence in session.scalars(stmt):
        key = normalize_sentence(sentence or "")
        if key and key not in seen:
            seen.add(key)
            out.append(sentence)
    return out


def count_chunks_other_models(session: Session, company_id: int, embedding_model: str) -> int:
    """Şirketin `embedding_model` DIŞINDAKİ modellerle gömülmüş parça sayısı (K4: /ask'ta
    "farklı gömme modeli, yeniden analiz edin" uyarısı için)."""
    return int(session.scalar(
        select(func.count()).select_from(ChunkEmbedding)
        .join(TextChunk, TextChunk.id == ChunkEmbedding.chunk_id)
        .join(FilingSection, FilingSection.id == TextChunk.section_id)
        .join(Filing, Filing.id == FilingSection.filing_id)
        .where(Filing.company_id == company_id, ChunkEmbedding.model_name != embedding_model)
    ) or 0)


def load_chunks_for_rag(session: Session, company_id: int, embedding_model: str) -> list[dict]:
    """rag.answer_question parçaları — yalnızca `embedding_model` ile gömülenler (başka
    modelin vektörleri aynı uzayda değildir)."""
    rows = session.execute(
        select(TextChunk, ChunkEmbedding, FilingSection, Filing)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == TextChunk.id)
        .join(FilingSection, FilingSection.id == TextChunk.section_id)
        .join(Filing, Filing.id == FilingSection.filing_id)
        .where(Filing.company_id == company_id, ChunkEmbedding.model_name == embedding_model)
        .order_by(Filing.filing_date, FilingSection.char_start, TextChunk.chunk_index)
    ).all()
    out = []
    for chunk, emb, section, filing in rows:
        out.append({
            "chunk_id": chunk.id,
            "text": chunk.text,
            "embedding": from_bytes(emb.vector, emb.dim),
            "section": section.item_code,
            "section_title": section.title or SECTION_TITLES.get(section.item_code),
            "form": filing.form,
            "filing_date": filing.filing_date.isoformat() if filing.filing_date else None,
            "accession_no": filing.accession_no,
            "url": filing.url,
        })
    return out


# --------------------------------------------------------------------------- risk ve skor


def save_risk_comparison(session: Session, company_id: int, old_filing_id: int,
                         new_filing_id: int, comparison: dict) -> int:
    """compare_risk_sections çıktısını yazar; aynı başvuru çiftinin eski kaydı silinir."""
    opts = {"synchronize_session": "fetch"}
    old_ids = select(RiskComparison.id).where(
        RiskComparison.company_id == company_id,
        RiskComparison.old_filing_id == old_filing_id,
        RiskComparison.new_filing_id == new_filing_id)
    session.execute(delete(RiskChange).where(RiskChange.comparison_id.in_(old_ids)),
                    execution_options=opts)
    session.execute(delete(RiskComparison).where(RiskComparison.id.in_(old_ids)),
                    execution_options=opts)
    counts = comparison.get("counts") or {}
    row = RiskComparison(
        company_id=company_id, old_filing_id=old_filing_id, new_filing_id=new_filing_id,
        added=int(counts.get("added") or 0), removed=int(counts.get("removed") or 0),
        modified=int(counts.get("modified") or 0), unchanged=int(counts.get("unchanged") or 0),
        created_at=utcnow())
    session.add(row)
    session.flush()
    session.add_all([
        RiskChange(comparison_id=row.id, position=i, change_type=c["change_type"],
                   old_text=c.get("old_text"), new_text=c.get("new_text"),
                   similarity=float(c["similarity"]) if c.get("similarity") is not None
                   else None)
        for i, c in enumerate(comparison.get("changes") or [])])
    session.commit()
    return row.id


def load_risk(session: Session, company_id: int) -> dict | None:
    """En son risk karşılaştırması: {old_filing, new_filing, counts, changes} veya None."""
    row = session.scalar(select(RiskComparison).where(RiskComparison.company_id == company_id)
                         .order_by(RiskComparison.created_at.desc(), RiskComparison.id.desc())
                         .limit(1))
    if row is None:
        return None
    changes = session.scalars(select(RiskChange).where(RiskChange.comparison_id == row.id)
                              .order_by(RiskChange.position, RiskChange.id)).all()
    return {
        "id": row.id,
        "old_filing": get_filing(session, row.old_filing_id),
        "new_filing": get_filing(session, row.new_filing_id),
        "counts": {"added": row.added, "removed": row.removed, "modified": row.modified,
                   "unchanged": row.unchanged},
        "changes": [{"change_type": c.change_type, "old_text": c.old_text,
                     "new_text": c.new_text, "similarity": c.similarity} for c in changes],
        "created_at": row.created_at,
    }


def save_score(session: Session, company_id: int, score: dict) -> int:
    """compute_score çıktısının anlık görüntüsünü yazar (payload JSON)."""
    as_of = score.get("as_of_period")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of[:10])
    row = CompanyScore(company_id=company_id, as_of_period=as_of,
                       total=float(score["total"]) if score.get("total") is not None else None,
                       band=score.get("band"),
                       weights_version=score.get("weights_version") or WEIGHTS_VERSION,
                       payload=dumps(score), created_at=utcnow())
    session.add(row)
    session.commit()
    return row.id


def load_latest_score(session: Session, company_id: int) -> dict | None:
    row = session.scalar(select(CompanyScore).where(CompanyScore.company_id == company_id)
                         .order_by(CompanyScore.created_at.desc(), CompanyScore.id.desc())
                         .limit(1))
    if row is None:
        return None
    payload = loads(row.payload, {}) or {}
    payload.setdefault("total", row.total)
    payload.setdefault("band", row.band)
    payload.setdefault("as_of_period", row.as_of_period)
    payload.setdefault("weights_version", row.weights_version)
    return payload


# --------------------------------------------------------------------------- soru-cevap kaydı


def log_qa(session: Session, company_id: int, question: str, result: dict) -> int:
    row = QALog(company_id=company_id, question=question, answer=str(result.get("answer", "")),
                mode=str(result.get("mode", "")), model=result.get("model"),
                source_chunk_ids=dumps([s.get("chunk_id") for s in result.get("sources") or []]),
                latency_ms=int(result.get("latency_ms") or 0), created_at=utcnow())
    session.add(row)
    session.commit()
    return row.id


# --------------------------------------------------------------------------- işler


def job_dict(row: IngestionJob) -> dict:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "status": row.status,
        "step": row.step,
        "progress": float(row.progress or 0.0),
        "error": row.error,
        "request_count": int(row.request_count or 0),
        "analyze": bool(row.analyze),
        "warnings": loads(row.warnings, []) or [],
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "updated_at": row.updated_at,
    }


def create_job(session: Session, ticker: str, analyze: bool = True) -> dict:
    now = utcnow()
    row = IngestionJob(ticker=ticker.upper(), status="queued", step="Sırada", progress=0.0,
                       request_count=0, analyze=analyze, created_at=now, updated_at=now)
    session.add(row)
    session.commit()
    return job_dict(row)


_JOB_FIELDS = {"status", "step", "progress", "error", "request_count", "started_at",
               "finished_at"}


def update_job(session: Session, job_id: int, **fields) -> dict | None:
    """İş alanlarını günceller ve commit eder (ilerleme başka oturumlardan görünsün)."""
    row = session.get(IngestionJob, job_id)
    if row is None:
        return None
    unknown = set(fields) - _JOB_FIELDS
    if unknown:
        raise ValueError(f"Bilinmeyen iş alanı: {sorted(unknown)}")
    for key, value in fields.items():
        if key in ("request_count", "progress"):
            if value is None:
                continue
            value = min(1.0, max(0.0, float(value))) if key == "progress" else int(value)
        setattr(row, key, value)
    if row.status == "running" and row.started_at is None:
        row.started_at = utcnow()
    row.updated_at = utcnow()  # canlılık işareti (bkz. find_running_job)
    session.commit()
    return job_dict(row)


def add_job_warning(session: Session, job_id: int, message: str) -> None:
    """İşi durdurmayan adım hatasını işin uyarı listesine ekler."""
    row = session.get(IngestionJob, job_id)
    if row is None:
        return
    warnings = loads(row.warnings, []) or []
    warnings.append(message[:500])
    row.warnings = dumps(warnings[-50:])
    row.updated_at = utcnow()
    session.commit()


def get_job(session: Session, job_id: int) -> dict | None:
    row = session.get(IngestionJob, job_id)
    return job_dict(row) if row is not None else None


def job_tickers(session: Session, ticker: str, aliases: Iterable[str] = ()) -> list[str]:
    """Aynı şirketi (CIK) gösteren ticker'lar: verilen ticker, ``aliases`` (ör. SEC ticker
    listesinde aynı CIK'li olanlar) ve veritabanındaki şirketin bilinen ticker'ları."""
    out: list[str] = []
    for t in [ticker, *aliases]:
        t = str(t or "").strip().upper()
        if t and t not in out:
            out.append(t)
    for t in list(out):
        row = get_company_row(session, t)
        if row is not None:
            for alias in company_tickers(row):
                if alias not in out:
                    out.append(alias)
    return out


def find_running_job(session: Session, ticker: str, now: datetime | None = None, *,
                     aliases: Iterable[str] = ()) -> dict | None:
    """Aynı şirket için kuyrukta / çalışan iş. Eşleşme ticker'a göre değil şirkete göredir:
    aynı CIK'in diğer ticker'larıyla (``job_tickers``) başlatılmış işler de bulunur (K3).

    STALE_JOB_AFTER boyunca ilerleme yazmamış iş (ör. sunucu yeniden başlatıldı) hata olarak
    kapatılır ve yok sayılır."""
    now = now or utcnow()
    tickers = job_tickers(session, ticker, aliases)
    rows = session.scalars(
        select(IngestionJob).where(IngestionJob.ticker.in_(tickers),
                                   IngestionJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(IngestionJob.id.desc())).all()
    found = None
    for row in rows:
        last = row.updated_at or row.started_at or row.created_at
        if last is not None and now - last > STALE_JOB_AFTER:
            row.status = "error"
            row.error = STALE_JOB_ERROR
            row.finished_at = now
            session.commit()
            continue
        if found is None:
            found = job_dict(row)
    return found
