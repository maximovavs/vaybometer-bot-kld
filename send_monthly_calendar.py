#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_monthly_calendar.py

Отправка месячного лунного поста-резюме в Telegram-канал.

• читает lunar_calendar.json
• формирует красивый HTML-текст
• фильтрует Void-of-Course короче MIN_VOC_MINUTES
• если данные по фазам «пустые/одинаковые» — делит месяц на 9 привычных
  отрезков и подставляет мягкие фолбэки, сохраняя тексты Gemini, где они есть
"""

from __future__ import annotations
import os
import json
import asyncio
import html
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

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

# Эмодзи фаз (для попытки вытащить символ из поля phase)
PHASE_EMOJI = {"🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"}

# Мягкие фолбэки, которые тебе нравились
FALLBACK_TEXTS = [
    "Первые трудности проявились, корректируйте курс и действуйте.",
    "Ускорение: расширяйте проекты, укрепляйте связи.",
    "Кульминация: максимум эмоций и результатов.",
    "Отпускаем лишнее, завершаем дела, наводим порядок.",
    "Аналитика, ретроспектива и пересмотр стратегии.",
    "Отдых, ретриты, подготовка к новому циклу.",
    "Нулевая точка цикла — закладывайте мечты и намерения.",
    "Энергия прибавляется — время запускать новые задачи.",
    "Первые трудности проявились, корректируйте курс и действуйте."
]
FALLBACK_EMOJI = ["🌓","🌔","🌕","🌖","🌗","🌘","🌑","🌒","🌓"]  # под тексты выше


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_dt(s: str, year: int) -> Optional[pendulum.DateTime]:
    """Парсит 'DD.MM HH:mm' или ISO-строку → pendulum.DateTime в TZ."""
    try:
        return pendulum.parse(s).in_tz(TZ)
    except Exception:
        try:
            dmy, hm = s.split()
            day, mon = map(int, dmy.split("."))
            hh, mm = map(int, hm.split(":"))
            return pendulum.datetime(year, mon, day, hh, mm, tz=TZ)
        except Exception:
            return None


def _phase_emoji_from_text(phase_text: str) -> Optional[str]:
    if not phase_text:
        return None
    first = phase_text.strip().split()[0]
    return first if first in PHASE_EMOJI else None


def _derive_phase_name_and_sign(rec: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """
    Достаём имя фазы и знак. Если phase_name пуст — берём из 'phase' (часть до запятой,
    без эмодзи). Если sign пуст — пробуем достать из 'phase' (часть после запятой).
    """
    name = (rec.get("phase_name") or "").strip()
    if not name:
        phase_text = (rec.get("phase") or "").strip()
        tmp = re.sub(r"^\W+", "", phase_text)  # срезаем эмодзи/символы
        name = tmp.split(",")[0].strip() if tmp else ""
    sign = rec.get("sign")
    if not sign:
        phase_text = (rec.get("phase") or "")
        if "," in phase_text:
            sign = phase_text.split(",")[-1].strip() or None
    return name or "", sign


def _month_span(days_sorted: List[str]) -> Tuple[pendulum.Date, pendulum.Date]:
    d1 = pendulum.parse(days_sorted[0]).date()
    d2 = pendulum.parse(days_sorted[-1]).date()
    return d1, d2


def _looks_collapsed(data: Dict[str, Any]) -> bool:
    """
    Проверяем, что «фаза» у всех дней по сути одна/пустая → вероятно,
    генератор отдал месяц без фаз (всё склеится в 1 блок).
    """
    tokens = set()
    for d in data:
        rec = data[d]
        name, _ = _derive_phase_name_and_sign(rec)
        emoji = _phase_emoji_from_text(rec.get("phase") or "")
        tokens.add((name or "", emoji or ""))
        if len(tokens) > 3:
            return False
    # Если ≤ 1–2 уникальных токенов на весь месяц — считаем «коллапсом»
    return len(tokens) <= 2


def _fallback_segments(days_sorted: List[str]) -> List[Tuple[int,int]]:
    """
    Делим месяц на 9 отрезков «как раньше».
    Возвращаем список пар индексов (start_idx, end_idx) по days_sorted (вкл.).
    """
    n = len(days_sorted)
    # Границы по дню месяца: [1], [2-5], [6-8], [9-12], [13-15], [16-19], [20-23], [24-27], [28-31]
    # Преобразуем в индексы
    day_of = [pendulum.parse(d).day for d in days_sorted]
    borders = [(1,1),(2,5),(6,8),(9,12),(13,15),(16,19),(20,23),(24,27),(28,31)]
    segs: List[Tuple[int,int]] = []
    for a,b in borders:
        # найдём первый/последний индекс, попадающий в этот диапазон
        si = next((i for i,dd in enumerate(day_of) if a <= dd <= b), None)
        ei = None
        if si is not None:
            for j in range(n-1, -1, -1):
                if a <= day_of[j] <= b:
                    ei = j
                    break
        if si is not None and ei is not None and si <= ei:
            segs.append((si, ei))
    # На всякий случай склеим пересекающиеся/внутренние
    merged: List[Tuple[int,int]] = []
    for s,e in segs:
        if not merged or s > merged[-1][1] + 1:
            merged.append((s,e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    return merged


def _format_span(days_sorted: List[str], i: int, j: int) -> str:
    d1 = pendulum.parse(days_sorted[i])
    d2 = pendulum.parse(days_sorted[j])
    if i == j:
        return d2.format("D MMM", locale="ru")
    return f"{d1.format('D')}–{d2.format('D MMM', locale='ru')}"


def build_phase_blocks(data: Dict[str, Any]) -> str:
    """
    Обычная группировка по фазам (и умная реконструкция, если каких-то полей нет).
    Если всё «одинаковое», ниже сработает аварийный фолбэк.
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
        emoji = _phase_emoji_from_text(rec.get("phase") or "") or "🌙"
        name, sign_first = _derive_phase_name_and_sign(rec)
        group_key = (name or f"__day_{start}__", emoji)

        signs = set()
        if sign_first:
            signs.add(sign_first)

        j = i
        while j + 1 < len(days):
            next_rec = data[days[j+1]]
            nm, sg = _derive_phase_name_and_sign(next_rec)
            em = _phase_emoji_from_text(next_rec.get("phase") or "") or "🌙"
            next_key = (nm or f"__day_{days[j+1]}__", em)
            if next_key != group_key:
                break
            if sg:
                signs.add(sg)
            j += 1

        # Шапка блока
        span = _format_span(days, i, j)
        signs_str = ", ".join([s for s in sorted(
            signs, key=lambda x: zodiac_order.index(x) if x in zodiac_order else 99
        ) if s])

        desc = (rec.get("long_desc") or "").strip()
        desc = html.escape(desc) if desc else ""

        header = f"<b>{emoji} {span}</b>"
        if signs_str:
            header += f" <i>({signs_str})</i>"

        if desc:
            lines.append(f"{header}\n<i>{desc}</i>\n")
        else:
            lines.append(f"{header}\n")

        i = j + 1
    return "\n".join(lines)


def build_phase_blocks_with_fallback(data: Dict[str, Any]) -> str:
    """
    Пытаемся обычную группировку. Если видим «коллапс» данных — делим месяц
    на 9 привычных отрезков и подставляем мягкие фолбэки, но тексты Gemini
    (long_desc) — если они есть — остаются приоритетными внутри своих отрезков.
    """
    # 1) Сначала обычный путь
    if not _looks_collapsed(data):
        return build_phase_blocks(data)

    # 2) Аварийный сценарий
    days = sorted(data.keys())
    segs = _fallback_segments(days)
    lines: List[str] = []

    for idx, (si, ei) in enumerate(segs):
        # заголовок
        span = _format_span(days, si, ei)
        emoji = FALLBACK_EMOJI[idx] if idx < len(FALLBACK_EMOJI) else "🌙"

        # описание: берём первый непустой long_desc внутри сегмента; иначе фолбэк
        desc = ""
        for k in range(si, ei+1):
            drec = data[days[k]]
            cand = (drec.get("long_desc") or "").strip()
            if cand:
                desc = cand
                break
        if not desc:
            desc = FALLBACK_TEXTS[idx] if idx < len(FALLBACK_TEXTS) else ""

        desc = html.escape(desc) if desc else ""
        header = f"<b>{emoji} {span}</b>"
        if desc:
            lines.append(f"{header}\n<i>{desc}</i>\n")
        else:
            lines.append(f"{header}\n")

    return "\n".join(lines)


def build_fav_blocks(rec: Dict[str, Any]) -> str:
    """Блок «благоприятных/неблагоприятных» с безопасной обработкой пустых значений."""
    fav = rec.get("favorable_days", {}) or {}
    general = fav.get("general", {}) or {}

    def fmt_list(key: str) -> str:
        lst = (fav.get(key, {}) or {}).get("favorable", []) or []
        return ", ".join(map(str, lst)) if lst else "—"

    def fmt_main(key: str) -> str:
        lst = (general.get(key, []) or [])
        return ", ".join(map(str, lst)) if lst else "—"

    parts = [
        f"✅ <b>Благоприятные:</b> {fmt_main('favorable')}",
        f"❌ <b>Неблагоприятные:</b> {fmt_main('unfavorable')}",
        f"✂️ <b>Стрижка:</b> {fmt_list('haircut')}",
        f"✈️ <b>Путешествия:</b> {fmt_list('travel')}",
        f"🛍️ <b>Покупки:</b> {fmt_list('shopping')}",
        f"❤️ <b>Здоровье:</b> {fmt_list('health')}",
    ]
    return "\n".join(parts)


def build_voc_list(data: Dict[str, Any], year: int) -> str:
    """Собирает VoC длительностью ≥ MIN_VOC_MINUTES."""
    items: List[str] = []
    for d in sorted(data):
        voc = data[d].get("void_of_course") or {}
        start_s = voc.get("start")
        end_s = voc.get("end")
        if not start_s or not end_s:
            continue
        t1 = _parse_dt(start_s, year)
        t2 = _parse_dt(end_s, year)
        if not t1 or not t2:
            continue
        if (t2 - t1).in_minutes() < MIN_VOC_MINUTES:
            continue
        items.append(f"{t1.format('DD.MM HH:mm')}  →  {t2.format('DD.MM HH:mm')}")
    if not items:
        return ""
    return "<b>⚫️ Void-of-Course:</b>\n" + "\n".join(items)


def build_message(data: Dict[str, Any]) -> str:
    """Собирает полный HTML-текст поста."""
    if not data:
        raise RuntimeError("lunar_calendar.json пуст")

    first_key = sorted(data.keys())[0]
    first_day = pendulum.parse(first_key)
    header = f"{MOON_EMOJI} <b>Лунный календарь {first_day.format('MMMM YYYY', locale='ru').upper()}</b>\n"

    # Фазы (с фолбэком при «коллапсе»)
    phases_block = build_phase_blocks_with_fallback(data)

    # Благоприятные (из первого дня)
    example_rec = next(iter(data.values()), {})
    fav_block = build_fav_blocks(example_rec)

    # VoC
    voc_block = build_voc_list(data, first_day.year)

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
    raw = Path(CAL_FILE).read_text("utf-8")
    data = json.loads(raw)
    text = build_message(data)

    bot = Bot(TOKEN)
    await bot.send_message(
        chat_id=CHAT_ID_INT,
        text=text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True
    )


if __name__ == "__main__":
    asyncio.run(main())