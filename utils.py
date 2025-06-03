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

def safe(v: Any, unit: str = "") -> str:
    """
    None / 'None' / '—' → «—»
    Число → форматированная строка с единицей:
      safe(5.237, "°C") → «5.2°C»
      safe("10", "мм") → «10мм»
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

    Если with_unit=True, добавляет « µg/м³».
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

# ──────────────────────── «Факт дня» по региону ────────────────────────────

FACTS_KLGD: Dict[str, str] = {
    # Январь
    "01-01": "1 января — Новый год: Калининград сияет гирляндами, а на Балтике пьют глинтвейн 🎆🍷",
    "01-07": "7 января — Рождество: в храмах Калининграда звучат колокола, а дома готовят клопсы 🔔🍲",
    "01-14": "14 января — Старый Новый год: в Янтарном вспоминают прусские традиции и пекут пироги 🥧",
    "01-25": "25 января — День основания Кёнигсберга (1255): отмечаем прусское наследие в старом городе 🏰",
    # … (остальные даты Калининград)
}

FACTS_KLGD_RANDOM: List[str] = [
    "Калининград — единственный регион России без сухопутной границы с остальной страной 🗺️",
    "Куршская коса — объект Всемирного наследия ЮНЕСКО, где танцуют дюны 🌾",
    "Калининградский янтарь называют 'солнечным камнем' за его теплый свет 🪶",
    "В Калининграде сохранились прусские форты и ворота Кёнигсберга 🏰",
    "Балтийское море выбрасывает янтарь после штормов — настоящий клад! 🌊🪶",
    "Калининградский клопс — традиционное блюдо с прусскими корнями 🍲",
]

FACTS_CY: Dict[str, str] = {
    "01-03": "3 января — Фестиваль гранатов в Ормидии: дегустации и танцы под звездами, а в 1960 году Кипр стал республикой 🍎",
    "02-03": "3 февраля — Фестиваль апельсинов в Лефкаре: ярмарки с цитрусовыми и вышивкой, а фламинго прилетают в Ларнакское озеро 🦩",
    "03-20": "20 марта — Весеннее равноденствие: кипрские поля покрываются маками, а в Лимассоле стартуют винные туры 🌺🍷",
    "04-20": "20 апреля — Пасхальная неделя начинается: в деревне Калавасос пекут флауну, а черепахи выходят на пляжи Лары 🐢",
    "05-06": "6 мая — Фестиваль роз в Агросе: деревня утопает в розовых лепестках, а в 2004 году Кипр стал членом ЕС 🍯🇪🇺",
    "06-02": "2 июня — Фестиваль меда в Лимассоле: дегустации меда и сладостей, а в 2004 году Кипр стал членом ЕС 🍯🇪🇺",
    # … (остальные даты Кипра)
}

DEFAULT_FACTS_CY: List[str] = [
    "На Кипре более 1 800 видов растений — весна тут цветёт ярче любого Instagram-поста 🌸",
    "Общая длина побережья острова — 985 км: хватит кататься на байке по пляжам 🏍️",
    "Летом вода в Средиземном море прогревается до 27 °C — купаться комфортно почти весь июнь–сентябрь 🌊",
    "В Лимассоле около 360 солнечных дней в году — бери солнечные очки и вперёд ☀️",
    "Кипр — дом для четырёх видов морских черепах: они тоже любят побережье нашей любимой бухты 🐢",
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
    """
    Возвращает «факт дня» для date и region.

    Логика:
      1) Если region содержит «калининград» → факт из FACTS_KLGD по дате (MM-DD),
         иначе случайный из FACTS_KLGD_RANDOM.
      2) Если region содержит «кипр»      → факт из FACTS_CY по дате (MM-DD),
         иначе случайный из DEFAULT_FACTS_CY.
      3) В остальных случаях (универсальный) —
         факт из DEFAULT_FACTS_UNI по индексу (date.day % len(DEFAULT_FACTS_UNI)).
    """
    r = region.lower()

    # 1) Калининград
    if "калининград" in r:
        key = date.format("MM-DD")
        return FACTS_KLGD.get(key, random.choice(FACTS_KLGD_RANDOM))

    # 2) Кипр
    if "кипр" in r:
        key = date.format("MM-DD")
        return FACTS_CY.get(key, random.choice(DEFAULT_FACTS_CY))

    # 3) Универсальный
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
    """
    Эмодзи по индексу Kp:
      k < 3  → ⚪
      3 ≤ k < 5  → 🟢
      k ≥ 5     → 🔴
      None   → ⚪
    """
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
    diff = hp[1] - hp[0]
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
    """
    Повторяет запрос до retries раз (экспоненциальный бэкоф: 0.5, 1, 2 сек).
    Возвращает JSON-словарь или None.
    """
    attempt = 0
    while attempt <= retries:
        try:
            r = requests.get(url, params=params, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            if attempt > retries:
                # логируем только после последней неудачной попытки
                return None
            time.sleep(0.5 * attempt)

def _get(url: str, **params) -> Optional[dict]:
    """
    Простая обёртка поверх _get_retry с двумя попытками.
    """
    return _get_retry(url, retries=2, **params)

# ─────────────────────── Module self-test ─────────────────────────────────

if __name__ == "__main__":
    import sys
    print("compass demo:", compass(0), compass(45), compass(180))
    print("clouds demo:", clouds_word(10), clouds_word(50), clouds_word(90))
    print("wind_phrase demo:", wind_phrase(1), wind_phrase(5), wind_phrase(12), wind_phrase(20))
    print("safe demo:", safe(None, "°C"), safe(5.237, "°C"), safe("10", "мм"))
    print("AQI demo:", aqi_color(42), aqi_color(160), aqi_color("—"))
    print("PM demo:", pm_color(8), pm_color(27), pm_color(78, True), pm_color(None))
    today = pendulum.today()
    print("Fact Kaliningrad:", get_fact(today, "Калининград"))
    print("Fact Cyprus:", get_fact(today, "Кипр"))
    print("Fact Universal:", get_fact(today, ""))
    print("kp_emoji demo:", kp_emoji(1.5), kp_emoji(4.2), kp_emoji(6.7), kp_emoji(None))
    # Демонстрация HTTP-запроса (замените URL на какой-нибудь доступный API)
    # print("HTTP demo:", _get("https://api.github.com", per_page=1))
    sys.exit(0)