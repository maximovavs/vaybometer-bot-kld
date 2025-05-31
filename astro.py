#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astro.py  ─ формирует блок «Астрособытия» для ежедневного поста.

• параметр offset_days позволяет получать запись на любой день
  (по умолчанию завтрашний = 1).
• выводит VoC ≥15 мин, метки «благоприятный/неблагоприятный»,
  категории ✂️ / ✈️ / 🛍 / ❤️
• строка фазы без процента, далее 3 совета списком «• …»
• добавляет next_event
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info        # ваши данные

TZ = pendulum.timezone("Asia/Nicosia")
CAT_EMO = {"haircut": "✂️", "travel": "✈️", "shopping": "🛍", "health": "❤️"}


# ───────── helpers ────────────────────────────────────────────────────
def _rec_for(day: pendulum.Date) -> Optional[Dict[str, Any]]:
    return get_day_lunar_info(day)


def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    voc = rec.get("void_of_course", {})
    if not voc.get("start") or not voc.get("end"):
        return None
    t1 = pendulum.parse(voc["start"]).in_tz(TZ)
    t2 = pendulum.parse(voc["end"]).in_tz(TZ)
    return (f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"
            if (t2 - t1).in_minutes() >= 15 else None)


def _good_bad(rec: Dict[str, Any], day_num: int) -> Optional[str]:
    gen = rec.get("favorable_days", {}).get("general", {})
    if day_num in gen.get("favorable", []):
        return "✅ Благоприятный день"
    if day_num in gen.get("unfavorable", []):
        return "❌ Неблагоприятный день"
    return None


def _categories(rec: Dict[str, Any], day_num: int) -> List[str]:
    out: List[str] = []
    fav = rec.get("favorable_days", {})
    for cat, emo in CAT_EMO.items():
        f = fav.get(cat, {}).get("favorable", [])
        u = fav.get(cat, {}).get("unfavorable", [])
        if day_num in f:
            out.append(f"{emo} {cat.capitalize()} — благоприятно")
        elif day_num in u:
            out.append(f"{emo} {cat.capitalize()} — неблагоприятно")
    return out


# ───────── main entry ────────────────────────────────────────────────
def astro_events(offset_days: int = 1) -> List[str]:
    """Возвращает готовый блок строк для сообщения."""
    target_date = pendulum.now(TZ).add(days=offset_days).date()
    rec = _rec_for(target_date)
    if not rec:
        return []

    day_num = target_date.day
    phase = rec.get("phase", "").split(" (")[0].strip()       # без процента
    tips = [t.strip() for t in rec.get("advice", []) if t.strip()][:3]

    lines: List[str] = []

    # доп-метки
    for extra in (_format_voc(rec), _good_bad(rec, day_num)):
        if extra:
            lines.append(extra)
    lines.extend(_categories(rec, day_num))

    # фаза + советы
    if phase:
        lines.append(phase)
    lines.extend(f"• {t}" for t in tips)

    # ближайшее событие
    nxt = rec.get("next_event", "").strip()
    if nxt:
        lines.append(nxt)

    return lines


# ───────── локальный тест ────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())            # завтра
    pprint(astro_events(0))           # сегодня
