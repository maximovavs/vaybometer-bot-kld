#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_monthly_calendar.py

Отправка месячного лунного поста-резюме в Telegram-канал.

• читает lunar_calendar.json
• формирует красивый HTML-текст
• фильтрует Void-of-Course короче MIN_VOC_MINUTES
"""

from __future__ import annotations
import os
import json
import asyncio
import html
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

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


# ── helpers ────────────────────────────────────────────────────────────────

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


def _derive_phase_name_and_sign(rec: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """
    Безопасно достаём название фазы и знак.
    • Если phase_name отсутствует — берём из 'phase' (убираем эмодзи и хвост после запятой).
    • Если sign отсутствует — пробуем вытащить из 'phase' (часть после запятой).
    """
    # 1) Фаза
    name = (rec.get("phase_name") or "").strip()
    if not name:
        phase_text = (rec.get("phase") or "").strip()
        # убираем ведущие эмодзи/символы и берём часть до запятой
        # пример: "🌓 Первая четверть , Дева" → "Первая четверть"
        tmp = re.sub(r"^\W+", "", phase_text)  # срезаем эмодзи и префикс
        name = tmp.split(",")[0].strip() if tmp else ""

    # 2) Знак
    sign = rec.get("sign")
    if not sign:
        phase_text = (rec.get("phase") or "")
        if "," in phase_text:
            sign = phase_text.split(",")[-1].strip() or None

    return name or "", sign


def build_phase_blocks(data: Dict[str, Any]) -> str:
    """
    Группирует подряд идущие дни одной фазы и формирует блок HTML-строк:
    <b>🌒 1–3</b> <i>(Лев, Дева)</i>\n<i>Описание периода…</i>\n

    Теперь устойчиво работает даже если в JSON нет phase_name/sign:
    • фаза и знак будут восстановлены из поля 'phase';
    • при отсутствии знаков скобки не выводятся;
    • при полном отсутствии имени фазы группировка распадается на отдельные дни.
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

        # Эмодзи пытаемся взять из 'phase' (первое «слово»)
        emoji = (rec.get("phase") or "").strip().split(" ")[0] or "🌙"

        # Нормализуем имя фазы и знак
        name, sign_first = _derive_phase_name_and_sign(rec)
        if not name:
            # нет имени фазы вообще — чтобы не «склеивать» в один блок, используем уникальное имя
            name = f"__day_{start}__"

        signs = set()
        if sign_first:
            signs.add(sign_first)

        # Продлеваем диапазон, пока имя фазы одинаковое
        j = i
        while j + 1 < len(days):
            next_rec = data[days[j + 1]]
            next_name, next_sign = _derive_phase_name_and_sign(next_rec)
            if not next_name:
                next_name = f"__day_{days[j+1]}__"
            if next_name != name:
                break
            if next_sign:
                signs.add(next_sign)
            j += 1

        # Диапазон дат
        d1 = pendulum.parse(start).format("D")
        d2 = pendulum.parse(days[j]).format("D MMM", locale="ru")
        span = f"{d1}–{d2}" if i != j else d2

        # Сортируем знаки в зодиакальном порядке и убираем пустые
        sorted_signs = [s for s in sorted(signs, key=lambda x: zodiac_order.index(x) if x in zodiac_order else 99) if s]
        signs_str = ", ".join(sorted_signs)

        # Длинное описание (long_desc) может содержать HTML — экранируем для безопасности
        desc = html.escape((rec.get("long_desc") or "").strip())

        # Если имя фазы было подставлено «техническим» (__day_*__), оно нам не нужно в тексте
        # Заголовок блока строим только из эмодзи и дат + (знаки если есть)
        header = f"<b>{emoji} {span}</b>"
        if signs_str:
            header += f" <i>({signs_str})</i>"

        if desc:
            lines.append(f"{header}\n<i>{desc}</i>\n")
        else:
            lines.append(f"{header}\n")

        i = j + 1

    return "\n".join(lines)


def build_fav_blocks(rec: Dict[str, Any]) -> str:
    """
    Формирует блок «благоприятных/неблагоприятных дней»:
    ✅ Благоприятные: 2, 3, 9, 27
    ❌ Неблагоприятные: 13, 14, 24
    ✂️ Стрижка: 2, 3, 9
    ✈️ Путешествия: 4, 5
    🛍️ Покупки: 1, 2, 7
    ❤️ Здоровье: 20, 21, 27
    """
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
    """
    Собирает все VoC длительностью ≥ MIN_VOC_MINUTES:
    02.06 14:30 → 02.06 15:10
    """
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
    """
    Собирает полный HTML-текст для месячного поста:
    1) Заголовок с месяцем и годом
    2) Блок фаз
    3) Блок благоприятных дней
    4) Блок VoC (если есть)
    5) Пояснение про VoC
    """
    if not data:
        raise RuntimeError("lunar_calendar.json пуст")

    # первая дата в словаре, используется для заголовка
    first_key = sorted(data.keys())[0]
    first_day = pendulum.parse(first_key)
    header = f"{MOON_EMOJI} <b>Лунный календарь {first_day.format('MMMM YYYY', locale='ru').upper()}</b>\n"

    phases_block = build_phase_blocks(data)

    # берём первый элемент словаря, чтобы получить список favorable_days
    example_rec = next(iter(data.values()), {})
    fav_block = build_fav_blocks(example_rec)

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
    # читаем lunar_calendar.json
    raw = Path(CAL_FILE).read_text("utf-8")
    data = json.loads(raw)  # ожидаем { "2025-06-01": { ... }, ... }

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