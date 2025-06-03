#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.

Теперь:
• Можно указывать offset_days, чтобы брать «завтрашние» (или дальше) события.
• Параметр show_all_voc=True выводит VoC даже если < 15 минут.
• Категории «Стрижка», «Путешествия», «Покупки», «Здоровье» читаются из JSON по-русски или по-английски.
• Убираем нумерацию советов («1. …» → «• …»).
"""

from __future__ import annotations
import pendulum
import re
from typing import Any, Dict, List, Optional, Union
from lunar import get_day_lunar_info  # берёт из lunar_calendar.json

# По умолчанию кипрский часовой пояс. Можно заменить на "Europe/Kaliningrad" или передавать строку.
DEFAULT_TZ = pendulum.timezone("Asia/Nicosia")


def _format_voc(
    rec: Dict[str, Any],
    tz: pendulum.Timezone,
    show_all_voc: bool
) -> Optional[str]:
    """
    Форматирует Void-of-Course (VoC) как:
      ⚫️ VoC HH:mm–HH:mm

    Если период < 15 минут и show_all_voc=False → возвращаем None.
    Если show_all_voc=True → выводим «микро-VoC» тоже.
    Если данных нет → None.
    """
    voc = rec.get("void_of_course", {})
    start = voc.get("start")
    end   = voc.get("end")
    if not start or not end:
        return None

    try:
        t1 = pendulum.parse(start).in_tz(tz)
        t2 = pendulum.parse(end).in_tz(tz)
    except Exception:
        return None

    minutes = (t2 - t1).in_minutes()
    if minutes < 15 and not show_all_voc:
        return None

    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"


def _format_general_day(
    rec: Dict[str, Any],
    date_obj: pendulum.Date
) -> Optional[str]:
    """
    Пометка, если date_obj — благоприятный или неблагоприятный день:
      ✅ Благоприятный день
      ❌ Неблагоприятный день
    """
    day = date_obj.day
    fav_general = rec.get("favorable_days", {}).get("general", {}).get("favorable", [])
    unf_general = rec.get("favorable_days", {}).get("general", {}).get("unfavorable", [])
    if day in fav_general:
        return "✅ Благоприятный день"
    if day in unf_general:
        return "❌ Неблагоприятный день"
    return None


# Словарь русских категорий → (английский ключ в JSON, эмодзи)
CATEGORY_MAPPING: Dict[str, tuple[str, str]] = {
    "Стрижка":     ("haircut",  "✂️"),
    "Путешествия": ("travel",   "✈️"),
    "Покупки":     ("shopping", "🛍️"),
    "Здоровье":    ("health",   "❤️"),
}


def _format_categories(
    rec: Dict[str, Any],
    date_obj: pendulum.Date
) -> List[str]:
    """
    Возвращает список строк вида:
      ✂️ Стрижка — благоприятно
      ✈️ Путешествия — неблагоприятно
      🛍️ Покупки — благоприятно
      ❤️ Здоровье — неблагоприятно

    Если в JSON нет «русских» ключей, то пробуем «английские» (haircut, travel, shopping, health).
    """
    day = date_obj.day
    lines: List[str] = []
    fav_all = rec.get("favorable_days", {})

    for rus_cat, (eng_key, emoji) in CATEGORY_MAPPING.items():
        fav_list = fav_all.get(rus_cat, {}).get("favorable", [])
        unf_list = fav_all.get(rus_cat, {}).get("unfavorable", [])

        if not fav_list and not unf_list:
            fav_list = fav_all.get(eng_key, {}).get("favorable", [])
            unf_list = fav_all.get(eng_key, {}).get("unfavorable", [])

        if day in fav_list:
            lines.append(f"{emoji} {rus_cat} — благоприятно")
        elif day in unf_list:
            lines.append(f"{emoji} {rus_cat} — неблагоприятно")

    return lines


def astro_events(
    offset_days: int = 1,
    show_all_voc: bool = False,
    tz: Union[str, pendulum.Timezone] = DEFAULT_TZ
) -> List[str]:
    """
    Формирует список строк «Астрособытия» для поста.

    offset_days:
      0 — сегодня
      1 — завтра
      2 — послезавтра
      и т. д.

    show_all_voc:
      False — скрыть VoC < 15 мин (по умолчанию)
      True  — показать любой VoC (даже < 15 мин)

    tz:
      Либо строка (например, "Europe/Kaliningrad"), либо объект pendulum.Timezone.
      По умолчанию — кипрский ("Asia/Nicosia").

    Возвращает список строк (каждая — свой «bullet»):
      1) ⚫️ VoC HH:mm–HH:mm  (если есть)
      2) ✅ Благоприятный день  или  ❌ Неблагоприятный день
      3) Категории (✂️ Стрижка — благоприятно / ✈️ Путешествия — неблагоприятно / 🛍️ Покупки — … / ❤️ Здоровье — …)
      4) Фаза Луны (имя + знак, без «(XX% освещ.)») и советы (каждый с новой строкой, без нумерации, просто «• …»)
      5) next_event («→ Через N дн. …») 
    """
    # Приведём tz к объекту pendulum.Timezone, если передали строку
    if isinstance(tz, str):
        tz = pendulum.timezone(tz)

    # Вычисляем целевую дату (с учётом offset_days)
    target_date = pendulum.now(tz).date().add(days=offset_days)
    rec = get_day_lunar_info(target_date)
    if not rec:
        return []

    lines: List[str] = []

    # 1) VoC
    voc_line = _format_voc(rec, tz, show_all_voc)
    if voc_line:
        lines.append(voc_line)

    # 2) Общий благоприятный/неблагоприятный день
    gen_line = _format_general_day(rec, target_date)
    if gen_line:
        lines.append(gen_line)

    # 3) Категории
    lines.extend(_format_categories(rec, target_date))

    # 4) Фаза Луны + советы
    raw_phase = rec.get("phase", "").strip()
    # Отрезаем «(XX% освещ.)», если он есть
    phase = raw_phase.split("(")[0].strip()
    advice_list = rec.get("advice", []) or []

    if phase:
        lines.append(phase)
        for adv in advice_list:
            text = adv.strip()
            # Убираем возможную пронумерованную префикс-часть «1. », «2) » и т.п.
            text = re.sub(r'^\s*\d+[\.\)]\s*', '', text)
            lines.append(f"• {text}")

    # 5) next_event
    next_ev = rec.get("next_event", "").strip()
    if next_ev:
        lines.append(next_ev)

    return lines


# Локальный тест
if __name__ == "__main__":
    from pprint import pprint

    # Астрособытия на сегодня (offset_days=0)
    print("=== Астрособытия на сегодня ===")
    pprint(astro_events(offset_days=0, show_all_voc=False, tz="Asia/Nicosia"))
    print()

    # Астрособытия на завтра (offset_days=1)
    print("=== Астрособытия на завтра ===")
    pprint(astro_events(offset_days=1, show_all_voc=True, tz="Asia/Nicosia"))