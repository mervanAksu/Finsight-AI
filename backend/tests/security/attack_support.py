"""Saldırı testlerinin ortak yardımcıları (saf Python; FastAPI/SQLAlchemy gerektirmez).

- ``xfail_until``: henüz düzeltilmemiş bulgu için ``xfail(strict=True, raises=AssertionError)``.
  ``raises=AssertionError``: test YALNIZCA savunma iddiası tuttuğunda "beklenen başarısızlık"
  sayılır; kurulum hatası, ImportError vb. gerçek hata olarak görünür (sahte kanıt olmaz).
  Düzeltme gelince test XPASS olur, strict=True bunu hataya çevirir → işaret kaldırılır.
- ``FakeLLM``: ağa çıkmayan, çağrıları sayan sahte LLM.
- ``KeywordEmbedder`` + ``make_chunk``: öngörülebilir sahte gömme ve RAG parçası.
"""

from __future__ import annotations

import numpy as np
import pytest

ATTACKER_ORIGIN = "https://kotu.example"
REBIND_HOST = "rebind.kotu.example:8000"
LOCAL_BASE = "http://127.0.0.1:8000"


def xfail_until(finding: str, note: str = ""):
    reason = f"{finding} düzeltilince geçmeli" + (f" ({note})" if note else "")
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


class FakeLLM:
    """Sahte LLM: ``reply`` sabit metin ya da ``(system, user) -> str`` fonksiyonu."""

    provider = "fake"
    model = "fake-llm-attack"

    def __init__(self, reply="Şirket tedarik riski bildiriyor [1]."):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        self.calls.append((system, user))
        return self.reply(system, user) if callable(self.reply) else self.reply


class KeywordEmbedder:
    """Anahtar kelime sayımına dayalı gömücü (hashing değil → sorgu çevirisi yapılmaz)."""

    name = "attack-keyword-embedder"
    default_min_similarity = 0.3
    KEYWORDS = ("supplier", "revenue", "risk", "cash", "stock", "billion")
    dim = len(KEYWORDS)

    def embed(self, texts):
        rows = [[t.lower().count(k) for k in self.KEYWORDS] for t in texts]
        mat = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms


def make_chunk(text: str, embedder: KeywordEmbedder | None = None, n: int = 1) -> dict:
    embedder = embedder or KeywordEmbedder()
    return {
        "chunk_id": 900 + n, "text": text, "section": "7",
        "section_title": "Yönetimin Değerlendirmesi (MD&A)", "form": "10-K",
        "filing_date": "2026-02-20", "accession_no": f"0009999999-26-00{n:04d}",
        "url": "https://www.sec.gov/Archives/edgar/data/9999999/x.htm",
        "embedding": embedder.embed([text])[0], "embedding_model": embedder.name,
    }
