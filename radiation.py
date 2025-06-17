#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py
~~~~~~~~~~~~

get_radiation(lat, lon)  →  dict | None
    • dose      – γ-доза, μSv/h  (float, ≥ 0)
    • station   – название / код станции
    • age_min   – «сколько минут назад» обновилось значение
                  (None, если время не распознано)

Источник № 1: EURDEP near-real-time feed
https://remap.jrc.ec.europa.eu/api/v1/gamma

Алгоритм:
1) берём *самую близкую* станцию в радиусе ≤ 500 км;
2) читаем поле со значением дозы (API меняло имя несколько раз);
3) если значение валидное — возвращаем словарь, иначе None.
"""

from __future__ import annotations
import datetime as _dt
import hashlib, json, logging, os
from pathlib import Path
from typing import Dict, Any, Optional

from utils import _get                                # тонкая обёртка над requests

# ────────────────────────────── настройки ──────────────────────────────
EURDEP_URL  = "https://remap.jrc.ec.europa.eu/api/v1/gamma"
RADIUS_KM   = 500                                   # запас по радиусу
CACHE_DIR   = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(lat: float, lon: float) -> Path:
    """Ключ кеша = sha1(<lat>|<lon>)"""
    key = hashlib.sha1(f"{lat:.3f}|{lon:.3f}".encode()).hexdigest()[:16]
    return CACHE_DIR / f"radiation_{key}.json"

def _load_cache(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    p = _cache_path(lat, lon)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text("utf-8"))
        ts = _dt.datetime.fromisoformat(data["_cached_at"])
        if (_dt.datetime.utcnow() - ts).total_seconds() < 3600:  # 1 ч
            return {k: v for k, v in data.items() if k != "_cached_at"}
    except Exception:
        pass
    return None

def _save_cache(lat: float, lon: float, data: Dict[str, Any]) -> None:
    d = dict(data)
    d["_cached_at"] = _dt.datetime.utcnow().isoformat(timespec="seconds")
    _cache_path(lat, lon).write_text(json.dumps(d, ensure_ascii=False), "utf-8")

# ──────────────────────── EURDEP helper ───────────────────────────────
_CANDIDATE_VALUE_KEYS = (
    "last_value",            # старая схема
    "last_value_calibrated", # новая схема
    "last_value_f64",
    "value",
)

def _eurdep(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Возвращает {'dose','station','age_min'} или None.
    """
    resp = _get(
        EURDEP_URL,
        lat     = lat,
        lon     = lon,
        radius  = RADIUS_KM,
        limit   = 1,                       # ближайшая станция
        quality = "all",                   # на всякий случай
    )
    if not resp or not resp.get("data"):
        return None

    rec = resp["data"][0]

    # 1) значение дозы
    value: Optional[float] = None
    for k in _CANDIDATE_VALUE_KEYS:
        if k in rec and rec[k] not in (None, "", "-9999"):
            try:
                value = float(rec[k])
            except Exception:
                pass
        if value is not None:
            break
    if value is None or value < 0:
        return None

    # 2) время последнего обновления
    age_min: Optional[int] = None
    ts = rec.get("last_update") or rec.get("lastUpdate")
    if ts:
        try:
            t_utc = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = int((_dt.datetime.utcnow() - t_utc).total_seconds() // 60)
        except Exception:
            pass

    return {
        "dose":    value,
        "station": rec.get("station") or rec.get("station_code"),
        "age_min": age_min,
    }

# ───────────────────────── публичная функция ──────────────────────────
def get_radiation(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    >>> get_radiation(54.71, 20.45)   # Калининград
    {'dose': 0.11, 'station': 'Kaliningrad-01', 'age_min': 17}

    >>> get_radiation(34.70, 33.02)   # Лимассол
    None   # сети EURDEP пока нет на Кипре
    """
    # 0) быстрый кеш (на случай нескольких вызовов в день)
    if (cached := _load_cache(lat, lon)):
        return cached

    # 1) пробуем EURDEP
    try:
        data = _eurdep(lat, lon)
        if data:
            _save_cache(lat, lon, data)
            return data
    except Exception as e:
        logging.warning("EURDEP fetch error: %s", e)

    return None
