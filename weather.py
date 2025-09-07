#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather.py
~~~~~~~~~~

1) OpenWeather One Call (v3 → v2) — если есть OWM_KEY
2) Open-Meteo (start_date / end_date = сегодня+завтра)
3) Фоллбэк — Open-Meteo «current_weather»

Дополнительно:
• day_night_stats(lat, lon, tz="UTC") → {"t_day_max","t_night_min","rh_avg","rh_min","rh_max"}
  Надёжная агрегация «день/ночь» на завтра:
    - если есть daily.sunrise/sunset — используем окно [sunrise..sunset] для «дня»;
    - «ночь» — [00:00..06:00] локального времени;
    - влажность — среднее/мин/макс по завтрашним 24 часам.
• fetch_tomorrow_temps(lat, lon, tz="UTC") → (t_day_max, t_night_min)
  Быстрый доступ к значениям «день/ночь» для рендера в post_common.py
"""

from __future__ import annotations
import os
import logging
from typing import Any, Dict, Optional, Tuple, List

import pendulum

from utils import _get  # HTTP-обёртка из utils.py

OWM_KEY = os.getenv("OWM_KEY")            # ключ OpenWeather, может быть None
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Единый сетевой таймаут (сек), можно переопределить переменной окружения HTTP_TIMEOUT
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ────────────────────────── Безопасный HTTP-обёртка с таймаутом ──────────────

def _safe_http_get(url: str, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Пытается вызвать utils._get с таймаутом. Если у _get нет аргумента timeout,
    повторно вызывает без него. Любые исключения логируются и возвращается None.
    """
    try:
        try:
            return _get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except TypeError:
            # на случай если твой _get не принимает timeout
            return _get(url, **kwargs)
    except Exception as e:
        logging.warning("_safe_http_get — HTTP error: %s", e)
        return None


# ────────────────────────── Вспомогательный запрос Open-Meteo ────────────────

def _openmeteo_hourly_daily(
    lat: float,
    lon: float,
    tz: str,
    start_date: str,
    end_date: str,
    need_sun: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь с hourly и daily полями на указанный диапазон дат (включая обе границы).
    Часовые метки приходят уже в локальной TZ, указанной в параметре timezone.
    """
    daily_list = ["temperature_2m_max", "temperature_2m_min", "weathercode"]
    if need_sun:
        daily_list += ["sunrise", "sunset"]

    hourly_list = [
        "temperature_2m",
        "relative_humidity_2m",
        "surface_pressure",
        "cloud_cover",
        "weathercode",
        "wind_speed_10m",
        "wind_direction_10m",
    ]

    j = _safe_http_get(
        OPEN_METEO_URL,
        latitude=lat,
        longitude=lon,
        timezone=tz or "UTC",
        start_date=start_date,
        end_date=end_date,
        current_weather="true",
        daily=",".join(daily_list),
        hourly=",".join(hourly_list),
    )
    if not j or "hourly" not in j or "daily" not in j:
        return None
    return j


# ────────────────────────── Агрегация день/ночь/влажность ────────────────────

def _filter_hours_for_date(
    times: List[str],
    values: List[Optional[float]],
    date_str: str,
    hour_from: int,
    hour_to: int,
) -> List[float]:
    """
    Берёт пары (time, value) за конкретную дату date_str ('YYYY-MM-DD'), фильтрует по часам [hour_from..hour_to] включительно,
    возвращает список валидных чисел.
    """
    out: List[float] = []
    for t, v in zip(times, values):
        if not isinstance(v, (int, float)):
            continue
        if not t or not t.startswith(date_str):
            continue
        try:
            hh = int(t[11:13])  # формат 'YYYY-MM-DDTHH:MM'
        except Exception:
            continue
        if hour_from <= hh <= hour_to:
            out.append(float(v))
    return out


def _all_hours_for_date(times: List[str], values: List[Optional[float]], date_str: str) -> List[float]:
    out: List[float] = []
    for t, v in zip(times, values):
        if not isinstance(v, (int, float)):
            continue
        if t and t.startswith(date_str):
            out.append(float(v))
    return out


def day_night_stats(
    lat: float,
    lon: float,
    tz: str | None = "UTC",
) -> Dict[str, Optional[float]]:
    """
    Считает:
      t_day_max   — максимум температуры за «дневное» окно (sunrise..sunset; если нет — 09:00..18:00),
      t_night_min — минимум температуры за «ночное» окно (00:00..06:00),
      rh_avg/min/max — по всем часам завтрашней даты.

    Возвращает словарь со значениями или None, если данных не хватило.
    """
    tz_name = tz or "UTC"
    now = pendulum.now(tz_name)
    tomorrow = now.add(days=1).date().to_date_string()

    j = _openmeteo_hourly_daily(
        lat=lat,
        lon=lon,
        tz=tz_name,
        start_date=tomorrow,
        end_date=tomorrow,
        need_sun=True,
    )
    if not j:
        logging.warning("day_night_stats — Open-Meteo не вернул данные")
        return {"t_day_max": None, "t_night_min": None, "rh_avg": None, "rh_min": None, "rh_max": None}

    hourly = j.get("hourly", {}) or {}
    daily = j.get("daily", {}) or {}

    times: List[str] = hourly.get("time", []) or []
    temps: List[Optional[float]] = hourly.get("temperature_2m", []) or []
    rhs: List[Optional[float]] = hourly.get("relative_humidity_2m", []) or []

    # 1) Окно дня
    sunrise_list = daily.get("sunrise", []) or []
    sunset_list = daily.get("sunset", []) or []
    if sunrise_list and sunset_list:
        # строки вида 'YYYY-MM-DDTHH:MM'
        try:
            sr_hh = int(sunrise_list[0][11:13])
            ss_hh = int(sunset_list[0][11:13])
            day_from, day_to = sr_hh, ss_hh
        except Exception:
            day_from, day_to = 9, 18
    else:
        day_from, day_to = 9, 18

    day_vals = _filter_hours_for_date(times, temps, tomorrow, day_from, day_to)

    # 2) Окно ночи (фиксированное)
    night_vals = _filter_hours_for_date(times, temps, tomorrow, 0, 6)

    # 3) Влажность по всем 24 часам
    rh_vals_all = _all_hours_for_date(times, rhs, tomorrow)

    def _safe_max(arr: List[float]) -> Optional[float]:
        return max(arr) if arr else None

    def _safe_min(arr: List[float]) -> Optional[float]:
        return min(arr) if arr else None

    def _safe_avg(arr: List[float]) -> Optional[float]:
        return (sum(arr) / len(arr)) if arr else None

    t_day_max = _safe_max(day_vals)
    t_night_min = _safe_min(night_vals)
    rh_avg = _safe_avg(rh_vals_all)
    rh_min = _safe_min(rh_vals_all)
    rh_max = _safe_max(rh_vals_all)

    # Бэкап на daily, если вдруг hourly пуст
    try:
        if t_day_max is None and daily.get("temperature_2m_max"):
            t_day_max = float(daily["temperature_2m_max"][0])
    except Exception:
        pass
    try:
        if t_night_min is None and daily.get("temperature_2m_min"):
            t_night_min = float(daily["temperature_2m_min"][0])
    except Exception:
        pass

    return {
        "t_day_max": t_day_max,
        "t_night_min": t_night_min,
        "rh_avg": rh_avg,
        "rh_min": rh_min,
        "rh_max": rh_max,
    }


# ─────────────────────────── legacy API: tmax/tmin ────────────────────────────

def fetch_tomorrow_temps(
    lat: float,
    lon: float,
    tz: str | None = "UTC"
) -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (t_day_max, t_night_min) на завтра.

    Логика:
      1) Пробуем взять из hourly с окнами (sunrise..sunset / ночь 00–06).
      2) Если hourly пустой — используем daily.temperature_2m_max/min.
      3) Если и daily нет — возвращаем (None, None).
    """
    stats = day_night_stats(lat, lon, tz=tz)
    t_day_max = stats.get("t_day_max")
    t_night_min = stats.get("t_night_min")

    # Явный фоллбэк на чистый daily, если вдруг day_night_stats ничего не смог
    if t_day_max is None or t_night_min is None:
        tomorrow = pendulum.now(tz or "UTC").add(days=1).to_date_string()
        j = None
        try:
            j = _openmeteo_hourly_daily(
                lat=lat, lon=lon, tz=tz or "UTC",
                start_date=tomorrow, end_date=tomorrow, need_sun=False
            )
        except Exception as e:
            logging.warning("fetch_tomorrow_temps — HTTP error (fallback): %s", e)

        if j and "daily" in j:
            d = j.get("daily", {}) or {}
            try:
                if t_day_max is None and d.get("temperature_2m_max"):
                    t_day_max = float((d["temperature_2m_max"] or [None])[0])
            except Exception:
                pass
            try:
                if t_night_min is None and d.get("temperature_2m_min"):
                    t_night_min = float((d["temperature_2m_min"] or [None])[0])
            except Exception:
                pass

    if t_day_max is None or t_night_min is None:
        logging.warning("fetch_tomorrow_temps — не удалось получить t_day_max/t_night_min")
    return t_day_max, t_night_min


# ───────────────────────── OpenWeather/Open-Meteo сводки ─────────────────────

def _openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Получение прогноза через OpenWeather One Call (v3 или v2).
    Возвращает структуру с ключами "current", "hourly", "daily", "strong_wind", "fog_alert",
    или None при ошибке.
    """
    if not OWM_KEY:
        return None

    for version in ("3.0", "2.5"):
        ow = _safe_http_get(
            f"https://api.openweathermap.org/data/{version}/onecall",
            lat=lat,
            lon=lon,
            appid=OWM_KEY,
            units="metric",
            exclude="minutely,hourly,alerts",
        )
        if not ow or "current" not in ow:
            logging.debug("_openweather — нет 'current' (v%s)", version)
            continue

        try:
            cur = ow.get("current", {}) or {}
            # unify current
            wind_speed_ms = cur.get("wind_speed")
            windspeed_kmh = (wind_speed_ms * 3.6) if isinstance(wind_speed_ms, (int, float)) else None

            unified_current = {
                "temperature":    cur.get("temp"),
                "pressure":       cur.get("pressure"),
                "clouds":         cur.get("clouds", 0),
                # В проекте используем км/ч → приводим из м/с
                "windspeed":      windspeed_kmh,
                "winddirection":  cur.get("wind_deg"),
                # weathercode ← id из weather[0]
                "weathercode":    (cur.get("weather", [{}])[0] or {}).get("id"),
            }

            # hourly.* заполним минимальным набором для единообразия
            unified_hourly = {
                "surface_pressure":    [unified_current.get("pressure")],
                "cloud_cover":         [unified_current.get("clouds")],
                "weathercode":         [unified_current.get("weathercode")],
                "wind_speed_10m":      [unified_current.get("windspeed")],
                "wind_direction_10m":  [unified_current.get("winddirection")],
            }

            temp_val = unified_current.get("temperature")
            weather_val = unified_current.get("weathercode")
            unified_daily = {
                "temperature_2m_max": [temp_val, temp_val],
                "temperature_2m_min": [temp_val, temp_val],
                "weathercode":        [weather_val, weather_val],
            }

            ow["current"] = unified_current
            ow["hourly"] = unified_hourly
            ow["daily"] = unified_daily
            # сильный ветер по км/ч
            ow["strong_wind"] = (unified_current.get("windspeed") or 0) > 30.0
            ow["fog_alert"] = bool(unified_current.get("weathercode") in (45, 48))

            return ow
        except Exception as e:
            logging.warning("_openweather — ошибка обработки данных: %s", e)
            continue

    return None


def _openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Запрос прогноза через Open-Meteo (полные daily + hourly + current).
    Возвращает структуру:
      {
        "current": { … },
        "hourly": { "surface_pressure": [...], "cloud_cover": [...], … },
        "daily": { "temperature_2m_max": [...], "temperature_2m_min": [...], "weathercode": [...] },
        "strong_wind": bool,
        "fog_alert": bool
      }
    или None при ошибке/отсутствии полей.
    """
    today = pendulum.today("UTC").to_date_string()
    tomorrow = pendulum.today("UTC").add(days=1).to_date_string()

    om = _safe_http_get(
        OPEN_METEO_URL,
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        start_date=today,
        end_date=tomorrow,
        current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed_10m,wind_direction_10m",
    )
    if not om or "current_weather" not in om or "daily" not in om or "hourly" not in om:
        logging.debug("_openmeteo — отсутствуют нужные ключи")
        return None

    try:
        cw = om.get("current_weather", {}) or {}
        cur_pressure = (om.get("hourly", {}).get("surface_pressure", [None]) or [None])[0]
        cur_clouds = (om.get("hourly", {}).get("cloud_cover", [None]) or [None])[0]

        unified_current = {
            "temperature":    cw.get("temperature"),
            "pressure":       cur_pressure if isinstance(cur_pressure, (int, float)) else None,
            "clouds":         cur_clouds if isinstance(cur_clouds, (int, float)) else None,
            "windspeed":      cw.get("windspeed"),     # у Open-Meteo это км/ч
            "winddirection":  cw.get("winddirection"),
            "weathercode":    cw.get("weathercode"),
        }

        om["current"] = unified_current
        om["strong_wind"] = (isinstance(unified_current.get("windspeed"), (int, float))
                             and unified_current["windspeed"] > 30.0)
        om["fog_alert"] = (om.get("daily", {}).get("weathercode", [0])[0] in (45, 48))

        return om
    except Exception as e:
        logging.warning("_openmeteo — ошибка обработки данных: %s", e)
        return None


def _openmeteo_current_only(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Фоллбэк: только текущая погода через Open-Meteo.
    Возвращает структуру того же формата (daily и hourly дублируют current).
    """
    om = _safe_http_get(
        OPEN_METEO_URL,
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        current_weather="true",
    )
    if not om or "current_weather" not in om:
        logging.debug("_openmeteo_current_only — нет 'current_weather'")
        return None

    try:
        cw = om.get("current_weather", {}) or {}
        unified_current = {
            "temperature":    cw.get("temperature"),
            "pressure":       None,                 # в current_weather нет давления
            "clouds":         None,                 # и облачности
            "windspeed":      cw.get("windspeed"),  # км/ч
            "winddirection":  cw.get("winddirection"),
            "weathercode":    cw.get("weathercode"),
        }
        om["current"] = unified_current

        # Дублируем текущие данные для daily и hourly
        om["daily"] = {
            "temperature_2m_max": [unified_current["temperature"], unified_current["temperature"]],
            "temperature_2m_min": [unified_current["temperature"], unified_current["temperature"]],
            "weathercode":        [unified_current["weathercode"], unified_current["weathercode"]],
        }
        om["hourly"] = {
            "surface_pressure":    [unified_current["pressure"]],
            "cloud_cover":         [unified_current["clouds"]],
            "weathercode":         [unified_current["weathercode"]],
            "wind_speed_10m":      [unified_current["windspeed"]],
            "wind_direction_10m":  [unified_current["winddirection"]],
        }

        om["strong_wind"] = (isinstance(unified_current.get("windspeed"), (int, float))
                             and unified_current["windspeed"] > 30.0)
        om["fog_alert"] = (unified_current.get("weathercode") in (45, 48))
        return om
    except Exception as e:
        logging.warning("_openmeteo_current_only — ошибка формирования данных: %s", e)
        return None


def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Возвращает единый словарь прогноза, стараясь:
      1) _openweather
      2) _openmeteo
      3) _openmeteo_current_only
    или None, если ни один источник не дал правильного результата.
    """
    for fn in (_openweather, _openmeteo, _openmeteo_current_only):
        try:
            data = fn(lat, lon)
        except Exception as e:
            logging.warning("get_weather — исключение в %s: %s", fn.__name__, e)
            data = None

        if data:
            return data

    return None