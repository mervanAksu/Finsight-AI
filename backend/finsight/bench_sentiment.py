"""FinBERT hız ve etiket uyumu ölçüm aracı (CPU / GPU / ONNX) (README "Performans").

Kullanım:
    python -m finsight.bench_sentiment MSFT                 # veritabanındaki MSFT cümleleri
    python -m finsight.bench_sentiment MSFT --limit 1000 --threads 4
    python -m finsight.bench_sentiment --file cumleler.txt  # satır başına bir cümle
    python -m finsight.bench_sentiment MSFT --configs bef --reference b   # GPU fp32 / fp16

Karşılaştırılan yapılandırmalar (aynı cümle örneği, aynı yüklenmiş ağırlıklar):
    a  Eski yöntem: transformers pipeline, metin sırası, batch 16, max_length 512 (CPU)
    b  Yeni varsayılan: AutoModel + inference_mode, uzunluğa göre sıralı dinamik dolgu, batch 32
       (CPU)
    c  b + int8 dinamik kuantizasyon (FINSIGHT_FINBERT_QUANTIZE=1) — REDDEDİLDİ, yalnızca kayıt
    d  b + max_length 256 (FINSIGHT_FINBERT_MAX_LENGTH=256)
    e  b, CUDA fp32 (FINSIGHT_DEVICE=cuda) — CUDA yoksa "çalışmadı"
    f  b, CUDA fp16 (FINSIGHT_DEVICE=cuda FINSIGHT_FP16=1)
    g  b, ONNX Runtime fp32 CPU (FINSIGHT_FINBERT_BACKEND=onnx; optimum[onnxruntime] gerekir)

Her yapılandırma için cümle/sn, toplam süre, referansa göre hızlanma ve etiket uyumu (%),
sınıf bazında karışıklık tablosu; ayrıca token uzunluğu dağılımı yazılır. Referans
varsayılan olarak (a)'dır (``--reference``); (a) çalıştırılmazsa ilk çalışan yapılandırma.
Önce kısa bir ısınma (warm-up) çağrısı yapılır, model yükleme / taşıma süresi ölçüme katılmaz.
GPU yapılandırmaları ağırlıkların ayrı bir kopyasını kullanır (CPU yapılandırmaları etkilenmez).

torch / transformers kurulu değilse anlaşılır bir mesajla 2 koduyla çıkar. Ağ erişimi yalnızca
modelin ilk indirilmesi içindir (Hugging Face önbelleği); SEC'e istek atılmaz.
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import sys
import time
from collections.abc import Sequence

from . import DISCLAIMER
from .sentiment import (FINBERT_NAME, LABELS, FinBertModel, _cuda_usable, _FINBERT_LABEL_MAP,
                        normalize_sentence, resolve_device)

CONFIG_DESCRIPTIONS: dict[str, str] = {
    "a": "Eski yöntem (pipeline, sırasız, batch 16)",
    "b": "Yeni varsayılan (sıralı dinamik dolgu, batch 32)",
    "c": "Yeni + int8 kuantizasyon",
    "d": "Yeni + max_length 256",
    "e": "Yeni, CUDA fp32",
    "f": "Yeni, CUDA fp16",
    "g": "Yeni, ONNX Runtime fp32 (CPU)",
}
# Doğruluğu etkileyebilecek / ölçülmesi gereken ayarlar ve ortam değişkenleri (öneri satırı).
SETTING_ENV: dict[str, str] = {
    "c": "FINSIGHT_FINBERT_QUANTIZE=1",
    "d": "FINSIGHT_FINBERT_MAX_LENGTH=256",
    "e": "FINSIGHT_DEVICE=cuda (auto zaten seçer)",
    "f": "FINSIGHT_FP16=1",
    "g": "FINSIGHT_FINBERT_BACKEND=onnx",
}
REFERENCE = "a"
LENGTH_THRESHOLDS = (64, 128, 256, 512)
# Kuantizasyon / kısa max_length ancak bu uyum oranı ve bu hızlanmayla önerilir (README).
RECOMMEND_MIN_AGREEMENT = 0.97
RECOMMEND_MIN_SPEEDUP = 1.2
WARMUP_SENTENCES = 8


# --------------------------------------------------------------------------- saf yardımcılar


def sample_sentences(sentences: Sequence[str], limit: int | None, seed: int = 0) -> list[str]:
    """Yinelenenleri atar; ``limit`` cümleden fazlaysa sabit tohumlu rastgele örnek alır."""
    seen: set[str] = set()
    unique: list[str] = []
    for s in sentences:
        key = normalize_sentence(s)
        if key and key not in seen:
            seen.add(key)
            unique.append(s)
    if limit is None or limit <= 0 or len(unique) <= limit:
        return unique
    return random.Random(seed).sample(unique, limit)


def _percentile(sorted_values: Sequence[int], q: float) -> int:
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return int(sorted_values[idx])


def length_distribution(lengths: Sequence[int],
                        thresholds: Sequence[int] = LENGTH_THRESHOLDS) -> dict:
    """Kesilmemiş token uzunluklarının özeti ve eşikleri aşan cümle sayıları."""
    values = sorted(int(x) for x in lengths)
    n = len(values)
    return {
        "n": n,
        "mean": round(sum(values) / n, 1) if n else 0.0,
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p99": _percentile(values, 0.99),
        "max": values[-1] if values else 0,
        "over": {t: sum(1 for v in values if v > t) for t in thresholds},
    }


def agreement(reference: Sequence[dict], other: Sequence[dict]) -> dict:
    """Etiket uyumu (0–1) ve karışıklık tablosu: confusion[referans][diğer] = sayı."""
    if len(reference) != len(other):
        raise ValueError("Tahmin listeleri aynı uzunlukta olmalı")
    confusion = {r: {o: 0 for o in LABELS} for r in LABELS}
    same = 0
    for ref, oth in zip(reference, other):
        r = ref["label"] if ref["label"] in confusion else "neutral"
        o = oth["label"] if oth["label"] in confusion else "neutral"
        confusion[r][o] += 1
        same += r == o
    n = len(reference)
    return {"rate": same / n if n else None, "matches": same, "n": n, "confusion": confusion}


def _pct(value: float | None) -> str:
    return "-" if value is None else f"%{100 * value:.1f}"


def format_results_table(rows: Sequence[dict], reference: str = REFERENCE) -> str:
    """rows: {key, description, seconds, n, agreement (0–1 | None), note}."""
    ref = next((r for r in rows if r["key"] == reference and r.get("seconds")), None)
    head = (f"{'':<3}{'Yapılandırma':<50}{'Süre (sn)':>10}{'Cümle/sn':>10}{'Hızlanma':>10}"
            f"{f'Uyum ({reference})':>10}")
    lines = [head, "-" * len(head)]
    for r in rows:
        if r.get("seconds") is None:
            lines.append(f"{r['key']:<3}{r['description']:<50}{'çalışmadı':>10}  {r.get('note', '')}")
            continue
        rate = r["n"] / r["seconds"] if r["seconds"] > 0 else 0.0
        speed = f"{ref['seconds'] / r['seconds']:.2f}x" if ref and r["seconds"] > 0 else "-"
        line = (f"{r['key']:<3}{r['description']:<50}{r['seconds']:>10.1f}{rate:>10.1f}"
                f"{speed:>10}{_pct(r.get('agreement')):>10}")
        if r.get("note"):
            line += f"  ({r['note']})"
        lines.append(line)
    return "\n".join(lines)


def format_confusion(key: str, result: dict, reference: str = REFERENCE) -> str:
    """Satır: referans etiketi; sütun: yapılandırmanın etiketi; son sütun sınıf bazında uyum."""
    names = {"positive": "olumlu", "negative": "olumsuz", "neutral": "nötr"}
    lines = [f"({key}) karışıklık — satır: ({reference}) etiketi, sütun: ({key}) etiketi",
             f"    {'':<10}" + "".join(f"{names[c]:>10}" for c in LABELS) + f"{'uyum':>10}"]
    for r in LABELS:
        row = result["confusion"][r]
        total = sum(row.values())
        cls = row[r] / total if total else None
        lines.append(f"    {names[r]:<10}" + "".join(f"{row[c]:>10}" for c in LABELS)
                     + f"{_pct(cls):>10}")
    return "\n".join(lines)


def format_length_distribution(dist: dict) -> str:
    n = dist["n"] or 1
    lines = [f"Token uzunluğu (özel belirteçler dahil, kesilmeden): {dist['n']} cümle, "
             f"ortalama {dist['mean']}, medyan {dist['p50']}, p90 {dist['p90']}, "
             f"p99 {dist['p99']}, en uzun {dist['max']}"]
    for t, count in dist["over"].items():
        lines.append(f"    {t:>4} token'ı aşan: {count:>6} cümle ({100 * count / n:.1f}%)")
    return "\n".join(lines)


def recommendations(rows: Sequence[dict], reference: str = REFERENCE) -> list[str]:
    """Ölçüte göre öneri: uyum ≥ %97 ve (b)'ye göre hızlanma ≥ 1,2x ise ayar açılabilir."""
    by_key = {r["key"]: r for r in rows}
    base = by_key.get("b")
    out: list[str] = []
    for key, env in SETTING_ENV.items():
        r = by_key.get(key)
        if not r or r.get("seconds") is None or r.get("agreement") is None:
            continue
        if not base or not base.get("seconds"):
            continue
        speedup = base["seconds"] / r["seconds"] if r["seconds"] > 0 else 0.0
        ok = (r["agreement"] >= RECOMMEND_MIN_AGREEMENT and speedup >= RECOMMEND_MIN_SPEEDUP)
        verdict = "açılması önerilir" if ok else "açılması önerilmez (varsayılan kapalı kalsın)"
        out.append(f"{env}: (b)'ye göre {speedup:.2f}x, ({reference}) ile uyum "
                   f"{_pct(r['agreement'])} → "
                   f"{verdict} [ölçüt: uyum ≥ %{100 * RECOMMEND_MIN_AGREEMENT:.0f} ve "
                   f"hızlanma ≥ {RECOMMEND_MIN_SPEEDUP:.1f}x]")
    return out


# --------------------------------------------------------------------------- modeller


class LegacyPipelineModel:
    """Eski FinBertModel davranışı: text-classification pipeline, metin sırası, batch 16."""

    def __init__(self, tokenizer, model, batch_size: int = 16) -> None:
        from transformers import pipeline

        self.batch_size = batch_size
        self._pipe = pipeline("text-classification", model=model, tokenizer=tokenizer, device=-1)

    def predict(self, sentences: Sequence[str]) -> list[dict]:
        raw = self._pipe(list(sentences), batch_size=self.batch_size, truncation=True,
                         max_length=512)
        out = []
        for r in raw:
            if isinstance(r, list):
                r = max(r, key=lambda x: x["score"])
            label = _FINBERT_LABEL_MAP.get(str(r["label"]).lower(), "neutral")
            out.append({"label": label, "score": float(r["score"])})
        return out


class CudaUnavailable(RuntimeError):
    """GPU yapılandırması istendi ama CUDA kullanılamıyor (satır "çalışmadı" olur)."""


def build_config_model(key: str, model_name: str, batch: int, tokenizer, base_model):
    """Yapılandırma anahtarına göre ölçülecek model nesnesi.

    GPU yapılandırmaları (e, f) ``base_model``'in derin kopyasını taşır: ``Module.to`` yerinde
    çalıştığı için aksi halde sonraki CPU yapılandırmaları GPU ağırlıklarıyla çalışırdı.
    """
    if key == "a":
        return LegacyPipelineModel(tokenizer, base_model)
    if key in ("e", "f"):
        import copy

        import torch

        ok, info = _cuda_usable(torch)
        if not ok:
            raise CudaUnavailable(info)
        return FinBertModel(model_name, batch, tokenizer=tokenizer,
                            model=copy.deepcopy(base_model), quantize=False, threads=0,
                            device="cuda", fp16="1" if key == "f" else "0", backend="torch")
    if key == "g":
        return FinBertModel(model_name, batch, tokenizer=tokenizer, model=None, quantize=False,
                            threads=0, backend="onnx")
    return FinBertModel(model_name, batch, tokenizer=tokenizer, model=base_model,
                        quantize=(key == "c"), max_length=256 if key == "d" else 512,
                        threads=0, device="cpu", fp16="0", backend="torch")


def _deps_missing() -> list[str]:
    return [m for m in ("torch", "transformers") if importlib.util.find_spec(m) is None]


def _load_from_db(ticker: str) -> list[str] | None:
    from . import repository as repo
    from .db import init_db, make_engine, make_session_factory

    engine = make_engine()
    init_db(engine)
    with make_session_factory(engine)() as session:
        company = repo.load_company(session, ticker)
        if company is None:
            return None
        return repo.load_company_sentences(session, company["id"])


def _time_predict(model, sentences: Sequence[str]) -> tuple[float, list[dict]]:
    model.predict(list(sentences[:WARMUP_SENTENCES]))  # ısınma
    started = time.perf_counter()
    preds = model.predict(list(sentences))
    return time.perf_counter() - started, preds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FinBERT hız ve uyum ölçümü (CPU / GPU / ONNX).")
    parser.add_argument("ticker", nargs="?", help="Veritabanında analiz edilmiş şirket (ör. MSFT)")
    parser.add_argument("--file", help="Cümle dosyası (UTF-8, satır başına bir cümle)")
    parser.add_argument("--limit", type=int, default=500, help="Örnek cümle sayısı (0 = tümü)")
    parser.add_argument("--seed", type=int, default=0, help="Örnekleme tohumu")
    parser.add_argument("--configs", default="abde",
                        help="Çalıştırılacak yapılandırmalar (a–g; c reddedildi, istenirse ekleyin)")
    parser.add_argument("--reference", default=REFERENCE,
                        help="Uyum ve hızlanma referansı (varsayılan a)")
    parser.add_argument("--batch", type=int, default=32, help="(b)/(c)/(d) grup boyutu")
    parser.add_argument("--threads", type=int, default=None,
                        help="torch iş parçacığı sayısı (verilmezse torch varsayılanı)")
    parser.add_argument("--model", default=FINBERT_NAME, help="Hugging Face model adı")
    args = parser.parse_args(argv)

    configs = [c for c in args.configs.lower() if c in CONFIG_DESCRIPTIONS]
    if not configs:
        print("Geçerli yapılandırma yok (a–g).", file=sys.stderr)
        return 2
    if not args.ticker and not args.file:
        print("Bir ticker (ör. MSFT) veya --file verin.", file=sys.stderr)
        return 2
    missing = _deps_missing()
    if missing:
        print("Bu ölçüm FinBERT gerektirir; kurulu olmayan paket(ler): " + ", ".join(missing)
              + ".\nKurulum: pip install torch --index-url https://download.pytorch.org/whl/cpu"
              " && pip install -r requirements-ml.txt", file=sys.stderr)
        return 2

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            pool = [line.strip() for line in fh if line.strip()]
    else:
        loaded = _load_from_db(args.ticker)
        if loaded is None:
            print(f"{args.ticker} veritabanında yok. Önce: python -m finsight.ingest "
                  f"{args.ticker} --analyze", file=sys.stderr)
            return 2
        pool = loaded
    sentences = sample_sentences(pool, args.limit, args.seed)
    if not sentences:
        print("Ölçülecek cümle bulunamadı (şirketi önce --analyze ile analiz edin).",
              file=sys.stderr)
        return 2

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from transformers.utils import logging as hf_logging

    hf_logging.set_verbosity_error()  # "sequence length is longer than..." uyarısı ölçümü bozmasın
    if args.threads:
        torch.set_num_threads(args.threads)
    print(f"Model yükleniyor: {args.model} …")
    t = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    base_model = AutoModelForSequenceClassification.from_pretrained(args.model)
    base_model.eval()
    load_seconds = time.perf_counter() - t
    print(f"Yüklendi ({load_seconds:.1f} sn). torch {torch.__version__}, iş parçacığı: "
          f"{torch.get_num_threads()}, örnek: {len(sentences)} cümle "
          f"(havuz: {len(pool)})")
    print(f"GPU: {resolve_device(torch, 'cuda', '0')[2]}\n")

    lengths = [len(ids) for ids in tokenizer(sentences, truncation=False)["input_ids"]]
    print(format_length_distribution(length_distribution(lengths)) + "\n")

    rows: list[dict] = []
    preds_by_key: dict[str, list[dict]] = {}
    for key in configs:
        row = {"key": key, "description": CONFIG_DESCRIPTIONS[key], "n": len(sentences),
               "seconds": None, "agreement": None, "note": ""}
        print(f"({key}) {CONFIG_DESCRIPTIONS[key]} çalışıyor …", flush=True)
        try:
            model = build_config_model(key, args.model, args.batch, tokenizer, base_model)
            if key == "c" and not model.quantized:
                row["note"] = "kuantizasyon yapılamadı, normal model ölçüldü"
            if key in ("e", "f"):
                row["note"] = getattr(model, "device_info", "")
            seconds, preds = _time_predict(model, sentences)
            row["seconds"] = seconds
            preds_by_key[key] = preds
        except Exception as exc:  # bir yapılandırmanın hatası diğerlerini durdurmasın
            row["note"] = f"{type(exc).__name__}: {exc}"[:120]
        rows.append(row)

    ref_key = args.reference if args.reference in preds_by_key else \
        next((r["key"] for r in rows if r["key"] in preds_by_key), args.reference)
    reference = preds_by_key.get(ref_key)
    details = []
    for row in rows:
        preds = preds_by_key.get(row["key"])
        if reference is not None and preds is not None:
            result = agreement(reference, preds)
            row["agreement"] = result["rate"]
            if row["key"] != ref_key:
                details.append(format_confusion(row["key"], result, ref_key))

    print("\n" + format_results_table(rows, ref_key))
    if reference is None:
        print(f"\n({ref_key}) çalışmadığı için uyum hesaplanamadı.")
    for block in details:
        print("\n" + block)
    recs = recommendations(rows, ref_key)
    if recs:
        print("\nÖneri")
        for line in recs:
            print("  " + line)
    print("\nNot: dizüstü bilgisayarlarda ısınma/termal kısma ölçümü etkiler; prize takılı ve "
          "yüksek performans güç modunda, iki kez çalıştırıp ikinci sonucu kullanın.")
    print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
