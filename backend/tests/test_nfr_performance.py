"""Performans NFR'leri için saf (veritabanı / HTTP olmadan) bütçe testleri (Aşama 4).

NFR-04 (özet ekranı p95 < 2 sn) ve NFR-05 (soru-cevap p95 < 10 sn) asıl olarak çalışan sunucuya
karşı ``python -m finsight.bench_api`` ile ölçülür (docs/test-plani-asama4.md). Buradaki testler
sunucu süresinin hesaplama payını sınırlar: bir isteğin saf hesap kısmı hedefin küçük bir
kesrini (≤ %10) aşarsa ölçüme gerek kalmadan hata verir. Bütçeler yavaş CI makineleri için
cömerttir; sabit tohum, ağ yok.
"""

from __future__ import annotations

import random
import statistics
import time

from conftest import load

from finsight.embeddings import HashingEmbedder
from finsight.rag import answer_question
from finsight.ratios import compute_ratios
from finsight.scoring import compute_score
from finsight.views import overview_view, ratios_view, score_view
from finsight.xbrl import extract_all

NFR04_TARGET_S = 2.0
NFR05_TARGET_S = 10.0
COMPUTE_SHARE = 0.10  # hesaplama payı hedefin en fazla %10'u

COMPANY = {"ticker": "EXJN", "cik": "0000000002", "name": "Example June Corp", "sic": None,
           "sic_description": None, "fiscal_year_end": "0630", "exchanges": [],
           "analysis_updated_at": None}

VOCAB = ("revenue supply chain customers regulation competition cloud margin cash liquidity "
         "tax interest rate inflation cybersecurity litigation workforce suppliers demand "
         "product pricing currency exchange debt covenant capital expenditure acquisition "
         "goodwill impairment inventory backlog contract warranty climate energy semiconductor "
         "logistics data privacy intellectual property patent license dividend repurchase").split()


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def _timed(fn, runs: int) -> list[float]:
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t0)
    return out


def test_nfr04_overview_request_compute_share_within_budget():
    """NFR-04: /overview + /score isteklerinin saf hesabı (oran + skor + görünüm) < 200 ms p95.

    API oranları her istekte kalemlerden yeniden hesaplar (Aşama 3 tasarım sapması); bu
    yüzden bu süre her sayfa açılışında ödenir.
    """
    facts = extract_all(load("companyfacts_full_example.json"), years=4)
    signals = {"mdna_net_tone": 0.05, "risk_net_tone": -0.4, "risk_added": 3}

    def request():
        ratios = compute_ratios(facts)
        score = compute_score(ratios, facts, signals)
        overview_view(COMPANY, facts, score, [], [], None)
        score_view(score)
        ratios_view(COMPANY, ratios)

    request()  # ısınma
    samples = _timed(request, 30)
    assert _p95(samples) < NFR04_TARGET_S * COMPUTE_SHARE, (
        f"p95 {_p95(samples) * 1000:.1f} ms; medyan {statistics.median(samples) * 1000:.1f} ms")


def _synthetic_chunks(n: int, embedder: HashingEmbedder, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    texts = [" ".join(rng.choice(VOCAB) for _ in range(120)) + "." for _ in range(n)]
    vectors = embedder.embed(texts)
    return [{"chunk_id": i + 1, "text": t, "embedding": vectors[i],
             "embedding_model": embedder.name, "section": "1A" if i % 2 else "7",
             "section_title": "Risk Faktörleri" if i % 2 else "MD&A", "form": "10-K",
             "filing_date": f"2026-0{1 + i % 9}-15", "accession_no": f"0000000002-26-{i:06d}",
             "url": f"https://www.sec.gov/Archives/edgar/data/2/{i}/"}
            for i, t in enumerate(texts)]


def test_nfr05_retrieval_without_llm_is_small_share_of_budget():
    """NFR-05: 2.000 parçalık bir şirkette (≈ birkaç 10-K + 10-Q) erişim + çıkarımsal cevap
    p95 < 1 sn; geri kalan bütçe LLM içindir. ``latency_ms`` yanıtta bulunur."""
    embedder = HashingEmbedder()
    chunks = _synthetic_chunks(2000, embedder)
    questions = ["What are the supply chain risks?", "How does inflation affect margin?",
                 "Tedarik zinciri riskleri nelerdir?", "cybersecurity litigation exposure"]
    samples = []
    for q in questions * 3:
        t0 = time.perf_counter()
        res = answer_question(q, chunks, embedder, llm=None)
        samples.append(time.perf_counter() - t0)
        assert isinstance(res["latency_ms"], int) and res["latency_ms"] >= 0
        assert res["mode"] in {"extractive", "not_found"}
    assert _p95(samples) < NFR05_TARGET_S * COMPUTE_SHARE, (
        f"p95 {_p95(samples) * 1000:.1f} ms")


def test_nfr05_reported_latency_matches_wall_clock():
    """NFR-05 ölçüm yöntemi: yanıttaki latency_ms gerçek süreyi (±50 ms) yansıtır; yük
    testindeki istemci tarafı ölçümle karşılaştırılabilir olmalı."""
    embedder = HashingEmbedder()
    chunks = _synthetic_chunks(300, embedder, seed=11)
    t0 = time.perf_counter()
    res = answer_question("What are the supply chain risks?", chunks, embedder, llm=None)
    wall_ms = (time.perf_counter() - t0) * 1000
    assert abs(res["latency_ms"] - wall_ms) <= 50
