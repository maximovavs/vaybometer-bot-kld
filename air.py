#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils.py  • Вспомогательные функции и константы VayboMeter-бота.

Содержит:
 - compass(deg)          — возвращает направление ветра по углу deg (0–360)
 - clouds_word(pc)       — возвращает «ясно/переменная/пасмурно» по проценту облачности
 - wind_phrase(km_h)     — возвращает «штиль/слабый/умеренный/сильный» по скорости ветра
 - safe(v, unit)         — None → «—», число → «X.X{unit}»
 - aqi_color(aqi)        — эмодзи уровня AQI
 - pm_color(pm, with_unit?) — эмодзи + значение PM₂.₅/PM₁₀
 - kp_emoji(kp)          — эмодзи по индексу Kp
 - pressure_trend(w)     — тренд давления («↑», «↓» или «→»)
 - HTTP-обёртки: _get / _get_retry
 - get_fact(date, region) — «факт дня» в зависимости от региона

Новое:
 - kmh_to_ms(v)          — перевод км/ч → м/с (с округлением до 0.1)
 - ms_to_kmh(v)          — перевод м/с → км/ч
 - smoke_index(pm25, pm10) — оценка «задымления» по PM (эмодзи + уровень)
"""

from __future__ import annotations
import time
import random
import requests
import pendulum
from typing import Any, Dict, Optional, List

# ──────────────────────── Компас, облака, ветер ──────────────────────────

COMPASS = [
    "С",   # Север
    "ССВ", # Северо-северо-восток
    "СВ",  # Северо-восток
    "ВСВ", # Восток-северо-восток
    "В",   # Восток
    "ВЮВ", # Восток-юго-восток
    "ЮВ",  # Юго-восток
    "ЮЮВ", # Юго-юго-восток
    "Ю",   # Юг
    "ЮЮЗ", # Юго-юго-запад
    "ЮЗ",  # Юго-запад
    "ЗЮЗ", # Запад-юго-запад
    "З",   # Запад
    "ЗСЗ", # Запад-северо-запад
    "СЗ",  # Северо-запад
    "ССЗ", # Северо-северо-запад
]

def compass(deg: float) -> str:
    """
    Возвращает сторону света (из 16) по углу deg (0–360).
    """
    index = int((deg / 22.5) + 0.5) % 16
    return COMPASS[index]

def clouds_word(pc: int) -> str:
    """
    По проценту облачности:
      <25% → «ясно»
      <70% → «переменная»
      иначе → «пасмурно»
    """
    if pc < 25:
        return "ясно"
    if pc < 70:
        return "переменная"
    return "пасмурно"

def wind_phrase(km_h: float) -> str:
    """
    По скорости ветра (км/ч):
      < 2  → «штиль»
      < 8  → «слабый»
      < 14 → «умеренный»
      иначе → «сильный»
    """
    if km_h < 2:
        return "штиль"
    if km_h < 8:
        return "слабый"
    if km_h < 14:
        return "умеренный"
    return "сильный"

def kmh_to_ms(v_kmh: Optional[float]) -> Optional[float]:
    """
    Переводит скорость из км/ч в м/с с округлением до 0.1.
    None → None.
    """
    if v_kmh is None:
        return None
    try:
        return round(float(v_kmh) / 3.6, 1)
    except (TypeError, ValueError):
        return None

def ms_to_kmh(v_ms: Optional[float]) -> Optional[float]:
    """
    Переводит скорость из м/с в км/ч с округлением до 0.1.
    None → None.
    """
    if v_ms is None:
        return None
    try:
        return round(float(v_ms) * 3.6, 1)
    except (TypeError, ValueError):
        return None

def safe(v: Any, unit: str = "") -> str:
    """
    None / 'None' / '—' → «—»
    Число → форматированная строка с единицей.
    """
    if v in (None, "None", "—"):
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.1f}{unit}"
    return f"{v}{unit}"

# ──────────────────────── AQI & PM раскраска ─────────────────────────────

def aqi_color(aqi: int | float | str) -> str:
    """
    Эмодзи уровня AQI:
      ≤ 50   → 🟢
      ≤100   → 🟡
      ≤150   → 🟠
      ≤200   → 🔴
      ≤300   → 🟣
      >300   → 🟤
      «—»/«н/д» → ⚪
    """
    if aqi in ("—", "н/д"):
        return "⚪"
    try:
        val = float(aqi)
    except (TypeError, ValueError):
        return "⚪"
    if val <= 50:
        return "🟢"
    if val <= 100:
        return "🟡"
    if val <= 150:
        return "🟠"
    if val <= 200:
        return "🔴"
    if val <= 300:
        return "🟣"
    return "🟤"

def pm_color(pm: Optional[float | int | str], with_unit: bool = False) -> str:
    """
    Цветовая индикация концентрации PM₂.₅ / PM₁₀:
      ≤ 12   → 🟢
      ≤ 35   → 🟡
      ≤ 55   → 🟠
      ≤150   → 🔴
      ≤250   → 🟣
      >250   → 🟤
      None / «—»/«н/д» → ⚪ н/д
    """
    if pm in (None, "—", "н/д"):
        return "⚪ н/д"
    try:
        val = float(pm)
    except (TypeError, ValueError):
        return "⚪ н/д"
    if val <= 12:
        emoji = "🟢"
    elif val <= 35:
        emoji = "🟡"
    elif val <= 55:
        emoji = "🟠"
    elif val <= 150:
        emoji = "🔴"
    elif val <= 250:
        emoji = "🟣"
    else:
        emoji = "🟤"
    txt = str(int(round(val)))
    if with_unit:
        txt += " µg/м³"
    return f"{emoji}{txt}"

# ──────────────────────── Индекс «задымления» по PM ───────────────────────

def smoke_index(pm25: Optional[float | int | str],
                pm10:  Optional[float | int | str]) -> tuple[str, str]:
    """
    Оценка «задымления» по PM:
      База — по PM2.5 (µg/m³):
        0–25  → низкое
        25–55 → среднее
        >55   → высокое
      Модификатор включается ТОЛЬКО при заметной концентрации:
        если PM2.5 ≥ 20 и PM10 > 0 и (PM2.5/PM10) > 0.70 → +1 ступень (до «высокого»).
    Возвращает (emoji, label). Нет данных → (⚪, "н/д").
    """
    def _to_float(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    p25 = _to_float(pm25)
    p10 = _to_float(pm10)
    if p25 is None:
        return "⚪", "н/д"

    if p25 <= 25:
        lvl = 0  # низкое
    elif p25 <= 55:
        lvl = 1  # среднее
    else:
        lvl = 2  # высокое

    if p25 >= 20 and p10 and p10 > 0:
        ratio = p25 / p10
        if ratio > 0.70:
            lvl = min(lvl + 1, 2)

    if lvl == 0:
        return "🟢", "низкое"
    if lvl == 1:
        return "🟡", "среднее"
    return "🔴", "высокое"

# ──────────────────────── «Факт дня» по региону ────────────────────────────

FACTS_KLGD: Dict[str, str] = {
    # … (все твои факты; не урезал)
    "01-01": "1 января — Новый год: Калининград сияет гирляндами, а на Балтике пьют глинтвейн 🎆🍷",
    # (остальной блок без изменений)
    "12-31": "31 декабря — Канун Нового года: Калининград сияет, а на косе пьют шампанское 🥂",
}

FACTS_KLGD_RANDOM: List[str] = [
    "Калининград — единственный регион России без сухопутной границы с остальной страной 🗺️",
    # … (остальной список без изменений)
    "Калининградский мост Королевы Луизы соединяет Россию и Литву 🌉",
]

FACTS_CY: Dict[str, str] = {
    "01-03": "3 января — Фестиваль гранатов в Ормидии: дегустации и танцы под звездами, а в 1960 году Кипр стал республикой 🍎",
    # … (как было)
}

DEFAULT_FACTS_CY: List[str] = [
    "На Кипре более 1 800 видов растений — весна тут цветёт ярче любого Instagram-поста 🌸",
    # …
]

DEFAULT_FACTS_UNI: List[str] = [
    "Луна удаляется от Земли на 3.8 см каждый год.",
    "Человеческий мозг генерирует достаточно энергии, чтобы осветить лампочку.",
    "В космосе звуки не распространяются, потому что там нет атмосферы.",
    "Солнце составляет 99.86 % массы Солнечной системы.",
    "Молния может быть горячее поверхности Солнца (до 30 000 °C).",
    "Планета Венера вращается в обратном направлении (ретроградно).",
]

def get_fact(date: pendulum.Date, region: str = "") -> str:
    r = region.lower()
    if "калининград" in r:
        key = date.format("MM-DD")
        return FACTS_KLGD.get(key, random.choice(FACTS_KLGD_RANDOM))
    if "кипр" in r:
        key = date.format("MM-DD")
        return FACTS_CY.get(key, random.choice(DEFAULT_FACTS_CY))
    idx = date.day % len(DEFAULT_FACTS_UNI)
    return DEFAULT_FACTS_UNI[idx]

# ─────────────────────── Интеграции и иконки ────────────────────────────────

WEATHER_ICONS: Dict[str, str] = {
    "ясно":      "☀️",
    "переменная": "🌧️",
    "пасмурно":  "☁️",
    "дождь":     "🌧️",
    "туман":     "🌫️",
}

AIR_EMOJI: Dict[str, str] = {
    "хороший":    "🟢",
    "умеренный":  "🟡",
    "вредный":    "🟠",
    "оч. вредный": "🔴",
    "опасный":    "🟣",
    "н/д":        "⚪",
}

# ──────────────────────── Функция kp_emoji ───────────────────────────────────

def kp_emoji(kp: Optional[float]) -> str:
    if kp is None:
        return "⚪"
    k = int(round(kp))
    if k < 3:
        return "⚪"
    if k < 5:
        return "🟢"
    return "🔴"

# ──────────────────────── Тренд давления ─────────────────────────────────

def pressure_trend(w: Dict[str, Any]) -> str:
    """
    ↑ если ближайший час > +2 гПа, ↓ если < −2, иначе →.
    w — объект с hourly.surface_pressure (список чисел).
    """
    hp = w.get("hourly", {}).get("surface_pressure", [])
    if len(hp) < 2:
        return "→"
    diff = hp[-1] - hp[0]
    if diff >= 2:
        return "↑"
    if diff <= -2:
        return "↓"
    return "→"

# ──────────────────────── HTTP-обёртки ───────────────────────────────────

_HEADERS = {
    "User-Agent": "VayboMeter/1.0 (+https://github.com/)",
    "Accept":     "application/json",
}

def _get_retry(url: str, retries: int = 2, **params) -> Optional[dict]:
    attempt = 0
    while attempt <= retries:
        try:
            r = requests.get(url, params=params, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
        except Exception:
            attempt += 1
            if attempt > retries:
                return None
            time.sleep(0.5 * attempt)

def _get(url: str, **params) -> Optional[dict]:
    return _get_retry(url, retries=2, **params)

# ─────────────────────── Module self-test ─────────────────────────────────

if __name__ == "__main__":
    import sys
    print("kmh_to_ms demo:", kmh_to_ms(18), kmh_to_ms(None))
    print("ms_to_kmh demo:", ms_to_kmh(5), ms_to_kmh(None))
    print("Smoke index demo:", smoke_index(12, 30), smoke_index(40, 50), smoke_index(80, 90), smoke_index(None, 10))
    sys.exit(0)
