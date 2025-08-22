#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — унифицированный модуль для КЛД и Кипра.

Что умеет:
• Берёт SR‑1 из GCI (несколько зеркал) → freq (Гц), amp (pT).
• Фоллбэк: локальный кэш ~/.cache/vaybometer/schumann_hourly.json
  — поддерживает ОБА формата:
    1) список: [{"ts": 1692700800, "freq": 7.83, "amp": 12.3, "h7_amp": 0.05}, ...]
    2) словарь по часам: {"2025-08-21T15": {"freq": 7.83, "amp": 12.3, "h7_amp": 0.05}, ...}
• Тренд по частоте за окно последних 24 точек: ↑/→/↓ (порог 0.1 Гц).
• Опционально: 7‑я гармоника — поле h7_amp (pT) и флаг h7_spike (⚡) — если есть данные.
  Всплеск детектим по медиане и MAD последних 48 h7_amp (last > median + 3*MAD и > 0.2 pT).

Возвращает словарь:
{
  "freq": float|None,
  "amp":  float|None,
  "trend": "↑"|"→"|"↓",
  "high": bool,          # freq > 8.0 или amp > 100.0
  "cached": bool,        # данные из кэша?
  "h7_amp": float,       # если есть
  "h7_spike": bool       # если смогли посчитать
}
или {"msg": "no data"} при полном отсутствии.

Зависимости: requests.
"""

from __future__ import annotations
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

__all__ = ("get_schumann",)

# ───────────────────── Константы ─────────────────────

# Зеркала GCI (примеры; некоторые могут быть недоступны — пройдём по списку)
GCI_URLS = [
    "https://gci-api.ucsd.edu/data/latest",  # формат: {"data":[{"freq":..,"amp":..,"ts":..}, ...]}
    "https://gci-api.com/sr/latest",         # формат: {"sr1":{"freq":..,"amp":..}}
]

# Локальный кэш
CACHE_FILE = Path.home() / ".cache" / "vaybometer" / "schumann_hourly.json"

AMP_SCALE = 1          # если амплитуда в nT — поставь 1000
TREND_WINDOW = 24      # сколько точек берём для тренда по freq
TREND_DELTA = 0.1      # порог для стрелки тренда в Гц
H7_WINDOW = 48         # окно для робастной оценки всплесков 7-й гармоники
H7_MIN_ABS = 0.2       # нижний порог шума по амплитуде 7-й гармоники (pT)

# ───────────────────── Вспомогательные ─────────────────────

def _trend_arrow(values: List[float]) -> str:
    if len(values) < 2:
        return "→"
    avg = sum(values[:-1]) / (len(values) - 1)
    d = values[-1] - avg
    if d >= TREND_DELTA:
        return "↑"
    if d <= -TREND_DELTA:
        return "↓"
    return "→"

def _parse_gci_payload(js: Dict[str, Any]) -> Tuple[float, float]:
    # Вариант 1: {"sr1":{"freq":7.83,"amp":112.4}}
    if isinstance(js.get("sr1"), dict):
        sr = js["sr1"]
        return float(sr["freq"]), float(sr["amp"]) * AMP_SCALE
    # Вариант 2: {"data":[ {...,"freq":..,"amp":..}, ... ]}
    data = js.get("data")
    if isinstance(data, list) and data:
        last = data[-1]
        return float(last["freq"]), float(last["amp"]) * AMP_SCALE
    raise ValueError("Unsupported GCI JSON structure")

def _fetch_live() -> Optional[Dict[str, Any]]:
    for url in GCI_URLS:
        try:
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            freq, amp = _parse_gci_payload(r.json())
            return {
                "freq": round(float(freq), 2),
                "amp":  round(float(amp), 1),
                "trend": "→",  # без истории
                "high": (freq > 8.0 or amp > 100.0),
                "cached": False,
            }
        except Exception:
            continue
    return None

def _read_cache_raw() -> Optional[Any]:
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

def _cache_to_series(raw: Any) -> Tuple[List[float], List[float], List[float]]:
    """
    Приводит обе возможные структуры к трем рядам одинаковой длины:
      freqs[], amps[], h7amps[]
    (могут быть пустыми).
    Формат 1 (список): [{"ts":..,"freq":..,"amp":..,"h7_amp":..}, ...]
    Формат 2 (словарь): {"YYYY-MM-DDTHH": {"freq":..,"amp":..,"h7_amp":..}, ...}
    """
    freqs: List[float] = []
    amps: List[float] = []
    h7s: List[float] = []

    # Список записей
    if isinstance(raw, list):
        # Отсортируем по ts если есть, иначе как есть
        try:
            raw = sorted(raw, key=lambda x: x.get("ts", 0))
        except Exception:
            pass
        for item in raw:
            if not isinstance(item, dict):
                continue
            f = item.get("freq")
            a = item.get("amp")
            h7 = item.get("h7_amp")
            if isinstance(f, (int, float)) and isinstance(a, (int, float)):
                freqs.append(float(f))
                amps.append(float(a) * AMP_SCALE)
                if isinstance(h7, (int, float)):
                    h7s.append(float(h7))
                else:
                    h7s.append(float("nan"))
        return freqs, amps, h7s

    # Словарь по часам
    if isinstance(raw, dict):
        try:
            items = sorted(raw.items(), key=lambda kv: kv[0])  # по ключу-часу
        except Exception:
            items = list(raw.items())
        for _, v in items:
            if not isinstance(v, dict):
                continue
            f = v.get("freq")
            a = v.get("amp")
            h7 = v.get("h7_amp")
            if isinstance(f, (int, float)) and isinstance(a, (int, float)):
                freqs.append(float(f))
                amps.append(float(a) * AMP_SCALE)
                if isinstance(h7, (int, float)):
                    h7s.append(float(h7))
                else:
                    h7s.append(float("nan"))
        return freqs, amps, h7s

    return freqs, amps, h7s

def _h7_spike(history: List[float], last: float) -> Optional[bool]:
    """
    Робастная детекция всплеска: last > median + 3*MAD и last > H7_MIN_ABS.
    История — без NaN.
    """
    vals = [v for v in history if isinstance(v, (int, float))]
    if len(vals) < 12 or not isinstance(last, (int, float)):
        return None
    med = statistics.median(vals)
    mad = statistics.median([abs(x - med) for x in vals]) or 0.01
    return bool(last > med + 3.0 * mad and last > H7_MIN_ABS)

def _from_cache() -> Optional[Dict[str, Any]]:
    raw = _read_cache_raw()
    if raw is None:
        return None
    freqs, amps, h7s = _cache_to_series(raw)
    if not freqs:
        return None

    # берём последние TREND_WINDOW точек
    freqs_w = freqs[-TREND_WINDOW:]
    amps_w  = amps[-TREND_WINDOW:]
    trend = _trend_arrow(freqs_w)

    out: Dict[str, Any] = {
        "freq":  round(float(freqs_w[-1]), 2),
        "amp":   round(float(amps_w[-1]), 1) if amps_w else None,
        "trend": trend,
        "high":  (freqs_w[-1] > 8.0 or (amps_w and amps_w[-1] > 100.0)),
        "cached": True,
    }

    # 7-я гармоника: возьмём последнюю ненан
    if h7s:
        # заменим NaN на None
        h7_clean = [v for v in h7s if isinstance(v, (int, float))]
        if h7_clean:
            h7_last = h7_clean[-1]
            out["h7_amp"] = round(float(h7_last), 3)
            hist = h7_clean[-H7_WINDOW:-1] if len(h7_clean) > 1 else []
            spike = _h7_spike(hist, h7_last)
            if spike is not None:
                out["h7_spike"] = spike

    return out

# ───────────────────── Публичное API ─────────────────────

def get_schumann() -> Dict[str, Any]:
    """
    1) пробуем получить live с GCI;
    2) если не вышло — читаем локальный кэш (любой из двух форматов);
    3) если совсем пусто — {"msg": "no data"}.
    """
    live = _fetch_live()
    if live:
        return live
    cached = _from_cache()
    if cached:
        return cached
    return {"msg": "no data"}

# ───────────────────── CLI ─────────────────────
if __name__ == "__main__":
    import pprint
    pprint.pp(get_schumann())
