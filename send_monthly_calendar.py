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

def build_phase_blocks(data: Dict[str, Any]) -> str:
    """
    Группирует подряд идущие дни одной фазы и формирует блок HTML-строк:
    <b>🌒 1–3</b> <i>(Лев, Дева)</i>\n<i>Описание периода…</i>\n
    """
    zodiac_order = [
        "Овен","Телец","Близнецы","Рак","Лев","Дева",
        "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"
    ]

    days = sorted(data.keys())
    lines: List[str] = []
    i = 0
    while i < len(days):
        start = days[i]
        rec = data[start]
        name = rec.get("phase_name", "")
        # "phase" хранит строку вида "🌒 Первая четверть , Дева"
        emoji = rec.get("phase", "").split()[0]
        signs = {rec.get("sign", "")}

        # ищем, пока фаза остаётся той же
        j = i
        while j + 1 < len(days) and data[days[j + 1]].get("phase_name") == name:
            j += 1
            signs.add(data[days[j]].get("sign", ""))

        # форматируем диапазон дат
        d1 = pendulum.parse(start).format("D")
        d2 = pendulum.parse(days[j]).format("D MMM", locale="ru")
        span = f"{d1}–{d2}" if i != j else d2

        # список знаков в нужном порядке
        sorted_signs = sorted(signs, key=lambda x: zodiac_order.index(x) if x in zodiac_order else 0)
        signs_str = ", ".join(sorted_signs)

        # длинное описание (long_desc) может содержать HTML
        desc = html.escape(rec.get("long_desc", "").strip())

        lines.append(f"<b>{emoji} {span}</b> <i>({signs_str})</i>\n<i>{desc}</i>\n")
        i = j + 1

    return "\n".join(lines)


def build_fav_blocks(rec_or_cats: Dict[str, Any]) -> str:
    """
    Формирует блок «благоприятных/неблагоприятных дней».
    Функция принимает либо запись дня с ключом 'favorable_days', либо сам словарь категорий.
    """
    fav = rec_or_cats.get("favorable_days") if "favorable_days" in rec_or_cats else rec_or_cats
    fav = fav or {}
    general = fav.get("general", {})

    def fmt_list(key: str) -> str:
        lst = fav.get(key, {}).get("favorable", [])
        return ", ".join(map(str, lst)) if lst else "—"

    parts = [
        f"✅ <b>Благоприятные:</b> {', '.join(map(str, general.get('favorable', [])) or ['—'])}",
        f"❌ <b>Неблагоприятные:</b> {', '.join(map(str, general.get('unfavorable', [])) or ['—'])}",
        f"✂️ <b>Стрижка:</b> {fmt_list('haircut')}",
        f"✈️ <b>Путешествия:</b> {fmt_list('travel')}",
        f"🛍️ <b>Покупки:</b> {fmt_list('shopping')}",
        f"❤️ <b>Здоровье:</b> {fmt_list('health')}",
    ]
    return "\n".join(parts)


def build_voc_block(voc_list: List[Tuple[pendulum.DateTime, pendulum.DateTime]]) -> str:
    """
    Рендерит месячный список VoC из уже нормализованных интервалов.
    Применяет порог MIN_VOC_MINUTES и единый стиль форматирования.
    """
    items: List[str] = []
    for s, e in voc_list:
        if (e - s).in_minutes() < MIN_VOC_MINUTES:
            continue
        items.append(_format_voc_interval(s, e))

    if not items:
        return ""
    return "<b>⚫️ VoC (Void-of-Course):</b>\n" + "\n".join(items)


# ── сборка финального сообщения ────────────────────────────────────────────

def build_message(days_map: Dict[str, Any],
                  month_voc: List[Tuple[pendulum.DateTime, pendulum.DateTime]],
                  cats: Dict[str, Any]) -> str:
    """
    Собирает полный HTML-текст для месячного поста:
    1) Заголовок с месяцем и годом
    2) Блок фаз
    3) Блок благоприятных дней
    4) Блок VoC (если есть)
    5) Пояснение про VoC
    """
    first_key = next(iter(days_map.keys()))
    first_day = pendulum.parse(first_key)
    header = f"{MOON_EMOJI} <b>Лунный календарь {first_day.format('MMMM YYYY', locale='ru').upper()}</b>\n"

    phases_block = build_phase_blocks(days_map)
    fav_block = build_fav_blocks(cats)
    voc_block = build_voc_block(month_voc)

    footer = (
        "\n<i>⚫️ Void-of-Course — период, когда Луна завершила все аспекты "
        "в знаке и не вошла в следующий; энергия рассеяна, новые начинания "
        "лучше отложить.</i>"
    )

    parts = [header, phases_block, fav_block]
    if voc_block:
        parts.append(voc_block)
    parts.append(footer)
    return "\n\n".join(parts)


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