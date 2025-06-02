#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.
Теперь умеет:
• Показать астрособытия не только на сегодня, но и на offset_days (например, завтрашний день).
• Показать Void-of-Course (VoC), если он есть.
• Показать маркер «благоприятный/неблагоприятный день» (по общему списку).
• Учитывает категории «Стрижка», «Путешествия», «Покупки», «Здоровье»,
  даже если они в JSON записаны как «haircut», «travel», «shopping», «health».
• Убирает нумерацию советов, каждый совет — с новой строки.
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info  # возвращает структуру из lunar_calendar.json

# Выберите часовой пояс, где вы фактически запускаете (Калининград или Никосия).
# Обычно для Калининграда достаточно "Europe/Kaliningrad", для Кипра — "Europe/Nicosia"
TZ = pendulum.timezone("Europe/Kaliningrad")


def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    """
    Форматирует Void-of-Course (VoC) в виде строки:
    ⚫️ VoC HH:mm–HH:mm
    Если VoC менее 15 минут или нет данных — возвращает None.
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

    # Если VoC < 15 минут, игнорируем как «микро-VoC»
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


# Словарь «русская категория → (английский_ключ_в_JSON, эмодзи)»
CATEGORY_MAPPING = {
    "Стрижка":     ("haircut",    "✂️"),
    "Путешествия": ("travel",     "✈️"),
    "Покупки":     ("shopping",   "🛍️"),
    "Здоровье":    ("health",     "❤️"),
}


def _format_categories(rec: Dict[str, Any], date_obj: pendulum.Date) -> List[str]:
    """
    Возвращает список строк вида:
      ✂️ Стрижка — благоприятно
      ✈️ Путешествия — неблагоприятно
      🛍️ Покупки — благоприятно
      ❤️ Здоровье — неблагоприятно

    В JSON может быть либо «Стрижка», «Путешествия», «Покупки», «Здоровье»,
    либо их английские эквиваленты («haircut», «travel», «shopping», «health»).
    Мы пытаемся вытянуть сначала «русскую» категорию, а если её нет — смотрим англ.
    """
    day = date_obj.day
    lines: List[str] = []
    fav = rec.get("favorable_days", {})

    for rus_cat, (eng_key, emoji) in CATEGORY_MAPPING.items():
        # 1) Пытаемся найти «русский» ключ:
        fav_list = fav.get(rus_cat, {}).get("favorable", [])
        unf_list = fav.get(rus_cat, {}).get("unfavorable", [])

        # 2) Если «русского» ключа нет (или он пуст), пробуем «английский»:
        if not fav_list and not unf_list:
            fav_list = fav.get(eng_key, {}).get("favorable", [])
            unf_list = fav.get(eng_key, {}).get("unfavorable", [])

        if day in fav_list:
            lines.append(f"{emoji} {rus_cat} — благоприятно")
        elif day in unf_list:
            lines.append(f"{emoji} {rus_cat} — неблагоприятно")

    return lines


def astro_events(offset_days: int = 1, show_all_voc: bool = False) -> List[str]:
    """
    Формирует список строк «Астрособытия» для поста.

    Параметр offset_days:
      0 — сегодня
      1 — завтра
      2 — послезавтра
      и т. д.

    Параметр show_all_voc:
      False — скрывать VoC < 15 минут (по умолчанию)
      True — показывать VoC даже если < 15 минут

    Возвращаемый список может содержать:
      • VoC (если show_all_voc=True или VoC ≥ 15 мин)
      • «✅ Благоприятный день» или «❌ Неблагоприятный день»
      • Категории (✂️ Стрижка, ✈️ Путешествия, 🛍️ Покупки, ❤️ Здоровье)
      • Фазу Луны (включая знак) и три совета (каждый с новой строки,
        без нумерации)
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
        # Если VoC < 15 минут, _format_voc вернёт None, но при show_all_voc=True нам нужно вывести любой VoC:
        if show_all_voc:
            # Пытаемся вручную вытянуть время VoC, даже если < 15 минут:
            voc = rec.get("void_of_course", {})
            start = voc.get("start")
            end = voc.get("end")
            if start and end:
                t1 = pendulum.parse(start).in_tz(TZ).format("HH:mm")
                t2 = pendulum.parse(end).in_tz(TZ).format("HH:mm")
                lines.append(f"⚫️ VoC {t1}–{t2}")
        else:
            lines.append(voc_line)

    # 2) Общий благоприятный/неблагоприятный день
    gen_line = _format_general_day(rec, target_date)
    if gen_line:
        lines.append(gen_line)

    # 3) Категории (Стрижка, Путешествия, Покупки, Здоровье)
    lines.extend(_format_categories(rec, target_date))

    # 4) Фаза Луны + три совета, каждый на новой строке
    phase = rec.get("phase", "").strip()
    advice_list = rec.get("advice", [])
    if phase:
        # Выводим фазу Луны отдельно, без «... (% освещ.)»
        lines.append(phase)
        for adv in advice_list:
            # Убираем нумерацию (поля вида "1. ", "2. " и т. п.)
            text = adv.strip()
            # Если в JSON советы уже с цифрами «1.»–«2.»… можно просто взять всё как есть:
            # text = adv.strip().lstrip("1234567890. ")
            lines.append(f"• {text}")

    # 5) next_event (Следующее астрособытие, если есть)
    next_ev = rec.get("next_event", "").strip()
    if next_ev:
        lines.append(next_ev)

    return lines


# Локальный тест (запуск из командной строки)
if __name__ == "__main__":
    from pprint import pprint

    print("=== Астрособытия на сегодня ===")
    pprint(astro_events(0, show_all_voc=True))
    print("=== Астрособытия на завтра ===")
    pprint(astro_events(1, show_all_voc=True))