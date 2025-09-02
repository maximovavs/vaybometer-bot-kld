#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_lunar_calendar.py
──────────────────────────────────────────────────────────────────────────────
Формирует файл lunar_calendar.json со всеми полями, нужными и для ежедневных
постов (короткие советы) и для месячного (длинные описания фаз + VoC).

• phase, percent, sign, phase_time
• advice      – 3 строки «💼 …», «⛔ …», «🪄 …»
• long_desc   – 1–2 предложения на фазу (разово на месяц)
• void_of_course: {start, end}  (UTC → TZ в JSON)
• favorable_days / unfavorable_days – словари CATS
"""

from __future__ import annotations

import os, json, math, asyncio
from pathlib import Path
from typing  import Dict, Any, List, Tuple, Optional

import pendulum
import swisseph as swe

# Часовой пояс для вывода времени (как и раньше)
TZ = pendulum.timezone(os.getenv("LUNAR_TZ", "Asia/Nicosia"))

# ───── Gemini (вместо OpenAI) ──────────────────────────────────────────────
# Не падаем без ключа: просто используем фоллбэки.
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
def _get_gemini_model():
    """Ленивая инициализация клиента Gemini. Возвращает модель или None."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(
            model_name=_GEMINI_MODEL,
            system_instruction=(
                "Ты — профессиональный астролог с мягким, поддерживающим стилем. "
                "Пишешь по-русски, кратко, без тревожных формулировок и медицинских советов."
            ),
            generation_config={
                "temperature": 0.65,
                "top_p": 0.9,
                "top_k": 40,
                "max_output_tokens": 400,
            },
        )
    except Exception:
        return None

GM = _get_gemini_model()

# ───────────────────────────────────────────────────────────────────────────

EMO = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

FALLBACK_LONG: Dict[str,str] = {
    "Новолуние"         : "Нулевая точка цикла — закладывайте мечты и намерения.",
    "Растущий серп"     : "Энергия прибавляется — время запускать новые задачи.",
    "Первая четверть"   : "Первые трудности проявились, корректируйте курс и действуйте.",
    "Растущая Луна"     : "Ускорение: расширяйте проекты, укрепляйте связи.",
    "Полнолуние"        : "Кульминация: максимум эмоций и результатов.",
    "Убывающая Луна"    : "Отпускаем лишнее, завершаем дела, наводим порядок.",
    "Последняя четверть": "Аналитика, ретроспектива и пересмотр стратегии.",
    "Убывающий серп"    : "Отдых, ретриты, подготовка к новому циклу.",
}

# Категории (пример; как и раньше — подменяйте под свои таблицы)
CATS = {
    "general" :{"favorable":[2,3,9,27],   "unfavorable":[13,14,24]},
    "haircut" :{"favorable":[2,3,9],      "unfavorable":[]},
    "travel"  :{"favorable":[4,5],        "unfavorable":[]},
    "shopping":{"favorable":[1,2,7],      "unfavorable":[]},
    "health"  :{"favorable":[20,21,27],   "unfavorable":[]},
}

# ───── helpers астрономии ─────────────────────────────────────────────────
def jd2dt(jd: float) -> pendulum.DateTime:
    """JD → pendulum UTC"""
    return pendulum.from_timestamp((jd - 2440587.5) * 86400, tz="UTC")

def phase_name(angle: float) -> str:
    idx = int(((angle + 22.5) % 360) // 45)
    return [
        "Новолуние","Растущий серп","Первая четверть","Растущая Луна",
        "Полнолуние","Убывающая Луна","Последняя четверть","Убывающий серп"
    ][idx]

def compute_phase(jd: float) -> Tuple[str,int,str]:
    lon_s = swe.calc_ut(jd, swe.SUN)[0][0]
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    ang   = (lon_m - lon_s) % 360
    illum = int(round((1 - math.cos(math.radians(ang))) / 2 * 100))
    name  = phase_name(ang)
    sign  = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
             "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"][int(lon_m // 30) % 12]
    return name, illum, sign

# ───── Void-of-Course (приближённый расчёт) ───────────────────────────────
ASPECTS = {0,60,90,120,180}          # мажоры
ORBIS   = 1.5                        # допуск, °
PLANETS = [swe.SUN,swe.MERCURY,swe.VENUS,swe.MARS,
           swe.JUPITER,swe.SATURN,swe.URANUS,swe.NEPTUNE,swe.PLUTO]

def _has_major_lunar_aspect(jd: float) -> bool:
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    for p in PLANETS:
        lon_p = swe.calc_ut(jd, p)[0][0]
        a = abs((lon_m - lon_p + 180) % 360 - 180)
        for asp in ASPECTS:
            if abs(a - asp) <= ORBIS:
                return True
    return False

def compute_voc_for_day(jd_start: float) -> Dict[str, Optional[str]]:
    """
    Находит VoC внутри календарных суток jd_start (00:00 UT).
    1) находим ближайший переход Луны в следующий знак;
    2) идём назад до последнего точного аспекта — это начало VoC;
    Если интервал не попадает в сутки — возвращаем None-значения.
    """
    sign0 = int(swe.calc_ut(jd_start, swe.MOON)[0][0] // 30)
    jd = jd_start
    step = 1/24  # 1 час
    # переход знака
    while True:
        jd += step
        if int(swe.calc_ut(jd, swe.MOON)[0][0] // 30) != sign0:
            sign_change = jd
            break

    # назад до последнего аспекта
    jd_back = sign_change
    step_b  = 10 / 1440   # 10 минут
    while jd_back > jd_start and not _has_major_lunar_aspect(jd_back):
        jd_back -= step_b

    start_dt = jd2dt(jd_back).in_tz(TZ)
    end_dt   = jd2dt(sign_change).in_tz(TZ)

    cur_day = jd2dt(jd_start).in_tz(TZ).date()
    if start_dt.date() != cur_day and end_dt.date() != cur_day:
        return {"start": None, "end": None}

    return {
        "start": start_dt.format("DD.MM HH:mm"),
        "end"  : end_dt.format("DD.MM HH:mm")
    }

# ───── Gemini helpers ──────────────────────────────────────────────────────
def _split_lines(text: str) -> List[str]:
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip().lstrip("-•—*0123456789. ").strip()
        if s:
            lines.append(s)
    return lines

async def ai_short(date: str, phase: str) -> List[str]:
    """
    3 одно-строчных совета с emoji: 💼 / ⛔ / 🪄
    Надёжно падает в фоллбэк при отсутствии ключа или ошибках сети.
    """
    if GM is not None:
        prompt = (
            f"Дата: {date}. Фаза Луны: {phase}. "
            "Дай 3 очень коротких дружелюбных совета, каждый в ОДНОЙ строке, "
            "с соответствующим эмодзи в начале: "
            "1) 💼 — про работу/дела; 2) ⛔ — что лучше отложить; 3) 🪄 — маленький ритуал/забота о себе. "
            "Без медицинских советов и тревожных формулировок."
        )
        try:
            r = GM.generate_content(prompt)
            text = (getattr(r, "text", "") or "").strip()
            lines = _split_lines(text)[:3]
            if len(lines) == 3:
                return lines
        except Exception:
            pass

    return [
        "💼 Сфокусируйся на главном.",
        "⛔ Отложи крупные решения.",
        "🪄 Пять минут тишины и дыхания.",
    ]

async def ai_long(phase_name: str, month_ru: str) -> str:
    """Короткий абзац на фазу (1–2 предложения)."""
    if GM is not None:
        prompt = (
            f"Месяц: {month_ru}. Фаза Луны: {phase_name}. "
            "Дай 1–2 предложения с мягкой, поддерживающей интерпретацией периода. "
            "Без эзотерических терминов, без негативных формулировок, по делу."
        )
        try:
            r = GM.generate_content(prompt)
            text = (getattr(r, "text", "") or "").strip()
            if text:
                return text
        except Exception:
            pass
    return FALLBACK_LONG[phase_name]

# ───── основной генератор ─────────────────────────────────────────────────
async def generate(year: int, month: int) -> Dict[str,Any]:
    # где лежат эфемериды (если кладёте их рядом, это ок)
    swe.set_ephe_path(".")

    first = pendulum.date(year, month, 1)
    last  = first.end_of('month')

    cal: Dict[str,Any] = {}
    long_tasks: Dict[str, asyncio.Task] = {}
    short_tasks: List[asyncio.Task] = []

    d = first
    while d <= last:
        jd = swe.julday(d.year, d.month, d.day, 0.0)

        # лунные данные
        name, illum, sign = compute_phase(jd)
        emoji       = EMO[name]
        phase_time  = jd2dt(jd).in_tz(TZ).to_iso8601_string()

        # асинхронные задачи для текста
        short_tasks.append(asyncio.create_task(ai_short(d.to_date_string(), name)))
        if name not in long_tasks:
            long_tasks[name] = asyncio.create_task(ai_long(name, d.format('MMMM')))

        # VoC (приближённо)
        voc = compute_voc_for_day(jd)

        # разложение по дням (совместимо с lunar.py)
        cal[d.to_date_string()] = {
            "phase_name"     : name,
            "phase"          : f"{emoji} {name} в {sign}",
            "percent"        : illum,
            "sign"           : sign,
            "phase_time"     : phase_time,
            "advice"         : [],          # позже заполним
            "long_desc"      : "",          # позже заполним
            "void_of_course" : voc,
            "favorable_days" : {k: v["favorable"] for k, v in CATS.items()},
            "unfavorable_days": {k: v["unfavorable"] for k, v in CATS.items()},
        }
        d = d.add(days=1)

    # ждём короткие советы
    short_ready = await asyncio.gather(*short_tasks)
    for idx, day in enumerate(sorted(cal)):
        cal[day]["advice"] = short_ready[idx]

    # распределяем длинные описания по всем дням соответствующей фазы
    for ph_name, tsk in long_tasks.items():
        try:
            long_txt = await tsk
        except Exception:
            long_txt = FALLBACK_LONG[ph_name]
        for rec in cal.values():
            if rec["phase_name"] == ph_name:
                rec["long_desc"] = long_txt

    return cal

# ───── entry-point ────────────────────────────────────────────────────────
async def _main():
    today = pendulum.today()
    data  = await generate(today.year, today.month)
    Path("lunar_calendar.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print("✅ lunar_calendar.json сформирован")

if __name__ == "__main__":
    asyncio.run(_main())
