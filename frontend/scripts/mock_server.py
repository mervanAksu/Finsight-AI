"""FinSight AI demo / sunum sunucusu (yalnızca standart kütüphane).

- `frontend/dist/` klasörünü `/` altından sunar (derlenmiş arayüz).
- `/api/*` uç noktalarını `frontend/mock/*.json` içindeki KURGUSAL verilerle yanıtlar.
  Yanıt şekilleri `docs/api-contract.md` ile birebir aynıdır.
- Gerçek backend'e, SEC'e veya bir LLM'e bağlanmaz; ağ erişimi gerektirmez.

Kullanım:
    python3 frontend/scripts/mock_server.py            # http://127.0.0.1:8765
    python3 frontend/scripts/mock_server.py 9000 --latency 300
    python3 frontend/scripts/mock_server.py 8765 --empty   # hiç işlenmiş şirket yok (boş ana sayfa)

Şirket durumları (gerçek backend'deki gibi):
- full: finansal veri + metin analizi (EXMP, HRBR, NVLT).
- fin:  yalnızca finansal veri; `/overview` → `analyzed: false` (QNTA). Şirket başlığındaki
        "Metin analizini çalıştır" ile ~9 sn'lik iş sonunda full olur.
- none: hiç işlenmemiş; şirket uçları 404 döner (CDRW, LMNR, ORVB, PLSD, EXMR).
        CDRW için iş başarıyla biter; LMNR, ORVB, PLSD, EXMR için örnek veri yoktur ve iş
        hata ile biter (şirket kaydı oluşur ama verisi boş kalır: "empty").

Tazelik (Aşama 4, `GET /api/companies/{t}/freshness`): EXMP, QNTA, CDRW güncel (`stale: false`);
HRBR'de analizden sonra yayımlanmış bir 10-Q var (`stale: true`; bu başvuru "Güncelle" ile iş
bitene kadar başvuru listelerinde görünmez); NVLT'de SEC'e ulaşılamamış gibi davranılır
(`stale: null`, `error`). `--no-freshness`: uç nokta yokmuş gibi 404 (eski sunucu). Not: demo
sunucusunda güncelleme yalnızca tazelik durumunu ve başvuru listesini değiştirir; skor ve
oranlar aynı kalır.

v1.5 sınır ve güvenlik yanıtları (`--reject`, virgülle birden fazla): `ingest-429` (iş
kapasitesi dolu, Retry-After 30), `ingest-503` (veritabanı meşgul, Retry-After 5), `ask-429`
(dakikalık soru sınırı, Retry-After 42), `post-403` (başka siteden istek; tüm POST'lar).
Metinler backend `api.py` ile aynıdır.

Soru-Cevap: soruda "bulunamadı"/"kripto" → `not_found`, "alıntı" → `extractive`, "gömme" →
`warning` alanı; "almalı mıyım" gibi tavsiye soruları → `advice_refused: true` ve cevabın başında
sabit ret cümlesi. Metin analizi yapılmamış şirkette her soru `not_found` döner.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import re
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = ROOT / "mock"
DIST_DIR = ROOT / "dist"
JOB_SECONDS = 9.0


def _load() -> tuple[dict, dict]:
    meta = json.loads((MOCK_DIR / "meta.json").read_text(encoding="utf-8"))
    companies: dict[str, dict] = {}
    for path in sorted(MOCK_DIR.glob("*.json")):
        if path.stem == "meta":
            continue
        companies[path.stem.upper()] = json.loads(path.read_text(encoding="utf-8"))
    return meta, companies


META, DATA = _load()
DISCLAIMER = META["disclaimer"]
STATE_LOCK = threading.RLock()
# Şirket durumu: "full" | "fin" | "empty" | "none" (bkz. modül açıklaması)
STATE: dict[str, str] = {c["ticker"]: c["state"] for c in META["search_index"]}
JOBS: dict[int, dict] = {}
NEXT_JOB = [1]
TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")  # backend api.TICKER_RE ile aynı
EMPTY_SCORE = {"total": None, "band": None, "as_of_period": None, "basis": None, "as_of_filing": None,
               "data_through": None, "weights_version": "v1-2026-11", "method_version": "v2-2026-11-ttm",
               "components": [], "missing_components": [], "explanation": None}
# "Güncelle" (POST /ingest) ile tamamlanmış tazelik güncellemeleri
REFRESHED: set[str] = set()

# Yatırım tavsiyesi niyeti (backend rag._ADVICE_QUESTION'ın sadeleştirilmiş kopyası)
_ADVICE_PATTERNS = (
    r"(?:al|sat|tut)mal[ıi]\s*m[ıi]y[ıi](?:m|z)",
    r"(?:al[ıi]n|sat[ıi]l|tutul)(?:[ıi]r|mal[ıi])\s*m[ıi]",
    r"yat[ıi]r[ıi]m\s+yap(?:mal[ıi]|[ıi]l[ıi]r|ay[ıi]m|s[ıi]n|al[ıi]m|mak\s+mant[ıi]kl[ıi])",
    r"\b(?:al|sat|tut)ay[ıi]m\s+m[ıi]",
    r"\bhedef\s+fiyat",
    r"yat[ıi]r[ıi]m\s+tavsiye",
    (r"\b(?:should|shall|would|could|do|must|can)\s+(?:i|we|you|one)\s+(?:still\s+|now\s+)?"
     r"(?:buy|sell|hold|invest|short)\b"),
    r"\b(?:price\s+target|target\s+price)\b",
    r"\bworth\s+(?:buying|investing|selling)\b",
)
ADVICE_RE = re.compile("|".join(_ADVICE_PATTERNS), re.IGNORECASE)


def state_of(ticker: str) -> str:
    with STATE_LOCK:
        return STATE.get(ticker, "none")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_view(job: dict) -> dict:
    """İşin anlık durumunu geçen süreye göre hesaplar."""
    elapsed = time.time() - job["t0"]
    ticker = job["ticker"]
    steps = META["ingest_steps"] if job["analyze"] else META["financial_steps"]
    has_data = ticker in DATA
    view = {"id": job["id"], "ticker": ticker, "status": "running", "step": None,
            "progress": 0.0, "error": None, "started_at": job["started_at"], "finished_at": None,
            "request_count": 0, "warnings": []}
    if elapsed < 0.8:
        view.update(status="queued", step="Sırada bekliyor", progress=0.0)
        return view
    frac = min(1.0, (elapsed - 0.8) / JOB_SECONDS)
    if frac >= 0.5:
        view["warnings"] = list(META.get("job_warnings", {}).get(ticker, []))
    if not has_data and frac >= 0.34:
        with STATE_LOCK:
            # submissions alındı, şirket kaydı oluştu; XBRL verisi yok → boş şirket
            if STATE.get(ticker, "none") == "none":
                STATE[ticker] = "empty"
        job.setdefault("finished_at", now_iso())
        view.update(status="error", step=steps[min(2, len(steps) - 1)], progress=0.34,
                    error="Demo modunda bu şirket için örnek veri yok; SEC'e bağlanılmadı.",
                    finished_at=job["finished_at"], request_count=6)
        return view
    if frac >= 1.0:
        with STATE_LOCK:
            REFRESHED.add(ticker)
            if job["analyze"]:
                STATE[ticker] = "full"
            elif STATE.get(ticker) != "full":
                STATE[ticker] = "fin"
        job.setdefault("finished_at", now_iso())
        view.update(status="done", step="Tamamlandı", progress=1.0, finished_at=job["finished_at"],
                    request_count=27 if job["analyze"] else 9)
        return view
    idx = min(len(steps) - 1, int(frac * len(steps)))
    view.update(step=steps[idx], progress=round(frac, 3), request_count=int(3 + frac * 24))
    return view


def search_index() -> list[dict]:
    return META["search_index"]


def company_record(ticker: str) -> dict | None:
    for c in search_index():
        if c["ticker"] == ticker:
            return c
    return None


def company_obj(ticker: str) -> dict:
    """Ortak Company nesnesi; örnek verisi olmayan şirketler için asgari alanlar."""
    if ticker in DATA:
        return DATA[ticker]["company"]
    rec = company_record(ticker) or {}
    return {"ticker": ticker, "cik": rec.get("cik"), "name": rec.get("name"), "sic": None,
            "sic_description": None, "fiscal_year_end": None, "exchanges": []}


def pending_of(ticker: str) -> set[str]:
    """Henüz analiz edilmemiş (Güncelle bekleyen) başvuruların accession numaraları."""
    d = DATA.get(ticker)
    if not d or ticker in REFRESHED:
        return set()
    return set(d["freshness"].get("pending", []))


def freshness_of(ticker: str) -> dict:
    d = DATA.get(ticker)
    base = {"checked_at": now_iso(), "disclaimer": DISCLAIMER}
    if not d or state_of(ticker) not in ("full", "fin"):
        return {**base, "sec_latest": None, "analyzed_latest": None, "new_filings": [], "stale": None,
                "error": None}
    fr = {k: v for k, v in d["freshness"].items() if k != "pending"}
    if fr["stale"] and ticker in REFRESHED:
        fr = {**fr, "analyzed_latest": fr["sec_latest"], "new_filings": [], "stale": False}
    return {**base, **fr}


def score_of(ticker: str) -> dict:
    """Şirketin durumuna göre skor yanıtı (disclaimer hariç)."""
    st = state_of(ticker)
    d = DATA.get(ticker)
    if d and st == "full":
        return d["score"]
    if d and st == "fin":
        return d["score_fin"]
    return {**EMPTY_SCORE, "disclaimer": DISCLAIMER}


class Handler(SimpleHTTPRequestHandler):
    server_version = "FinSightMock/0.3"
    latency_ms = 250

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST_DIR), **kwargs)

    # ------------------------------------------------------------ yardımcılar
    search_reason: str | None = None
    no_freshness = False
    reject: frozenset[str] = frozenset()

    def log_message(self, fmt: str, *args) -> None:  # sessiz
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Retry-After")

    def _json(self, payload, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, detail: str, retry_after: int | None = None) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        self._json({"detail": detail}, status, headers=headers)

    def _rejected(self, what: str) -> bool:
        """--reject ile istenen v1.5 hata yanıtını üretir (backend api.py metinleriyle)."""
        if "post-403" in Handler.reject:
            self._error(403, "Başka bir siteden gelen istek reddedildi (CSRF koruması).")
            return True
        if what == "ingest" and "ingest-429" in Handler.reject:
            self._error(429, "Şu anda çok fazla veri çekme işi var (en fazla 2 çalışan, toplam 10 iş). "
                             "Mevcut işler bitince tekrar deneyin.", retry_after=30)
            return True
        if what == "ingest" and "ingest-503" in Handler.reject:
            self._error(503, "Veritabanı şu anda meşgul (başka bir işlem yazıyor). Birkaç saniye sonra "
                             "tekrar deneyin.", retry_after=5)
            return True
        if what == "ask" and "ask-429" in Handler.reject:
            self._error(429, "Çok fazla soru gönderildi; lütfen biraz bekleyip tekrar deneyin.",
                        retry_after=42)
            return True
        return False

    def _delay(self, factor: float = 1.0) -> None:
        if self.latency_ms > 0:
            time.sleep(self.latency_ms * factor / 1000)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ HTTP
    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            self._delay()
            self._api_get(url.path, parse_qs(url.query))
            return
        # SPA: bilinmeyen yollar index.html'e düşer (hash yönlendirme kullanıldığı için nadiren gerekir)
        target = DIST_DIR / unquote(url.path).lstrip("/")
        if url.path == "/" or not target.exists():
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_POST(self) -> None:
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        self._delay()
        if len(parts) == 4 and parts[:2] == ["api", "companies"] and parts[3] in ("ingest", "ask") \
                and self._rejected(parts[3]):
            return
        if len(parts) == 4 and parts[:2] == ["api", "companies"] and parts[3] == "ingest":
            ticker = parts[2].upper()
            if not TICKER_RE.match(ticker):
                self._error(400, "Geçersiz ticker: yalnızca harf, rakam, nokta ve tire kullanılabilir "
                                 "(en fazla 10 karakter).")
                return
            if company_record(ticker) is None:
                self._error(404, f"'{ticker}' SEC şirket listesinde bulunamadı.")
                return
            analyze = self._body().get("analyze", True) is not False
            with STATE_LOCK:
                for job in JOBS.values():
                    if job["ticker"] == ticker and job_view(job)["status"] in ("queued", "running"):
                        self._json({"job_id": job["id"], "status": "running"}, 202)
                        return
                jid = NEXT_JOB[0]
                NEXT_JOB[0] += 1
                JOBS[jid] = {"id": jid, "ticker": ticker, "t0": time.time(), "started_at": now_iso(),
                             "analyze": analyze}
            self._json({"job_id": jid, "status": "queued"}, 202)
            return
        if len(parts) == 4 and parts[:2] == ["api", "companies"] and parts[3] == "ask":
            self._ask(parts[2].upper(), self._body())
            return
        self._error(404, "Uç nokta bulunamadı.")

    # ------------------------------------------------------------ API
    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        parts = [p for p in path.split("/") if p]
        q = {k: v[0] for k, v in query.items() if v}

        if parts == ["api", "health"]:
            sec = {"user_agent_configured": Handler.search_reason != "sec_user_agent_missing",
                   "json_ttl_seconds": 3600}
            self._json({**META["health"], "sec": sec, "auto_refresh_minutes": 0})
            return
        if parts == ["api", "companies"]:
            out = []
            with STATE_LOCK:
                listed = sorted(t for t, st in STATE.items() if st != "none")
            for tk in listed:
                st = state_of(tk)
                d = DATA.get(tk)
                c = company_obj(tk)
                sc = score_of(tk)
                out.append({"ticker": tk, "cik": c["cik"], "name": c["name"],
                            "sic_description": c["sic_description"], "analyzed": st == "full",
                            "last_filing_date": d["filings"][0]["filing_date"] if (d and st != "empty") else None,
                            "score": None if sc["total"] is None else {"total": sc["total"], "band": sc["band"]}})
            self._json(out)
            return
        if parts == ["api", "companies", "search"]:
            needle = (q.get("q") or "").strip().lower()
            limit = max(1, min(50, int(q.get("limit") or 10)))
            res = []
            local = Handler.search_reason is not None  # --search-local: SEC listesi yok
            for c in search_index():
                if local and state_of(c["ticker"]) == "none":
                    continue
                hay = f"{c['ticker']} {c['name']} {c['cik']} {c['cik'].lstrip('0')}".lower()
                if not needle or needle in hay:
                    res.append({"ticker": c["ticker"], "cik": c["cik"], "name": c["name"],
                                "ingested": state_of(c["ticker"]) != "none"})
            res.sort(key=lambda r: (not r["ticker"].lower().startswith(needle), r["ticker"]))
            scope = {"X-FinSight-Search-Scope": "local" if local else "sec"}
            if local:
                scope["X-FinSight-Search-Reason"] = Handler.search_reason
            self._json(res[:limit], headers=scope)
            return
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            try:
                job = JOBS[int(parts[2])]
            except (ValueError, KeyError):
                self._error(404, "İş bulunamadı.")
                return
            self._json(job_view(job))
            return
        if parts == ["api", "compare"]:
            self._compare(q.get("tickers", ""))
            return
        if len(parts) == 4 and parts[:2] == ["api", "companies"]:
            self._company(parts[2].upper(), parts[3], q)
            return
        self._error(404, "Uç nokta bulunamadı.")

    def _check_company(self, ticker: str) -> bool:
        """Backend company_or_404 davranışı: geçersiz ticker 400, işlenmemiş şirket 404."""
        if not TICKER_RE.match(ticker):
            self._error(400, "Geçersiz ticker: yalnızca harf, rakam, nokta ve tire kullanılabilir "
                             "(en fazla 10 karakter).")
            return False
        if company_record(ticker) is None or state_of(ticker) == "none":
            self._error(404, f"{ticker} henüz işlenmedi. Önce verisini çekin: "
                             f"POST /api/companies/{ticker}/ingest")
            return False
        return True

    def _company(self, ticker: str, what: str, q: dict[str, str]) -> None:
        if not self._check_company(ticker):
            return
        company = company_obj(ticker)
        st = state_of(ticker)
        d = DATA.get(ticker)
        full = st == "full" and d is not None
        has_fin = st in ("full", "fin") and d is not None

        if what == "freshness":
            if Handler.no_freshness:
                self._error(404, "Not Found")
                return
            self._delay(2.0)  # SEC'e tek istek
            self._json(freshness_of(ticker))
            return
        if what == "overview":
            if has_fin:
                ov = d["overview"] if full else d["overview_fin"]
                hidden = pending_of(ticker)
                visible = [f for f in d["filings"] if f["accession_no"] not in hidden][:5]
                keys = ("accession_no", "form", "filing_date", "report_date", "url")
                self._json({**ov, "latest_filings": [{k: f[k] for k in keys} for f in visible]})
            else:
                self._json({"company": company, "analyzed": False, "score": None, "key_metrics": [],
                            "latest_filings": [], "sentiment_summary": None, "risk_summary": None,
                            "analysis_updated_at": None, "disclaimer": DISCLAIMER})
            return
        if what == "facts":
            metrics = d["facts"]["metrics"] if has_fin else []
            if q.get("metric"):
                metrics = [m for m in metrics if m["metric"] == q["metric"]]
            self._json({"company": company, "metrics": metrics, "disclaimer": DISCLAIMER})
            return
        if what == "ratios":
            if has_fin:
                self._json(d["ratios"])
            else:
                self._json({"company": company, "periods": [], "ratios": [], "disclaimer": DISCLAIMER})
            return
        if what == "score":
            self._json(score_of(ticker))
            return
        if what == "filings":
            filings = d["filings"] if has_fin else []
            hidden = pending_of(ticker)
            filings = [f for f in filings if f["accession_no"] not in hidden]
            if not full:
                filings = [{**f, "analyzed": False} for f in filings]
            form = q.get("form")
            if form:
                filings = [f for f in filings if f["form"] == form or f["form"] == f"{form}/A"]
            limit = int(q.get("limit") or 50)
            self._json({"company": company, "filings": filings[:limit], "disclaimer": DISCLAIMER})
            return
        if what == "sentiment":
            if full:
                self._json(d["sentiment"])
            else:
                self._json({"model": META["health"]["models"]["sentiment"], "sections": [],
                            "disclaimer": DISCLAIMER})
            return
        if what == "risk-changes":
            rc = d["risk_changes"] if full else None
            if rc and rc["old_filing"] is not None:
                self._json(rc)
            else:
                # v1.1: karşılaştırma yoksa old/new null, sayılar 0, değişiklik yok
                self._json({"old_filing": None, "new_filing": None,
                            "counts": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
                            "changes": [], "disclaimer": DISCLAIMER})
            return
        if what == "export.csv":
            self._csv(ticker, d if has_fin else None, with_score=full or st == "fin")
            return
        self._error(404, "Uç nokta bulunamadı.")

    def _csv(self, ticker: str, d: dict | None, with_score: bool) -> None:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["ticker", "cik", "section", "key", "label", "period_start", "period_end", "value",
                    "unit", "status", "xbrl_tag", "form", "accession_no", "filed"])
        cik = company_obj(ticker)["cik"]
        for m in (d["facts"]["metrics"] if d else []):
            for v in m["values"]:
                s = v["source"]
                w.writerow([ticker, cik, "fact", m["metric"], m["label"], v["period_start"] or "",
                            v["period_end"], v["value"], "USD", "ok", s["xbrl_tag"], s["form"],
                            s["accession_no"], s["filed"]])
        for r in (d["ratios"]["ratios"] if d else []):
            for v in r["values"]:
                w.writerow([ticker, cik, "ratio", r["key"], r["label"], "", v["period_end"],
                            "" if v["value"] is None else v["value"], r["unit"], v["status"], "",
                            "10-Q" if v.get("basis") == "ttm" else "10-K",
                            (v["inputs"][0]["accession_no"] if v["inputs"] else ""), ""])
        sc = score_of(ticker)
        if with_score and sc["total"] is not None:
            w.writerow([ticker, cik, "score", "total", "Toplam skor", "", sc["as_of_period"], sc["total"],
                        "points", "ok", "", "", "", ""])
        for c in (sc["components"] if with_score else []):
            w.writerow([ticker, cik, "score", c["key"], c["label"], "", sc["as_of_period"],
                        "" if c["score"] is None else c["score"], "points",
                        "ok" if c["available"] else "missing", "", "", "", ""])
        body = ("﻿" + buf.getvalue()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{ticker}-finsight.csv"')
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _ask(self, ticker: str, body: dict) -> None:
        question = str(body.get("question") or "").strip()
        if not 1 <= len(question) <= 500:
            self._error(422, "Soru 1–500 karakter olmalıdır.")
            return
        if not self._check_company(ticker):
            return
        t0 = time.time()
        self._delay(3.0)  # yükleniyor animasyonu görünsün
        low = question.lower()
        advice = bool(ADVICE_RE.search(question))
        llm_model = META["health"]["models"]["llm"]["model"]
        emb_model = META["health"]["models"]["embedding"]

        def reply(answer: str, found: bool, sources: list, mode: str, model, extra_ms: int = 0,
                  warning: str | None = None) -> None:
            if advice:
                answer = f"{META['advice_refusal']}\n\n{answer}"
            out = {"question": question, "answer": answer, "found": found, "sources": sources,
                   "mode": mode, "model": model,
                   "latency_ms": int((time.time() - t0) * 1000) + extra_ms,
                   "advice_refused": advice, "disclaimer": DISCLAIMER}
            if warning:
                out["warning"] = warning
            self._json(out)

        if state_of(ticker) != "full" or ticker not in DATA:
            # Metin analizi yok → parça yok → bulunamadı (backend answer_question ile aynı)
            reply(META["not_found_answer"], False, [], "not_found", emb_model)
            return
        if "gömme" in low:
            reply(META["not_found_answer"], False, [], "not_found", emb_model,
                  warning=META["embedding_mismatch_warning"])
            return
        if "bulunamadı" in low or "kripto" in low:
            reply(META["not_found_answer"], False, [], "not_found", emb_model)
            return
        entries = DATA[ticker]["ask"]
        chosen = next((e for e in entries if any(k in low for k in e["keywords"])), entries[0])
        if "alıntı" in low:
            # LLM yok: en ilgili alıntılar sırayla verilir; model = gömme modeli
            sources = chosen["sources"]
            answer = "\n".join(f"[{s['n']}] {s['text']}" for s in sources)
            reply(answer, True, sources, "extractive", emb_model)
            return
        reply(chosen["answer"], True, chosen["sources"], "llm", llm_model, extra_ms=1400)

    def _compare(self, raw: str) -> None:
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
        tickers = list(dict.fromkeys(tickers))
        if not 2 <= len(tickers) <= 4:
            self._error(400, "Karşılaştırma için 2–4 şirket seçin.")
            return
        for t in tickers:
            if not TICKER_RE.match(t) or company_record(t) is None or state_of(t) == "none":
                self._error(404, f"{t} henüz işlenmedi. Önce verisini çekin: POST /api/companies/{t}/ingest")
                return
        # v1.1: metin analizi gerekmez; finansal verisi olan her şirket karşılaştırılır
        ref_ratios = DATA[next(iter(DATA))]["ratios"]["ratios"]
        ratio_meta = [{"key": r["key"], "label": r["label"], "unit": r["unit"],
                       "higher_is_better": r["higher_is_better"]} for r in ref_ratios]
        companies = []
        for t in tickers:
            d = DATA.get(t)
            has_fin = d is not None and state_of(t) in ("full", "fin")
            sc = score_of(t)
            period_end = d["ratios"]["periods"][-1] if has_fin else None
            info = d["ratios"].get("period_info") if has_fin else None
            companies.append({
                "ticker": t, "name": company_obj(t)["name"],
                "period_end": period_end,
                # v1.4: son sütunun dayanağı ve dönemi (backend views.compare_view)
                "basis": info[-1]["basis"] if info else "annual", "as_of_period": period_end,
                "score": None if sc["total"] is None else {"total": sc["total"], "band": sc["band"]},
                "ratios": ({r["key"]: r["values"][-1]["value"] for r in d["ratios"]["ratios"]} if has_fin
                           else {m["key"]: None for m in ratio_meta}),
            })
        self._json({"ratio_meta": ratio_meta, "companies": companies, "disclaimer": DISCLAIMER})


def main() -> None:
    ap = argparse.ArgumentParser(description="FinSight AI demo sunucusu (sahte API + dist/)")
    ap.add_argument("port", nargs="?", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--latency", type=int, default=250, help="yapay gecikme (ms)")
    ap.add_argument("--empty", action="store_true",
                    help="hiç işlenmiş şirket olmadan başla (boş ana sayfa durumu)")
    ap.add_argument("--search-local", choices=["sec_user_agent_missing", "sec_unavailable"],
                    help="SEC şirket listesi alınamamış gibi davran (yalnızca yerel arama)")
    ap.add_argument("--no-freshness", action="store_true",
                    help="/freshness uç noktası yokmuş gibi davran (v1.3 öncesi sunucu)")
    ap.add_argument("--reject", default="",
                    help="v1.5 hata yanıtları: ingest-429, ingest-503, ask-429, post-403 (virgülle)")
    args = ap.parse_args()
    Handler.reject = frozenset(x.strip() for x in args.reject.split(",") if x.strip())
    Handler.no_freshness = args.no_freshness
    Handler.search_reason = args.search_local
    if args.empty:
        with STATE_LOCK:
            for tk in STATE:
                STATE[tk] = "none"
    Handler.latency_ms = args.latency
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    if not (DIST_DIR / "index.html").exists():
        print(f"Uyarı: {DIST_DIR}/index.html yok. Önce 'npm run build' (veya 'node build.mjs') çalıştırın.")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FinSight AI demo sunucusu: http://{args.host}:{args.port}  (kurgusal veri, Ctrl+C ile durdurun)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
