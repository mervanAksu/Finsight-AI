"""Savunma testleri — 2. bağımsız inceleme turu (K1–K5, O1–O8). Saf Python kısımlar bulutta
koşar; FastAPI/SQLAlchemy gerektirenler ``importorskip`` ile kullanıcının bilgisayarında."""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from finsight.rag import _sentence_allowed, filter_answer, is_advice_question

REPO = pathlib.Path(__file__).resolve().parents[3]

# --------------------------------------------------------------------------- K2 (doğru cümle silinmemeli)

SRC_DATES = ("The fiscal year ended September 27, 2025. Net sales were $416,161 million, "
             "total revenue was $211,915 million and gross margin was 46.21%. "
             "We repurchased 211 915 shares. Services revenue was $5.2 billion.")


@pytest.mark.parametrize("answer", [
    "Mali yıl 27.09.2025 tarihinde sona erdi [1].",
    "Mali yıl 27/09/2025 tarihinde sona erdi [1].",
    "Mali yıl 09/27/2025 tarihinde sona erdi [1].",
    "Mali yıl 2025-09-27 tarihinde sona erdi [1].",
    "Mali yıl 27 Eylül 2025'te sona erdi [1].",
    "Mali yıl Eylül 2025'te sona erdi [1].",
    "Hizmet geliri 5.2B dolar oldu [1].",
    "Hizmet geliri 5.2bn dolar oldu [1].",
    "Hizmet geliri 5,2 mlr dolar oldu [1].",
    "Hizmet geliri 5,2 milyar dolar oldu [1].",
    "Toplam hasılat 211,9 milyar dolar oldu [1].",
    "Toplam hasılat $211.9 billion oldu [1].",
    "Toplam hasılat 211.915 milyon dolar oldu [1].",
    "Net satışlar 416,2 milyar dolar oldu [1].",
    "Net satışlar 416.161 milyon dolar oldu [1].",
    "Brüt marj %46,2 oldu [1].",
    "Brüt marj %46 oldu [1].",
    "Şirket 211 915 hisse geri aldı [1].",
    "Madde 7 ve Bölüm 7 bu konuyu ele alıyor; hasılat 211,9 milyar dolar [1].",
    "Item 7'ye göre brüt marj 46.21% [1].",
])
def test_k2_correct_sentences_are_kept(answer):
    """K2: tarih biçimleri, bitişik ölçek ekleri, ölçek dönüşümü + gösterilen hassasiyete
    yuvarlama, yüzde yuvarlaması, boşluklu binlik ve madde / bölüm referansları."""
    assert filter_answer(answer, [SRC_DATES])[0] == answer


def test_k2_table_scale_context():
    """"(in millions)" tablosundaki ölçeksiz sayı ölçekli cevapla eşleşir."""
    src = "Total net sales (in millions) 391,035 383,285"
    assert filter_answer("Net satışlar 391 milyar dolar [1].", [src])[0] != ""
    assert filter_answer("Net satışlar 391,0 milyar dolar [1].", [src])[0] != ""
    assert filter_answer("Net satışlar 392,5 milyar dolar [1].", [src])[0] == ""
    assert filter_answer("Net satışlar 391 milyar dolar [1].", ["Net sales 391,035"])[0] == ""


# --------------------------------------------------------------------------- K3 (uydurma sayı geçmemeli)


@pytest.mark.parametrize("answer,source,meta", [
    ("Hasılat 20 milyar dolar oldu [1].", "Revenue grew.", "2026-02-20"),   # üst veri günü
    ("Hasılat 2026 milyon dolar [1].", "Revenue grew.", "2026-02-20"),       # üst veri yılı
    ("Hasılat 7 milyar dolar [1].", "Revenue grew in Item 7.", ""),           # bölüm kodu
    ("Hasılat 15 milyar dolar [1].", "Margin was 15%.", ""),                  # ölçek yok
    ("Hasılat 15 [1].", "Revenue was 1.5 billion.", ""),                      # 1.5 ≠ 15
    ("Hasılat 1.5 milyar dolar [1].", "Margin was 15%.", ""),                 # TR/EN belirsizlik
    ("Hasılat yüz milyar dolar oldu [1].", "Revenue was $90 billion.", ""),   # yazıyla
    ("Revenue was one hundred billion dollars [1].", "Revenue was $90 billion.", ""),
    ("Marj %15 oldu [1].", "Revenue was $15 billion.", ""),                   # yüzde ≠ tutar
    ("Rapor 21.02.2026 tarihli [1].", "Revenue grew.", "2026-02-20"),        # yanlış gün
])
def test_k3_fabricated_numbers_are_removed(answer, source, meta):
    text, _ = filter_answer(answer, [source], [(meta,)])
    assert text == "", text


@pytest.mark.parametrize("answer", [
    "Başvuru 20.02.2026 tarihinde yapıldı [1].",
    "Başvuru 2026-02-20 tarihinde yapıldı [1].",
])
def test_k3_metadata_full_date_still_allowed(answer):
    assert filter_answer(answer, ["Revenue grew."], [("2026-02-20",)])[0] == answer


def test_k3_written_number_matching_source_kept():
    assert filter_answer("Hasılat yüz milyar dolar [1].", ["Revenue was $100 billion."])[0]


# --------------------------------------------------------------------------- K4


@pytest.mark.parametrize("sentence,allowed", [
    ("Şirket tedarik riskini açıklıyor.", False),
    ("The company faces competition.", False),
    ("Özet:", True),
    ("Öne çıkan riskler şunlar:", True),
    ("Özetle,", True),
    ("FinSight AI yatırım tavsiyesi vermez.", True),
    ("Bu bilgi işlenen raporlarda bulunamadı.", True),
    ("Gelir 2025'te arttı.", False),
])
def test_k4_uncited_content_sentences(sentence, allowed):
    assert _sentence_allowed(sentence) is allowed


# --------------------------------------------------------------------------- K5 / O1


@pytest.mark.parametrize("question", [
    "Can you add more detail on revenue?", "Can we add the 2024 numbers?",
    "Is the company going short on cash?", "Ürün portföyü alanında hangi riskler var?",
    "Şirket hangi pazarlara girmek için uygun zaman diyor?",
    "Does management recommend buying back shares?",
])
def test_k5_legit_questions_not_advice(question):
    assert is_advice_question(question) is False


@pytest.mark.parametrize("sentence", [
    "The company sees new market entry opportunities in India [1].",
    "Hizmetler segmenti portföye katkı sağladı [1].",
    "Şirket kendi hisselerini geri almak için uygun nakde sahip [1].",
    "Şirket ürün fiyatlarını cazip tutmayı hedefliyor [1].",
    "Management suggests investing in AI infrastructure [1].",
    "The company recommends holding the annual meeting virtually [1].",
    "Uzun pozisyon taşıyan türev sözleşmeleri var [1].",
])
def test_k5_legit_sentences_kept(sentence):
    assert _sentence_allowed(sentence) is True


@pytest.mark.parametrize("question", [
    "Is AAPL a buy?", "Would you recommend buying Apple stock?", "Should I be buying Apple?",
    "Buy Apple now?", "Apple almaya değer mi?", "Hisseye girmeli miyim?",
    "Hisse toplamalı mıyım?", "Elimdeki hisseleri elden çıkarmalı mıyım?",
    "Bu hisseyi alsam mı?", "Apple'ı almak mantıklı mı?", "Hisseyi tutmaya devam edeyim mi?",
])
def test_o1_advice_questions_detected(question):
    assert is_advice_question(question) is True


@pytest.mark.parametrize("sentence", [
    "Bu hisse alınmaya değer [1].", "Apple is a compelling buy [1].", "I'd buy the shares [1].",
    "Investors should buy [1].", "I recommend you buy [1].", "Consider buying the stock [1].",
    "Yatırımcılar hisseyi toplayabilir [1].", "Hisseyi elde tutmak mantıklı görünüyor [1].",
    "Bu seviyelerden alım yapılabilir [1].", "Hisse satın alınabilir [1].",
    "Stock looks like a bargain [1].",
])
def test_o1_advice_sentences_removed(sentence):
    assert _sentence_allowed(sentence) is False


# --------------------------------------------------------------------------- O3


def test_o3_transient_oserror_retried_then_recovers(tmp_path, monkeypatch):
    """O3: geçici OSError (Windows PermissionError) birkaç kısa denemeyle aşılır; kalıcı
    hata süreç içi sınırlayıcıya düşer ve 60 sn sonra dosya yolu yeniden denenir."""
    from finsight import rate_limiter as rl

    now = [1_000_000.0]
    lim = rl.FileRateLimiter(str(tmp_path), 8, clock=lambda: now[0],
                             sleep=lambda s: now.__setitem__(0, now[0] + s))
    real = rl._FileLock.__enter__
    fails = {"n": 2}

    def flaky(self):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise PermissionError("kilitli")
        return real(self)

    monkeypatch.setattr(rl._FileLock, "__enter__", flaky)
    lim.acquire()
    assert not lim.broken and (tmp_path / ".ratelimit").exists()
    fails["n"] = 10_000
    lim.acquire()
    assert lim.broken
    fails["n"] = 0
    now[0] += 61
    lim.acquire()
    assert not lim.broken


def test_o3_lock_wait_has_timeout(tmp_path):
    """O3: kilit beklemesinin zaman aşımı var (başka süreç kilidi bırakmazsa sonsuz beklenmez)."""
    from finsight import rate_limiter as rl

    assert rl.LOCK_TIMEOUT_SECONDS <= 10
    holder = rl._FileLock(str(tmp_path / "x.lock"), lambda s: None)
    with holder:
        waiter = rl._FileLock(str(tmp_path / "x.lock"), lambda s: None, timeout=0.2)
        with pytest.raises(OSError):
            waiter.__enter__()


# --------------------------------------------------------------------------- O4


def test_o4_default_cache_dir_is_absolute_backend_sec_cache(monkeypatch, tmp_path):
    from finsight import config

    monkeypatch.delenv("SEC_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    path = config.get_cache_dir()
    assert os.path.isabs(path)
    assert pathlib.Path(path) == REPO / "backend" / ".sec_cache"
    monkeypatch.setenv("SEC_CACHE_DIR", "ozel")
    assert config.get_cache_dir() == "ozel"


# --------------------------------------------------------------------------- O5


@pytest.mark.parametrize("status,body,paused", [
    (429, b"", True),
    (403, b"<html>Request Rate Threshold Exceeded</html>", True),
    (403, b"<html>Forbidden</html>", False),
])
def test_o5_pause_only_on_real_rate_limit(status, body, paused):
    from finsight.rate_limiter import RateLimiter
    from finsight.sec_client import SecClient

    calls = []

    class Lim(RateLimiter):
        def pause(self, seconds):
            calls.append(seconds)

    now = [0.0]
    lim = Lim(8, clock=lambda: now[0], sleep=lambda s: None)
    client = SecClient("FinSight t@example.com", limiter=lim, max_retries=0,
                       fetcher=lambda u, h: (status, body), sleep=lambda s: None)
    with pytest.raises(Exception):  # noqa: B017
        client.get_bytes("https://data.sec.gov/x.json")
    assert bool(calls) is paused


# --------------------------------------------------------------------------- K1 / O8


def test_k1_docker_sqlite_path_writable_by_non_root():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"DATABASE_URL=sqlite:////app/data/finsight\.db", text)
    assert re.search(r"chown[^\n]*/app/data", text)
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/app/data" in compose


def test_o8_host_rejection_message_suggests_loopback():
    src = (REPO / "backend" / "finsight" / "api.py").read_text(encoding="utf-8")
    m = re.search(r"HOST_REJECTED = \((.*?)\)\n", src, re.S)
    assert m and "127.0.0.1" in m.group(1) and "0.0.0.0" in m.group(1)


# --------------------------------------------------------------------------- O2 (SQLAlchemy)


def test_o2_queued_job_not_staled_and_not_duplicated():
    """O2: 30 dk kuyrukta bekleyen iş hata sayılmaz → aynı şirkete ikinci iş açılmaz."""
    pytest.importorskip("sqlalchemy")
    from datetime import timedelta

    from integration_support import memory_session_factory

    from finsight import repository as repo
    from finsight.models import utcnow

    factory = memory_session_factory()
    with factory() as s:
        job, created = repo.create_job_if_idle(s, "EXMP", True)
        later = utcnow() + repo.STALE_JOB_AFTER + timedelta(minutes=5)
        found = repo.find_running_job(s, "EXMP", now=later)
        assert created and found is not None and found["id"] == job["id"]
        again, created2 = repo.create_job_if_idle(s, "EXMP", True, now=later)
        assert created2 is False and again["id"] == job["id"]


def test_o2_run_ingest_job_skips_job_no_longer_queued():
    """O2: iş kuyruktan çıkmışsa (hata / başka süreç başlattı) run_ingest_job SEC'e gitmez."""
    pytest.importorskip("sqlalchemy")
    from integration_support import FakeSec, memory_session_factory, use_light_models

    from finsight import jobs
    from finsight import repository as repo

    use_light_models()
    factory, sec = memory_session_factory(), FakeSec()
    with factory() as s:
        job = repo.create_job(s, "EXMP")
        repo.update_job(s, job["id"], status="error", error="eskidi")
    jobs.run_ingest_job(job["id"], "EXMP", False, session_factory=factory, client_factory=sec)
    assert sec.urls() == []
    with factory() as s:
        assert repo.get_job(s, job["id"])["status"] == "error"
        assert repo.claim_job(s, job["id"]) is False
    jobs.reset_models()
