"""FinSight AI API yük ve performans testi aracı (Aşama 4, NFR-04 / NFR-05).

Çalışan bir FinSight AI sunucusuna (yalnızca bu makinede: 127.0.0.1 / localhost) karşı
eşzamanlı kullanıcı simülasyonu yapar. Her sanal kullanıcı ayrı bir iş parçacığında, ağırlıklı
bir uç nokta karışımından sabit tohumlu rastgele seçimle istek gönderir.

Kullanım:
    python -m finsight.bench_api --ticker MSFT --users 10 --duration 60
    python -m finsight.bench_api --ticker MSFT --compare-with AAPL --users 20 --duration 120 \\
        --json sonuc.json --label "ASUS X515JF, SQLite, FinBERT"
    python -m finsight.bench_api --base-url http://127.0.0.1:8790 --ticker EXMP --duration 10

Ölçülenler (uç nokta başına): istek sayısı, hata sayısı ve oranı, p50 / p95 / p99 / en büyük
gecikme (ms), saniyedeki istek (RPS). Ayrıca "özet ekranı" bileşik ölçümü (arayüzün şirket
sayfasında yaptığı /overview + /score istekleri art arda; tarayıcı bunları paralel gönderdiği
için bu değer bir üst sınırdır) ve NFR hedefleriyle karşılaştırma satırları yazılır.

Güvenlik ve maliyet kuralları:
- Yalnızca yerel sunucu kabul edilir (``--base-url`` ana makinesi 127.0.0.1, localhost, ::1).
- SEC'e veri çeken ``POST /ingest`` uç noktası varsayılan olarak ÇAĞRILMAZ (SEC hız sınırı,
  NFR-02). ``--include-ingest`` verilirse ölçüm başında yalnızca BİR kez çağrılır.
- ``/ask`` sunucuda LLM etkinse (``/api/health`` → ``models.llm.enabled``) her çağrı ücretli
  bir LLM isteğidir; bu durumda ``--allow-llm`` verilmeden ``/ask`` karışımdan çıkarılır.
- Arka plan istekleri: ``/freshness`` (Aşama 4) SEC'e istek atabildiği için karışımda yoktur.

Yalnızca standart kütüphane + httpx kullanır. Ortamdaki vekil sunucu (HTTP_PROXY) ayarları yok
sayılır (``trust_env=False``): ölçüm doğrudan yerel sunucuya yapılır.

Sonuçlar ölçümün yapıldığı makineye, veritabanına ve modellere bağlıdır; rapora yazılırken
makine bilgisi ``--label`` ile birlikte verilmelidir. Sahte sunucuya (frontend/scripts/
mock_server.py) karşı yapılan ölçüm aracın çalıştığını gösterir, sistemin başarımını göstermez.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import re
import sys
import threading
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")  # backend api.TICKER_RE ile aynı kural
MAX_USERS = 200
OVERVIEW_SCREEN = "ekran: şirket özeti (overview + score)"

# Soru-cevap karışımında sırayla kullanılan sorular (yatırım tavsiyesi sorusu da var: ret yolu).
ASK_QUESTIONS = (
    "Şirketin başlıca risk faktörleri nelerdir?",
    "Gelir artışının nedenleri nelerdir?",
    "Tedarik zinciri riskleri nasıl değişti?",
    "Yönetim nakit akışı hakkında ne söylüyor?",
    "What are the main competition risks?",
    "Bu hisseyi almalı mıyım?",
)

# NFR hedefleri (docs/raporlar/00-Asama1-Analiz-ve-Tasarim-Planlama.md, Bölüm 5).
# (kimlik, açıklama, ölçülen seri adı, yüzdelik, hedef ms)
NFR_TARGETS: tuple[tuple[str, str, str, str, float], ...] = (
    ("NFR-04", "Özet ekranı p95 < 2 sn", OVERVIEW_SCREEN, "p95_ms", 2000.0),
    ("NFR-04", "GET /overview p95 < 2 sn", "GET /api/companies/{ticker}/overview", "p95_ms",
     2000.0),
    ("NFR-05", "Soru-cevap p95 < 10 sn", "POST /api/companies/{ticker}/ask", "p95_ms", 10000.0),
)


@dataclass(frozen=True)
class Request:
    """Tek bir HTTP isteği. ``name`` raporlamada kullanılan şablon adıdır."""

    name: str
    method: str
    path: str
    body: dict | None = None


@dataclass(frozen=True)
class Action:
    """Sanal kullanıcının bir adımı: bir veya daha fazla istek (bileşik ekran ölçümü)."""

    name: str
    weight: float
    requests: tuple[Request, ...]
    composite: str | None = None  # verilirse isteklerin toplam süresi bu adla da kaydedilir


@dataclass
class Record:
    name: str
    latency_ms: float
    ok: bool
    status: int | None
    error: str | None = None


@dataclass
class Recorder:
    records: list[Record] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, rec: Record) -> None:
        with self.lock:
            self.records.append(rec)


# --------------------------------------------------------------------------- saf yardımcılar


def validate_base_url(url: str) -> str:
    """Yalnızca yerel http(s) adresini kabul eder; sondaki ``/`` silinmiş adresi döndürür."""
    text = (url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"Geçersiz adres şeması: {text!r} (http veya https olmalı).")
    host = (parts.hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise ValueError(
            f"Yalnızca yerel sunucu ölçülebilir (127.0.0.1 / localhost); verilen: {host or text!r}.")
    if parts.username or parts.password:
        raise ValueError("Adreste kullanıcı adı / parola kullanılamaz.")
    if parts.query or parts.fragment:
        raise ValueError("Adres sorgu (?…) veya parça (#…) içeremez.")
    try:
        parts.port  # noqa: B018 - geçersiz port ValueError fırlatır
    except ValueError as exc:
        raise ValueError(f"Geçersiz port: {text!r}.") from exc
    return text.rstrip("/")


def validate_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not TICKER_RE.match(t):
        raise ValueError(f"Geçersiz ticker: {ticker!r} (harf, rakam, nokta, tire; en fazla 10).")
    return t


def build_actions(ticker: str, *, compare_with: str | None = None,
                  include_ask: bool = True) -> list[Action]:
    """Uç nokta karışımı. Ağırlıklar arayüzün tipik kullanımını kabaca yansıtır: şirket
    sayfası açılışı (overview + score) en sık, sekme geçişleri daha seyrek.

    ``POST /ingest`` ve ``/freshness`` (SEC'e istek atabilen uçlar) bu listede hiçbir zaman yer
    almaz.
    """
    t = quote(ticker, safe="")
    base = f"/api/companies/{t}"

    def get(template: str, path: str) -> Request:
        return Request(f"GET {template}", "GET", path)

    actions = [
        Action("health", 0.5, (get("/api/health", "/api/health"),)),
        Action("companies", 1.0, (get("/api/companies", "/api/companies"),)),
        Action("search", 1.0, (get("/api/companies/search?q=…",
                                    f"/api/companies/search?q={t.lower()}&limit=10"),)),
        Action("overview_screen", 4.0,
               (get("/api/companies/{ticker}/overview", f"{base}/overview"),
                get("/api/companies/{ticker}/score", f"{base}/score")),
               composite=OVERVIEW_SCREEN),
        Action("financials", 2.0, (get("/api/companies/{ticker}/facts", f"{base}/facts"),
                                   get("/api/companies/{ticker}/ratios", f"{base}/ratios"))),
        Action("filings", 1.0, (get("/api/companies/{ticker}/filings",
                                    f"{base}/filings?limit=50"),)),
        Action("risk_tab", 1.0, (get("/api/companies/{ticker}/sentiment", f"{base}/sentiment"),
                                 get("/api/companies/{ticker}/risk-changes",
                                     f"{base}/risk-changes"))),
        Action("export", 0.5, (get("/api/companies/{ticker}/export.csv", f"{base}/export.csv"),)),
    ]
    if compare_with:
        other = quote(compare_with, safe="")
        actions.append(Action("compare", 1.0, (get("/api/compare?tickers=…",
                                                   f"/api/compare?tickers={t},{other}"),)))
    if include_ask:
        # Gövde her çağrıda yeniden seçilir (bkz. _ask_request); burada şablon
        actions.append(Action("ask", 1.0, (Request("POST /api/companies/{ticker}/ask", "POST",
                                                   f"{base}/ask", {"question": ""}),)))
    return actions


def pick_action(actions: Sequence[Action], rng: random.Random) -> Action:
    weights = [a.weight for a in actions]
    return rng.choices(list(actions), weights=weights, k=1)[0]


def percentile(values: Sequence[float], q: float) -> float | None:
    """Doğrusal aradeğerlemeli yüzdelik (numpy varsayılanı ile aynı). Boş girdide None."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q 0–100 arasında olmalı")
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(data[lo])
    return float(data[lo] + (data[hi] - data[lo]) * (pos - lo))


def summarize(records: Sequence[Record], wall_seconds: float) -> dict[str, dict]:
    """Seri adı → özet. Ad sırası: ilk görülme sırası. Gecikme yüzdelikleri yalnızca başarılı
    isteklerden hesaplanır (hatalı isteğin süresi başarımı yanlış gösterir); hata oranı tüm
    isteklerden."""
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(r.name, []).append(r)
    out: dict[str, dict] = {}
    for name, recs in groups.items():
        ok = [r.latency_ms for r in recs if r.ok]
        errors = [r for r in recs if not r.ok]
        kinds = Counter((str(r.status) if r.status is not None else (r.error or "hata"))
                        for r in errors)
        out[name] = {
            "requests": len(recs),
            "errors": len(errors),
            "error_rate": len(errors) / len(recs) if recs else 0.0,
            "error_kinds": dict(sorted(kinds.items())),
            "p50_ms": _round(percentile(ok, 50)),
            "p95_ms": _round(percentile(ok, 95)),
            "p99_ms": _round(percentile(ok, 99)),
            "max_ms": _round(max(ok) if ok else None),
            "mean_ms": _round(sum(ok) / len(ok) if ok else None),
            "rps": round(len(recs) / wall_seconds, 2) if wall_seconds > 0 else None,
        }
    return out


def total_row(records: Sequence[Record], wall_seconds: float) -> dict:
    """Bileşik (ekran) kayıtları hariç tüm HTTP istekleri."""
    http = [r for r in records if not r.name.startswith("ekran:")]
    return summarize([Record("TOPLAM", r.latency_ms, r.ok, r.status, r.error) for r in http],
                     wall_seconds).get("TOPLAM", {"requests": 0, "errors": 0, "error_rate": 0.0,
                                                  "error_kinds": {}, "p50_ms": None,
                                                  "p95_ms": None, "p99_ms": None,
                                                  "max_ms": None, "mean_ms": None,
                                                  "rps": 0.0})


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def nfr_comparison(summary: dict[str, dict]) -> list[dict]:
    """Hedeflerle karşılaştırma. Ölçülmemiş veya hiç başarılı isteği olmayan seri için
    ``met: None`` (değerlendirilemedi); hata oranı %1'i aşan seri hedefi karşılamış sayılmaz."""
    rows = []
    for nfr_id, desc, series, stat, target in NFR_TARGETS:
        s = summary.get(series)
        measured = s.get(stat) if s else None
        if measured is None:
            met = None
            note = "ölçülmedi" if not s else "başarılı istek yok"
        else:
            met = measured < target and s["error_rate"] <= 0.01
            note = (None if s["error_rate"] <= 0.01
                    else f"hata oranı %{s['error_rate'] * 100:.1f} (> %1)")
        rows.append({"nfr": nfr_id, "description": desc, "series": series, "stat": stat,
                     "target_ms": target, "measured_ms": measured, "met": met, "note": note})
    return rows


def _fmt_ms(x: float | None) -> str:
    return "—" if x is None else f"{x:,.1f}".replace(",", " ")


def format_table(summary: dict[str, dict], total: dict, nfr_rows: Sequence[dict]) -> str:
    head = (f"{'Uç nokta':<46} {'İstek':>7} {'Hata':>6} {'Hata %':>7} {'p50':>9} "
            f"{'p95':>9} {'p99':>9} {'en büyük':>9} {'RPS':>8}")
    lines = [head, "-" * len(head)]

    def line(name: str, s: dict) -> str:
        rps = "—" if s.get("rps") is None else f"{s['rps']:.2f}"
        return (f"{name[:46]:<46} {s['requests']:>7} {s['errors']:>6} "
                f"{s['error_rate'] * 100:>6.1f}% {_fmt_ms(s['p50_ms']):>9} "
                f"{_fmt_ms(s['p95_ms']):>9} {_fmt_ms(s['p99_ms']):>9} "
                f"{_fmt_ms(s['max_ms']):>9} {rps:>8}")

    for name, s in summary.items():
        if not name.startswith("ekran:"):
            lines.append(line(name, s))
    lines.append("-" * len(head))
    lines.append(line("TOPLAM (HTTP)", total))
    for name, s in summary.items():
        if name.startswith("ekran:"):
            lines.append(line(name, s))
    lines.append("")
    lines.append("Gecikmeler ms; yüzdelikler yalnızca başarılı isteklerden.")
    lines.append("")
    lines.append("NFR hedefleriyle karşılaştırma")
    for r in nfr_rows:
        verdict = {True: "SAĞLANDI", False: "SAĞLANMADI", None: "DEĞERLENDİRİLEMEDİ"}[r["met"]]
        note = f" ({r['note']})" if r["note"] else ""
        lines.append(f"  {r['nfr']}  {r['description']:<28} ölçülen {r['stat'][:3]} = "
                     f"{_fmt_ms(r['measured_ms'])} ms, hedef < {_fmt_ms(r['target_ms'])} ms "
                     f"→ {verdict}{note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- HTTP yürütme


def _ask_request(req: Request, rng: random.Random) -> Request:
    return Request(req.name, req.method, req.path, {"question": rng.choice(ASK_QUESTIONS)})


def send(client, req: Request) -> Record:
    """Tek isteği gönderir; 2xx başarılıdır. Yanıt gövdesi tamamen okunur (süreye dahil)."""
    t0 = time.perf_counter()
    try:
        if req.method == "POST":
            resp = client.post(req.path, json=req.body)
        else:
            resp = client.get(req.path)
        _ = resp.content
        ms = (time.perf_counter() - t0) * 1000
        return Record(req.name, ms, 200 <= resp.status_code < 300, resp.status_code)
    except Exception as exc:  # noqa: BLE001 - ağ hataları hata olarak sayılır
        ms = (time.perf_counter() - t0) * 1000
        return Record(req.name, ms, False, None, type(exc).__name__)


def run_action(client, action: Action, rng: random.Random, recorder: Recorder | None) -> None:
    total = 0.0
    all_ok = True
    for req in action.requests:
        if req.method == "POST" and req.name.endswith("/ask"):
            req = _ask_request(req, rng)
        rec = send(client, req)
        total += rec.latency_ms
        all_ok = all_ok and rec.ok
        if recorder is not None:
            recorder.add(rec)
    if action.composite and recorder is not None:
        recorder.add(Record(action.composite, total, all_ok, None if all_ok else -1,
                            None if all_ok else "bileşen hatası"))


def make_client(base_url: str, timeout: float):
    import httpx

    return httpx.Client(base_url=base_url, timeout=timeout, trust_env=False,
                        headers={"User-Agent": "FinSight-AI bench_api"})


def warmup(base_url: str, actions: Sequence[Action], timeout: float, rounds: int) -> int:
    """Her eylemi sırayla ``rounds`` kez çalıştırır; sonuçlar ölçüme katılmaz. Hata sayısı."""
    rng = random.Random(0)
    errors = 0
    with make_client(base_url, timeout) as client:
        for _ in range(max(0, rounds)):
            for action in actions:
                rec = Recorder()
                run_action(client, action, rng, rec)
                errors += sum(1 for r in rec.records if not r.ok and not r.name.startswith("ekran:"))
    return errors


def run_load(base_url: str, actions: Sequence[Action], *, users: int, duration: float,
             timeout: float, seed: int, think_ms: float = 0.0,
             clock=time.perf_counter) -> tuple[list[Record], float]:
    """``users`` iş parçacığı ``duration`` saniye boyunca eylem seçip çalıştırır.

    Süre dolduğunda yeni eylem başlatılmaz; süren eylem tamamlanır. Döndürülen duvar saati
    süresi, son eylemin bitişine kadardır (RPS bu süreye bölünür)."""
    recorder = Recorder()
    stop = threading.Event()
    # İstemciler ölçüm penceresinden ÖNCE kurulur: Windows'ta httpx.Client'ın SSL bağlamını
    # yüklemesi yüzlerce ms sürebilir; pencere içinde kalırsa kısa ölçümlerde hiç istek atılmaz.
    clients = [make_client(base_url, timeout) for _ in range(users)]
    start = clock()
    deadline = start + duration

    def user(uid: int) -> None:
        rng = random.Random(seed * 1000 + uid)
        client = clients[uid]
        while not stop.is_set() and clock() < deadline:
            run_action(client, pick_action(actions, rng), rng, recorder)
            if think_ms > 0:
                stop.wait(think_ms / 1000 * rng.uniform(0.5, 1.5))

    threads = [threading.Thread(target=user, args=(i,), daemon=True, name=f"user-{i}")
               for i in range(users)]
    try:
        for th in threads:
            th.start()
        try:
            for th in threads:
                th.join()
        except KeyboardInterrupt:
            stop.set()
            for th in threads:
                th.join(timeout)
        return recorder.records, clock() - start
    finally:
        for c in clients:
            c.close()


def preflight(base_url: str, ticker: str, timeout: float) -> dict:
    """Sağlık ve şirket kontrolü. Sunucuya ulaşılamazsa ConnectionError."""
    import httpx

    with make_client(base_url, timeout) as client:
        try:
            health = client.get("/api/health")
        except httpx.HTTPError as exc:
            raise ConnectionError(f"Sunucuya ulaşılamadı ({base_url}): {type(exc).__name__}") from exc
        if health.status_code != 200:
            raise ConnectionError(f"/api/health HTTP {health.status_code} döndü.")
        info = health.json()
        ov = client.get(f"/api/companies/{quote(ticker, safe='')}/overview")
        info = {"health": info, "overview_status": ov.status_code}
        if ov.status_code == 200:
            body = ov.json()
            info["analyzed"] = body.get("analyzed")
        return info


def _llm_enabled(health: dict) -> bool:
    llm = ((health or {}).get("models") or {}).get("llm") or {}
    return bool(llm.get("enabled"))


# --------------------------------------------------------------------------- komut satırı


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m finsight.bench_api",
        description="FinSight AI API yük/performans testi (yalnızca yerel sunucu).")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help=f"sunucu adresi (varsayılan {DEFAULT_BASE_URL}; yalnızca 127.0.0.1/localhost)")
    p.add_argument("--ticker", default="MSFT", help="ölçülecek, önceden işlenmiş şirket")
    p.add_argument("--compare-with", default=None,
                   help="karışıma /compare eklemek için ikinci (işlenmiş) şirket")
    p.add_argument("--users", type=int, default=10, help=f"eşzamanlı kullanıcı (1–{MAX_USERS})")
    p.add_argument("--duration", type=float, default=30.0, help="ölçüm süresi (sn)")
    p.add_argument("--warmup-rounds", type=int, default=1,
                   help="ısınma turu sayısı (her eylem bir kez; ölçüme katılmaz)")
    p.add_argument("--think-ms", type=float, default=0.0,
                   help="eylemler arası ortalama düşünme süresi (ms; 0 = sürekli yük)")
    p.add_argument("--timeout", type=float, default=30.0, help="istek zaman aşımı (sn)")
    p.add_argument("--seed", type=int, default=42, help="rastgele seçim tohumu")
    p.add_argument("--no-ask", action="store_true", help="/ask uç noktasını karışımdan çıkar")
    p.add_argument("--allow-llm", action="store_true",
                   help="sunucuda LLM etkinken de /ask çağır (her çağrı ücretli LLM isteğidir)")
    p.add_argument("--include-ingest", action="store_true",
                   help="ölçüm başında POST /ingest'i BİR kez çağır (SEC'e istek atar!)")
    p.add_argument("--json", dest="json_path", default=None,
                   help="sonuçları JSON olarak bu dosyaya yaz ('-' = standart çıktı)")
    p.add_argument("--label", default=None, help="ölçüm etiketi (makine, veritabanı, modeller)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_url = validate_base_url(args.base_url)
        ticker = validate_ticker(args.ticker)
        compare_with = validate_ticker(args.compare_with) if args.compare_with else None
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2
    if not 1 <= args.users <= MAX_USERS:
        print(f"Hata: --users 1–{MAX_USERS} arasında olmalı.", file=sys.stderr)
        return 2
    if not args.duration > 0 or not math.isfinite(args.duration):
        print("Hata: --duration pozitif olmalı.", file=sys.stderr)
        return 2
    if not args.timeout > 0:
        print("Hata: --timeout pozitif olmalı.", file=sys.stderr)
        return 2
    if args.think_ms < 0:
        print("Hata: --think-ms negatif olamaz.", file=sys.stderr)
        return 2
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("Hata: httpx kurulu değil (pip install httpx).", file=sys.stderr)
        return 2

    try:
        info = preflight(base_url, ticker, args.timeout)
    except ConnectionError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2
    notes: list[str] = []
    if info["overview_status"] != 200:
        notes.append(f"{ticker} için /overview HTTP {info['overview_status']} döndü; şirket "
                     "işlenmemiş olabilir (önce: python -m finsight.ingest TICKER --analyze). "
                     "Şirket uçları hata olarak sayılacak.")
    include_ask = not args.no_ask
    if include_ask and _llm_enabled(info["health"]) and not args.allow_llm:
        include_ask = False
        notes.append("Sunucuda LLM etkin: /ask ücretli LLM çağrısı yapacağı için karışımdan "
                     "çıkarıldı (--allow-llm ile eklenir).")

    actions = build_actions(ticker, compare_with=compare_with, include_ask=include_ask)

    ingest_record = None
    if args.include_ingest:
        with make_client(base_url, args.timeout) as client:
            ingest_record = send(client, Request("POST /api/companies/{ticker}/ingest", "POST",
                                                 f"/api/companies/{quote(ticker, safe='')}/ingest",
                                                 {"analyze": False}))
        notes.append("POST /ingest bir kez çağrıldı (arka planda SEC'e istek atar); sonuçları "
                     "etkileyebilir.")

    warm_errors = warmup(base_url, actions, args.timeout, args.warmup_rounds)
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records, wall = run_load(base_url, actions, users=args.users, duration=args.duration,
                             timeout=args.timeout, seed=args.seed, think_ms=args.think_ms)
    if ingest_record is not None:
        records.append(ingest_record)
    summary = summarize(records, wall)
    total = total_row(records, wall)
    nfr_rows = nfr_comparison(summary)

    result = {
        "tool": "finsight.bench_api",
        "label": args.label,
        "started_at": started_at,
        "base_url": base_url,
        "ticker": ticker,
        "compare_with": compare_with,
        "users": args.users,
        "duration_s": args.duration,
        "wall_s": round(wall, 3),
        "think_ms": args.think_ms,
        "seed": args.seed,
        "warmup_rounds": args.warmup_rounds,
        "warmup_errors": warm_errors,
        "actions": [{"name": a.name, "weight": a.weight,
                     "requests": [r.name for r in a.requests]} for a in actions],
        "server": info["health"],
        "company_analyzed": info.get("analyzed"),
        "client": {"python": platform.python_version(), "platform": platform.platform()},
        "endpoints": summary,
        "total": total,
        "nfr": nfr_rows,
        "notes": notes,
    }

    print(f"FinSight AI yük testi: {base_url}  ticker={ticker}  kullanıcı={args.users}  "
          f"süre={args.duration:g} sn (duvar {wall:.1f} sn)  ısınma hatası={warm_errors}")
    if args.label:
        print(f"Etiket: {args.label}")
    for n in notes:
        print(f"Not: {n}")
    print()
    print(format_table(summary, total, nfr_rows))
    if args.json_path:
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.json_path == "-":
            print(text)
        else:
            with open(args.json_path, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            print(f"\nJSON: {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
