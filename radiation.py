#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py
~~~~~~~~~~~~

get_radiation(lat, lon)  →  dict | None
    • dose      – мгновенная γ-доза, μSv/h
    • station   – название / код станции-датчика
    • age_min   – «сколько минут назад» обновились данные
                  (None, если время не распознано)

Алгоритм: 
1) EURDEP near-real-time feed (remap.jrc.ec.europa.eu) – охватывает почти
   всю Европу и приграничные регионы, включая Калининград.
   Берём *одну* ближайшую станцию в радиусе ≤ 300 км.
2) при ошибке или отсутствии значений → возвращаем None.

Зависит только от utils._get (как и остальные модули бота).
"""

from __future__ import annotations
import logging, datetime as _dt
from typing import Dict, Any, Optional

from utils import _get   # тот же тонкий обёрточный fetch, что в проекте

# ─────────────────────── EURDEP ────────────────────────
EURDEP_URL = "https://remap.jrc.ec.europa.eu/api/v1/gamma"

def _eurdep(lat: float, lon: float, radius_km: int = 300) -> Optional[Dict[str, Any]]:
    """
    Ищем ближайший γ-датчик EURDEP в radius_km (по умолч. 300 км).
    Возвращаем {'dose', 'station', 'age_min'} или None.
    """
    resp = _get(
        EURDEP_URL,
        lat=lat,
        lon=lon,
        radius=radius_km,
        limit=1,          # нужен только самый близкий
    )
    if not resp or "data" not in resp or not resp["data"]:
        return None

    rec = resp["data"][0]
    try:
        val = float(rec["last_value"])
    except (KeyError, ValueError, TypeError):
        return None    # нет численного значения

    # Пытаемся оценить «возраст» измерения
    age_min: Optional[int] = None
    ts = rec.get("last_update")
    if ts:
        try:
            # API возвращает ISO-строку, чаще всего с 'Z' на конце
            t_utc = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = int((_dt.datetime.utcnow() - t_utc).total_seconds() // 60)
        except Exception:
            pass

    return {
        "dose":    val,
        "station": rec.get("station"),
        "age_min": age_min,
    }

# ─────────────────────── Публичный API модуля ────────────────────────
def get_radiation(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Возвращает dict с ключами 'dose', 'station', 'age_min' или None,
    если поблизости нет данных.  Доп. источники можно подключать
    ниже по схеме try/except, не трогая внешний интерфейс.
    """
    for src in (_eurdep,):           # можно добавить другие источники
        try:
            data = src(lat, lon)
            if data:
                return data
        except Exception as e:
            logging.warning("Radiation source %s failed: %s", src.__name__, e)

    return None                      # ничего не нашли
