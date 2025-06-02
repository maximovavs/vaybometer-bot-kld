#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.
Теперь умеет:
• Показать астрособытия не только на сегодня, но и на offset_days (например, завтрашний день).
• Показать Void-of-Course, если он есть.
• Показать маркер «благоприятный/неблагоприятный день» (по общему списку).
• Убрать нумерацию советов, каждый совет с новой строки.
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info  # возвращает всю структуру из lunar_calendar.json

TZ = pendulum.timezone("Asia/Nicosia")


def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    """
    Форматирует Void-of-Course (VoC) в виде строки:
    ⚫️ VoC HH:mm–HH:mm
    Если VoC менее 15 минут или нет полей start/end — возвращает None.
    """
    voc = rec.get("void_of_course", {})
    start = voc.get("start")
    end = voc.get("end")
    if not start or not end:
        return None

    try:
        t1 = pendulum.parse(start).in_tz(TZ)
        t2 = pendulum.parse(end).in_tz(TZ)
    except Exception:
        return None

    # Считаем «микро-VoC» (<15 минут) неактуальным
    if (t2 - t1).in_minutes() < 15:
        return None

    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"


def _format_general_day(rec: Dict[str, Any], date_obj: pendulum.Date) -> Optional[str]:
    """
    Пометка, если дата (с offset) — благоприятный или неблагоприятный день:
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


CAT_EMOJI = {
    "Стрижка":  "✂️",
    "Путешествия":   "✈️",
    "Покупки": "🛍️",
    "Здоровье":   "❤️",
}


def _format_categories(rec: Dict[str, Any], date_obj: pendulum.Date) -> List[str]:
    """
    Возвращает список строк вида:
    ✂️ Стрижка — благоприятно
    ✈️ Путешествия — неблагоприятно
    и т.д., если категория актуальна для данной даты.
    """
    day = date_obj.day
    lines: List[str] = []
    fav = rec.get("favorable_days", {})

    for cat, emoji in CAT_EMOJI.items():
        fav_list = fav.get(cat, {}).get("favorable", [])
        unf_list = fav.get(cat, {}).get("unfavorable", [])
        label = cat.capitalize()
        if day in fav_list:
            lines.append(f"{emoji} {label} — благоприятно")
        elif day in unf_list:
            lines.append(f"{emoji} {label} — неблагоприятно")

    return lines


def astro_events(offset_days: int = 1) -> List[str]:
    """
    Формирует список строк «Астрособытия» для поста.

    Параметр offset_days:
      0 — сегодня
      1 — завтра
      2 — послезавтра
      и т.д.

    Возвращаемый список может содержать:
    • VoC (если есть)
    • Маркер благоприятного/неблагоприятного дня
    • Категории (✂️, ✈️, 🛍️, ❤️)
    • Фазу Луны (включая знак), после неё — три совета, каждый с новой строке (без нумерации)
    • next_event («→ Через N дн. ...»)
    """
    target_date = pendulum.now(TZ).date().add(days=offset_days)
    rec = get_day_lunar_info(target_date)
    if not rec:
        return []

    lines: List[str] = []

    # 1) Void-of-Course
    voc_line = _format_voc(rec)
    if voc_line:
        lines.append(voc_line)

    # 2) Общий благоприятный/неблагоприятный день
    gen_line = _format_general_day(rec, target_date)
    if gen_line:
        lines.append(gen_line)

    # 3) Категории
    lines.extend(_format_categories(rec, target_date))

    # 4) Фаза Луны + три совета, каждый на новой строке
    phase = rec.get("phase", "").strip()
    advice_list = rec.get("advice", [])
    if phase:
        # выводим фазу отдельно, без процента на той же строке
        lines.append(phase)
        # затем каждый совет на новой строке
        for adv in advice_list:
            lines.append(f"• {adv.strip()}")

    # 5) next_event
    next_ev = rec.get("next_event", "").strip()
    if next_ev:
        lines.append(next_ev)

    return lines


# Локальный тест
if __name__ == "__main__":
    from pprint import pprint

    print("=== Астрособытия на сегодня ===")
    pprint(astro_events(0))
    print("=== Астрособытия на завтра ===")
    pprint(astro_events(1))