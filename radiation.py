#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py  •  Функции для получения мощности дозы γ-излучения (µSv/h).

Используем открытый слой EURDEP (European Radiological Data Exchange Platform).
Почти все европейские страны публикуют туда данные почасово, в том числе:
    • Кипр — Nicosia
    • Литва — Клайпеда, Паланга (близко к Калининграду)

Функция get_radiation(lat, lon, radius_km=150) ищет ближайшую станцию
в указанном радиусе и возвращает последнее значение в µSv/h.
Если данных нет → None.
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List
import logging
import pendulum

from utils import _get            # в проекте уже есть HTTP-обёртка

# ---------------------------------------------------------------------------
# EURDEP “REM” public API (не документирована, но стабильна много лет)
EURDEP_URL = (
    "https://remap.jrc.ec.europa.eu/api/remap/monitoringStations/"
    "search/stationDistanceSorted"
)

def _nearest_station(lat: float, lon: float, radius_km: int = 150) -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь первой (ближайшей) станции или None.
    """
    try:
        data: List[Dict[str, Any]] = _get(
            EURDEP_URL,
            latitude = lat,
            longitude= lon,
            distance = radius_km,
            page     = 0,
            size     = 1,          # только ближайшая
            radiationType="doseRate"
        ) or []
        return data[0] if data else None
    except Exception as e:
        logging.warning("EURDEP station lookup error: %s", e)
        return None

def get_radiation(lat: float, lon: float, radius_km: int = 150) -> Optional[float]:
    """
    Возвращает последнюю мощность дозы в µSv/h или None.
    """
    st = _nearest_station(lat, lon, radius_km)
    if not st:
        return None

    # В объекте станции уже лежит последнее значение
    try:
        value = float(st.get("lastValue", "nan"))
        # EURDEP отдает нЗв/ч → переводим в µSv/h (1 µSv = 1000 nSv)
        return round(value / 1000.0, 3)
    except Exception as e:
        logging.warning("EURDEP value parse error: %s", e)
        return None

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Быстрый тест: Nicosia, Cyprus
    cy_lat, cy_lon = 35.17, 33.36
    val = get_radiation(cy_lat, cy_lon)
    print("Nicosia:", val, "µSv/h" if val is not None else "(нет данных)")

    # Клайпеда (ближайшая к Калининграду)
    lt_lat, lt_lon = 55.70, 21.13
    val = get_radiation(lt_lat, lt_lon)
    print("Klaipeda:", val, "µSv/h" if val is not None else "(нет данных)")
