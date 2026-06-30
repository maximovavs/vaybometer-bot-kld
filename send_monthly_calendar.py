#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_monthly_calendar.py

Отправка месячного лунного поста-резюме в Telegram-канал.

• читает lunar_calendar.json (новый формат {"days": ..., "month_voc": ...}
  или старый — даты на верхнем уровне)
• формирует красивый HTML-текст
• корректно собирает/склеивает Void-of-Course и фильтрует интервалы короче MIN_VOC_MINUTES
"""

import os
import json
import asyncio
import html
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from collections import OrderedDict

import pendulum
from telegram import Bot, constants

# ── настройки ──────────────────────────────────────────────────────────────

TZ = pendulum.timezone("Asia/Nicosia")
CAL_FILE = "lunar_calendar.json"
MIN_VOC_MINUTES = 15
VOC_IMPORTANT_MIN_MINUTES = 180
MAX_VOC_VISIBLE = 8
MAX_RHYTHM_LINES = 5
MOON_EMOJI = "🌙"

TOKEN = os.getenv("TELEGRAM_TOKEN_KLG", "")
CHAT_ID = os.getenv("CHANNEL_ID_KLG", "")
if not TOKEN or not CHAT_ID:
    raise RuntimeError("TELEGRAM_TOKEN_KLG / CHANNEL_ID_KLG не заданы")

try:
    CHAT_ID_INT = int(CHAT_ID)
except ValueError:
    raise RuntimeError("CHANNEL_ID_KLG должен быть числом")


# ── helpers (общие) ────────────────────────────────────────────────────────

def _parse_dt(s: str, year: int) -> Optional[pendulum.DateTime]:
    """
    Парсит строку вида "DD.MM HH:mm" или ISO-строку,
    возвращает pendulum.DateTime в таймзоне TZ.
    """
    try:
        # пробуем ISO
        return pendulum.parse(s).in_tz(TZ)
    except Exception:
        try:
            # формат "DD.MM HH:mm"
            dmy, hm = s.split()
            day, mon = map(int, dmy.split("."))
            hh, mm = map(int, hm.split(":"))
            return pendulum.datetime(year, mon, day, hh, mm, tz=TZ)
        except Exception:
            return None


def _merge_intervals(
    intervals: List[Tuple[pendulum.DateTime, pendulum.DateTime]],
    tol_min: int = 1
) -> List[Tuple[pendulum.DateTime, pendulum.DateTime]]:
    """Склейка пересекающихся/смежных интервалов (допускаем стык ±tol_min)."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda ab: ab[0])
    out = [intervals[0]]
    tol = pendulum.duration(minutes=tol_min)
    for s, e in intervals[1:]:
        ps, pe = out[-1]
        if s <= pe + tol:  # пересечение или почти стык
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def _parse_voc_entry_local(obj: Dict[str, Any]) -> Tuple[Optional[pendulum.DateTime], Optional[pendulum.DateTime]]:
    """Парсинг дневного VoC из локальных строк 'DD.MM HH:mm' → pendulum в TZ."""
    if not obj or not obj.get("start") or not obj.get("end"):
        return None, None
    try:
        s = pendulum.from_format(obj["start"], "DD.MM HH:mm", tz=TZ)
        e = pendulum.from_format(obj["end"],   "DD.MM HH:mm", tz=TZ)
    except Exception:
        return None, None
    if e <= s:
        return None, None
    return s, e


def _format_voc_interval(start: pendulum.DateTime, end: pendulum.DateTime) -> str:
    """
    Единый стиль для VoC:
      • если в одни сутки:  02.06 09:10–13:25
      • если на разные дни: 02.06 23:10–03.06 01:05
    """
    same_day = (start.date() == end.date())
    if same_day:
        return f"{start.format('DD.MM')} {start.format('HH:mm')}–{end.format('HH:mm')}"
    return f"{start.format('DD.MM HH:mm')}–{end.format('DD.MM HH:mm')}"


def load_calendar(src: Any = None
) -> Tuple[OrderedDict[str, Dict[str, Any]], List[Tuple[pendulum.DateTime, pendulum.DateTime]], Dict[str, Any]]:
    """
    Нормализованный загрузчик календаря.

    Вход: путь к файлу, Path, либо уже разобранный dict.
    Выход:
      days_map  — OrderedDict[YYYY-MM-DD] -> запись дня
      month_voc — список (start_dt, end_dt) в TZ (локальные даты/время)
      cats      — словарь категорий месяца
    """
    if src is None:
        obj = json.loads(Path(CAL_FILE).read_text("utf-8"))
    elif isinstance(src, (str, Path)):
        obj = json.loads(Path(src).read_text("utf-8"))
    else:
        obj = src  # уже dict

    # Новый формат
    if isinstance(obj, dict) and "days" in obj:
        days_map: OrderedDict[str, Dict[str, Any]] = OrderedDict(sorted(obj["days"].items()))
        first_day = next(iter(days_map.values()), {})
        cats = first_day.get("favorable_days") or {}

        # month_voc из корня, если есть
        voc_list: List[Tuple[pendulum.DateTime, pendulum.DateTime]] = []
        for it in obj.get("month_voc") or []:
            try:
                s = pendulum.from_format(it["start"], "DD.MM HH:mm", tz=TZ)
                e = pendulum.from_format(it["end"],   "DD.MM HH:mm", tz=TZ)
                if e > s:
                    voc_list.append((s, e))
            except Exception:
                continue

        # Если month_voc нет — собираем из дневных кусков
        if not voc_list:
            pieces: List[Tuple[pendulum.DateTime, pendulum.DateTime]] = []
            for rec in days_map.values():
                s, e = _parse_voc_entry_local(rec.get("void_of_course"))
                if s and e:
                    pieces.append((s, e))
            voc_list = _merge_intervals(pieces)

    # Старый формат
    else:
        days_map = OrderedDict(sorted(obj.items()))
        first_day = next(iter(days_map.values()), {})
        cats = first_day.get("favorable_days") or {}

        pieces: List[Tuple[pendulum.DateTime, pendulum.DateTime]] = []
        for rec in days_map.values():
            s, e = _parse_voc_entry_local(rec.get("void_of_course"))
            if s and e:
                pieces.append((s, e))
        voc_list = _merge_intervals(pieces)

    # Обрежем интервалы VoC рамками месяца на всякий случай
    y, m = map(int, next(iter(days_map.keys())).split("-")[:2])
    month_start = pendulum.datetime(y, m, 1, 0, 0, tz=TZ)
    month_end   = month_start.end_of("month")
    clipped: List[Tuple[pendulum.DateTime, pendulum.DateTime]] = []
    for s, e in voc_list:
        if s < month_end and e > month_start:
            s2 = max(s, month_start)
            e2 = min(e, month_end)
            if e2 > s2:
                clipped.append((s2, e2))
    voc_list = _merge_intervals(clipped)

    return days_map, voc_list, cats


# ── рендер блоков ──────────────────────────────────────────────────────────

_ZODIAC_ORDER = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы",
]

_PHASE_ACTIONS = {
    "waxing": "рост, планы, первые шаги",
    "full": "ясность, пик эмоций, завершение",
    "waning": "разбор, очищение, закрытие хвостов",
    "new": "пауза, восстановление, мягкое планирование",
}

_PHASE_TITLES = {
    "waxing": ("🌒", "Растущая Луна"),
    "full": ("🌕", "Полнолуние"),
    "waning": ("🌘", "Убывающая Луна"),
    "new": ("🌑", "Новолуние"),
}


def _phase_kind(rec: Dict[str, Any]) -> str:
    text = f"{rec.get('phase_name', '')} {rec.get('phase', '')}".lower()
    if "полн" in text or "🌕" in text:
        return "full"
    if "нов" in text or "🌑" in text:
        return "new"
    if any(x in text for x in ("раст", "серп", "первая четверть", "🌒", "🌓", "🌔")):
        return "waxing"
    if any(x in text for x in ("убыв", "последняя четверть", "🌖", "🌗", "🌘")):
        return "waning"
    return "other"


def _phase_label(kind: str, rec: Dict[str, Any]) -> Tuple[str, str]:
    if kind in _PHASE_TITLES:
        return _PHASE_TITLES[kind]
    phase = str(rec.get("phase") or "").strip()
    parts = phase.split(maxsplit=1)
    emoji = parts[0] if parts else "🌙"
    name = rec.get("phase_name") or (parts[1] if len(parts) > 1 else "Лунный период")
    return emoji, str(name).replace(",", "").strip()


def _date_span(start_key: str, end_key: str) -> str:
    start = pendulum.parse(start_key)
    end = pendulum.parse(end_key)
    if start.date() == end.date():
        return start.format("DD.MM")
    if start.month == end.month:
        return f"{start.format('DD')}–{end.format('DD.MM')}"
    return f"{start.format('DD.MM')}–{end.format('DD.MM')}"


def _sort_signs(signs: set[str]) -> List[str]:
    clean = [s for s in signs if s]
    return sorted(clean, key=lambda x: _ZODIAC_ORDER.index(x) if x in _ZODIAC_ORDER else 99)


def _phase_periods(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    days = sorted(data.keys())
    periods: List[Dict[str, Any]] = []
    i = 0
    while i < len(days):
        start = days[i]
        rec = data[start]
        kind = _phase_kind(rec)
        signs = {rec.get("sign", "")}
        j = i
        while j + 1 < len(days) and _phase_kind(data[days[j + 1]]) == kind:
            j += 1
            signs.add(data[days[j]].get("sign", ""))
        periods.append({"kind": kind, "start": start, "end": days[j], "rec": rec, "signs": _sort_signs(signs)})
        i = j + 1
    return periods


def build_main_rhythm_block(data: Dict[str, Any]) -> str:
    lines: List[str] = []
    for period in _phase_periods(data)[:MAX_RHYTHM_LINES]:
        kind = period["kind"]
        emoji, title = _phase_label(kind, period["rec"])
        action = _PHASE_ACTIONS.get(kind, html.escape(str(period["rec"].get("long_desc") or "спокойный лунный ритм"))[:80])
        signs = ", ".join(period["signs"])
        signs_part = f" ({html.escape(signs)})" if signs else ""
        lines.append(f"{emoji} {_date_span(period['start'], period['end'])} — {title.lower()}: {action}.{signs_part}")
    return "\n".join(lines)


def build_phase_blocks(data: Dict[str, Any]) -> str:
    """Backward-compatible wrapper: now returns the compact monthly rhythm."""
    return build_main_rhythm_block(data)


def build_key_points_block(data: Dict[str, Any]) -> str:
    periods = _phase_periods(data)

    def first_span(kind: str) -> str:
        for period in periods:
            if period["kind"] == kind:
                return _date_span(period["start"], period["end"])
        return ""

    lines: List[str] = []
    full = first_span("full")
    new = first_span("new")
    waxing = first_span("waxing")
    waning = first_span("waning")
    if full:
        lines.append(f"🌕 Полнолуние: {full} — ясность, эмоции сильнее, хорошо завершать.")
    if new:
        lines.append(f"🌑 Новолуние: {new} — пауза, восстановление, мягкое планирование.")
    if waxing:
        lines.append(f"🌓 Рост Луны: {waxing} — лучше для запусков, поездок и покупок.")
    if waning:
        lines.append(f"🌘 Убывание Луны: {waning} — разбор, очищение, закрытие хвостов.")
    return "\n".join(lines)


def _date_values(values: List[Any]) -> List[int]:
    out: List[int] = []
    for value in values or []:
        try:
            day = int(value)
        except Exception:
            continue
        if 1 <= day <= 31 and day not in out:
            out.append(day)
    return sorted(out)


def _fmt_days(days: List[int]) -> str:
    return ", ".join(map(str, days))


def _fav_dates(cats: Dict[str, Any], category: str, key: str = "favorable") -> List[int]:
    return _date_values(((cats or {}).get(category) or {}).get(key, []))


def _general_overlap(cats: Dict[str, Any]) -> Tuple[List[int], List[int], List[int]]:
    favorable = _fav_dates(cats, "general", "favorable")
    unfavorable = _fav_dates(cats, "general", "unfavorable")
    overlap = sorted(set(favorable) & set(unfavorable))
    plain_unfavorable = [day for day in unfavorable if day not in overlap]
    return favorable, plain_unfavorable, overlap


def build_best_days_block(rec_or_cats: Dict[str, Any]) -> str:
    cats = rec_or_cats.get("favorable_days") if "favorable_days" in rec_or_cats else rec_or_cats
    cats = cats or {}
    labels = [
        ("general", "Общие дела"),
        ("haircut", "Стрижка"),
        ("shopping", "Покупки"),
        ("health", "Здоровье"),
        ("travel", "Путешествия"),
    ]
    lines: List[str] = []
    for key, label in labels:
        days = _fav_dates(cats, key, "favorable")
        if days:
            lines.append(f"• {label}: {_fmt_days(days)}")
    return "\n".join(lines) if lines else "• Нет отдельных сильных дат — лучше идти по самочувствию."


def build_caution_block(cats: Dict[str, Any]) -> str:
    _favorable, plain_unfavorable, overlap = _general_overlap(cats or {})
    lines: List[str] = []
    if plain_unfavorable:
        lines.append(f"• Не для резких стартов: {_fmt_days(plain_unfavorable)}")
    if overlap:
        lines.append(f"• Дни с двойным фоном: {_fmt_days(overlap)} — лучше для завершения, анализа и мягких решений, не для резких стартов.")
    if not lines:
        lines.append("• Резкие старты лучше сверять с самочувствием и VoC-окнами.")
    return "\n".join(lines)


def build_fav_blocks(rec_or_cats: Dict[str, Any]) -> str:
    """Backward-compatible wrapper for the redesigned best/caution blocks."""
    cats = rec_or_cats.get("favorable_days") if "favorable_days" in rec_or_cats else rec_or_cats
    return build_best_days_block(cats or {}) + "\n\n" + build_caution_block(cats or {})


def _overlaps_active_daytime(start: pendulum.DateTime, end: pendulum.DateTime) -> bool:
    cursor = start.start_of("day")
    last = end.start_of("day")
    while cursor <= last:
        active_start = cursor.replace(hour=8, minute=0, second=0, microsecond=0)
        active_end = cursor.replace(hour=22, minute=0, second=0, microsecond=0)
        if max(start, active_start) < min(end, active_end):
            return True
        cursor = cursor.add(days=1)
    return False


def _important_voc(start: pendulum.DateTime, end: pendulum.DateTime) -> bool:
    duration = (end - start).in_minutes()
    return duration > VOC_IMPORTANT_MIN_MINUTES or start.date() != end.date() or _overlaps_active_daytime(start, end)


def build_voc_block(voc_list: List[Tuple[pendulum.DateTime, pendulum.DateTime]]) -> str:
    """
    Рендерит только важные VoC-окна для мобильного поста.
    Базовый MIN_VOC_MINUTES сохраняется, дополнительно ограничиваем видимый список.
    """
    valid = [(s, e) for s, e in voc_list if (e - s).in_minutes() >= MIN_VOC_MINUTES]
    important = [(s, e) for s, e in valid if _important_voc(s, e)]
    visible = important[:MAX_VOC_VISIBLE]
    hidden_count = max(0, len(valid) - len(visible))

    lines = ["⚫️ VoC — важные окна"]
    if visible:
        lines.extend(_format_voc_interval(s, e) for s, e in visible)
    else:
        lines.append("Значимых длинных VoC-окон мало; короткие используем как паузы.")
    if hidden_count:
        lines.append(f"Ещё {hidden_count} коротких VoC-окон — используем как паузы, не как запрет.")
    lines.append("⚫️ VoC — время “без курса”: лучше завершать, отдыхать, не запускать важное с нуля.")
    return "\n".join(lines)


# ── сборка финального сообщения ────────────────────────────────────────────

def build_message(days_map: Dict[str, Any],
                  month_voc: List[Tuple[pendulum.DateTime, pendulum.DateTime]],
                  cats: Dict[str, Any]) -> str:
    """
    Собирает компактный HTML-safe текст для месячного поста.
    """
    first_key = next(iter(days_map.keys()))
    first_day = pendulum.parse(first_key)
    header = f"{MOON_EMOJI} Лунный календарь {first_day.format('MMMM YYYY', locale='ru').upper()}"

    parts = [
        header,
        "🔭 Главный ритм месяца\n" + build_main_rhythm_block(days_map),
        "🌕 Ключевые точки\n" + build_key_points_block(days_map),
        "✅ Лучшие дни месяца\n" + build_best_days_block(cats),
        "⚠️ Осторожнее\n" + build_caution_block(cats),
        build_voc_block(month_voc),
    ]
    return "\n\n".join(part for part in parts if part).strip()


# ── main ──────────────────────────────────────────────────────────────────

async def main():
    # читаем lunar_calendar.json
    raw = Path(CAL_FILE).read_text("utf-8")
    obj = json.loads(raw)

    # нормализуем данные (работает и с новым, и со старым форматом)
    days_map, month_voc, cats = load_calendar(obj)

    text = build_message(days_map, month_voc, cats)

    bot = Bot(TOKEN)
    await bot.send_message(
        chat_id=CHAT_ID_INT,
        text=text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True
    )


if __name__ == "__main__":
    asyncio.run(main())
