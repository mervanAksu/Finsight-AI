"""Analiz boru hattını adım adım ölçen araç (ağsız; docs/performans-asama4.md).

Kullanım:
    python -m finsight.bench_pipeline                       # sentetik 2×10-K + 10-Q, yedek modeller
    python -m finsight.bench_pipeline --models configured   # FinBERT / ST (kuruluysa) ile
    python -m finsight.bench_pipeline --dir <önbellek>\\documents\\789019   # diske kaydedilmiş belgeler
    python -m finsight.bench_pipeline --file a.htm --file b.htm:10-Q --repeat 3 --json sonuc.json

Ölçülen adımlar (``analyze.analyze_sections`` / ``embed_sections`` / ``compare_risk`` ile aynı
fonksiyonlar, aynı sırayla): HTML → metin, bölüm ayıklama, cümle bölme, duygu, parçalama,
gömme, risk karşılaştırması. SEC'e ve LLM'e istek atılmaz. ``--models configured`` ilk
çalıştırmada model ağırlıklarını Hugging Face önbelleğinden okur (yoksa indirir).

Sentetik belgeler gerçek EDGAR iXBRL'ine benzer yapıdadır (gizli ``ix:header`` olgu bloğu,
satır içi stiller, iç içe ``span``/``font``, içindekiler tablosu, sayfa numaraları, büyük
finansal tablolar, ``ix:nonFraction`` hücreleri); gerçek belgelerin yerini tutmaz, yalnızca
önce/sonra karşılaştırması için tekrarlanabilir bir girdi sağlar. Yatırım tavsiyesi değildir.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import sys
import time
from collections.abc import Sequence

from . import DISCLAIMER

# --------------------------------------------------------------------------- sentetik belge

_RISK_TOPICS = (
    "supply chain", "cybersecurity", "foreign exchange", "interest rate", "competition",
    "regulatory", "litigation", "talent retention", "product defects", "climate",
    "data privacy", "intellectual property", "tax law", "credit facility", "customer concentration",
)
_POS = ("strong", "improved", "growth", "record", "favorable", "resilient", "successful")
_NEG = ("adverse", "decline", "loss", "impairment", "disruption", "litigation", "weakness")
_FILLER = (
    "our operations", "the Company", "our customers", "our results of operations",
    "the global economy", "our financial condition", "third-party suppliers",
    "our cloud services", "the fiscal year", "our competitors", "applicable laws",
)


def _sentence(rng: random.Random, topic: str) -> str:
    a, b, c = rng.sample(_FILLER, 3)
    tone = rng.choice(_POS + _NEG + ("material", "significant", "uncertain"))
    n = rng.randint(2, 40) * 100
    templates = (
        f"Changes in {topic} could have a {tone} effect on {a} and {b}, and may require us to "
        f"incur additional costs of approximately ${n} million.",
        f"In fiscal year {rng.randint(2020, 2025)}, {a} experienced {tone} results related to "
        f"{topic}, which affected {b} and {c}.",
        f"We may not be able to manage {topic} risks effectively, and any {tone} development "
        f"could harm {a}, {b} or {c}.",
        f"There can be no assurance that {a} will not be affected by {topic} in future periods.",
        f"Revenue from {a} increased {rng.randint(1, 30)}% driven by {tone} trends in {topic} "
        f"and higher demand from {b}.",
    )
    return rng.choice(templates)


def _paragraph(rng: random.Random, topic: str, sentences: int) -> str:
    return " ".join(_sentence(rng, topic) for _ in range(sentences))


def _styled(text: str, _rng: random.Random | None = None) -> str:
    """EDGAR'daki gibi satır içi stilli, iç içe span'lı paragraf.

    Bölme noktası deterministiktir (rastgele sayı tüketmez): değiştirilen bir paragraf
    sonraki paragrafların rastgele akışını kaydırmasın (eski/yeni 10-K hizalı kalsın)."""
    style = ("font-family:'Times New Roman',serif;font-size:10pt;color:#000000;"
             "line-height:120%;text-align:justify")
    words = text.split(" ")
    cut = max(1, len(words) // 3)
    return (f'<div style="margin-top:6pt;margin-bottom:6pt;text-indent:24pt">'
            f'<span style="{style}">{" ".join(words[:cut])} </span>'
            f'<span style="{style}"><font style="font-size:10pt">{" ".join(words[cut:])}'
            f'</font></span></div>\n')


def _heading(text: str) -> str:
    return (f'<div style="margin-top:12pt"><span style="font-family:\'Times New Roman\';'
            f'font-size:10pt;font-weight:700">{text}</span></div>\n')


def _page_break(page: int) -> str:
    return (f'<div style="text-align:center"><span style="font-size:9pt">{page}</span></div>'
            '<hr style="page-break-after:always"/>'
            '<div><a href="#toc" style="font-size:8pt">Table of Contents</a></div>\n')


def _financial_table(rng: random.Random, rows: int, cols: int, prefix: str) -> str:
    cell = 'style="padding:0 1pt;vertical-align:bottom;border-bottom:1px solid #000"'
    out = ['<table style="border-collapse:collapse;width:100%">']
    for r in range(rows):
        cells = [f'<td {cell}><span style="font-size:9pt">{prefix} line item {r}</span></td>']
        for c in range(cols):
            value = f"{rng.randint(1, 999_999):,}"
            cells.append(
                f'<td {cell}><span style="font-size:9pt">$</span></td>'
                f'<td {cell}><span style="font-size:9pt"><ix:nonFraction unitRef="usd" '
                f'contextRef="c-{c}" decimals="-6" name="us-gaap:Item{r}" scale="6" '
                f'format="ixt:num-dot-decimal">{value}</ix:nonFraction></span></td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>\n")
    return "".join(out)


def synthetic_filing_html(form: str = "10-K", *, seed: int = 0, risk_paragraphs: int = 160,
                          mdna_paragraphs: int = 120, table_rows: int = 40,
                          tables: int = 30, hidden_facts: int = 3000,
                          changed_fraction: float = 0.0) -> str:
    """EDGAR iXBRL benzeri tekrarlanabilir belge (ölçüm girdisi; gerçek veri değildir).

    ``changed_fraction``: aynı ``seed``'li belgeye göre yeniden yazılan risk paragrafı oranı
    (eski/yeni 10-K risk karşılaştırması için). Varsayılan boyutlarla ~2–3 MB HTML üretir.
    """
    is_q = form.upper().startswith("10-Q")
    base = random.Random(seed)
    change = random.Random(seed + 7919)
    parts = ['<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
             'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><head><title>synthetic</title>'
             '<style>body{font-family:serif}</style></head><body>\n'
             '<div style="display:none"><ix:header><ix:hidden>'
             f'<ix:nonNumeric name="dei:DocumentType" contextRef="c-0">{form}</ix:nonNumeric>'
             '</ix:hidden><ix:resources>']
    parts += [f'<xbrli:context id="c-{i}"><xbrli:entity><xbrli:identifier scheme="http://www.sec'
              f'.gov/CIK">0000000000</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>'
              f'2025-06-30</xbrli:instant></xbrli:period></xbrli:context>'
              for i in range(hidden_facts)]
    parts.append("</ix:resources></ix:header></div>\n")
    parts.append(_heading(f"FORM {form}"))
    # İçindekiler (ilk eşleşme tuzağı)
    toc = (["Item 1.", "Financial Statements", "Item 2.", "Management's Discussion and Analysis "
            "of Financial Condition and Results of Operations", "Item 3.", "Quantitative and "
            "Qualitative Disclosures About Market Risk", "Item 4.", "Controls and Procedures"]
           if is_q else
           ["Item 1.", "Business", "Item 1A.", "Risk Factors", "Item 1B.",
            "Unresolved Staff Comments", "Item 7.", "Management's Discussion and Analysis of "
            "Financial Condition and Results of Operations", "Item 7A.", "Quantitative and "
            "Qualitative Disclosures About Market Risk", "Item 8.", "Financial Statements"])
    parts.append('<div id="toc"><table>')
    for i in range(0, len(toc), 2):
        parts.append(f"<tr><td><a href='#x'>{toc[i]}</a></td><td><a href='#x'>{toc[i + 1]}</a>"
                     f"</td><td>{i + 3}</td></tr>")
    parts.append("</table></div>\n")
    page = 3

    def section(title: str, n_paras: int, mutable: bool) -> None:
        nonlocal page
        parts.append(_heading(title))
        for k in range(n_paras):
            topic = _RISK_TOPICS[k % len(_RISK_TOPICS)]
            if k % 9 == 0:
                parts.append(_heading(f"Risks Related to {topic.title()}"))
            text = _paragraph(base, topic, base.randint(3, 7))
            if mutable and change.random() < changed_fraction:
                text = _paragraph(change, topic, change.randint(3, 7))
            parts.append(_styled(text, base))
            if k % 12 == 11:
                parts.append(_page_break(page))
                page += 1

    if is_q:
        parts.append(_heading("PART I — FINANCIAL INFORMATION"))
        parts.append(_heading("Item 1. Financial Statements"))
        for t in range(tables):
            parts.append(_financial_table(base, table_rows, 4, f"T{t}"))
        section("Item 2. Management's Discussion and Analysis of Financial Condition and "
                "Results of Operations", mdna_paragraphs // 2, False)
        parts.append(_heading("Item 3. Quantitative and Qualitative Disclosures About Market Risk"))
        parts.append(_styled(_paragraph(base, "market risk", 4), base))
        parts.append(_heading("Item 4. Controls and Procedures"))
        parts.append(_styled(_paragraph(base, "controls", 3), base))
        parts.append(_heading("PART II — OTHER INFORMATION"))
        section("Item 1A. Risk Factors", max(3, risk_paragraphs // 10), True)
        parts.append(_heading("Item 2. Unregistered Sales of Equity Securities"))
        parts.append(_styled(_paragraph(base, "equity", 3), base))
    else:
        parts.append(_heading("PART I"))
        parts.append(_heading("Item 1. Business"))
        for _ in range(20):
            parts.append(_styled(_paragraph(base, "business", 4), base))
        section("Item 1A. Risk Factors", risk_paragraphs, True)
        parts.append(_heading("Item 1B. Unresolved Staff Comments"))
        parts.append(_styled("None.", base))
        parts.append(_heading("PART II"))
        section("Item 7. Management's Discussion and Analysis of Financial Condition and "
                "Results of Operations", mdna_paragraphs, False)
        for t in range(tables // 3):
            parts.append(_financial_table(base, table_rows // 2, 3, f"M{t}"))
        parts.append(_heading("Item 7A. Quantitative and Qualitative Disclosures About Market "
                              "Risk"))
        parts.append(_styled(_paragraph(base, "market risk", 4), base))
        parts.append(_heading("Item 8. Financial Statements and Supplementary Data"))
        for t in range(tables):
            parts.append(_financial_table(base, table_rows, 3, f"F{t}"))
    parts.append("</body></html>\n")
    return "".join(parts)


def synthetic_company(seed: int = 0, scale: float = 1.0) -> list[tuple[str, str, str]]:
    """(ad, form, html): eski 10-K, yeni 10-K (risk paragraflarının ~%30'u değişmiş), 10-Q."""
    def k(n: int) -> int:
        return max(3, int(n * scale))

    common = {"risk_paragraphs": k(160), "mdna_paragraphs": k(120), "tables": k(30),
              "hidden_facts": k(3000)}
    return [
        ("synthetic-10k-old", "10-K", synthetic_filing_html("10-K", seed=seed, **common)),
        ("synthetic-10k-new", "10-K", synthetic_filing_html("10-K", seed=seed,
                                                            changed_fraction=0.3, **common)),
        ("synthetic-10q", "10-Q", synthetic_filing_html("10-Q", seed=seed + 1, **common)),
    ]


# --------------------------------------------------------------------------- belge yükleme

_DOC_TYPE = re.compile(rb"DocumentType[^>]*>\s*(10-[KQ](?:/A)?)", re.I)


def detect_form(body: bytes, default: str = "10-K") -> str:
    """iXBRL ``dei:DocumentType`` değerinden form türü (yoksa ``default``)."""
    m = _DOC_TYPE.search(body[:2_000_000])
    return m.group(1).decode().upper() if m else default


def load_documents(files: Sequence[str], directory: str | None) -> list[tuple[str, str, bytes]]:
    """``--file yol[:FORM]`` ve ``--dir`` (özyinelemeli *.htm/*.html) → (ad, form, gövde)."""
    paths: list[tuple[str, str | None]] = []
    for spec in files:
        path, form = spec, None
        m = re.match(r"^(.*):(10-[KQ](?:/A)?)$", spec, re.I)
        if m:
            path, form = m.group(1), m.group(2).upper()
        paths.append((path, form))
    if directory:
        for pattern in ("**/*.htm", "**/*.html"):
            for path in sorted(glob.glob(os.path.join(directory, pattern), recursive=True)):
                paths.append((path, None))
    out = []
    for path, form in paths:
        with open(path, "rb") as fh:
            body = fh.read()
        out.append((os.path.basename(path), form or detect_form(body), body))
    return out


# --------------------------------------------------------------------------- ölçüm

STEPS: tuple[tuple[str, str], ...] = (
    ("html_to_text", "HTML → metin"),
    ("sections", "Bölüm ayıklama"),
    ("sentences", "Cümle bölme + başlık eleme"),
    ("sentiment", "Duygu analizi"),
    ("chunking", "Parçalama"),
    ("embedding", "Gömme"),
    ("risk", "Risk karşılaştırması"),
)


def run_pipeline(documents: Sequence[tuple[str, str, bytes | str]], sentiment_model,
                 embedder, chunk_words: int | None = None) -> dict:
    """Belgeleri sırayla işler; adım süreleri (sn) ve sayaçlar döner.

    Cümle önbelleği KULLANILMAZ (modelin gerçek hızı); belge içi yinelenen cümleler ise
    ``predict_with_cache`` ile bir kez tahmin edilir (analiz hattıyla aynı).
    """
    from .analyze import CHUNK_OVERLAP_WORDS, default_chunk_words, predict_with_cache
    from .risk_diff import compare_risk_sections
    from .text_extract import chunk_text, content_sentences, extract_sections, html_to_text

    words = chunk_words or default_chunk_words(embedder)
    overlap = CHUNK_OVERLAP_WORDS if CHUNK_OVERLAP_WORDS < words else max(0, words // 4)
    timings = {key: 0.0 for key, _ in STEPS}
    counts = {"documents": 0, "bytes": 0, "sentences": 0, "predicted": 0, "chunks": 0,
              "risk_paragraph_pairs": 0}
    labels: list[str] = []
    risk_texts: list[str] = []
    for _name, form, body in documents:
        counts["documents"] += 1
        counts["bytes"] += len(body)
        t = time.perf_counter()
        text = html_to_text(body)
        t = _tick(timings, "html_to_text", t)
        sections = sorted(extract_sections(text, form).values(), key=lambda s: s["char_start"])
        t = _tick(timings, "sections", t)
        per_section = [content_sentences(sec["text"]) for sec in sections]
        flat = [s for sentences in per_section for s in sentences]
        t = _tick(timings, "sentences", t)
        stats: dict = {}
        preds = predict_with_cache(sentiment_model, flat, stats=stats) if flat else []
        t = _tick(timings, "sentiment", t)
        counts["sentences"] += len(flat)
        counts["predicted"] += int(stats.get("predicted", 0))
        labels += [p["label"] for p in preds]
        pieces_per_section = [chunk_text(sec["text"], target_words=words, overlap_words=overlap)
                              for sec in sections]
        t = _tick(timings, "chunking", t)
        for pieces in pieces_per_section:
            if pieces:
                embedder.embed(pieces)
            counts["chunks"] += len(pieces)
        _tick(timings, "embedding", t)
        if form.upper().startswith("10-K"):
            risk = next((s["text"] for s in sections if s["item_code"] == "1A"), None)
            if risk:
                risk_texts.append(risk)
    risk_counts = None
    if len(risk_texts) >= 2:
        t = time.perf_counter()
        risk_counts = compare_risk_sections(risk_texts[-2], risk_texts[-1])["counts"]
        _tick(timings, "risk", t)
    return {"timings": timings, "counts": counts, "risk": risk_counts,
            "label_counts": {lab: labels.count(lab) for lab in sorted(set(labels))},
            "total_seconds": sum(timings.values()),
            "sentiment_model": getattr(sentiment_model, "name", "?"),
            "embedding_model": getattr(embedder, "name", "?")}


def _tick(timings: dict, key: str, since: float) -> float:
    now = time.perf_counter()
    timings[key] += now - since
    return now


def best_of(results: Sequence[dict]) -> dict:
    """Tekrarlar arasında her adımın en kısa süresi (gürültüyü azaltır); sayaçlar ilkinden."""
    best = dict(results[0])
    best["timings"] = {key: min(r["timings"][key] for r in results) for key in results[0]["timings"]}
    best["total_seconds"] = sum(best["timings"].values())
    best["repeats"] = len(results)
    return best


def format_report(result: dict) -> str:
    total = result["total_seconds"] or 1e-9
    c = result["counts"]
    lines = [f"Belgeler: {c['documents']} ({c['bytes'] / 1e6:.1f} MB), cümle: {c['sentences']} "
             f"(modele giden {c['predicted']}), parça: {c['chunks']}",
             f"Modeller: duygu={result['sentiment_model']}, gömme={result['embedding_model']}"
             + (f", en iyi {result['repeats']} tekrar" if result.get("repeats") else ""),
             f"{'Adım':<32}{'Süre (sn)':>12}{'Pay':>8}", "-" * 52]
    for key, label in STEPS:
        sec = result["timings"].get(key, 0.0)
        lines.append(f"{label:<32}{sec:>12.3f}{100 * sec / total:>7.1f}%")
    lines.append(f"{'Toplam':<32}{result['total_seconds']:>12.3f}")
    if c["sentences"] and result["timings"]["sentiment"] > 0:
        lines.append(f"Duygu hızı: {c['predicted'] / result['timings']['sentiment']:.1f} cümle/sn")
    if result.get("risk"):
        lines.append("Risk farkı: " + ", ".join(f"{k}={v}" for k, v in result["risk"].items()))
    return "\n".join(lines)


def _models(kind: str):
    if kind == "fallback":
        from .embeddings import HashingEmbedder
        from .sentiment import LexiconModel
        return LexiconModel(), HashingEmbedder()
    from .embeddings import get_embedder
    from .sentiment import get_sentiment_model
    return get_sentiment_model(), get_embedder()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analiz boru hattı adım süreleri (ağsız).")
    parser.add_argument("--file", action="append", default=[],
                        help="Belge yolu, isteğe bağlı ':10-K' / ':10-Q' ekiyle (tekrarlanabilir)")
    parser.add_argument("--dir", help="Belge klasörü (ör. <önbellek>/documents/<CIK>)")
    parser.add_argument("--models", choices=("fallback", "configured"), default="fallback",
                        help="fallback: sözlük + hashing (varsayılan); configured: ortam "
                             "değişkenlerine göre FinBERT / sentence-transformers")
    parser.add_argument("--scale", type=float, default=1.0, help="Sentetik belge boyut çarpanı")
    parser.add_argument("--repeat", type=int, default=1, help="Tekrar sayısı (adım başına en iyisi)")
    parser.add_argument("--json", help="Sonucu bu JSON dosyasına da yaz")
    args = parser.parse_args(argv)

    if args.file or args.dir:
        docs = load_documents(args.file, args.dir)
        if not docs:
            print("Belge bulunamadı.", file=sys.stderr)
            return 2
    else:
        docs = [(n, f, h.encode("utf-8")) for n, f, h in synthetic_company(scale=args.scale)]
    print("Belgeler: " + ", ".join(f"{n} [{f}]" for n, f, _ in docs))
    t = time.perf_counter()
    sentiment_model, embedder = _models(args.models)
    print(f"Model yükleme: {time.perf_counter() - t:.1f} sn\n")
    runs = [run_pipeline(docs, sentiment_model, embedder) for _ in range(max(1, args.repeat))]
    result = best_of(runs)
    print(format_report(result))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    print("\n" + DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
