#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic editorial voice helpers for Kaliningrad VayboMeter posts."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Iterable


KLD_VARIANTS = {
    "WARM_UV": [
        "день скорее летний, чем жаркий. В городе будет тепло, а Балтика сохранит прохладную поправку.",
        "тепло настоящее, но не тяжёлое — хороший день для обычных дел и прогулок.",
        "в городе будет комфортно, но высокий УФ всё равно потребует летней внимательности.",
    ],
    "WINDY_BALTIC": [
        "день с балтийским характером: в городе спокойно, а у воды всё ощущается сильнее.",
        "планы лучше держать гибкими — ветер может немного изменить настроение маршрута.",
        "с Балтикой сегодня лучше не спорить, а выбрать защищённое место.",
    ],
    "RAIN_WINDOWS": [
        "погода не отменяет прогулку, но может немного изменить её маршрут.",
        "сегодня полезно оставить погоде немного места для манёвра.",
        "запасной маршрут пригодится больше, чем жёсткое расписание.",
    ],
    "CALM": [
        "редкий день, когда погода почти не спорит с планами.",
        "можно планировать свободнее, только перед поездкой к морю всё равно стоит проверить ветер.",
    ],
}

KLD_EVENING_VARIANTS = {
    "WARM_UV": [
        "день будет скорее летним, чем жарким; Балтика сохранит прохладную поправку.",
        "в городе будет тепло, но высокий УФ потребует обычной летней внимательности.",
        "завтра достаточно выбрать удобное окно и не забыть про защиту от солнца.",
    ],
    "WINDY_BALTIC": [
        "у воды всё будет ощущаться сильнее, поэтому поездку к Балтике лучше решить утром.",
        "планы у моря лучше оставить гибкими и проверить направление ветра по факту.",
        "Балтика попросит защищённый маршрут и небольшой запас по одежде.",
    ],
    "RAIN_WINDOWS": [
        "погода может скорректировать маршрут, поэтому запасной вариант завтра пригодится.",
        "лучше оставить расписанию немного места для дождевого окна.",
        "прогулку отменять не обязательно, но маршрут стоит подтвердить утром.",
    ],
    "CALM": [
        "погода почти не спорит с планами, хотя ветер у моря всё равно лучше проверить.",
        "завтра можно планировать свободнее, сохранив небольшой балтийский запас.",
    ],
}

KLD_WEEKLY_VARIANTS = [
    "Держать планы гибкими и не воспринимать переменчивость как помеху. Иногда именно смена погоды помогает вовремя скорректировать маршрут.",
    "Неделя просит оставить место для импровизации. Не каждый хороший план обязан пройти точно по расписанию.",
    "Главный навык недели — быстро перестраиваться, не теряя общего направления.",
]


def deterministic_variant(region: str, date_value: Any, scenario: str, variants: Iterable[str]) -> str:
    choices = list(variants)
    if not choices:
        return ""
    seed = hashlib.sha256(f"{region}|{date_value}|{scenario}".encode("utf-8")).hexdigest()
    return choices[int(seed[:8], 16) % len(choices)]


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "н/д"):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _scenario(conditions: dict[str, Any]) -> str:
    gust = _num(conditions.get("gust"))
    wind = _num(conditions.get("wind"))
    max_temp = _num(conditions.get("max_temp"))
    uv = _num(conditions.get("uv"))

    if conditions.get("rain") or conditions.get("precipitation"):
        return "RAIN_WINDOWS"
    if conditions.get("wind") or isinstance(gust, (int, float)) and gust >= 8 or isinstance(wind, (int, float)) and wind >= 6:
        return "WINDY_BALTIC"
    if conditions.get("uv_high") or conditions.get("warm") or isinstance(uv, (int, float)) and uv >= 6 or isinstance(max_temp, (int, float)) and max_temp >= 20:
        return "WARM_UV"
    return "CALM"


def build_morning_human_line(region: str, date_value: Any, conditions: dict[str, Any]) -> str:
    scenario = _scenario(conditions)
    phrase = deterministic_variant(region, date_value, scenario, KLD_VARIANTS[scenario])
    return f"💬 По-человечески: {phrase}"


def build_evening_human_line(region: str, date_value: Any, conditions: dict[str, Any]) -> str:
    scenario = _scenario(conditions)
    phrase = deterministic_variant(region, date_value, f"EVENING_{scenario}", KLD_EVENING_VARIANTS[scenario])
    return f"💬 Настрой на завтра: {phrase}"


def build_weekly_meaning(region: str, start_date: date | str, metrics: dict[str, Any]) -> str:
    scenario = "WEEKLY_WIND" if _num(metrics.get("gust_max")) and float(metrics["gust_max"]) >= 8 else "WEEKLY"
    return deterministic_variant(region, start_date, scenario, KLD_WEEKLY_VARIANTS)
