#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.
Теперь умеет:
• Показать астрособытия на дату с offset_days относительно переданного tz.
• Показать Void-of-Course (VoC) с учётом флага show_all_voc.
• Показать маркер «благоприятный/неблагоприятный день» (по общему списку).
• Учитывает категории «Стрижка», «Путешествия», «Покупки», «Здоровье»,
  даже если они в JSON записаны как «haircut», «travel», «shopping», «health».
• Убирает нумерацию советов, каждый совет — с новой строкой.
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional, Union
from lunar import get_day_lunar_info  # возвращает структуру из lunar_calendar.json

# По умолчанию используем кипрский часовой пояс.
DEFAULT_TZ = pendulum.timezone("Asia/Nicosia")


def _format_voc(
    rec: Dict[str, Any],
    tz: pendulum.Timezone,
    show_all_voc: bool
) -> Optional[str]:
    """
    Форматирует Void-of-Course (VoC) в виде строки:
    ⚫️ VoC HH:mm–HH:mm

    Если VoC меньше 15 минут и show_all_voc=False → возвращает None.
    Если show_all_voc=True → возвращает даже «микро-VoC» (даже < 15 мин).
    Если данных о VoC нет → None.
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

    duration_minutes = (t2 - t1).in_minutes()
    if duration_minutes < 15 and not show_all_voc:
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


# Русские категории → (англ.ключ в JSON, эмодзи)
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

    При этом внутри JSON могли быть ключи как на русском,
    так и на английском (“haircut”, “travel”, “shopping”, “health”).
    """
    day = date_obj.day
    lines: List[str] = []
    fav: Dict[str, Any] = rec.get("favorable_days", {})

    for rus_cat, (eng_key, emoji) in CATEGORY_MAPPING.items():
        fav_list = fav.get(rus_cat, {}).get("favorable", [])
        unf_list = fav.get(rus_cat, {}).get("unfavorable", [])

        # Если «русских» ключей не нашлось, пробуем «английские»
        if not fav_list and not unf_list:
            fav_list = fav.get(eng_key, {}).get("favorable", [])
            unf_list = fav.get(eng_key, {}).get("unfavorable", [])

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
      False — скрывать VoC < 15 минут (по умолчанию)
      True  — показывать любой VoC

    tz:
      Либо строка с названием часового пояса (например, "Asia/Nicosia" или "Europe/Kaliningrad"),
      либо объект pendulum.Timezone. По умолчанию — кипрский ("Asia/Nicosia").

    Возвращаемый список может содержать:
      • Сразу VoC (если он есть и не < 15 мин, или show_all_voc=True)
      • «✅ Благоприятный день» / «❌ Неблагоприятный день»
      • Категории (✂️ Стрижка, ✈️ Путешествия, 🛍️ Покупки, ❤️ Здоровье)
      • Фазу Луны (именно текст, без процента «(XX% освещ.)») и три совета (каждый с новой строкой,
        без нумерации — просто «• текст»)
      • next_event («→ Через N дн. …»)
    """
    # Приводим tz к объекту pendulum.Timezone (если передали строку)
    if isinstance(tz, str):
        tz = pendulum.timezone(tz)

    # Берём «сегодня» в этом час. поясе, прибавляем offset_days
    target_date = pendulum.now(tz).date().add(days=offset_days)
    rec = get_day_lunar_info(target_date)
    if not rec:
        return []

    lines: List[str] = []

    # 1) VoC
    voc_line = _format_voc(rec, tz, show_all_voc)
    if voc_line:
        lines.append(voc_line)

    # 2) Благоприятный/Неблагоприятный общий день
    gen_line = _format_general_day(rec, target_date)
    if gen_line:
        lines.append(gen_line)

    # 3) Категории (Стрижка, Путешествия, Покупки, Здоровье)
    lines.extend(_format_categories(rec, target_date))

    # 4) Фаза Луны + советы
    phase = rec.get("phase", "").strip()
    advice_list = rec.get("advice", [])
    if phase:
        # Показываем саму фазу (имя + знак) без процента на этой же строке
        lines.append(phase)
        for adv in advice_list:
            txt = adv.strip()
            # Каждый совет — новая строка, без цифр
            lines.append(f"• {txt}")

    # 5) next_event (если есть)
    next_ev = rec.get("next_event", "").strip()
    if next_ev:
        lines.append(next_ev)

    return lines


# Для быстрой отладки и проверки
if __name__ == "__main__":
    from pprint import pprint

    print("=== Астрособытия на сегодня (offset_days=0) ===")
    pprint(astro_events(offset_days=0, show_all_voc=False, tz="Asia/Nicosia"))

    print("=== Астрособытия на завтра (offset_days=1) ===")
    pprint(astro_events(offset_days=1, show_all_voc=True, tz="Asia/Nicosia"))