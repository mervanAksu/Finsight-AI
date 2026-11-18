"""FinBertModel testleri için sahte torch / belirteçleyici / model (torch'suz, ağsız).

Sahte belirteçleyici her kelimeyi bir belirtece çevirir (+[CLS], [SEP]); sahte model etiketi
yalnızca dolgu OLMAYAN belirteçlerden hesaplar. Böylece bir cümlenin etiketi hangi grupta ve
hangi dolguyla işlendiğinden bağımsızdır; sıralama + geri yerleştirme hatası etiket
uyumsuzluğu olarak yakalanır.
"""

from __future__ import annotations

import contextlib
import types
from types import SimpleNamespace

import numpy as np

ID2LABEL = {0: "positive", 1: "negative", 2: "neutral"}


def token_ids(text: str, max_length: int | None = None, truncation: bool = False) -> list[int]:
    ids = [101] + [100 + len(w) for w in text.split()] + [102]
    if truncation and max_length and len(ids) > max_length:
        ids = ids[:max_length - 1] + [102]
    return ids


def expected_label(text: str, max_length: int = 512) -> str:
    """Sahte modelin bu cümle için (tek başına işlense) vereceği etiket."""
    return ID2LABEL[sum(token_ids(text, max_length, truncation=True)) % 3]


class FakeTokenizer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, texts, truncation=False, max_length=None, padding=False,
                 return_tensors=None):
        ids = [token_ids(t, max_length, truncation) for t in texts]
        self.calls.append({"n": len(texts), "padding": padding, "return_tensors": return_tensors,
                           "lengths": [len(i) for i in ids], "max_length": max_length})
        if padding == "longest":
            width = max(len(i) for i in ids)
            mask = [[1] * len(i) + [0] * (width - len(i)) for i in ids]
            padded = [i + [0] * (width - len(i)) for i in ids]
            return {"input_ids": np.array(padded), "attention_mask": np.array(mask)}
        return {"input_ids": ids}


class FakeModel:
    def __init__(self, fixed_label: int | None = None, id2label: dict | None = None) -> None:
        self.config = SimpleNamespace(id2label=dict(id2label or ID2LABEL))
        self.fixed_label = fixed_label
        self.batch_shapes: list[tuple[int, int]] = []
        self.seen: list[int] = []  # her cümlenin belirteç toplamı (çağrı sayacı)
        self.eval_called = False

    def eval(self):
        self.eval_called = True
        return self

    def __call__(self, input_ids, attention_mask=None, **_kwargs):
        ids = np.asarray(input_ids)
        mask = np.asarray(attention_mask) if attention_mask is not None else np.ones_like(ids)
        self.batch_shapes.append(tuple(ids.shape))
        logits = []
        for row, m in zip(ids, mask):
            total = int(row[m.astype(bool)].sum())
            self.seen.append(total)
            label = self.fixed_label if self.fixed_label is not None else total % 3
            vec = np.zeros(len(self.config.id2label))
            vec[label] = 3.0
            logits.append(vec)
        return SimpleNamespace(logits=np.array(logits))


def fake_torch_module(quantize=None) -> types.ModuleType:
    """quantize: None → kuantizasyon kopya döndürür; Exception örneği → o hatayı yükseltir."""
    torch = types.ModuleType("torch")
    torch.threads = []  # type: ignore[attr-defined]
    torch.quantize_calls = []  # type: ignore[attr-defined]

    def set_num_threads(n):
        torch.threads.append(n)  # type: ignore[attr-defined]

    def quantize_dynamic(model, layers, dtype=None):
        torch.quantize_calls.append((layers, dtype))  # type: ignore[attr-defined]
        if isinstance(quantize, Exception):
            raise quantize
        return model

    torch.set_num_threads = set_num_threads  # type: ignore[attr-defined]
    torch.inference_mode = contextlib.nullcontext  # type: ignore[attr-defined]
    torch.nn = SimpleNamespace(Linear="Linear")  # type: ignore[attr-defined]
    torch.qint8 = "qint8"  # type: ignore[attr-defined]
    torch.ao = SimpleNamespace(  # type: ignore[attr-defined]
        quantization=SimpleNamespace(quantize_dynamic=quantize_dynamic))
    return torch
