#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pollen.py
~~~~~~~~~
Берёт пыльцу из Open-Meteo Air Quality API и возвращает:
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

# координаты Лимассола по умолчанию
LAT, LON = 34.707, 33.022

def _risk_level(val: Optional[float]) -> str:
    if val is None:
        return "н/д"
    if val <  10: return "низкий"
    if val <  30: return "умеренный"
    if val <  70: return "высокий"
    return "экстремальный"

def get_pollen(lat: float = LAT, lon: float = LON) -> Dict[str, Any]:
    empty = {"tree": None, "grass": None, "weed": None, "risk": "н/д"}

    j = _get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=lat, longitude=lon,
        hourly="birch_pollen,grass_pollen,ragweed_pollen",
        timezone="UTC"
    )
    if not j or "hourly" not in j:
        logging.warning("Pollen API: no data")
        return empty

    try:
        h = j["hourly"]
        tree_arr  = h.get("birch_pollen", [])
        grass_arr = h.get("grass_pollen", [])
        weed_arr  = h.get("ragweed_pollen", [])

        tree  = tree_arr[0]  if tree_arr  and tree_arr[0]  >= 0 else None
        grass = grass_arr[0] if grass_arr and grass_arr[0] >= 0 else None
        weed  = weed_arr[0]  if weed_arr  and weed_arr[0]  >= 0 else None

        # риски считаем от максимальной концентрации
        highest = max(x for x in (tree or 0, grass or 0, weed or 0))
        return {
            "tree":  round(tree,  1) if tree  is not None else None,
            "grass": round(grass, 1) if grass is not None else None,
            "weed":  round(weed,  1) if weed  is not None else None,
            "risk":  _risk_level(highest),
        }
    except Exception as e:
        logging.warning("Pollen parse error: %s", e)
        return empty

if __name__ == "__main__":
    from pprint import pprint
    pprint(get_pollen())
