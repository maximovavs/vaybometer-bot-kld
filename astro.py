#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.
Теперь умеет:
• Показать астрособытия на дату с любым смещением (offset_days).
• Показать Void-of-Course всегда (даже <15 минут) при флаге show_all_voc=True.
• Показать маркер «благоприятный/неблагоприятный день».
• Показать категории (✂️, ✈️, 🛍️, ❤️).
• Выводить фазу Луны и зону (элемент "phase").
• Убирать нумерацию советов, каждый совет на новой строке без цифр.
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info  # возвращает данные из lunar_calendar.json

# По умолчанию часовой пояс “Никосия”. В daily- или monthly-скриптах можно
# переопределять TZ, если нужно для другого региона (например, Kaliningrad).
TZ = pendulum.timezone("Asia/Nicosia")


def _format_voc(rec: Dict[str, Any], show_all_voc: bool) -> Optional[str]:
    """
    Форматирует Void-of-Course (VoC) в виде строки:
    ⚫️ VoC HH:mm–HH:mm

    Если show_all_voc=False, то фильтруем «микро-VoC» (<15 минут).
    Если show_all_voc=True, то показываем всегда (даже 1 минута).
    """
    voc = rec.get("void_of_course", {})
    start = voc.get("start")
    end   = voc.get("end")
    if not start or not end:
        return None

    # Попробуем распарсить и перевести в локальное время TZ
    try:
        t1 = pendulum.parse(start).in_tz(TZ)
        t2 = pendulum.parse(end).in_tz(TZ)
    except Exception:
        return None

    duration = (t2 - t1).in_minutes()

    # Если микро-VoC (<15 мин) и флаг show_all_voc=False — не показываем
    if not show_all_voc and duration < 15:
        return None

    # Иначе всегда выводим
    # Формат: ⚫️ VoC 05:29–06:59  (если show_all_voc=False или >=15)
    # или:   ⚫️ VoC 05:29–06:00 (3 мин)  (если show_all_voc=True, добавим длительность)
    if show_all_voc and duration < 15:
        return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')} ({duration:.0f} мин)"
    else:
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
    "Стрижка":     "✂️",
    "Путешествия": "✈️",
    "Покупки":     "🛍️",
    "Здоровье":    "❤️",
}


def _format_categories(rec: Dict[str, Any], date_obj: pendulum.Date) -> List[str]:
    """
    Возвращает список строк вида:
    ✂️ Стрижка — благоприятно
    ✈️ Путешествия — неблагоприятно
    и т. д., если категория актуальна для данной даты.
    """
    day = date_obj.day
    lines: List[str] = []
    fav = rec.get("favorable_days", {})

    for cat, emoji in CAT_EMOJI.items():
        fav_list = fav.get(cat, {}).get("favorable", [])
        unf_list = fav.get(cat, {}).get("unfavorable", [])
        label = cat  # уже с заглавной буквы
        if day in fav_list:
            lines.append(f"{emoji} {label} — благоприятно")
        elif day in unf_list:
            lines.append(f"{emoji} {label} — неблагоприятно")

    return lines


def astro_events(offset_days: int = 1, show_all_voc: bool = False) -> List[str]:
    """
    Формирует список строк «Астрособытия» для поста.

    Параметры:
      offset_days: 0 — сегодня, 1 — завтра, 2 — послезавтра, и т. д.
      show_all_voc: если True, то выводим VoC всегда (даже <15 мин). 
                    Если False, то только VoC >= 15 мин.

    Возвращаемый список может содержать:
      • VoC (при условии, если есть)
      • Маркер «благоприятный/неблагоприятный день»
      • Категории (✂️ Стрижка, ✈️ Путешествия, 🛍️ Покупки, ❤️ Здоровье)
      • Фаза Луны (rec["phase"]), а потом три совета (rec["advice"]) без нумерации
      • next_event («→ Первая четверть 10.06 08:51» или аналог)
    """
    target_date = pendulum.now(TZ).date().add(days=offset_days)
    rec = get_day_lunar_info(target_date)
    if not rec:
        return []

    lines: List[str] = []

    # 1) Void-of-Course
    voc_line = _format_voc(rec, show_all_voc)
    if voc_line:
        lines.append(voc_line)

    # 2) Общий благоприятный/неблагоприятный день
    gen_line = _format_general_day(rec, target_date)
    if gen_line:
        lines.append(gen_line)

    # 3) Категории
    lines.extend(_format_categories(rec, target_date))

    # 4) Фаза Луны + советы (каждый на новой строке, без цифр)
    phase = rec.get("phase", "").strip()
    advice_list = rec.get("advice", [])
    if phase:
        lines.append(phase)
        for adv in advice_list:
            # удаляем ведущие «1. », «2. » и т. д., если они остались в тексте
            adv_clean = adv.lstrip("0123456789. ").strip()
            lines.append(f"• {adv_clean}")

    # 5) next_event (если есть)
    next_ev = rec.get("next_event", "").strip()
    if next_ev:
        lines.append(next_ev)

    return lines


# ──────────────────────────────────────────────────────────────────────────
# Локальный тест (можно запускать «python astro.py» для быстрой проверки)
if __name__ == "__main__":
    from pprint import pprint

    print("=== Астрособытия на сегодня ===")
    pprint(astro_events(0, show_all_voc=True))
    print("=== Астрособытия на завтра ===")
    pprint(astro_events(1, show_all_voc=True))