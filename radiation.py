#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py
~~~~~~~~~~~~

• get_radiation(lat, lon, max_km=150) → dict | None

  Возвращает ближайшую радиационную дозу (µSv/h) и источник:
      {"dose": 0.11, "src": "eurdep"}
  или None, если в радиусе max_km нет валидных пунктов.

  Алгоритм источников — по приоритету:
    1) EURDEP (официальные станции ЕС, CSV-поток, обновляется ~ hourly)
    2) OpenRadiation (общественные датчики, REST JSON)

© 2025 VayboMeter — можно смело копировать / дорабатывать.
"""

from __future__ import annotations
import csv
import io
import json
import math
import time
from typing import Dict, Optional, Tuple, List

import requests

# ──────────────────────────── базовые константы ────────────────────────────
EURDEP_CSV_URL = (
    "https://remap.jrc.ec.europa.eu/data/latest_data.csv"  # ~15 МБ, но только 1-2 сек на GitHub CI
)
EURDEP_TIMEOUT = 15

OPENRAD_URL = (
    "https://www.openradiation.org/api/measurements?"
    "fields=lat,lng,value&size=3000"                      # ограничились последними 3k
)
OPENRAD_TIMEOUT = 15

# грубый кеш в памяти (живёт весь run workflow)
_CACHE: Dict[str, Tuple[float, float, float]] = {}
#                    key            lat    lon   dose_µSv


# ──────────────────────────── гео-утилиты ──────────────────────────────
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Возвращает расстояние между двумя координатами в километрах.
    """
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dl   = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ───────────────────────────── EURDEP ──────────────────────────────────
def _eurdep_fetch() -> List[Tuple[float, float, float]]:
    """
    Парсит CSV, возвращает список (lat, lon, dose_µSv/h).
    """
    resp = requests.get(EURDEP_CSV_URL, timeout=EURDEP_TIMEOUT)
    resp.raise_for_status()

    out: List[Tuple[float, float, float]] = []
    # EURDEP даёт в нЗв/ч  → делим на 1000
    for row in csv.DictReader(io.StringIO(resp.text)):
        try:
            lat  = float(row["LAT"])
            lon  = float(row["LON"])
            dose = float(row["RADIATION"]) / 1000.0
            if dose > 0:
                out.append((lat, lon, dose))
        except (KeyError, ValueError):
            continue
    return out


def _eurdep(lat: float, lon: float, max_km: float) -> Optional[Dict]:
    if "eurdep" not in _CACHE:
        try:
            _CACHE["eurdep"] = ("DATA",)  # метка, что уже пытались
            _CACHE["eurdep_list"] = _eurdep_fetch()
        except Exception:
            return None

    best: Tuple[float, float, float] | None = None
    for slat, slon, dose in _CACHE.get("eurdep_list", []):
        dist = _haversine(lat, lon, slat, slon)
        if dist <= max_km and (best is None or dist < _haversine(lat, lon, *best[:2])):
            best = (slat, slon, dose)

    if best:
        return {"dose": round(best[2], 3), "src": "eurdep"}
    return None


# ─────────────────────────── OpenRadiation ─────────────────────────────
def _openrad_fetch() -> List[Tuple[float, float, float]]:
    """
    Берём последние community-измерения, value в µSv/h.
    """
    resp = requests.get(OPENRAD_URL, timeout=OPENRAD_TIMEOUT)
    resp.raise_for_status()

    data = resp.json().get("hydra:member", [])
    out: List[Tuple[float, float, float]] = []
    for m in data:
        try:
            lat  = float(m["lat"])
            lon  = float(m["lng"])
            dose = float(m["value"])
            if dose > 0:
                out.append((lat, lon, dose))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _openrad(lat: float, lon: float, max_km: float) -> Optional[Dict]:
    if "openrad" not in _CACHE:
        try:
            _CACHE["openrad"] = ("DATA",)
            _CACHE["openrad_list"] = _openrad_fetch()
        except Exception:
            return None

    best: Tuple[float, float, float] | None = None
    for slat, slon, dose in _CACHE.get("openrad_list", []):
        dist = _haversine(lat, lon, slat, slon)
        if dist <= max_km and (best is None or dist < _haversine(lat, lon, *best[:2])):
            best = (slat, slon, dose)

    if best:
        return {"dose": round(best[2], 3), "src": "openradiation"}
    return None


# ──────────────────────────── публичная API ───────────────────────────
def get_radiation(lat: float, lon: float, max_km: float = 150) -> Optional[Dict]:
    """
    Ищет ближайшую станцию на расстоянии ≤ max_km км.
    При успехе:  {"dose": µSv/h, "src": "eurdep|openradiation"}
    При отсутствии — None.
    """
    # сначала пробуем EURDEP
    res = _eurdep(lat, lon, max_km)
    if res:
        return res

    # затем community-датчики
    return _openrad(lat, lon, max_km)


# для интерактивных тестов
if __name__ == "__main__":
    for city, (la, lo) in {
        "Калининград": (54.71, 20.45),
        "Лимассол":    (34.70, 33.02),
    }.items():
        print(city, get_radiation(la, lo))
