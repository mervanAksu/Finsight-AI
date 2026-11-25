"""İndirme/çıkarım örtüşmesi (docs/performans-asama4.md §6.1) — VERİTABANSIZ test.

``jobs.run_analysis_for_company`` bellek içi sahte bir ``repo`` ile çalıştırılır; böylece
SQLAlchemy kurulu olmayan ortamda da (bulut) örtüşme ve davranış korunumu doğrulanır.
SQLAlchemy yoksa ``jobs`` içe aktarılabilsin diye ``sqlalchemy.orm``, ``finsight.repository``
ve ``finsight.models`` için yalnızca bu testte geçerli boş modüller yerleştirilir (çalışma
sonunda geri alınır). Gerçek veritabanlı sürüm: tests/test_jobs.py
``test_download_overlaps_inference_same_summary_and_score``.

Sentetik belgeler NLP fixture'larıdır ("Kestrel Dynamics"); ağ yok.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
import types

import pytest
from conftest import FIXTURES, UA, FakeClock, FakeFetcher

from finsight.edgar import document_url
from finsight.embeddings import HashingEmbedder
from finsight.rate_limiter import RateLimiter
from finsight.sec_client import SecClient
from finsight.sentiment import LexiconModel

CIK = "0000000001"
HTML_10K = (FIXTURES / "nlp" / "kestrel_10k.html").read_bytes()
HTML_10Q = (FIXTURES / "nlp" / "kestrel_10q.html").read_bytes()
FILINGS = [  # load_filings sırası: yeniden eskiye
    {"id": 3, "accession_no": "0000000001-26-000012", "form": "10-Q", "is_amendment": False,
     "filing_date": "2026-05-01", "report_date": "2026-03-31", "primary_document": "q.htm"},
    {"id": 2, "accession_no": "0000000001-26-000010", "form": "10-K", "is_amendment": False,
     "filing_date": "2026-02-01", "report_date": "2025-12-31", "primary_document": "k2.htm"},
    {"id": 1, "accession_no": "0000000001-25-000005", "form": "10-K", "is_amendment": False,
     "filing_date": "2025-02-01", "report_date": "2024-12-31", "primary_document": "k1.htm"},
]


@pytest.fixture
def jobs(monkeypatch):
    """finsight.jobs; SQLAlchemy yoksa geçici boş modüllerle içe aktarılır."""
    if importlib.util.find_spec("sqlalchemy") is None:
        orm = types.ModuleType("sqlalchemy.orm")
        orm.Session = object
        orm.sessionmaker = object
        sa = types.ModuleType("sqlalchemy")
        sa.orm = orm
        fake_repo = types.ModuleType("finsight.repository")
        fake_models = types.ModuleType("finsight.models")
        fake_models.utcnow = lambda: None
        for name, mod in (("sqlalchemy", sa), ("sqlalchemy.orm", orm),
                          ("finsight.repository", fake_repo), ("finsight.models", fake_models)):
            monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.delitem(sys.modules, "finsight.jobs", raising=False)
        module = importlib.import_module("finsight.jobs")
        yield module
        sys.modules.pop("finsight.jobs", None)
        import finsight

        # İçe aktarma paket özniteliklerini de bağlar; geçici modüller sızmasın.
        for attr, mod in (("jobs", module), ("repository", fake_repo), ("models", fake_models)):
            if getattr(finsight, attr, None) is mod:
                delattr(finsight, attr)
    else:
        yield importlib.import_module("finsight.jobs")


class FakeRepo:
    """run_analysis_for_company'nin kullandığı repository fonksiyonlarının bellek içi hali.
    Her çağrının iş parçacığını kaydeder (Session arka planda kullanılmamalı)."""

    def __init__(self) -> None:
        self.analyzed: dict[int, tuple[str, str]] = {}
        self.sections: dict[tuple[int, str], str] = {}
        self.saved: dict[int, dict] = {}
        self.threads: set[str] = set()

    def _t(self) -> None:
        self.threads.add(threading.current_thread().name)

    def load_company_by_id(self, session, company_id):
        self._t()
        return {"id": company_id, "cik": CIK, "ticker": "EXMP"}

    def load_filings(self, session, company_id):
        self._t()
        return [dict(f) for f in FILINGS]

    def is_filing_analyzed(self, session, filing_id, s_name, e_name):
        self._t()
        return self.analyzed.get(filing_id) == (s_name, e_name)

    def load_sentence_predictions(self, session, company_id, name):
        self._t()
        return {}

    def save_analysis_document(self, session, filing_id, analysis, document=None):
        self._t()
        self.analyzed[filing_id] = (analysis["sentiment_model"], analysis["embedding_model"])
        for sec in analysis["sections"]:
            self.sections[(filing_id, sec["item_code"])] = sec["text"]
        out = {"sections": [s["item_code"] for s in analysis["sections"]],
               "sentences": sum(len(s["sentences"]) for s in analysis["sections"]),
               "chunks": sum(len(s.get("chunks") or []) for s in analysis["sections"])}
        self.saved[filing_id] = out
        return out

    def load_section_text(self, session, filing_id, code):
        self._t()
        return self.sections.get((filing_id, code))

    def save_risk_comparison(self, session, company_id, old_id, new_id, comparison):
        self._t()
        return 1

    def touch_analysis(self, session, company_id):
        self._t()


class FakeSession:
    def rollback(self) -> None:
        pass


class Timeline:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[tuple[str, str, float, float]] = []

    def add(self, kind: str, start: float) -> None:
        with self.lock:
            self.events.append((kind, threading.current_thread().name, start,
                                time.perf_counter()))


def make_client(delay: float, timeline: Timeline, missing: tuple[str, ...] = ()) -> SecClient:
    routes = {document_url(CIK, f["accession_no"], f["primary_document"]):
              (200, HTML_10Q if f["form"] == "10-Q" else HTML_10K) for f in FILINGS}
    for acc in missing:
        f = next(x for x in FILINGS if x["accession_no"] == acc)
        routes[document_url(CIK, acc, f["primary_document"])] = (404, b"")
    inner = FakeFetcher(routes)

    def fetch(url, headers):
        start = time.perf_counter()
        time.sleep(delay)
        out = inner(url, headers)
        timeline.add("download", start)
        return out

    clock = FakeClock()
    return SecClient(UA, fetcher=fetch, limiter=RateLimiter(8, clock=clock, sleep=clock.sleep),
                     sleep=lambda _s: None)


class SlowSentiment:
    def __init__(self, delay: float, timeline: Timeline) -> None:
        self._inner = LexiconModel(dictionary_path="")
        self.name = self._inner.name
        self.device_info = "CPU (test)"
        self.delay = delay
        self.timeline = timeline

    def predict(self, sentences):
        start = time.perf_counter()
        time.sleep(self.delay)
        out = self._inner.predict(sentences)
        self.timeline.add("sentiment", start)
        return out


def analyze(jobs, monkeypatch, delay: float, *, missing=(), tmp_path=None, repo=None):
    timeline = Timeline()
    repo = repo or FakeRepo()
    monkeypatch.setattr(jobs, "repo", repo)
    monkeypatch.setattr(jobs, "compute_and_save_score",
                        lambda session, company_id: {"total": 61.5, "band": "Dengeli",
                                                     "as_of_period": "2025-12-31"})
    jobs.override_models(llm=None)
    client = make_client(delay, timeline, missing)
    started = time.perf_counter()
    summary = jobs.run_analysis_for_company(
        FakeSession(), client, 1, sentiment_model=SlowSentiment(delay, timeline),
        embedder=HashingEmbedder(), documents_dir=str(tmp_path), analyze_quarterly=True)
    return summary, timeline, time.perf_counter() - started, repo, client


def _shape(summary: dict) -> list[dict]:
    keys = ("accession_no", "form", "status", "sections", "sentences", "chunks")
    return [{k: f.get(k) for k in keys} for f in summary["filings"]]


def test_prefetch_overlaps_download_with_inference(jobs, monkeypatch, tmp_path):
    delay = 0.25
    summary, timeline, elapsed, repo, client = analyze(jobs, monkeypatch, delay,
                                                       tmp_path=tmp_path)
    assert [f["status"] for f in summary["filings"]] == ["analyzed"] * 3
    downloads = [e for e in timeline.events if e[0] == "download"]
    inference = [e for e in timeline.events if e[0] == "sentiment"]
    assert len(downloads) == 3 and len(inference) >= 2  # 2. 10-K cümle önbelleğinden
    assert client.request_count == 3  # tüm indirmeler SecClient'tan (sayaç + hız sınırı)
    assert all(e[1].startswith("sec-prefetch") for e in downloads)
    ordered = sorted(downloads, key=lambda e: e[2])
    assert all(a[3] <= b[2] + 1e-3 for a, b in zip(ordered, ordered[1:]))  # tek SEC isteği
    assert any(d[2] < s[3] and s[2] < d[3] for d in downloads for s in inference)
    # Sıralı olsaydı ≥ (3 indirme + çıkarım sayısı) × delay; örtüşme en az bir delay kazandırır
    assert elapsed < (3 + len(inference)) * delay - 0.15, elapsed
    # Session / repository yalnızca çağıran iş parçacığında kullanıldı
    assert not any(t.startswith("sec-prefetch") for t in repo.threads)
    assert summary["sentiment_device"] == "CPU (test)"


def test_prefetch_keeps_summary_identical(jobs, monkeypatch, tmp_path):
    fast, *_ = analyze(jobs, monkeypatch, 0.0, tmp_path=tmp_path / "a")
    slow, *_ = analyze(jobs, monkeypatch, 0.05, tmp_path=tmp_path / "b")
    assert _shape(fast) == _shape(slow)
    assert fast["risk"] == slow["risk"] and fast["score"] == slow["score"]
    assert fast["warnings"] == slow["warnings"] == []


def test_prefetch_error_marks_only_that_filing(jobs, monkeypatch, tmp_path):
    summary, *_ = analyze(jobs, monkeypatch, 0.0, tmp_path=tmp_path,
                          missing=("0000000001-26-000010",))
    status = {f["accession_no"]: f["status"] for f in summary["filings"]}
    assert status["0000000001-26-000010"] == "error"
    assert sorted(status.values()) == ["analyzed", "analyzed", "error"]
    assert any("404" in w for w in summary["warnings"])


def test_skipped_filings_are_not_downloaded(jobs, monkeypatch, tmp_path):
    repo = FakeRepo()
    name = LexiconModel(dictionary_path="").name
    repo.analyzed = {1: (name, HashingEmbedder().name), 2: (name, HashingEmbedder().name)}
    repo.sections = {(1, "1A"): "Risk one.", (2, "1A"): "Risk one."}
    summary, timeline, _e, _r, client = analyze(jobs, monkeypatch, 0.0, tmp_path=tmp_path,
                                                repo=repo)
    assert [f["status"] for f in summary["filings"]] == ["skipped", "skipped", "analyzed"]
    assert client.request_count == 1  # yalnızca 10-Q indirildi
