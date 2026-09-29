#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • блок «Астрособытия» для ежедневного поста (без сетевых вызовов).

Стиль:
• ⚫️ VoC сегодня HH:mm–HH:mm (N мин) — если есть и не слишком короткий
• ✅/❌ Общая оценка дня
• ✂️/✈️/🛍️/❤️ Категории (благо/неблаго)
• 🌕 Полнолуние • ♓ Рыбы — «фаза • знак»
• Советы из календаря (сохраняем их эмодзи; если нет — «•»)
• → Следующее событие (если есть)

Никогда не бросает исключения — максимум вернёт [].
"""

from __future__ import annotations
import os
import re
import logging
from typing import Any, Dict, List, Optional, Union

import pendulum

# ───────────────────────── логирование ─────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───────────────────────── импорт lunar с защитой ──────────────
try:
    from lunar import get_day_lunar_info as _raw_get_day
except Exception as e:
    logging.warning("astro: cannot import lunar.get_day_lunar_info: %s", e)
    _raw_get_day = None  # type: ignore[misc]

def _safe_get_day_lunar_info(date_obj: pendulum.Date) -> Optional[Dict[str, Any]]:
    if _raw_get_day is None:
        return None
    try:
        return _raw_get_day(date_obj)
    except Exception as e:
        logging.warning("astro: error in get_day_lunar_info(%s): %s", date_obj, e)
        return None

# ───────────────────────── настройки / константы ───────────────
DEFAULT_TZ_NAME = os.getenv("LUNAR_TZ", "Asia/Nicosia")  # где сформирован lunar_calendar.json
VOC_HIDE_MINUTES = 5  # если show_all_voc=False, прячем очень короткий VoC

CATEGORY_MAPPING: Dict[str, tuple[str, str]] = {
    "Стрижка":     ("haircut",  "✂️"),
    "Путешествия": ("travel",   "✈️"),
    "Покупки":     ("shopping", "🛍️"),
    "Здоровье":    ("health",   "❤️"),
}

ZODIAC_EMOJI = {
    "Овен": "♈", "Телец": "♉", "Близнецы": "♊", "Рак": "♋",
    "Лев": "♌", "Дева": "♍", "Весы": "♎", "Скорпион": "♏",
    "Стрелец": "♐", "Козерог": "♑", "Водолей": "♒", "Рыбы": "♓",
}

# ───────────────────────── утилиты времени/строк ───────────────
def _to_tz(tz: Union[str, pendulum.Timezone, None]) -> pendulum.Timezone:
    if isinstance(tz, pendulum.tz.timezone.Timezone):
        return tz
    name = str(tz or DEFAULT_TZ_NAME)
    try:
        return pendulum.timezone(name)
    except Exception:
        return pendulum.timezone("UTC")

def _pick(rec: Dict[str, Any], *names: str) -> Any:
    for n in names:
        v = rec.get(n)
        if v not in (None, "", []):
            return v
    return None

def _extract_voc_record(rec: Dict[str, Any]) -> Optional[Dict[str, str]]:
    voc = _pick(rec, "void_of_course", "voc", "void")
    if not isinstance(voc, dict):
        return None
    start = _pick(voc, "start", "from", "start_time")
    end   = _pick(voc, "end",   "to",   "end_time")
    if not (start and end):
        return None
    return {"start": str(start), "end": str(end)}

def _parse_local_dt(s: str, tz: pendulum.Timezone, fallback_year: int) -> Optional[pendulum.DateTime]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return pendulum.parse(s).in_tz(tz)
    except Exception:
        pass
    try:
        dt = pendulum.from_format(s, "DD.MM HH:mm", tz=tz)
        return dt.replace(year=fallback_year)
    except Exception:
        return None

def _format_voc_line(voc: Dict[str, str], tz: pendulum.Timezone, show_all_voc: bool, year_hint: int) -> Optional[str]:
    try:
        t1 = _parse_local_dt(voc.get("start", ""), tz, year_hint)
        t2 = _parse_local_dt(voc.get("end",   ""), tz, year_hint)
        if not t1 or not t2:
            return None
        minutes = max(0, (t2 - t1).in_minutes())
        if minutes < VOC_HIDE_MINUTES and not show_all_voc:
            return None
        return f"⚫️ VoC сегодня {t1.format('HH:mm')}–{t2.format('HH:mm')} ({minutes} мин)"
    except Exception:
        return None

def _strip_numbering(s: str) -> str:
    s = str(s or "").strip()
    # убираем "1. ", "2) ", ведущие буллеты
    s = re.sub(r'^\s*(?:\d+[\.\)]|[-–—•●•])\s*', '', s)
    return s

def _ensure_bullet(s: str) -> str:
    """Если совет не начинается с эмодзи/символа — добавим '• '."""
    if not s:
        return s
    first = s[0]
    # простая эвристика: буква/цифра → добавим буллет
    if first.isalnum():
        return f"• {s}"
    return s

def _phase_line(rec: Dict[str, Any]) -> Optional[str]:
    """
    Формат «🌕 Полнолуние • ♓ Рыбы».
    Берём «phase_name» (или «phase» до первой запятой) + знак с символом.
    """
    try:
        phase = (rec.get("phase_name") or "").strip()
        if not phase:
            raw = (rec.get("phase") or "").strip()
            if raw:
                phase = raw.split(",")[0].strip()
        if not phase:
            return None
        sign = (rec.get("sign") or "").strip()
        sign_emoji = ZODIAC_EMOJI.get(sign, "")
        sign_part = f" • {sign_emoji} {sign}" if sign else ""
        # если в phase уже есть эмодзи (🌕/🌔/…), оставляем как есть
        return f"{phase}{sign_part}"
    except Exception:
        return None

# ───────────────────────── форматирование дня/категорий ─────────
def _format_general_day(rec: Dict[str, Any], date_obj: pendulum.Date) -> Optional[str]:
    try:
        day = int(date_obj.day)
        fav_general = (rec.get("favorable_days") or {}).get("general") or {}
        fav = fav_general.get("favorable") or []
        unf = fav_general.get("unfavorable") or []
        if day in fav:
            return "✅ Благоприятный день"
        if day in unf:
            return "❌ Неблагоприятный день"
        return None
    except Exception:
        return None

def _format_categories(rec: Dict[str, Any], date_obj: pendulum.Date) -> List[str]:
    lines: List[str] = []
    try:
        day = int(date_obj.day)
        fav_all = rec.get("favorable_days") or {}
        for rus_cat, (eng_key, emoji) in CATEGORY_MAPPING.items():
            fav_list = (fav_all.get(rus_cat) or {}).get("favorable") or []
            unf_list = (fav_all.get(rus_cat) or {}).get("unfavorable") or []
            if not fav_list and not unf_list:
                fav_list = (fav_all.get(eng_key) or {}).get("favorable") or []
                unf_list = (fav_all.get(eng_key) or {}).get("unfavorable") or []
            if day in fav_list:
                lines.append(f"{emoji} {rus_cat} — благоприятно")
            elif day in unf_list:
                lines.append(f"{emoji} {rus_cat} — неблагоприятно")
    except Exception:
        pass
    return lines

# ───────────────────────── публичное API ────────────────────────
def astro_events(
    offset_days: int = 1,
    show_all_voc: bool = False,
    tz: Union[str, pendulum.Timezone, None] = DEFAULT_TZ_NAME
) -> List[str]:
    """
    Возвращает список строк для блока «Астрособытия».
    По стилю уже готово для вставки в пост; заголовок «🪐 <b>Астрособытия</b>» добавляй снаружи.
    """
    try:
        tz_obj = _to_tz(tz)
        target_date = pendulum.now(tz_obj).date().add(days=offset_days)

        rec = _safe_get_day_lunar_info(target_date)
        if not rec:
            return []

        lines: List[str] = []

        # 1) VoC
        voc = _extract_voc_record(rec)
        voc_line = _format_voc_line(voc, tz_obj, show_all_voc, target_date.year) if voc else None
        if voc_line:
            lines.append(voc_line)

        # 2) Общая оценка дня
        gen_line = _format_general_day(rec, target_date)
        if gen_line:
            lines.append(gen_line)

        # 3) Категории
        lines.extend(_format_categories(rec, target_date))

        # 4) Фаза • знак
        ph = _phase_line(rec)
        if ph:
            lines.append(ph)

        # 5) Советы (до 3-х), сохраняем их эмодзи/значки
        advice_list = rec.get("advice") or []
        if isinstance(advice_list, list):
            for adv in advice_list[:3]:
                s = _ensure_bullet(_strip_numbering(adv))
                if s:
                    lines.append(s)

        # 6) Следующее событие
        next_ev = _strip_numbering(rec.get("next_event", ""))
        if next_ev:
            if not next_ev.startswith(("→", "➡", "🗓", "⏭")):
                next_ev = f"→ {next_ev}"
            lines.append(next_ev)

        return [l for l in lines if isinstance(l, str) and l.strip()]
    except Exception as e:
        logging.warning("astro_events: unexpected error: %s", e)
        return []

# ───────────────────────── локальный тест ───────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("=== Астрособытия на сегодня ===")
    pprint(astro_events(offset_days=0, show_all_voc=False, tz=os.getenv("TZ_LOCAL","Europe/Kaliningrad")))
    print("\n=== Астрособытия на завтра (показывать короткие VoC) ===")
    pprint(astro_events(offset_days=1, show_all_voc=True, tz=os.getenv("LUNAR_TZ","Asia/Nicosia")))