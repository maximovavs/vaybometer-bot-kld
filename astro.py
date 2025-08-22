#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.

Фичи:
• offset_days — берём «завтра»/«послезавтра» и т.д.
• show_all_voc=True — выводим VoC даже если короче порога.
• Поддержка разных форматов VoC в JSON: void_of_course / voc / void
  и полей start|end|from|to|start_time|end_time.
• Категории («Стрижка», «Путешествия», «Покупки», «Здоровье») читаются из JSON
  по-русски или по-английски (haircut, travel, shopping, health).
• Советы без нумерации (срезаем "1. ", "2)" и т.п.).

Ожидается, что get_day_lunar_info(date) возвращает dict с
ключами: phase, advice (list[str]), favorable_days, next_event,
и опционально VoC-структурой.
"""

from __future__ import annotations
import pendulum
import re
from typing import Any, Dict, List, Optional, Union
from lunar import get_day_lunar_info  # берёт из lunar_calendar.json

# По умолчанию кипрский часовой пояс. Можно передавать строку TZ извне.
DEFAULT_TZ = pendulum.timezone("Asia/Nicosia")

# Порог, ниже которого VoC скрываем (если show_all_voc=False)
VOC_HIDE_MINUTES = 5

# Сопоставление русских категорий к английским ключам и эмодзи
CATEGORY_MAPPING: Dict[str, tuple[str, str]] = {
    "Стрижка":     ("haircut",  "✂️"),
    "Путешествия": ("travel",   "✈️"),
    "Покупки":     ("shopping", "🛍️"),
    "Здоровье":    ("health",   "❤️"),
}

# ─────────────────────── VoC helpers ───────────────────────

def _pick(rec: Dict[str, Any], *names: str) -> Any:
    """Возвращает первое существующее поле из rec по списку возможных имён."""
    for n in names:
        if n in rec and rec[n] not in (None, ""):
            return rec[n]
    return None

def _extract_voc_record(rec: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Унифицируем возможные форматы:
      rec["void_of_course"] | rec["voc"] | rec["void"] -> dict
      и внутри: start|end|from|to|start_time|end_time
    Возвращает {"start": iso, "end": iso} или None.
    """
    voc = _pick(rec, "void_of_course", "voc", "void")
    if not isinstance(voc, dict):
        return None
    start = _pick(voc, "start", "from", "start_time")
    end   = _pick(voc, "end", "to", "end_time")
    if not (start and end):
        return None
    return {"start": str(start), "end": str(end)}

def _parse_local_dt(s: str, tz: pendulum.Timezone, fallback_year: int) -> Optional[pendulum.DateTime]:
    """
    Пытаемся распарсить:
      • ISO/естественные форматы → pendulum.parse
      • 'DD.MM HH:mm' (как в твоём JSON) → подставляем fallback_year
    """
    s = (s or "").strip()
    if not s:
        return None
    # 1) стандартные форматы
    try:
        return pendulum.parse(s).in_tz(tz)
    except Exception:
        pass
    # 2) наш кастом: '23.08 04:29'
    try:
        dt = pendulum.from_format(s, "DD.MM HH:mm", tz=tz)
        return dt.replace(year=fallback_year)
    except Exception:
        return None

def _format_voc_line(voc: Dict[str, str], tz: pendulum.Timezone, show_all_voc: bool, year_hint: int) -> Optional[str]:
    """
    Форматируем строку VoC:
      ⚫️ VoC HH:mm–HH:mm (N мин)
    Поддерживаем 'DD.MM HH:mm' (год берём из year_hint).
    """
    t1 = _parse_local_dt(voc.get("start", ""), tz, year_hint)
    t2 = _parse_local_dt(voc.get("end",   ""), tz, year_hint)
    if not t1 or not t2:
        return None

    minutes = max(0, (t2 - t1).in_minutes())
    if minutes < VOC_HIDE_MINUTES and not show_all_voc:
        return None

    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')} ({minutes} мин)"

# ─────────────────────── День/категории ───────────────────────

def _format_general_day(rec: Dict[str, Any], date_obj: pendulum.Date) -> Optional[str]:
    """
    ✅ Благоприятный день / ❌ Неблагоприятный день (если помечен).
    """
    day = date_obj.day
    fav_general = rec.get("favorable_days", {}).get("general", {}).get("favorable", [])
    unf_general = rec.get("favorable_days", {}).get("general", {}).get("unfavorable", [])
    if day in fav_general:
        return "✅ Благоприятный день"
    if day in unf_general:
        return "❌ Неблагоприятный день"
    return None

def _format_categories(rec: Dict[str, Any], date_obj: pendulum.Date) -> List[str]:
    """
    ✂️ Стрижка — благоприятно/неблагоприятно
    ✈️ Путешествия — …
    🛍️ Покупки — …
    ❤️ Здоровье — …
    """
    day = date_obj.day
    lines: List[str] = []
    fav_all = rec.get("favorable_days", {}) or {}

    for rus_cat, (eng_key, emoji) in CATEGORY_MAPPING.items():
        fav_list = fav_all.get(rus_cat, {}).get("favorable", [])
        unf_list = fav_all.get(rus_cat, {}).get("unfavorable", [])

        # fallback на английские ключи, если русских нет
        if not fav_list and not unf_list:
            fav_list = fav_all.get(eng_key, {}).get("favorable", [])
            unf_list = fav_all.get(eng_key, {}).get("unfavorable", [])

        if day in fav_list:
            lines.append(f"{emoji} {rus_cat} — благоприятно")
        elif day in unf_list:
            lines.append(f"{emoji} {rus_cat} — неблагоприятно")

    return lines

# ─────────────────────── Публичная функция ───────────────────────

def astro_events(
    offset_days: int = 1,
    show_all_voc: bool = False,
    tz: Union[str, pendulum.Timezone] = DEFAULT_TZ
) -> List[str]:
    """
    Возвращает список строк для блока «Астрособытия».

    1) ⚫️ VoC HH:mm–HH:mm (N мин), если есть (и не скрыт порогом)
    2) ✅/❌ Общая оценка дня
    3) Категории (Стрижка/Путешествия/Покупки/Здоровье)
    4) Фаза Луны (без процента освещенности) + советы (без нумерации)
    5) next_event («→ Через N дн. …»), если есть
    """
    # TZ → объект
    if isinstance(tz, str):
        tz = pendulum.timezone(tz)

    # Целевая дата
    target_date = pendulum.now(tz).date().add(days=offset_days)
    rec = get_day_lunar_info(target_date)
    if not rec:
        return []

    lines: List[str] = []

    # 1) VoC
    voc = _extract_voc_record(rec)
    voc_line = _format_voc_line(voc, tz, show_all_voc, target_date.year) if voc else None
    if voc_line:
        lines.append(voc_line)

    # 2) Общая оценка дня
    gen_line = _format_general_day(rec, target_date)
    if gen_line:
        lines.append(gen_line)

    # 3) Категории
    lines.extend(_format_categories(rec, target_date))

    # 4) Фаза и советы
    raw_phase = (rec.get("phase") or "").strip()
    phase = raw_phase.split("(")[0].strip() if raw_phase else ""
    advice_list = rec.get("advice") or []

    if phase:
        lines.append(phase)
        for adv in advice_list:
            text = str(adv).strip()
            # Срезаем возможные пронумерованные префиксы «1. », «2) » и т.п.
            text = re.sub(r'^\s*\d+[\.\)]\s*', '', text)
            lines.append(text)

    # 5) Следующее событие
    next_ev = (rec.get("next_event") or "").strip()
    if next_ev:
        lines.append(next_ev)

    return lines

# ─────────────────────── Локальный тест ───────────────────────
if __name__ == "__main__":
    from pprint import pprint

    print("=== Астрособытия на сегодня ===")
    pprint(astro_events(offset_days=0, show_all_voc=False, tz="Asia/Nicosia"))
    print("\n=== Астрособытия на завтра (включая микро‑VoC) ===")
    pprint(astro_events(offset_days=1, show_all_voc=True, tz="Asia/Nicosia"))
