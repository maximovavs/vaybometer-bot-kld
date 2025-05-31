#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astro.py – формирует блок «Астрособытия» для ежедневного поста.
Теперь:
• убирает процент-освещённость из строки фазы
• выводит советы с «•», а не 1./2./3.
• оставляет VoC, благоприятный день и категории как раньше
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info         # ← ваш календарь

TZ = pendulum.timezone("Asia/Nicosia")

# ───────── helpers ─────────
def _today_rec() -> Optional[Dict[str, Any]]:
    return get_day_lunar_info(pendulum.now(TZ).date())

def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    voc = rec.get("void_of_course", {})
    if not (voc.get("start") and voc.get("end")):
        return None
    t1 = pendulum.parse(voc["start"]).in_tz(TZ)
    t2 = pendulum.parse(voc["end"]).in_tz(TZ)
    if (t2 - t1).in_minutes() < 15:
        return None
    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"

def _good_bad(rec: Dict[str, Any]) -> Optional[str]:
    day = pendulum.now(TZ).day
    gen = rec.get("favorable_days", {}).get("general", {})
    if day in gen.get("favorable", []):
        return "✅ Благоприятный день"
    if day in gen.get("unfavorable", []):
        return "❌ Неблагоприятный день"
    return None

CAT_EMO = {"haircut":"✂️", "travel":"✈️", "shopping":"🛍", "health":"❤️"}
def _categories(rec: Dict[str, Any]) -> List[str]:
    day = pendulum.now(TZ).day
    out: List[str] = []
    fav = rec.get("favorable_days", {})
    for cat, emo in CAT_EMO.items():
        f = fav.get(cat, {}).get("favorable", [])
        u = fav.get(cat, {}).get("unfavorable", [])
        if day in f: out.append(f"{emo} {cat.capitalize()} — благоприятно")
        elif day in u: out.append(f"{emo} {cat.capitalize()} — неблагоприятно")
    return out

# ───────── main ────────────
def astro_events() -> List[str]:
    rec = _today_rec()
    if not rec:
        return []

    phase_full = rec.get("phase","")
    # убираем всё после «(»  → без процента
    phase_clean = phase_full.split(" (")[0].strip()

    tips = [s.strip("").strip() for s in rec.get("advice", []) if s.strip()]

    lines: List[str] = []

    for extra in (_format_voc(rec), _good_bad(rec)):
        if extra:
            lines.append(extra)

    lines += _categories(rec)

    # фаза + советы
    if phase_clean:
        lines.append(phase_clean)
    for tip in tips:
        lines.append(f"• {tip}")

    # ближайшее событие
    nxt = rec.get("next_event","").strip()
    if nxt:
        lines.append(nxt)

    return lines


# тест локально
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
