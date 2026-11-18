"""FinSight AI demo / sunum sunucusu (yalnızca standart kütüphane).

- `frontend/dist/` klasörünü `/` altından sunar (derlenmiş arayüz).
- `/api/*` uç noktalarını `frontend/mock/*.json` içindeki KURGUSAL verilerle yanıtlar.
  Yanıt şekilleri `docs/api-contract.md` ile birebir aynıdır.
- Gerçek backend'e, SEC'e veya bir LLM'e bağlanmaz; ağ erişimi gerektirmez.

Kullanım:
    python3 frontend/scripts/mock_server.py            # http://127.0.0.1:8765
    python3 frontend/scripts/mock_server.py 9000 --latency 300

Demo akışları:
- QNTA henüz işlenmemiştir: arayıp "Analiz et" deyince ~9 sn süren sahte bir iş başlar,
  bitince şirket sayfası dolar.
- LMNR, ORVB, PLSD, EXMR için örnek veri yoktur: iş hata ile biter (hata durumunu gösterir).
- Soru-Cevap: soruda "bulunamadı" geçerse `not_found`, "alıntı" geçerse `extractive` yanıt döner.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
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
# Başlangıçta işlenmiş şirketler: arama dizininde analyzed=true olanlar
ANALYZED = {c["ticker"] for c in META["search_index"] if c["analyzed"]}
JOBS: dict[int, dict] = {}
NEXT_JOB = [1]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_view(job: dict) -> dict:
    """İşin anlık durumunu geçen süreye göre hesaplar."""
    elapsed = time.time() - job["t0"]
    steps = META["ingest_steps"]
    has_data = job["ticker"] in DATA
    view = {"id": job["id"], "ticker": job["ticker"], "status": "running", "step": None,
            "progress": 0.0, "error": None, "started_at": job["started_at"], "finished_at": None,
            "request_count": 0}
    if elapsed < 0.8:
        view.update(status="queued", step="Sırada bekliyor", progress=0.0)
        return view
    frac = min(1.0, (elapsed - 0.8) / JOB_SECONDS)
    if not has_data and frac >= 0.34:
        view.update(status="error", step=steps[2], progress=0.34,
                    error="Demo modunda bu şirket için örnek veri yok; SEC'e bağlanılmadı.",
                    finished_at=job.get("finished_at") or now_iso(), request_count=6)
        job["finished_at"] = view["finished_at"]
        return view
    if frac >= 1.0:
        with STATE_LOCK:
            ANALYZED.add(job["ticker"])
        job.setdefault("finished_at", now_iso())
        view.update(status="done", step="Tamamlandı", progress=1.0, finished_at=job["finished_at"],
                    request_count=27)
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


def is_analyzed(ticker: str) -> bool:
    with STATE_LOCK:
        return ticker in ANALYZED and ticker in DATA


class Handler(SimpleHTTPRequestHandler):
    server_version = "FinSightMock/0.3"
    latency_ms = 250

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST_DIR), **kwargs)

    # ------------------------------------------------------------ yardımcılar
    def log_message(self, fmt: str, *args) -> None:  # sessiz
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, detail: str) -> None:
        self._json({"detail": detail}, status)

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
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
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

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        self._delay()
        if len(parts) == 4 and parts[:2] == ["api", "companies"] and parts[3] == "ingest":
            ticker = parts[2].upper()
            if company_record(ticker) is None:
                self._error(404, f"'{ticker}' SEC şirket listesinde bulunamadı.")
                return
            with STATE_LOCK:
                for job in JOBS.values():
                    if job["ticker"] == ticker and job_view(job)["status"] in ("queued", "running"):
                        self._json({"job_id": job["id"], "status": "running"}, 202)
                        return
                jid = NEXT_JOB[0]
                NEXT_JOB[0] += 1
                JOBS[jid] = {"id": jid, "ticker": ticker, "t0": time.time(), "started_at": now_iso()}
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
            self._json(META["health"])
            return
        if parts == ["api", "companies"]:
            out = []
            for tk in sorted(t for t in DATA if is_analyzed(t)):
                d = DATA[tk]
                c = d["company"]
                out.append({"ticker": tk, "cik": c["cik"], "name": c["name"],
                            "sic_description": c["sic_description"], "analyzed": True,
                            "last_filing_date": d["filings"][0]["filing_date"],
                            "score": {"total": d["score"]["total"], "band": d["score"]["band"]}})
            self._json(out)
            return
        if parts == ["api", "companies", "search"]:
            needle = (q.get("q") or "").strip().lower()
            limit = max(1, min(50, int(q.get("limit") or 10)))
            res = []
            for c in search_index():
                hay = f"{c['ticker']} {c['name']} {c['cik']} {c['cik'].lstrip('0')}".lower()
                if not needle or needle in hay:
                    res.append({"ticker": c["ticker"], "cik": c["cik"], "name": c["name"],
                                "ingested": is_analyzed(c["ticker"])})
            res.sort(key=lambda r: (not r["ticker"].lower().startswith(needle), r["ticker"]))
            self._json(res[:limit])
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

    def _company(self, ticker: str, what: str, q: dict[str, str]) -> None:
        if company_record(ticker) is None:
            self._error(404, f"'{ticker}' için şirket bulunamadı.")
            return
        company = company_obj(ticker)
        analyzed = is_analyzed(ticker)
        d = DATA.get(ticker)

        if what == "overview":
            if analyzed and d:
                self._json(d["overview"])
            else:
                self._json({"company": company, "score": None, "key_metrics": [], "latest_filings": [],
                            "sentiment_summary": None, "risk_summary": None, "analysis_updated_at": None,
                            "disclaimer": DISCLAIMER})
            return
        if what == "facts":
            metrics = d["facts"]["metrics"] if (analyzed and d) else []
            if q.get("metric"):
                metrics = [m for m in metrics if m["metric"] == q["metric"]]
            self._json({"company": company, "metrics": metrics, "disclaimer": DISCLAIMER})
            return
        if what == "ratios":
            if analyzed and d:
                self._json(d["ratios"])
            else:
                self._json({"company": company, "periods": [], "ratios": [], "disclaimer": DISCLAIMER})
            return
        if what == "score":
            if analyzed and d:
                self._json(d["score"])
            else:
                self._json({"total": None, "band": None, "as_of_period": None, "weights_version": "v1-2026-11",
                            "components": [], "missing_components": [], "explanation": None,
                            "disclaimer": DISCLAIMER})
            return
        if what == "filings":
            filings = d["filings"] if (analyzed and d) else []
            form = q.get("form")
            if form:
                filings = [f for f in filings if f["form"] == form or f["form"] == f"{form}/A"]
            limit = int(q.get("limit") or 50)
            self._json({"company": company, "filings": filings[:limit], "disclaimer": DISCLAIMER})
            return
        if what == "sentiment":
            if analyzed and d:
                self._json(d["sentiment"])
            else:
                self._json({"model": META["health"]["models"]["sentiment"], "sections": [],
                            "disclaimer": DISCLAIMER})
            return
        if what == "risk-changes":
            if analyzed and d:
                self._json(d["risk_changes"])
            else:
                self._json({"old_filing": None, "new_filing": None,
                            "counts": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
                            "changes": [], "disclaimer": DISCLAIMER})
            return
        if what == "export.csv":
            if not (analyzed and d):
                self._error(404, "Bu şirket için henüz analiz yok; önce analiz başlatın.")
                return
            self._csv(ticker, d)
            return
        self._error(404, "Uç nokta bulunamadı.")

    def _csv(self, ticker: str, d: dict) -> None:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["ticker", "cik", "section", "key", "label", "period_start", "period_end", "value",
                    "unit", "status", "xbrl_tag", "form", "accession_no", "filed"])
        cik = d["company"]["cik"]
        for m in d["facts"]["metrics"]:
            for v in m["values"]:
                s = v["source"]
                w.writerow([ticker, cik, "fact", m["metric"], m["label"], v["period_start"] or "",
                            v["period_end"], v["value"], "USD", "ok", s["xbrl_tag"], s["form"],
                            s["accession_no"], s["filed"]])
        for r in d["ratios"]["ratios"]:
            for v in r["values"]:
                w.writerow([ticker, cik, "ratio", r["key"], r["label"], "", v["period_end"],
                            "" if v["value"] is None else v["value"], r["unit"], v["status"], "", "10-K",
                            (v["inputs"][0]["accession_no"] if v["inputs"] else ""), ""])
        sc = d["score"]
        w.writerow([ticker, cik, "score", "total", "Toplam skor", "", sc["as_of_period"], sc["total"],
                    "points", "ok", "", "", "", ""])
        for c in sc["components"]:
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
        if company_record(ticker) is None:
            self._error(404, f"'{ticker}' için şirket bulunamadı.")
            return
        if not is_analyzed(ticker):
            self._error(409, "Bu şirketin raporları henüz işlenmedi; önce analiz başlatın.")
            return
        t0 = time.time()
        self._delay(3.0)  # yükleniyor animasyonu görünsün
        low = question.lower()
        model = META["health"]["models"]["llm"]["model"]
        base = {"question": question, "disclaimer": DISCLAIMER}
        if "bulunamadı" in low or "kripto" in low:
            self._json({**base, "answer": META["not_found_answer"], "found": False, "sources": [],
                        "mode": "not_found", "model": model,
                        "latency_ms": int((time.time() - t0) * 1000)})
            return
        entries = DATA[ticker]["ask"]
        chosen = next((e for e in entries if any(k in low for k in e["keywords"])), entries[0])
        if "alıntı" in low:
            # LLM yok: en ilgili alıntılar sırayla verilir
            sources = chosen["sources"]
            answer = " ".join(f"[{s['n']}] {s['text']}" for s in sources)
            self._json({**base, "answer": answer, "found": True, "sources": sources, "mode": "extractive",
                        "model": None, "latency_ms": int((time.time() - t0) * 1000)})
            return
        self._json({**base, "answer": chosen["answer"], "found": True, "sources": chosen["sources"],
                    "mode": "llm", "model": model, "latency_ms": int((time.time() - t0) * 1000) + 1400})

    def _compare(self, raw: str) -> None:
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
        tickers = list(dict.fromkeys(tickers))
        if not 2 <= len(tickers) <= 4:
            self._error(422, "Karşılaştırma için 2–4 şirket seçin.")
            return
        for t in tickers:
            if company_record(t) is None:
                self._error(404, f"'{t}' için şirket bulunamadı.")
                return
            if not is_analyzed(t):
                self._error(409, f"'{t}' henüz analiz edilmedi; karşılaştırmadan önce analiz başlatın.")
                return
        first = DATA[tickers[0]]["ratios"]["ratios"]
        ratio_meta = [{"key": r["key"], "label": r["label"], "unit": r["unit"],
                       "higher_is_better": r["higher_is_better"]} for r in first]
        companies = []
        for t in tickers:
            d = DATA[t]
            companies.append({
                "ticker": t, "name": d["company"]["name"], "period_end": d["ratios"]["periods"][-1],
                "score": {"total": d["score"]["total"], "band": d["score"]["band"]},
                "ratios": {r["key"]: r["values"][-1]["value"] for r in d["ratios"]["ratios"]},
            })
        self._json({"ratio_meta": ratio_meta, "companies": companies, "disclaimer": DISCLAIMER})


def main() -> None:
    ap = argparse.ArgumentParser(description="FinSight AI demo sunucusu (sahte API + dist/)")
    ap.add_argument("port", nargs="?", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--latency", type=int, default=250, help="yapay gecikme (ms)")
    args = ap.parse_args()
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
