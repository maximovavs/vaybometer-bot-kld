#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pollen.py
~~~~~~~~~
Получение данных о концентрациях пыльцы (берёзы, травы, амброзии) из Open-Meteo Air Quality API.

get_pollen(lat, lon) → None | {
  "tree":  float|None,   # birch_pollen
  "grass": float|None,   # grass_pollen
  "weed":  float|None,   # ragweed_pollen
  "risk":  "низкий"|"умеренный"|"высокий"|"экстремальный"|"н/д"
}

Все сетевые вызовы обёрнуты в try/except, используется общий таймаут HTTP_TIMEOUT (сек).
"""

from __future__ import annotations
import os
import math
import logging
from typing import Dict, Any, Optional, List, Union

from utils import _get  # ваша HTTP-обёртка

# ───────────────────────── Логирование / таймаут ────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

# ───────────────────────── Константы (дефолт) ───────────────────────────────
# По умолчанию — Лимассол (Кипр). Для Калининграда передавайте координаты явно.
DEFAULT_LAT = 34.707
DEFAULT_LON = 33.022

# ───────────────────────── Безопасная HTTP-обёртка ──────────────────────────
def _safe_http_get(url: str, **params) -> Optional[Dict[str, Any]]:
    """Пробует вызвать utils._get с таймаутом (если поддерживается). При ошибке → None."""
    try:
        try:
            return _get(url, timeout=REQUEST_TIMEOUT, **params)
        except TypeError:
            # если ваша _get не принимает timeout
            return _get(url, **params)
    except Exception as e:
        logging.warning("pollen: HTTP error: %s", e)
        return None

# ───────────────────────── Вспомогательные функции ──────────────────────────
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

def _pick_nearest_past_hour(times: List[str], values: List[Any]) -> Optional[float]:
    """
    Берёт значение по ближайшему ПРОШЕДШЕМУ часу (UTC). Если нет валидных — None.
    """
    if not times or not values or len(times) != len(values):
        return None
    try:
        # формат меток у Open-Meteo: 'YYYY-MM-DDTHH:MM'
        from time import gmtime, strftime
        now_iso = strftime("%Y-%m-%dT%H:00", gmtime())
        idxs = [i for i, t in enumerate(times) if isinstance(t, str) and t <= now_iso]
        if not idxs:
            return None
        v = values[max(idxs)]
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            return float(v)
    except Exception:
        pass
    return None

# ───────────────────────── Публичное API ────────────────────────────────────
def get_pollen(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> Optional[Dict[str, Any]]:
    """
    Запрашивает концентрации пыльцы (birch, grass, ragweed) из Open-Meteo Air Quality API.
    Возвращает None при любой ошибке или словарь с данными (см. шапку файла).
    """
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    "birch_pollen,grass_pollen,ragweed_pollen",
        "timezone":  "UTC",
    }

    j = _safe_http_get(url, **params)
    if not j or "hourly" not in j:
        logging.debug("pollen: нет hourly в ответе")
        return None

    try:
        h = j["hourly"]
        times: List[str] = h.get("time", []) or []
        birch: List[Union[float, None]]   = h.get("birch_pollen", [])   or []
        grass: List[Union[float, None]]   = h.get("grass_pollen", [])   or []
        ragweed: List[Union[float, None]] = h.get("ragweed_pollen", []) or []

        tree_val  = _pick_nearest_past_hour(times, birch)
        grass_val = _pick_nearest_past_hour(times, grass)
        weed_val  = _pick_nearest_past_hour(times, ragweed)

        # округлим до 1 знака, оставляя None как есть
        def _rnd(x: Optional[float]) -> Optional[float]:
            return round(float(x), 1) if isinstance(x, (int, float)) and math.isfinite(x) else None

        tree_r  = _rnd(tree_val)
        grass_r = _rnd(grass_val)
        weed_r  = _rnd(weed_val)

        # риск по максимальной доступной концентрации; если все None → "н/д"
        candidates = [v for v in (tree_r, grass_r, weed_r) if isinstance(v, (int, float))]
        max_val = max(candidates) if candidates else None

        return {
            "tree":  tree_r,
            "grass": grass_r,
            "weed":  weed_r,
            "risk":  _risk_level(max_val),
        }

    except Exception as e:
        logging.warning("pollen: parse error: %s", e)
        return None

# ───────────────────────── CLI ──────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Пыльца для Калининграда:")
    pprint(get_pollen(54.71, 20.45))
    print("\nПыльца (дефолт):")
    pprint(get_pollen())