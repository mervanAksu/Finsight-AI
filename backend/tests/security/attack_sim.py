"""FinSight AI saldırı simülasyonu — çalışan YEREL sunucuya karşı (kırmızı takım).

Kullanım (depo kökünden):
    python backend/tests/security/attack_sim.py --base-url http://127.0.0.1:8000
    python backend/tests/security/attack_sim.py --base-url http://127.0.0.1:8000 --json sonuc.json
    python backend/tests/security/attack_sim.py --base-url http://127.0.0.1:8000 --allow-ingest AAPL

Kurallar:
- Yalnızca ``127.0.0.1`` / ``localhost`` hedeflenir; başka adres verilirse betik çıkar (kod 2).
- Varsayılan senaryolar SEC'e trafik ÜRETMEZ: CSRF ve Host denetimi "yoklama" isteğiyle
  (geçersiz veya kayıtsız ticker) ölçülür; istek uç noktaya ulaşırsa sunucu SEC'e gitmeden
  400/404 döner, savunma varsa önce 403/400/422 döner. Fark, savunmanın varlığının kanıtıdır.
- ``--allow-ingest TICKER``: gerçek CSRF ingest denemesi — TEK istek (sunucu SEC'ten o şirketi
  çeker). Başka hiçbir senaryo SEC'e istek göndermez.
- LLM'e ücretli istek gönderilmez: ``/ask`` senaryoları yalnızca ``/api/health`` LLM'in KAPALI
  olduğunu bildiriyorsa çalışır (çıkarımsal mod), aksi halde ATLANDI.
- Sahte sunucuya (``frontend/scripts/mock_server.py``) karşı alınan sonuçlar yalnızca betiğin
  çalıştığını gösterir; gerçek güvenlik sonucu DEĞİLDİR.

Çıkış kodu: 0 = açık bulunmadı, 1 = en az bir saldırı başarılı, 2 = kullanım/bağlantı hatası.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
ATTACKER_ORIGIN = "https://kotu.example"
REBIND_HOST = "rebind.kotu.example"
PROBE_INVALID = "ZZ%24Z"      # "ZZ$Z": check_ticker'a ulaşırsa 400 "Geçersiz ticker"
PROBE_UNKNOWN = "ZZZZQ"       # geçerli biçim, kayıtsız: uç noktaya ulaşırsa 404
MARKER = "KIRMIZI_TAKIM_ISARET"

BLOCKED = "ENGELLENDİ"
VULNERABLE = "BAŞARILI (açık)"
SKIPPED = "ATLANDI"
UNKNOWN = "BELİRSİZ"


@dataclass
class Result:
    sid: str
    finding: str
    title: str
    verdict: str
    evidence: str
    requests: list[str] = field(default_factory=list)


class Target:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        parts = urlsplit(base_url)
        self.host = parts.hostname or ""
        self.port = parts.port or 80
        self.netloc = f"{self.host}:{self.port}"
        self.timeout = timeout
        self.log: list[str] = []

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        hdrs = {"Host": self.netloc, "Connection": "close", **(headers or {})}
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            text = resp.read(200_000).decode("utf-8", "replace")
            out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, text)
        finally:
            conn.close()
        self.log.append(f"{method} {path} Host={hdrs['Host']} → {out[0]}")
        return out


def _short(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def _detail(text: str) -> str:
    try:
        return str(json.loads(text).get("detail", ""))[:120]
    except (ValueError, AttributeError):
        return _short(text)


# --------------------------------------------------------------------------- senaryolar


def s01_csrf_origin(t: Target) -> tuple[str, str]:
    st, _, body = t.request("POST", f"/api/companies/{PROBE_INVALID}/ingest",
                            headers={"Origin": ATTACKER_ORIGIN})
    if st == 400 and "ticker" in body.lower():
        return VULNERABLE, f"{st}: kötü kökenli istek uç noktaya ulaştı ({_detail(body)})"
    if st in (403, 415, 422):
        return BLOCKED, f"{st}: {_detail(body)}"
    return UNKNOWN, f"{st}: {_detail(body)}"


def s01_csrf_sec_fetch(t: Target) -> tuple[str, str]:
    st, _, body = t.request("POST", f"/api/companies/{PROBE_INVALID}/ingest",
                            headers={"Sec-Fetch-Site": "cross-site"})
    if st == 400 and "ticker" in body.lower():
        return VULNERABLE, f"{st}: Sec-Fetch-Site: cross-site kabul edildi ({_detail(body)})"
    if st in (403, 415, 422):
        return BLOCKED, f"{st}: {_detail(body)}"
    return UNKNOWN, f"{st}: {_detail(body)}"


def s01_bodiless(t: Target) -> tuple[str, str]:
    st, _, body = t.request("POST", f"/api/companies/{PROBE_INVALID}/ingest")
    if st == 400 and "ticker" in body.lower():
        return VULNERABLE, f"{st}: gövdesiz POST uç noktaya ulaştı (gövde isteğe bağlı)"
    if st in (403, 415, 422):
        return BLOCKED, f"{st}: {_detail(body)}"
    return UNKNOWN, f"{st}: {_detail(body)}"


def s01_real_ingest(t: Target, ticker: str) -> tuple[str, str]:
    st, _, body = t.request("POST", f"/api/companies/{ticker}/ingest",
                            headers={"Origin": ATTACKER_ORIGIN})
    if st in (200, 202):
        return VULNERABLE, f"{st}: kötü kökenli gövdesiz POST iş başlattı ({_short(body)})"
    if st in (403, 415, 422):
        return BLOCKED, f"{st}: {_detail(body)}"
    return UNKNOWN, f"{st}: {_detail(body)}"


def s03_rebind_health(t: Target) -> tuple[str, str]:
    st, _, body = t.request("GET", "/api/health",
                            headers={"Host": f"{REBIND_HOST}:{t.port}"})
    if st == 200:
        return VULNERABLE, f"200: Host={REBIND_HOST} kabul edildi ({_short(body, 60)})"
    if st in (400, 403, 421):
        return BLOCKED, f"{st}: {_short(body, 80)}"
    return UNKNOWN, f"{st}: {_short(body, 80)}"


def s03_rebind_ask(t: Target) -> tuple[str, str]:
    st, _, body = t.request("POST", f"/api/companies/{PROBE_UNKNOWN}/ask",
                            body=json.dumps({"question": "Revenue?"}).encode(),
                            headers={"Host": f"{REBIND_HOST}:{t.port}",
                                     "Content-Type": "application/json"})
    if st == 404:
        return VULNERABLE, f"404: rebinding Host'u /ask uç noktasına ulaştı ({_detail(body)})"
    if st in (400, 403, 421):
        return BLOCKED, f"{st}: {_short(body, 80)}"
    return UNKNOWN, f"{st}: {_detail(body)}"


def s12_headers(t: Target) -> tuple[str, str]:
    missing = []
    for path in ("/", "/api/health"):
        st, hdrs, _ = t.request("GET", path)
        csp = hdrs.get("content-security-policy", "")
        frame_ok = "frame-ancestors" in csp or hdrs.get("x-frame-options", "").upper() == "DENY"
        if not frame_ok:
            missing.append(f"{path}: çerçeveleme koruması yok")
        if hdrs.get("x-content-type-options", "").lower() != "nosniff":
            missing.append(f"{path}: nosniff yok")
    if missing:
        return VULNERABLE, "; ".join(missing)
    return BLOCKED, "CSP/X-Frame-Options ve nosniff mevcut"


def s18_dash_ticker(t: Target) -> tuple[str, str]:
    results = []
    vulnerable = False
    for raw in ("-", "..%2E", "EXMP%0A"):
        st, _, body = t.request("GET", f"/api/companies/{raw}/overview")
        results.append(f"{raw}→{st}")
        if st == 404 and "not found" not in body.lower():  # yönlendirici 404'ü değil
            vulnerable = True
    verdict = VULNERABLE if vulnerable else BLOCKED
    note = " (404 = doğrulamadan geçti)" if vulnerable else ""
    return verdict, ", ".join(results) + note


def err_reflection(t: Target) -> tuple[str, str]:
    q = f"<script>{MARKER}</script>" + "x" * 600
    st, _, body = t.request("POST", f"/api/companies/{PROBE_UNKNOWN}/ask",
                            body=json.dumps({"question": q}).encode(),
                            headers={"Content-Type": "application/json"})
    st2, _, body2 = t.request("GET", f"/api/jobs/{MARKER}")
    if MARKER in body or MARKER in body2:
        return VULNERABLE, f"{st}/{st2}: girdi hata yanıtında yansıtıldı"
    return BLOCKED, f"{st}/{st2}: girdi yansıtılmadı"


def cors_preflight(t: Target) -> tuple[str, str]:
    st, hdrs, _ = t.request("OPTIONS", f"/api/companies/{PROBE_UNKNOWN}/ask",
                            headers={"Origin": ATTACKER_ORIGIN,
                                     "Access-Control-Request-Method": "POST",
                                     "Access-Control-Request-Headers": "content-type"})
    allow = hdrs.get("access-control-allow-origin")
    if allow in ("*", ATTACKER_ORIGIN):
        return VULNERABLE, f"{st}: Access-Control-Allow-Origin={allow}"
    return BLOCKED, f"{st}: Access-Control-Allow-Origin={allow!r}"


def static_traversal(t: Target) -> tuple[str, str]:
    seen = []
    for path in ("/..%2f..%2f..%2fetc%2fpasswd", "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
                 "/..%5c..%5c..%5cwindows%5cwin.ini", "/../../.env"):
        st, _, body = t.request("GET", path)
        seen.append(f"{st}")
        if st == 200 and ("root:" in body or "[fonts]" in body.lower()
                          or "API_KEY" in body):
            return VULNERABLE, f"{path} → 200 ve hassas içerik"
    return BLOCKED, "durumlar: " + ",".join(seen)


def _llm_enabled(t: Target) -> bool | None:
    st, _, body = t.request("GET", "/api/health")
    if st != 200:
        return None
    try:
        return bool(json.loads(body)["models"]["llm"]["enabled"])
    except (ValueError, KeyError, TypeError):
        return None


def _analyzed_ticker(t: Target) -> str | None:
    st, _, body = t.request("GET", "/api/companies")
    if st != 200:
        return None
    try:
        for c in json.loads(body):
            if c.get("analyzed"):
                return c.get("ticker") or c.get("company", {}).get("ticker")
    except (ValueError, AttributeError):
        return None
    return None


def s04_advice(t: Target) -> tuple[str, str]:
    llm = _llm_enabled(t)
    if llm is not False:
        return SKIPPED, "LLM açık veya bilinmiyor: ücretli istek gönderilmez (pytest esas)"
    ticker = _analyzed_ticker(t)
    if not ticker:
        return SKIPPED, "analiz edilmiş şirket yok"
    st, _, body = t.request("POST", f"/api/companies/{ticker}/ask",
                            body=json.dumps({"question": "Bu hisseyi portföyüme eklemeli "
                                                         "miyim?"}).encode(),
                            headers={"Content-Type": "application/json"})
    if st != 200:
        return UNKNOWN, f"{st}: {_detail(body)}"
    refused = json.loads(body).get("advice_refused")
    if refused:
        return BLOCKED, f"200 {ticker}: advice_refused=true"
    return VULNERABLE, f"200 {ticker}: advice_refused=false (tavsiye niyeti algılanmadı)"


def s03_ask_burst(t: Target) -> tuple[str, str]:
    llm = _llm_enabled(t)
    if llm is not False:
        return SKIPPED, "LLM açık veya bilinmiyor: ücretli istek gönderilmez (pytest esas)"
    ticker = _analyzed_ticker(t)
    if not ticker:
        return SKIPPED, "analiz edilmiş şirket yok"
    codes = []
    for i in range(25):
        st, _, _ = t.request("POST", f"/api/companies/{ticker}/ask",
                             body=json.dumps({"question": f"Risk {i}?"}).encode(),
                             headers={"Content-Type": "application/json"})
        codes.append(st)
        if st == 429:
            return BLOCKED, f"{i + 1}. istekte 429"
    return VULNERABLE, f"25 istekte 429 yok ({sorted(set(codes))})"


SCENARIOS = [
    ("S01a", "SEC-01", "CSRF: kötü kökenli gövdesiz POST /ingest (yoklama)", s01_csrf_origin),
    ("S01b", "SEC-01", "CSRF: Sec-Fetch-Site: cross-site (yoklama)", s01_csrf_sec_fetch),
    ("S01c", "SEC-01", "CSRF: gövdesiz/Content-Type'sız POST (yoklama)", s01_bodiless),
    ("S03a", "SEC-03", "DNS rebinding: yabancı Host ile /api/health", s03_rebind_health),
    ("S03b", "SEC-03", "DNS rebinding: yabancı Host ile /ask (kayıtsız şirket)",
     s03_rebind_ask),
    ("S04", "SEC-04", "Tavsiye sorusu kalıp dışı ifade (LLM kapalıysa)", s04_advice),
    ("S12", "SEC-12", "Güvenlik başlıkları (CSP, X-Frame-Options, nosniff)", s12_headers),
    ("S18", "SEC-18", "Geçersiz ticker '-', '..%2E', 'EXMP%0A'", s18_dash_ticker),
    ("C1", "§3", "Hata yanıtında girdi yansıması", err_reflection),
    ("C2", "§3", "CORS preflight (kötü köken)", cors_preflight),
    ("C3", "§3", "Statik dosya yol geçişi", static_traversal),
]

NOT_SIMULATED = [
    ("S02", "SEC-02", "Eşzamanlı ingest seli",
     "SEC'e yoğun trafik üretir → yalnızca pytest (test_attack_dos.py, sahte SEC)"),
    ("S05/S06", "SEC-05/06", "Uydurma sayı / istem enjeksiyonu",
     "LLM gerekir → pytest (test_attack_rag.py, sahte LLM)"),
    ("S08", "SEC-08", "ReDoS", "HTTP ile tetiklenmez (SEC belgesi gerekir) → pytest"),
    ("S16", "SEC-16", "/freshness seli", "SEC'e istek üretebilir → pytest (sahte SEC)"),
]


def validate_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme != "http" or (parts.hostname or "") not in ALLOWED_HOSTS:
        sys.stderr.write("HATA: --base-url yalnızca http://127.0.0.1:<port> veya "
                         "http://localhost:<port> olabilir (yerel sunucu dışında saldırı "
                         "trafiği gönderilmez).\n")
        sys.exit(2)
    if parts.path not in ("", "/") or parts.query or parts.username:
        sys.stderr.write("HATA: --base-url yol, sorgu veya kimlik bilgisi içermemeli.\n")
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", required=True, help="ör. http://127.0.0.1:8000")
    ap.add_argument("--allow-ingest", metavar="TICKER", default=None,
                    help="gerçek CSRF ingest denemesi (TEK istek; sunucu SEC'ten veri çeker)")
    ap.add_argument("--ask-burst", action="store_true",
                    help="/ask hız sınırı için 25 istek (yalnızca LLM kapalıysa)")
    ap.add_argument("--json", metavar="DOSYA", help="sonuçları JSON olarak yaz")
    args = ap.parse_args(argv)
    validate_base_url(args.base_url)

    target = Target(args.base_url)
    try:
        target.request("GET", "/api/health")
    except OSError as exc:
        sys.stderr.write(f"HATA: {args.base_url} adresine bağlanılamadı ({exc}).\n")
        return 2

    scenarios = list(SCENARIOS)
    if args.allow_ingest:
        if not all(ch.isalnum() or ch in ".-" for ch in args.allow_ingest):
            sys.stderr.write("HATA: --allow-ingest geçerli bir ticker olmalı.\n")
            return 2
        tk = args.allow_ingest.upper()
        scenarios.append(("S01d", "SEC-01", f"CSRF: gerçek ingest {tk} (TEK istek, SEC'e gider)",
                          lambda t: s01_real_ingest(t, tk)))
    if args.ask_burst:
        scenarios.append(("S03c", "SEC-03", "/ask hız sınırı (25 istek)", s03_ask_burst))

    results: list[Result] = []
    for sid, finding, title, fn in scenarios:
        start = len(target.log)
        try:
            verdict, evidence = fn(target)
        except OSError as exc:
            verdict, evidence = UNKNOWN, f"bağlantı hatası: {exc}"
        results.append(Result(sid, finding, title, verdict, evidence, target.log[start:]))
    for sid, finding, title, why in NOT_SIMULATED:
        results.append(Result(sid, finding, title, SKIPPED, why))

    print(f"FinSight AI saldırı simülasyonu → {args.base_url}")
    print("Not: sahte (mock) sunucuya karşı sonuçlar gerçek güvenlik sonucu değildir.\n")
    for r in results:
        print(f"[{r.verdict:<16}] {r.sid:<7} {r.finding:<9} {r.title}")
        print(f"{'':19}kanıt: {r.evidence}")
    counts = {v: sum(r.verdict == v for r in results)
              for v in (BLOCKED, VULNERABLE, SKIPPED, UNKNOWN)}
    print("\nÖzet: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"base_url": args.base_url, "results": [asdict(r) for r in results],
                       "summary": counts}, fh, ensure_ascii=False, indent=2)
    return 1 if counts[VULNERABLE] else 0


if __name__ == "__main__":
    sys.exit(main())
