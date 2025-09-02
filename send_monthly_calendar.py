#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_monthly_calendar.py

Отправка месячного лунного поста-резюме в Telegram-канал.

• читает lunar_calendar.json
• формирует красивый HTML-текст
• если данные «ровные» (все дни в одну фазу/пустые) — включает аварийное
  разбиение на 9 отрезков с мягкими фолбэками; тексты Gemini (long_desc)
  используются приоритетно, но если они повторяются/короткие — подменяются
  фолбэком для сегмента;
• фильтрует Void-of-Course короче MIN_VOC_MINUTES.
"""

from __future__ import annotations

import os
import json
import asyncio
import html
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

# ── справочники/фолбэки ────────────────────────────────────────────────────

# 9-отрезковая «сетка» месяца (аварийный режим)
FALLBACK_EMOJI = ["🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌑", "🌒", "🌓"]
FALLBACK_TEXTS = [
    "В период первой четверти энергия растёт: хорошо для стартов и активных действий.",
    "Растущая Луна — время наращивать темп, укреплять планы и связи.",
    "Полнолуние — кульминация эмоций и результатов; подведите итоги и отдохните.",
    "Убывающая Луна — мягкое замедление, завершайте лишнее и наводите порядок.",
    "Последняя четверть — аналитика, ретроспектива и пересмотр стратегии.",
    "Убывающий серп — отдых, ретриты, подготовка к новому циклу.",
    "Новолуние — нулевая точка цикла; засевайте намерения и мечты.",
    "Растущий серп — энергия прибавляется, запускайте новые задачи.",
    "Первая четверть — снова импульс к росту и уверенным шагам вперёд.",
]

ZODIAC_ORDER = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
]
LUNAR_EMOJIS = set("🌑🌒🌓🌔🌕🌖🌗🌘")

# считаем «слишком короткой» описательную фразу от модели
MIN_DESC_LEN = 60


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_dt(s: str, year: int) -> Optional[pendulum.DateTime]:
    """
    Парсит строку вида "DD.MM HH:mm" или ISO-строку,
    возвращает pendulum.DateTime в таймзоне TZ.
    """
    if not s:
        return None
    try:
        # пробуем ISO
        return pendulum.parse(s).in_tz(TZ)
    except Exception:
        try:
            parts = s.strip().split()
            if len(parts) != 2:
                return None
            dmy, hm = parts
            day, mon = map(int, dmy.split("."))
            hh, mm = map(int, hm.split(":"))
            return pendulum.datetime(year, mon, day, hh, mm, tz=TZ)
        except Exception:
            return None


def _phase_emoji_from_text(phase_text: str) -> Optional[str]:
    """Берём первый лунный эмодзи из 'phase', если есть."""
    if not isinstance(phase_text, str):
        return None
    for ch in phase_text.strip():
        if ch in LUNAR_EMOJIS:
            return ch
    return None


def _derive_phase_name_and_sign(rec: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Достаём осмысленное имя фазы и знак.
    Сначала берём 'phase_name', иначе пытаемся распарсить 'phase'.
    """
    name = rec.get("phase_name")
    sign = rec.get("sign")
    if isinstance(name, str) and name.strip():
        name = name.strip()
    else:
        ph = rec.get("phase")
        if isinstance(ph, str) and ph.strip():
            em = _phase_emoji_from_text(ph) or ""
            ph_clean = ph.strip()
            if em and ph_clean.startswith(em):
                ph_clean = ph_clean[len(em):].strip()
            parts = [p.strip() for p in ph_clean.split(",")]
            if parts:
                name = parts[0] or None
            if len(parts) > 1 and not sign:
                sign = parts[1] or None
        else:
            name = None

    if not isinstance(sign, str) or not sign.strip():
        sign = rec.get("sign") if isinstance(rec.get("sign"), str) else None

    return (name, sign)


def _format_span(days: List[str], si: int, ei: int) -> str:
    """Форматирует диапазон дат «1–3 сент.» или «1 сент.»."""
    d1 = pendulum.parse(days[si]).format("D MMM", locale="ru")
    d2 = pendulum.parse(days[ei]).format("D MMM", locale="ru")
    return d1 if si == ei else f"{d1}–{d2}"


def _fallback_segments(days: List[str]) -> List[Tuple[int, int]]:
    """Делим месяц на 9 примерно равных отрезков по индексам."""
    if not days:
        return []
    n = len(days)
    cuts = [round(n * x / 9) for x in range(10)]
    segs: List[Tuple[int, int]] = []
    for i in range(9):
        si = max(0, min(n - 1, cuts[i]))
        ei = max(0, min(n - 1, cuts[i + 1] - 1))
        if ei < si:
            ei = si
        if segs and si <= segs[-1][1]:
            si = segs[-1][1] + 1
            if si > ei:
                si = ei
        segs.append((si, ei))
    segs[-1] = (segs[-1][0], n - 1)
    return segs


def _looks_collapsed(data: Dict[str, Any]) -> bool:
    """
    Считаем месяц «коллапсным», если у большинства дней нет phase_name,
    или фазы практически не меняются, или знаков почти нет.
    """
    names: set[str] = set()
    emojis: set[str] = set()
    signs: set[str] = set()
    total = 0
    empty_name = 0

    for _, rec in data.items():
        total += 1
        nm, sg = _derive_phase_name_and_sign(rec)
        if nm:
            names.add(nm.strip().lower())
        else:
            empty_name += 1
        em = _phase_emoji_from_text(rec.get("phase") or "")
        if em:
            emojis.add(em)
        if sg:
            signs.add(sg)

    if total == 0:
        return True
    if empty_name / total >= 0.60:
        return True
    if len(names) <= 1 and len(emojis) <= 1:
        return True
    if len(names) <= 2 and len(signs) <= 1:
        return True
    return False


def _produces_single_span(data: Dict[str, Any]) -> bool:
    """Проверяем, что обычная группировка не даст один блок на весь месяц."""
    days = sorted(data.keys())
    if not days:
        return True
    first = days[0]
    name0, _ = _derive_phase_name_and_sign(data[first])
    em0 = _phase_emoji_from_text(data[first].get("phase") or "") or "🌙"
    key0 = (name0 or "__single__", em0)
    for d in days[1:]:
        nm, _ = _derive_phase_name_and_sign(data[d])
        em = _phase_emoji_from_text(data[d].get("phase") or "") or "🌙"
        if (nm or "__single__", em) != key0:
            return False
    return True


# ── блоки сообщения ───────────────────────────────────────────────────────

def build_phase_blocks(data: Dict[str, Any]) -> str:
    """
    Группирует подряд идущие дни одной фазы и формирует блок HTML-строк:
    <b>🌒 1–3 сент.</b> <i>(Лев, Дева)</i>\n<i>Описание периода…</i>\n
    """
    days = sorted(data.keys())
    lines: List[str] = []
    i = 0
    while i < len(days):
        start = days[i]
        rec = data[start]

        name, sign = _derive_phase_name_and_sign(rec)
        emoji = _phase_emoji_from_text(rec.get("phase") or "") or "🌙"
        signs = set([sign]) if sign else set()

        j = i
        while j + 1 < len(days):
            n2, s2 = _derive_phase_name_and_sign(data[days[j + 1]])
            if (n2 or "").strip().lower() != (name or "").strip().lower():
                break
            j += 1
            if s2:
                signs.add(s2)

        span = _format_span(days, i, j)
        sorted_signs = [s for s in ZODIAC_ORDER if s in signs]
        signs_str = ", ".join(sorted_signs)

        desc_raw = (rec.get("long_desc") or "").strip()
        desc = html.escape(desc_raw) if desc_raw else ""

        header = f"<b>{emoji} {span}</b>"
        if signs_str:
            header += f" <i>({signs_str})</i>"

        if desc:
            lines.append(f"{header}\n<i>{desc}</i>\n")
        else:
            lines.append(f"{header}\n")

        i = j + 1

    return "\n".join(lines)


def _collect_segment_signs(data: Dict[str, Any], days: List[str], si: int, ei: int) -> List[str]:
    """Собираем знаки внутри сегмента и сортируем по зодиакальному порядку."""
    signs: set[str] = set()
    for k in range(si, ei + 1):
        rec = data[days[k]]
        _, s = _derive_phase_name_and_sign(rec)
        if s:
            signs.add(s)
    return [s for s in ZODIAC_ORDER if s in signs][:3]  # максимум 3 для компактности


def _major_emoji_for_segment(data: Dict[str, Any], days: List[str], si: int, ei: int, fallback: str) -> str:
    """Пытаемся выбрать «большинство» лунных эмодзи в сегменте."""
    counts: Dict[str, int] = {}
    for k in range(si, ei + 1):
        em = _phase_emoji_from_text((data[days[k]].get("phase") or ""))
        if em:
            counts[em] = counts.get(em, 0) + 1
    if not counts:
        return fallback
    return max(counts.items(), key=lambda kv: kv[1])[0]


def build_phase_blocks_with_fallback(data: Dict[str, Any]) -> str:
    """
    Сначала пробуем обычную группировку. Если «коллапс» данных или
    получился бы один блок — делим на 9 отрезков, добавляем знаки,
    берём majority-эмодзи, и ДЕДУПЛИРУЕМ одинаковые короткие фразы Gemini.
    """
    if not (_looks_collapsed(data) or _produces_single_span(data)):
        return build_phase_blocks(data)

    days = sorted(data.keys())
    segs = _fallback_segments(days)
    lines: List[str] = []

    last_desc_norm = ""  # для дедупликации

    for idx, (si, ei) in enumerate(segs):
        span = _format_span(days, si, ei)
        # majority emoji по сегменту (если нет — фолбэк из набора)
        emoji = _major_emoji_for_segment(data, days, si, ei, FALLBACK_EMOJI[idx] if idx < len(FALLBACK_EMOJI) else "🌙")

        # знаки внутри сегмента
        sign_list = _collect_segment_signs(data, days, si, ei)
        signs_str = ", ".join(sign_list)

        # ищем хороший long_desc в сегменте
        desc = ""
        for k in range(si, ei + 1):
            cand = (data[days[k]].get("long_desc") or "").strip()
            if cand:
                desc = cand
                break

        # нормализуем для сравнения: убираем двойные пробелы/регистр
        def _norm(s: str) -> str:
            return " ".join(s.split()).strip().lower()

        # если Gemini-текст слишком короткий или совпадает с прошлым — берём мягкий фолбэк
        if not desc or len(desc) < MIN_DESC_LEN or _norm(desc) == last_desc_norm:
            desc = FALLBACK_TEXTS[idx] if idx < len(FALLBACK_TEXTS) else ""
            last_desc_norm = ""  # фолбэк не участвует в дедупе
        else:
            last_desc_norm = _norm(desc)

        desc = html.escape(desc) if desc else ""
        header = f"<b>{emoji} {span}</b>"
        if signs_str:
            header += f" <i>({signs_str})</i>"

        lines.append(f"{header}\n<i>{desc}</i>\n" if desc else f"{header}\n")

    return "\n".join(lines)


def _aggregate_favorable(rec_map: Dict[str, Any]) -> Dict[str, Any]:
    """
    Объединяем favorable_days из всех дней месяца (union).
    Структура:
      {
        "general": {"favorable":[...],"unfavorable":[...]},
        "haircut": {"favorable":[...]},
        "travel":  {"favorable":[...]},
        "shopping":{"favorable":[...]},
        "health":  {"favorable":[...]},
      }
    """
    agg = {
        "general": {"favorable": set(), "unfavorable": set()},
        "haircut": {"favorable": set()},
        "travel": {"favorable": set()},
        "shopping": {"favorable": set()},
        "health": {"favorable": set()},
    }
    any_data = False

    for _, rec in rec_map.items():
        fav = rec.get("favorable_days")
        if not isinstance(fav, dict):
            continue
        any_data = True
        gen = fav.get("general") or {}
        for k in ("favorable", "unfavorable"):
            v = gen.get(k) or []
            for x in v:
                if isinstance(x, int):
                    agg["general"][k].add(x)
        for sub in ("haircut", "travel", "shopping", "health"):
            vv = (fav.get(sub) or {}).get("favorable") or []
            for x in vv:
                if isinstance(x, int):
                    agg[sub]["favorable"].add(x)

    if not any_data:
        # вернём пустой шаблон
        return {
            "general": {"favorable": [], "unfavorable": []},
            "haircut": {"favorable": []},
            "travel": {"favorable": []},
            "shopping": {"favorable": []},
            "health": {"favorable": []},
        }

    # преобразуем множества в отсортированные списки
    def _sorted(s: set[int]) -> List[int]:
        return sorted(s)

    return {
        "general": {
            "favorable": _sorted(agg["general"]["favorable"]),
            "unfavorable": _sorted(agg["general"]["unfavorable"]),
        },
        "haircut": {"favorable": _sorted(agg["haircut"]["favorable"])},
        "travel": {"favorable": _sorted(agg["travel"]["favorable"])},
        "shopping": {"favorable": _sorted(agg["shopping"]["favorable"])},
        "health": {"favorable": _sorted(agg["health"]["favorable"])},
    }


def build_fav_blocks(rec_map: Dict[str, Any]) -> str:
    """
    Формирует блок «благоприятных/неблагоприятных дней».
    Теперь объединяем данные со всех дней месяца.
    """
    fav = _aggregate_favorable(rec_map)
    general = fav.get("general", {}) or {}

    def fmt_main(key: str) -> str:
        vals = general.get(key) or []
        return ", ".join(map(str, vals)) if vals else "—"

    def fmt_sub(key: str) -> str:
        vals = (fav.get(key) or {}).get("favorable") or []
        return ", ".join(map(str, vals)) if vals else "—"

    parts = [
        f"✅ <b>Благоприятные:</b> {fmt_main('favorable')}",
        f"❌ <b>Неблагоприятные:</b> {fmt_main('unfavorable')}",
        f"✂️ <b>Стрижка:</b> {fmt_sub('haircut')}",
        f"✈️ <b>Путешествия:</b> {fmt_sub('travel')}",
        f"🛍️ <b>Покупки:</b> {fmt_sub('shopping')}",
        f"❤️ <b>Здоровье:</b> {fmt_sub('health')}",
    ]
    return "\n".join(parts)


def build_voc_list(data: Dict[str, Any], year: int) -> str:
    """
    Собирает все VoC длительностью ≥ MIN_VOC_MINUTES:
    02.06 14:30 → 02.06 15:10
    """
    items: List[str] = []
    for d in sorted(data):
        voc = data[d].get("void_of_course")
        if not isinstance(voc, dict):
            continue
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
    2) Блок фаз (с аварийным режимом при «коллапсе» + дедуп)
    3) Блок благоприятных дней (объединённый)
    4) Блок VoC (если есть)
    5) Пояснение про VoC
    """
    first_key = sorted(data.keys())[0]
    first_day = pendulum.parse(first_key)
    header = f"{MOON_EMOJI} <b>Лунный календарь {first_day.format('MMMM YYYY', locale='ru').upper()}</b>\n"

    phases_block = build_phase_blocks_with_fallback(data)
    fav_block = build_fav_blocks(data)
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
    data = json.loads(raw)  # ожидаем { "YYYY-MM-DD": { ... }, ... }

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