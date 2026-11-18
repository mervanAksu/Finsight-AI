"""Entegrasyon testleri (test_api / test_repository / test_jobs) için ortak sahte veriler.

Ağa çıkılmaz: SecClient, conftest.FakeFetcher ile URL → sabit yanıt eşlemesinden beslenir.
Sentetik şirket "Example Corp" (EXMP, CIK 1) gerçek bir şirket değildir; belge metinleri
NLP ajanının sentetik "Kestrel Dynamics" HTML'leridir. Başvuru tarihleri bugüne göre
hesaplanır ki ``collect``'in "son N yıl" süzgeci testin çalıştığı yıldan bağımsız olsun.

sqlalchemy/fastapi bu modülde içe aktarılmaz; kullanan test dosyaları önce importorskip yapar.
"""

import copy
import json
from datetime import date, timedelta

from conftest import FIXTURES, UA, FakeClock, FakeFetcher, load

from finsight.edgar import document_url
from finsight.rate_limiter import RateLimiter
from finsight.sec_client import SecClient

NLP = FIXTURES / "nlp"
CIK = "0000000001"
TICKER = "EXMP"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"

ACC_K_NEW = "0000000001-26-000010"
ACC_K_OLD = "0000000001-25-000005"
ACC_Q = "0000000001-26-000012"
ACC_8K = "0000000001-26-000011"
ACC_K_AMEND = "0000000001-26-000014"

DOCS = {
    ACC_K_NEW: "exmp-10k-new.htm",
    ACC_K_OLD: "exmp-10k-old.htm",
    ACC_Q: "exmp-10q.htm",
    ACC_8K: "exmp-8k.htm",
    ACC_K_AMEND: "exmp-10ka.htm",
}
# Eski 10-K'da olmayan (yeni 10-K'da "eklenen") risk paragrafının başı
ADDED_RISK_PREFIX = "Changes in tax laws"


def build_submissions(today: date | None = None) -> dict:
    """İki 10-K (eski/yeni), bir 10-Q, bir 8-K ve aynı döneme ait bir 10-K/A."""
    today = today or date.today()

    def ago(days: int) -> str:
        return (today - timedelta(days=days)).isoformat()

    sub = copy.deepcopy(load("submissions_example.json"))
    sub["sicDescription"] = "Services-Prepackaged Software"
    sub["exchanges"] = ["Nasdaq"]
    sub["filings"]["recent"] = {
        "accessionNumber": [ACC_K_AMEND, ACC_8K, ACC_Q, ACC_K_NEW, ACC_K_OLD],
        "filingDate": [ago(150), ago(200), ago(190), ago(280), ago(645)],
        "reportDate": [ago(320), ago(200), ago(230), ago(320), ago(685)],
        "form": ["10-K/A", "8-K", "10-Q", "10-K", "10-K"],
        "primaryDocument": [DOCS[a] for a in (ACC_K_AMEND, ACC_8K, ACC_Q, ACC_K_NEW,
                                              ACC_K_OLD)],
        "items": ["", "2.02,9.01", "", "", ""],
        "isXBRL": [1, 0, 1, 1, 1],
    }
    return sub


def old_10k_html() -> bytes:
    """Yeni 10-K'dan vergi riski paragrafı çıkarılmış önceki yıl 10-K'sı."""
    lines = (NLP / "kestrel_10k.html").read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if ADDED_RISK_PREFIX not in line).encode("utf-8")


def doc_url(accession: str) -> str:
    return document_url(CIK, accession, DOCS[accession])


def build_routes(today: date | None = None, *, missing_docs: tuple[str, ...] = ()) -> dict:
    """FakeFetcher rotaları. missing_docs: 404 dönecek başvuru accession numaraları."""
    routes: dict[str, tuple[int, bytes]] = {
        TICKERS_URL: (200, json.dumps(load("company_tickers_example.json")).encode()),
        SUBMISSIONS_URL: (200, json.dumps(build_submissions(today)).encode()),
        FACTS_URL: (200, json.dumps(load("companyfacts_full_example.json")).encode()),
        doc_url(ACC_K_NEW): (200, (NLP / "kestrel_10k.html").read_bytes()),
        doc_url(ACC_K_OLD): (200, old_10k_html()),
        doc_url(ACC_Q): (200, (NLP / "kestrel_10q.html").read_bytes()),
    }
    for acc in missing_docs:
        routes[doc_url(acc)] = (404, b"")
    return routes


class FakeSec:
    """Her çağrıda aynı sahte fetcher'ı paylaşan yeni bir SecClient üretir (client_factory)."""

    def __init__(self, routes: dict | None = None) -> None:
        self.fetcher = FakeFetcher(routes if routes is not None else build_routes())
        self.clock = FakeClock()
        self.clients: list[SecClient] = []

    def __call__(self) -> SecClient:
        client = SecClient(UA, fetcher=self.fetcher,
                           limiter=RateLimiter(8, clock=self.clock, sleep=self.clock.sleep),
                           sleep=lambda _s: None)
        self.clients.append(client)
        return client

    def urls(self) -> list[str]:
        return [url for url, _headers in self.fetcher.calls]

    def document_calls(self) -> list[str]:
        return [u for u in self.urls() if "/Archives/edgar/data/" in u]


def memory_session_factory():
    """Bellek içi SQLite (StaticPool: arka plan işi ve istekler aynı veriyi görür)."""
    from finsight.db import init_db, make_engine, make_session_factory

    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


def use_light_models():
    """Testlerde ağır modeller yerine sözlük + hashing; LLM kapalı."""
    from finsight import jobs
    from finsight.embeddings import HashingEmbedder
    from finsight.sentiment import LexiconModel

    jobs.reset_models()
    jobs.override_models(sentiment=LexiconModel(dictionary_path=""), embedder=HashingEmbedder(),
                         llm=None)
