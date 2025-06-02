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

Возвращаемый словарь:
{
    "freq":   float,      # Гц
    "amp":    float,      # pT
    "trend":  "↑"|"→"|"↓",
    "high":   bool,       # freq > 8.0 или amp > 100.0
    "cached": True|False, # источник — кэш или живые данные
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
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
import pendulum

# ─────────────────── Константы ──────────────────────────────

# Список возможных эндпоинтов GCI
GCI_URLS = [
    "https://gci-api.ucsd.edu/data/latest",  # пример зеркала
    "https://gci-api.com/sr/latest",         # запасной эндпоинт
]

# Путь к локальному JSON-файлу с историческими записями (список объектов)
CACHE_FILE = Path.home() / ".cache" / "vaybometer" / "schumann_hourly.json"
# Во многих реализациях амплитуда хранится в nT, но мы хотим pT, поэтому:
AMP_SCALE = 1  # если амплитуда в nT, ставьте 1000 → чтобы перевести в pT
# Сколько последних часов учитывать для расчёта тренда
TREND_WINDOW_H = 24
# Порог (в Гц) роста/падения для тренда
TREND_DELTA_P = 0.1

# ─────────────────── helpers ────────────────────────────────

def _compute_trend(values: List[float]) -> str:
    """
    Стрелка на основе отклонения последнего значения от среднего.
    Если delta ≥ TREND_DELTA_P → "↑", если delta ≤ -TREND_DELTA_P → "↓", иначе "→"
    """
    if len(values) < 2:
        return "→"
    avg = sum(values[:-1]) / (len(values) - 1)
    delta = values[-1] - avg
    if delta >= TREND_DELTA_P:
        return "↑"
    if delta <= -TREND_DELTA_P:
        return "↓"
    return "→"

def _parse_gci_payload(js: Dict[str, Any]) -> Tuple[float, float]:
    """
    Из ответа API GCI достаём последнюю частоту и амплитуду SR-1.
    Возможные структуры:
      1) {"sr1":{"freq":7.83,"amp":112.4}}
      2) {"data":[{"freq":7.83,"amp":112.4,"ts":...}, ...]}
    Возвращает (freq, amp_in_pT).
    """
    # 1) Современный формат: вложенный словарь under "sr1"
    if "sr1" in js:
        sr = js["sr1"]
        return float(sr["freq"]), float(sr["amp"]) * AMP_SCALE

    # 2) Формат-список: ключ "data" → список записей
    if "data" in js and isinstance(js["data"], list) and js["data"]:
        last = js["data"][-1]
        return float(last["freq"]), float(last["amp"]) * AMP_SCALE

    raise ValueError("Unsupported GCI JSON structure")

def _fetch_live() -> Optional[Dict[str, Any]]:
    """
    Пробует обратиться к зеркалам GCI. 
    При успешном парсинге возвращает словарь с текущими данными.
    """
    for url in GCI_URLS:
        try:
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            payload = r.json()
            freq, amp = _parse_gci_payload(payload)
            # Пока не используем историю для trend, выставим "→"
            return {
                "freq":  round(freq, 2),
                "amp":   round(amp, 1),
                "trend": "→",
                "high":  (freq > 8.0 or amp > 100.0),
                "cached": False,
            }
        except Exception:
            continue
    return None

def _from_cache() -> Optional[Dict[str, Any]]:
    """
    Берёт последние TREND_WINDOW_H записей из локального JSON (список объекта).
    Ожидаемый формат файла:
      [
        {"ts": 1748623012, "freq": 7.83, "amp": 0.48},
        {"ts": 1748624596, "freq": 7.83, "amp": -2.41},
        ...
      ]
    Берёт записи с наибольшими "ts", сортирует, берёт последние TREND_WINDOW_H элементов,
    вычисляет trend по списку freq и возвращает итоговый словарь.
    """
    if not CACHE_FILE.exists():
        return None

    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            return None

        # Оставляем только объекты с ключами "ts", "freq", "amp"
        filtered: List[Dict[str, Any]] = [
            item for item in raw
            if isinstance(item, dict) and "ts" in item and "freq" in item and "amp" in item
        ]
        if not filtered:
            return None

        # Сортируем по timestamp, берём последние TREND_WINDOW_H элементов
        sorted_by_ts = sorted(filtered, key=lambda x: x["ts"])
        window = sorted_by_ts[-TREND_WINDOW_H :]

        freqs = [float(entry["freq"]) for entry in window]
        amps  = [float(entry["amp"]) * AMP_SCALE for entry in window]

        if not freqs:
            return None

        trend = _compute_trend(freqs)
        last_freq = freqs[-1]
        last_amp  = amps[-1]
        return {
            "freq":   round(last_freq, 2),
            "amp":    round(last_amp, 1),
            "trend":  trend,
            "high":   (last_freq > 8.0 or last_amp > 100.0),
            "cached": True,
        }
    except Exception:
        return None

# ─────────────────── public API ─────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Возвращает актуальные данные SR-1:
     1) Пытается взять с живого GCI API (_fetch_live).
     2) Если не удалось, берёт из локального кэша (_from_cache).
     3) Если и кэша нет — возвращает {"msg": "no data"}.
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