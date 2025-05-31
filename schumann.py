#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
schumann.py
~~~~~~~~~~~
Получает текущие значения резонанса Шумана SR-1 и
возвращает единый словарь → удобный для post.py.

Порядок источников  
1. 🎯 GCI API — `https://gci-api.com/sr/` (часть зеркал без CORS).  
2. 📄 Локальный кэш `~/.cache/vaybometer/schumann_hourly.json`
   (писать туда можно отдельным кроном).

Формат возвращаемого словаря:

{
    "freq"   : 7.83,      # Гц
    "amp"    : 112.4,     # pT (одна шкала для всех источников!)
    "trend"  : "↑|→|↓",   # сравниваем с усреднением за 24 ч
    "high"   : True|False,# частота > 8 Гц **или** амплитуда > 100 pT
    "cached" : True|False,# данные из кэша?
}
или
{
    "msg": "no data"
}
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import requests
import pendulum

# ─────────────────── Константы ──────────────────────────────
GCI_URLS = [
    "https://gci-api.ucsd.edu/data/latest",        # пример зеркала
    "https://gci-api.com/sr/latest",               # вымышленный энд-поинт
]

CACHE_FILE = Path.home() / ".cache" / "vaybometer" / "schumann_hourly.json"
# используем pT везде
AMP_SCALE = 1          # если в файле nanoT, ставьте 1000
TREND_WINDOW_H  = 24   # часов для тренда
TREND_DELTA_P   = 0.1  # порог изменения частоты

# ─────────────────── helpers ────────────────────────────────
def _compute_trend(values: List[float]) -> str:
    """Стрелка на основе отклонения последнего значения от среднего."""
    if len(values) < 2:
        return "→"
    avg = sum(values[:-1]) / (len(values) - 1)
    delta = values[-1] - avg
    if   delta >= TREND_DELTA_P:
        return "↑"
    elif delta <= -TREND_DELTA_P:
        return "↓"
    return "→"


def _parse_gci_payload(js: Dict[str, Any]) -> Tuple[float, float]:
    """
    Из ответа API достаём последнюю частоту и амплитуду SR-1.
    Структура может отличаться у разных зеркал — подстраховываемся.
    """
    # пример: {"sr1":{"freq":7.83,"amp":112.4}}
    if "sr1" in js:
        sr = js["sr1"]
        return float(sr["freq"]), float(sr["amp"]) * AMP_SCALE

    # пример: {"data":[{"freq":7.83,"amp":112.4,"ts":...}, ...]}
    if "data" in js and js["data"]:
        rec = js["data"][-1]
        return float(rec["freq"]), float(rec["amp"]) * AMP_SCALE

    raise ValueError("Unsupported GCI JSON structure")


def _fetch_live() -> Dict[str, Any] | None:
    """Пробуем зеркала GCI. При успехе отдаём dict."""
    for url in GCI_URLS:
        try:
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            freq, amp = _parse_gci_payload(r.json())
            # тренд вычислим, взяв ещё 23 предыдущих значений,
            # если они есть в ответе (опционально). Здесь упрощённо —
            trend = "→"
            return {
                "freq": round(freq, 2),
                "amp":  round(amp, 1),
                "trend": trend,
                "high": freq > 8.0 or amp > 100.0,
                "cached": False,
            }
        except Exception:
            continue
    return None


def _from_cache() -> Dict[str, Any] | None:
    """Берём последние 24 ч из локального файла."""
    if not CACHE_FILE.exists():
        return None

    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        # ожидаем {"YYYY-MM-DDTHH": {"freq":7.83,"amp":112.4}, ...}
        rows = sorted(data.items())[-TREND_WINDOW_H:]
        freqs = [float(v["freq"]) for _, v in rows]
        amps  = [float(v["amp"]) * AMP_SCALE for _, v in rows]

        if not freqs:
            return None

        trend = _compute_trend(freqs)
        last_freq = freqs[-1]
        last_amp  = amps[-1]
        return {
            "freq": round(last_freq, 2),
            "amp":  round(last_amp, 1),
            "trend": trend,
            "high": last_freq > 8.0 or last_amp > 100.0,
            "cached": True,
        }
    except Exception:
        return None


# ─────────────────── public API ─────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Возвращает актуальные данные SR-1.
    Сначала пробуем живой API, потом локальный кэш.
    """
    live = _fetch_live()
    if live:
        return live

    cached = _from_cache()
    if cached:
        return cached

    return {"msg": "no data"}


# ───────────── Проверка (ручной запуск) ─────────────────────
if __name__ == "__main__":
    res = get_schumann()
    print(res)
