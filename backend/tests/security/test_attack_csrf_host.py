"""Saldırı testleri — HTTP katmanı: CSRF, Host / DNS rebinding, başlıklar, hata mesajları,
girdi doğrulama, gizlilik (SEC-01, SEC-03, SEC-12, SEC-18, SEC-19; denetim §3 kontrolleri).

FastAPI ``TestClient`` ile; FastAPI/SQLAlchemy yoksa (bulut) tüm dosya atlanır ve kullanıcının
bilgisayarında koşar. SEC ve LLM sahte; ağa çıkılmaz. xfail işaretleri 2026-11-23 tarihli
çalışma ağacının kod okumasına göredir: kullanıcının bilgisayarında XPASS(strict) görülürse
açık kapanmıştır → işaret kaldırılır.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from integration_support import TICKER  # noqa: E402

from .attack_support import (  # noqa: E402
    ATTACKER_ORIGIN,
    REBIND_HOST,
    FakeLLM,
    xfail_until,
)

INGEST = f"/api/companies/{TICKER}/ingest"
ASK = f"/api/companies/{TICKER}/ask"


def _job_count(session_factory) -> int:
    from sqlalchemy import func, select

    from finsight.models import IngestionJob

    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(IngestionJob))


# --------------------------------------------------------------------------- SEC-01 CSRF


def test_sec01_cross_origin_bodiless_ingest_rejected(client, sec, session_factory):
    """SEC-01 (CWE-352) — Saldırgan A1: kullanıcının açtığı kötü site
    ``fetch("http://127.0.0.1:8000/api/companies/EXMP/ingest", {method:"POST", mode:"no-cors"})``
    çalıştırır (preflight'sız basit istek). Yük: gövdesiz POST + ``Origin: https://kotu.example``.
    Beklenen savunma: 403, iş açılmaz, SEC'e istek gitmez."""
    res = client.post(INGEST, headers={"Origin": ATTACKER_ORIGIN})
    assert res.status_code == 403, f"{res.status_code} {res.text[:200]}"
    assert _job_count(session_factory) == 0
    assert sec.urls() == []


def test_sec01_sec_fetch_site_cross_site_rejected(client, session_factory):
    """SEC-01 — Bazı tarayıcılar/eklentiler ``Origin`` göndermez ama ``Sec-Fetch-Site:
    cross-site`` gönderir. Yük: gövdesiz POST + bu başlık. Beklenen savunma: 403."""
    res = client.post(INGEST, headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403, f"{res.status_code} {res.text[:200]}"
    assert _job_count(session_factory) == 0


@xfail_until("SEC-01", "bilinçli açık bırakıldı: gövdesiz POST sözleşmede geçerli (curl/CLI); tarayıcı CSRF Origin/Sec-Fetch-Site ile engelli")
def test_sec01_bodiless_ingest_requires_json(client, session_factory):
    """SEC-01 — Origin başlığı olmayan (ör. eski tarayıcı, ``<form>``) gövdesiz POST.
    Beklenen savunma: 4xx (gövde/JSON zorunlu → tarayıcı preflight yapmak zorunda kalır)."""
    res = client.post(INGEST)
    assert 400 <= res.status_code < 500, f"{res.status_code} {res.text[:200]}"
    assert _job_count(session_factory) == 0


def test_sec01_text_plain_form_post_rejected(client, session_factory):
    """SEC-01 — ``<form enctype="text/plain">`` / ``fetch(no-cors, body: "x")`` ile gelen
    basit istek. Yük: ``Content-Type: text/plain`` + ``{"analyze": true}`` + kötü köken.
    Beklenen savunma: 403/415/422, iş açılmaz."""
    res = client.post(INGEST, content=b'{"analyze": true}',
                      headers={"Content-Type": "text/plain", "Origin": ATTACKER_ORIGIN})
    assert res.status_code in (403, 415, 422), f"{res.status_code} {res.text[:200]}"
    assert _job_count(session_factory) == 0


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:5173", "http://localhost:5173",
                                    "http://127.0.0.1:8000"])
def test_sec01_legit_ingest_still_works(client, origin):
    """SEC-01 yanlış pozitif kontrolü — arayüzün kendi isteği (JSON gövde; geliştirme sunucusu
    kökeni veya API'nin kendi kökeni ``http://127.0.0.1:8000``) 202 almaya devam etmeli."""
    headers = {"Origin": origin} if origin else {}
    res = client.post(INGEST, json={"analyze": False}, headers=headers)
    assert res.status_code == 202, f"{res.status_code} {res.text[:200]}"


def test_sec01_ask_text_plain_csrf_already_blocked(client):
    """Denetim §3 kontrolü — ``/ask`` gövdesi zorunlu Pydantic modeli; ``text/plain`` basit
    istek JSON olarak ayrıştırılmaz → 422 (CSRF'e kapalı, bugün de)."""
    res = client.post(ASK, content=b'{"question": "Revenue?"}',
                      headers={"Content-Type": "text/plain", "Origin": ATTACKER_ORIGIN})
    assert res.status_code == 422


def test_cors_preflight_from_attacker_not_allowed(client):
    """Denetim §3 kontrolü — kötü kökenin preflight'ına ``Access-Control-Allow-Origin``
    verilmez (yanıtı okuyamaz)."""
    res = client.options(ASK, headers={"Origin": ATTACKER_ORIGIN,
                                       "Access-Control-Request-Method": "POST",
                                       "Access-Control-Request-Headers": "content-type"})
    allow = res.headers.get("access-control-allow-origin")
    assert allow not in ("*", ATTACKER_ORIGIN), allow


# --------------------------------------------------------------------------- SEC-03 Host


def test_sec03_dns_rebinding_host_rejected(make_client):
    """SEC-03 (CWE-346) — Saldırgan A1, DNS rebinding: ``rebind.kotu.example`` önce kendi
    IP'sine, sonra 127.0.0.1'e çözülür; tarayıcı aynı köken sayar, CORS devre dışı kalır.
    Yük: ``Host: rebind.kotu.example:8000``. Beklenen savunma: 400."""
    rebound = make_client(base_url=f"http://{REBIND_HOST}")
    res = rebound.get("/api/health")
    assert res.status_code == 400, f"{res.status_code} {res.text[:200]}"


def test_sec03_dns_rebinding_cannot_reach_ask(make_client):
    """SEC-03 — Rebinding ile ``/ask`` (kullanıcının LLM kotası). Kayıtsız ticker kullanılır:
    istek uç noktaya ulaşırsa 404, Host denetimi varsa 400 döner (LLM çağrılmaz)."""
    rebound = make_client(base_url=f"http://{REBIND_HOST}")
    res = rebound.post("/api/companies/ZZZZQ/ask", json={"question": "Revenue?"})
    assert res.status_code == 400, f"{res.status_code} {res.text[:200]}"


@pytest.mark.parametrize("base", ["http://127.0.0.1:8000", "http://localhost:8000"])
def test_sec03_loopback_hosts_allowed(make_client, base):
    """SEC-03 yanlış pozitif kontrolü — 127.0.0.1 ve localhost çalışmaya devam etmeli."""
    assert make_client(base_url=base).get("/api/health").status_code == 200


def test_sec03_ask_rate_limited(client, ingested):
    """SEC-03 ek savunma — Saldırganın amacı: (rebinding veya yerel ağ, SEC-14) ``/ask`` ile
    kullanıcının ücretli LLM kotasını tüketmek. Yük: art arda 25 soru (sahte LLM, ücretsiz).
    Beklenen savunma: en geç 21. istekte 429; LLM çağrısı ≤ 40 (soru başına en fazla 2)."""
    from finsight import jobs

    llm = FakeLLM("Revenue risk discussed [1].")
    jobs.override_models(llm=llm)
    codes = [client.post(f"/api/companies/{ingested}/ask",
                         json={"question": f"Supplier risk {i}?"}).status_code
             for i in range(25)]
    assert 429 in codes[:21], codes
    assert len(llm.calls) <= 40


# --------------------------------------------------------------------------- SEC-12


def _dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>FinSight</title>", encoding="utf-8")
    return str(dist)


@pytest.mark.parametrize("path", ["/", "/api/health"])
def test_sec12_security_headers_present(make_client, tmp_path, path):
    """SEC-12 (CWE-1021) — Saldırganın amacı: FinSight'ı görünmez ``<iframe>`` içinde açıp
    kullanıcıyı "Analiz et"e tıklatmak (clickjacking; SEC-01 düzeltilse bile çalışır).
    Beklenen savunma: CSP ``frame-ancestors 'none'`` veya ``X-Frame-Options: DENY`` ve
    ``X-Content-Type-Options: nosniff``."""
    c = make_client(serve_frontend=True, frontend_dist=_dist(tmp_path))
    res = c.get(path)
    assert res.status_code == 200
    csp = res.headers.get("content-security-policy", "")
    xfo = res.headers.get("x-frame-options", "").upper()
    assert "frame-ancestors 'none'" in csp or xfo == "DENY", dict(res.headers)
    assert res.headers.get("x-content-type-options", "").lower() == "nosniff"


def test_static_path_traversal_blocked(make_client, tmp_path):
    """Denetim §3 kontrolü — ``StaticFiles`` üzerinden dist dışındaki dosyayı okumak.
    Yük: ``/..%2fsecret.txt``, ``/%2e%2e/secret.txt``. Beklenen savunma: içerik sızmaz."""
    (tmp_path / "secret.txt").write_text("GIZLI-ICERIK", encoding="utf-8")
    c = make_client(serve_frontend=True, frontend_dist=_dist(tmp_path))
    for path in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/..%5csecret.txt"):
        assert "GIZLI-ICERIK" not in c.get(path).text, path


# --------------------------------------------------------------------------- SEC-18 HTTP


def test_sec18_dash_ticker_ingest_rejected(client, session_factory):
    """SEC-18 (CWE-20) — Yük: ``POST /api/companies/-/ingest`` (geçerli JSON). Bugün iş kaydı
    açılır ve ``KeyError`` ile hataya düşer (SEC-01 ile çoğaltılabilir çöp kayıt).
    Beklenen savunma: 400, iş yok."""
    res = client.post("/api/companies/-/ingest", json={"analyze": False})
    assert res.status_code == 400, f"{res.status_code} {res.text[:200]}"
    assert _job_count(session_factory) == 0


def test_sec18_newline_ticker_rejected(client):
    """SEC-18 varyantı (kırmızı takım) — ``/api/companies/EXMP%0A/overview``: ``re.match`` +
    ``$`` sondaki ``\\n``'i kabul eder. Beklenen savunma: 400 (bugün 404: doğrulamadan geçti)."""
    res = client.get("/api/companies/EXMP%0A/overview")
    assert res.status_code == 400, f"{res.status_code} {res.text[:200]}"


@pytest.mark.parametrize("ticker", ["bad$tick", "ABCDEFGHIJK", "%3Cscript%3E"])
def test_sec18_malformed_ticker_http_400(client, ticker):
    """SEC-18 kontrol — biçimsiz ticker'lar bugün de 400."""
    assert client.get(f"/api/companies/{ticker}/overview").status_code == 400


# --------------------------------------------------------------------------- hata mesajları


def test_validation_error_does_not_reflect_input(client):
    """Denetim §3 kontrolü — 422 yanıtı girdiyi yansıtmaz (yansıyan XSS / iz sızıntısı yok).
    Yük: 600 karakterlik soru içinde ``<script>`` işaretçisi."""
    marker = "<script>KIRMIZI_TAKIM_ISARET</script>"
    res = client.post(ASK, json={"question": marker + "x" * 600})
    assert res.status_code == 422
    assert "KIRMIZI_TAKIM_ISARET" not in res.text
    res = client.get("/api/jobs/KIRMIZI_TAKIM_ISARET")
    assert res.status_code == 422 and "KIRMIZI_TAKIM_ISARET" not in res.text


def test_job_error_masks_api_key(monkeypatch):
    """Denetim §3 kontrolü — iş hata metni (``safe_error``) API anahtarını ve DB parolasını
    maskeler. Yük: anahtarı içeren istisna mesajı."""
    from finsight.jobs import safe_error
    from finsight.sec_client import SecError

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-kirmizi-takim-123")
    monkeypatch.setenv("POSTGRES_PASSWORD", "parola-kirmizi")
    msg = safe_error(SecError("hata sk-ant-kirmizi-takim-123 parola-kirmizi"))
    assert "sk-ant-kirmizi-takim-123" not in msg and "parola-kirmizi" not in msg


def test_health_does_not_leak_secrets(client, monkeypatch):
    """Denetim §3 kontrolü — ``/api/health`` anahtar veya User-Agent (e-posta) içermez."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-kirmizi-takim-456")
    monkeypatch.setenv("SEC_USER_AGENT", "FinSight Kirmizi gizli@example.com")
    text = client.get("/api/health").text
    assert "sk-ant-kirmizi-takim-456" not in text and "gizli@example.com" not in text


# --------------------------------------------------------------------------- SEC-19


def test_sec19_qa_log_can_be_disabled(client, ingested, session_factory, monkeypatch):
    """SEC-19 (CWE-359) — Saldırganın amacı (A4): paylaşılan/teslim edilen veritabanı
    dosyasından kullanıcının sorularını okumak. Beklenen savunma: ``FINSIGHT_QA_LOG=0`` iken
    ``/ask`` sonrası ``qa_log`` satır sayısı değişmez."""
    from sqlalchemy import func, select

    from finsight.models import QALog

    monkeypatch.setenv("FINSIGHT_QA_LOG", "0")
    with session_factory() as s:
        before = s.scalar(select(func.count()).select_from(QALog))
    res = client.post(f"/api/companies/{ingested}/ask",
                      json={"question": "Kişisel portföy notum: supplier risk?"})
    assert res.status_code == 200
    with session_factory() as s:
        after = s.scalar(select(func.count()).select_from(QALog))
    assert after == before, f"qa_log {before} → {after}"

