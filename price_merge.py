"""Объединение двух карт лучших цен BYN: по каждому ключу берётся минимум (выгоднее для покупателя)."""
from __future__ import annotations

from typing import Dict, TypeVar

K = TypeVar("K")


def merge_min_byn(a: Dict[K, int], b: Dict[K, int]) -> Dict[K, int]:
    out: Dict[K, int] = {}
    for k in set(a) | set(b):
        va, vb = a.get(k), b.get(k)
        if va is not None and vb is not None:
            out[k] = min(va, vb)
        elif va is not None:
            out[k] = va
        else:
            out[k] = vb  # type: ignore[assignment] — vb задан, иначе k не в union с ценой
    return out
