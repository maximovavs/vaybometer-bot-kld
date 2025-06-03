#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pollen.py
~~~~~~~~~
Получение данных о концентрациях пыльцы (берёзы, травы, амброзии) из Open-Meteo Air Quality API.
Функция get_pollen возвращает либо None (если нет данных), либо словарь:
 {
   "tree":  <birch_pollen>  | None,
   "grass": <grass_pollen>  | None,
   "weed":  <ragweed_pollen>| None,
   "risk":  "низкий"|"умеренный"|"высокий"|"экстремальный"|"н/д"
 }
"""

import logging
from typing import Dict, Any, Optional

from utils import _get

# ───────────────────────── Constants ───────────────────────────────────────
# По умолчанию — Лимассол (Кипр). Для Калининграда лучше передавать lat, lon явно.
DEFAULT_LAT = 34.707   # Limassol
DEFAULT_LON = 33.022

# ────────────────────── Уровень риска пыльцы ──────────────────────────────
def _risk_level(val: Optional[float]) -> str:
    """
    По максимальной концентрации возвращает:
      < 10  → "низкий"
      < 30  → "умеренный"
      < 70  → "высокий"
      ≥ 70  → "экстремальный"
      None → "н/д"
    """
    if val is None:
        return "н/д"
    if val < 10:
        return "низкий"
    if val < 30:
        return "умеренный"
    if val < 70:
        return "высокий"
    return "экстремальный"


def get_pollen(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> Optional[Dict[str, Any]]:
    """
    Запрашивает концентрации пыльцы (birch, grass, ragweed) из Open-Meteo Air Quality API.
    Возвращает либо None (если нет данных или формат изменился), либо словарь:
      {
        "tree":  float | None,   # берёза (birch_pollen)
        "grass": float | None,   # трава (grass_pollen)
        "weed":  float | None,   # амброзия (ragweed_pollen)
        "risk":  str             # уровень риска: "низкий"/"умеренный"/"высокий"/"экстремальный"/"н/д"
      }
    """
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    "birch_pollen,grass_pollen,ragweed_pollen",
        "timezone":  "UTC",
    }

    j = _get(url, **params)
    if not j or "hourly" not in j:
        logging.warning("Pollen API: нет данных или формат ответа изменился")
        return None

    try:
        hourly = j["hourly"]
        tree_arr  = hourly.get("birch_pollen", [])
        grass_arr = hourly.get("grass_pollen", [])
        weed_arr  = hourly.get("ragweed_pollen", [])

        # Берём первый часовой отсчёт, если он ≥ 0
        tree = (
            float(tree_arr[0])
            if isinstance(tree_arr, list) and tree_arr and tree_arr[0] is not None and tree_arr[0] >= 0
            else None
        )
        grass = (
            float(grass_arr[0])
            if isinstance(grass_arr, list) and grass_arr and grass_arr[0] is not None and grass_arr[0] >= 0
            else None
        )
        weed = (
            float(weed_arr[0])
            if isinstance(weed_arr, list) and weed_arr and weed_arr[0] is not None and weed_arr[0] >= 0
            else None
        )

        # Рассчитываем риск по максимальной из доступных концентраций
        max_val = max(x for x in (tree or 0.0, grass or 0.0, weed or 0.0))
        return {
            "tree":  round(tree,  1) if tree  is not None else None,
            "grass": round(grass, 1) if grass is not None else None,
            "weed":  round(weed,  1) if weed  is not None else None,
            "risk":  _risk_level(max_val),
        }

    except Exception as e:
        logging.warning("Pollen parse error: %s", e)
        return None


if __name__ == "__main__":
    from pprint import pprint

    # Пример для Калининграда
    print("Пыльца для Калининграда:")
    pprint(get_pollen(54.71, 20.45))

    # Пример для Кипра (дефолтные координаты)
    print("\nПыльца для Кипра:")
    pprint(get_pollen())