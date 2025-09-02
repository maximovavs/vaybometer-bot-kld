#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_lunar_calendar.py — генерация lunar_calendar.json (Gemini, многораундовые запросы, корректный VoC)

- для каждого дня считает фазу Луны, освещённость и знак;
- добавляет короткие советы (3 строки) и длинное описание фазы через Gemini;
- если часть ответов не пришла, делает несколько РАУНДОВ запросов с паузой (без фоллбэков посередине);
- строгость управляется ENV: GEMINI_STRICT=1 — только Gemini, без локальных фоллбэков;
- рассчитывает Void-of-Course: от последнего точного мажорного аспекта к планетам
  (0/60/90/120/180) до входа Луны в следующий знак;
- пишет результат в lunar_calendar.json.

Зависимости: pendulum, pyswisseph, google-generativeai (для Gemini).
"""

from __future__ import annotations
import os
import json
import math
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import pendulum
import swisseph as swe

# Таймзона показа
TZ = pendulum.timezone(os.getenv("LUNAR_TZ", "Asia/Nicosia"))

# ─────────────────────────── Имена и эмодзи ──────────────────────────────
EMO = {
    "Новолуние": "🌑",
    "Растущий серп": "🌒",
    "Первая четверть": "🌓",
    "Растущая Луна": "🌔",
    "Полнолуние": "🌕",
    "Убывающая Луна": "🌖",
    "Последняя четверть": "🌗",
    "Убывающий серп": "🌘",
}
SIGNS = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
]

FALLBACK_LONG: Dict[str, str] = {
    "Новолуние":         "Нулевая точка цикла — закладывайте мечты и намерения.",
    "Растущий серп":     "Энергия прибавляется — время запускать новые задачи.",
    "Первая четверть":   "Первые трудности проявились, корректируйте курс и действуйте.",
    "Растущая Луна":     "Ускорение: расширяйте проекты, укрепляйте связи.",
    "Полнолуние":        "Кульминация: максимум эмоций и результатов.",
    "Убывающая Луна":    "Отпускаем лишнее, завершаем дела, наводим порядок.",
    "Последняя четверть":"Аналитика, ретроспектива и пересмотр стратегии. Спокойно завершайте начатое и планируйте следующие шаги.",
    "Убывающий серп":    "Отдых, ретриты, подготовка к новому циклу. Бережно распределяйте силы и освобождайте пространство для нового.",
}

# Категории «благоприятных» (как в прежних версиях)
CATS = {
    "general" :{"favorable":[2,3,9,27],   "unfavorable":[13,14,24]},
    "haircut" :{"favorable":[2,3,9],      "unfavorable":[]},
    "travel"  :{"favorable":[4,5],        "unfavorable":[]},
    "shopping":{"favorable":[1,2,7],      "unfavorable":[]},
    "health"  :{"favorable":[20,21,27],   "unfavorable":[]},
}

# ─────────────────────────── Gemini client ────────────────────────────────
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_STRICT = os.getenv("GEMINI_STRICT", "0") == "1"

# лимиты/раунды (настраиваются в workflow env)
GEMINI_CONCURRENCY    = int(os.getenv("GEMINI_CONCURRENCY", "3"))    # одновременные запросы
GEMINI_MAX_ROUNDS     = int(os.getenv("GEMINI_MAX_ROUNDS", "4"))     # максимум волн (раундов)
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "2")) # ретраев внутри одного вызова
GEMINI_ROUND_PAUSE    = float(os.getenv("GEMINI_ROUND_PAUSE", "1.0"))# пауза между раундами, сек

_SEM = asyncio.Semaphore(GEMINI_CONCURRENCY)

def _get_gemini_model():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(
            model_name=_GEMINI_MODEL,
            system_instruction=(
                "Ты — астролог со спокойным, поддерживающим стилем; пишешь по-русски, "
                "кратко и без пугающих формулировок. Без медицинских/финансовых советов."
            ),
            generation_config={
                "temperature": 0.65, "top_p": 0.9, "top_k": 40,
                "max_output_tokens": 400
            },
        )
    except Exception:
        return None

GM = _get_gemini_model()

async def _gemini_call(prompt: str) -> str:
    """Вызов Gemini с ретраями и экспоненциальным backoff. Возвращает текст (может быть пустым)."""
    if GM is None:
        return ""
    last_err = None
    for attempt in range(GEMINI_RETRY_ATTEMPTS + 1):
        try:
            r = await asyncio.to_thread(GM.generate_content, prompt)
            return (getattr(r, "text", "") or "").strip()
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (2 ** attempt))  # 0.6s, 1.2s, ...
    return ""

def _split_lines(text: str) -> List[str]:
    out = []
    for raw in (text or "").splitlines():
        s = raw.strip().lstrip("-•—*0123456789. ").strip()
        if s:
            out.append(s)
    return out

# — советы (3 строки)
async def ai_get_advice_from_gemini(date_str: str, phase_name: str) -> List[str]:
    prompt = (
        f"Дата: {date_str}. Фаза Луны: {phase_name}. "
        "Дай 3 очень коротких совета (по одной строке, без пояснений) с эмодзи: "
        "1) 💼 про дела; 2) ⛔ что лучше отложить; 3) 🪄 самоподдержка."
    )
    async with _SEM:
        text = await _gemini_call(prompt)
    lines = _split_lines(text)[:3]
    return lines if len(lines) == 3 else []

# — длинное описание фазы (1–2 предложения)
async def ai_get_phase_long_from_gemini(phase_name: str, month_ru: str) -> str:
    prompt = (
        f"Месяц: {month_ru}. Фаза Луны: {phase_name}. "
        "Дай 1–2 предложения, позитивно и спокойно, без эзотерики и советов."
    )
    async with _SEM:
        txt = await _gemini_call(prompt)
    return (txt or "").strip()

# ─────────────────────────── Астрономия ──────────────────────────────────
PLANETS = [
    swe.SUN, swe.MERCURY, swe.VENUS, swe.MARS, swe.JUPITER, swe.SATURN,
    swe.URANUS, swe.NEPTUNE, swe.PLUTO
]
ASPECTS = [0.0, 60.0, 90.0, 120.0, 180.0]

def _moon_lon(jd_ut: float) -> float:
    return swe.calc_ut(jd_ut, swe.MOON)[0][0] % 360.0

def _body_lon(jd_ut: float, body: int) -> float:
    return swe.calc_ut(jd_ut, body)[0][0] % 360.0

def _ang_diff(a: float, b: float) -> float:
    """Подписанная угловая разность a-b в интервале [-180; 180]."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return d

def _phase_angle(jd_ut: float) -> float:
    m = _moon_lon(jd_ut)
    s = _body_lon(jd_ut, swe.SUN)
    return (m - s) % 360.0

def phase_name_from_angle(angle: float) -> str:
    idx = int(((angle + 22.5) % 360.0) // 45.0)
    return [
        "Новолуние", "Растущий серп", "Первая четверть", "Растущая Луна",
        "Полнолуние", "Убывающая Луна", "Последняя четверть", "Убывающий серп"
    ][idx]

def illumination(angle: float) -> int:
    return int(round(50.0 * (1.0 - math.cos(math.radians(angle)))))

def zodiac_sign(jd_ut: float) -> str:
    lon = _moon_lon(jd_ut)
    return SIGNS[int(lon // 30)]

def _jd_to_pendulum(jd_ut: float) -> pendulum.DateTime:
    y, m, d, h = swe.revjul(jd_ut, swe.GREG_CAL)
    hour = float(h)
    hh = int(hour)
    mm = int((hour - hh) * 60.0)
    ss = int(((hour - hh) * 60.0 - mm) * 60.0)
    return pendulum.datetime(y, m, d, hh, mm, ss, tz="UTC")

# ───── Вспомогательное: ингрeсс Луны в следующий знак ─────
def _next_sign_ingress(jd_start: float) -> float:
    """Возвращает JD UTC ближайшего входа Луны в следующий знак от jd_start."""
    lon0 = _moon_lon(jd_start)
    target = (math.floor(lon0 / 30.0) + 1) * 30.0 % 360.0
    if target <= lon0:
        target = (target + 30.0) % 360.0

    # грубый шаг вперёд — 1 час
    step = 1.0 / 24.0
    t0 = jd_start
    for _ in range(200):
        lon = _moon_lon(t0)
        ahead = (target - lon) % 360.0
        if ahead < 1.0:
            break
        t0 += step

    # бинарное уточнение до ~минуты
    a = t0 - 2 * step
    b = t0 + 2 * step
    for _ in range(40):
        mid = 0.5 * (a + b)
        lon_mid = _moon_lon(mid)
        if (target - lon_mid) % 360.0 < 0.5:
            b = mid
        else:
            a = mid
    return 0.5 * (a + b)

# ───── Поиск точного мажорного аспекта Луны к планетам ─────
def _aspect_function(jd_ut: float, body: int, aspect: float) -> float:
    """f(t) = signed( (λ_Moon - λ_body) - aspect ), сводим в [-180;180]."""
    m = _moon_lon(jd_ut)
    p = _body_lon(jd_ut, body)
    return _ang_diff((m - p), aspect)

def _refine_root(a: float, b: float, body: int, asp: float) -> float:
    """Бисекция до точности ~1 мин."""
    for _ in range(40):
        m = 0.5 * (a + b)
        fm = _aspect_function(m, body, asp)
        if abs(fm) < 1e-4:
            return m
        fa = _aspect_function(a, body, asp)
        if fa * fm <= 0.0:
            b = m
        else:
            a = m
    return 0.5 * (a + b)

def _find_aspects_in_interval(jd_a: float, jd_b: float) -> List[float]:
    """Возвращает JD точных аспектов Луны к планетам в интервале [jd_a, jd_b]."""
    roots: List[float] = []
    step = 1.0 / 24.0  # 1 час
    t = jd_a
    while t < jd_b:
        t_next = min(t + step, jd_b)
        for body in PLANETS:
            for asp in ASPECTS:
                f1 = _aspect_function(t, body, asp)
                f2 = _aspect_function(t_next, body, asp)
                if abs(f1) < 0.3:  # первичная близость
                    roots.append(_refine_root(t - step, t + step, body, asp))
                elif f1 * f2 < 0.0:
                    roots.append(_refine_root(t, t_next, body, asp))
        t = t_next
    roots = sorted(set(round(r, 6) for r in roots))
    return roots

def compute_voc_window(jd_day_start: float) -> Optional[Tuple[float, float]]:
    """
    Возвращает (jd_start, jd_end) VoC, который ПЕРЕСЕКАЕТ сутки, начинающиеся в jd_day_start (UTC).
    Логика: берём ближайший ингрeсс после полуночи и ищем последний точный аспект до него
    в окне [ingress-3 суток; ingress].
    """
    jd_ing = _next_sign_ingress(jd_day_start + 1.0/24.0)  # после начала суток
    jd_a = jd_ing - 3.0
    jd_b = jd_ing
    roots = _find_aspects_in_interval(jd_a, jd_b)
    last_aspect = max([r for r in roots if r < jd_ing], default=None)
    if last_aspect is None:
        return None

    voc_start = last_aspect
    voc_end   = jd_ing

    jd_day_end = jd_day_start + 1.0
    if voc_end <= jd_day_start or voc_start >= jd_day_end:
        return None
    return (max(voc_start, jd_day_start), min(voc_end, jd_day_end))

# ─────────────────────────── Основная генерация ──────────────────────────
async def generate(year: int, month: int) -> Dict[str, Any]:
    start = pendulum.datetime(year, month, 1, tz="UTC")
    days = start.days_in_month

    cal: Dict[str, Any] = {}

    for dnum in range(1, days + 1):
        d = pendulum.datetime(year, month, dnum, tz="UTC")
        jd_mid = swe.julday(d.year, d.month, d.day, 12.0)
        ang = _phase_angle(jd_mid)
        ph_name = phase_name_from_angle(ang)
        illum = illumination(ang)
        sign = zodiac_sign(jd_mid)
        emoji = EMO[ph_name]
        phase_time = d.in_timezone(TZ).to_datetime_string()

        # VoC
        jd0 = swe.julday(d.year, d.month, d.day, 0.0)
        voc_jd = compute_voc_window(jd0)
        voc_ru = None
        if voc_jd:
            st_utc = _jd_to_pendulum(voc_jd[0]).in_timezone(TZ).to_datetime_string()
            en_utc = _jd_to_pendulum(voc_jd[1]).in_timezone(TZ).to_datetime_string()
            voc_ru = {"start": st_utc, "end": en_utc}

        key = d.to_date_string()
        cal[key] = {
            "phase_name": ph_name,
            "phase": f"{emoji} {ph_name}, {sign}",
            "percent": illum,
            "sign": sign,
            "phase_time": phase_time,
            "advice": [],          # <- заполним ниже через многораундовый Gemini
            "long_desc": "",       # <- заполним ниже через многораундовый Gemini
            "void_of_course": voc_ru,
            "favorable_days": CATS,
            "unfavorable_days": CATS,
        }

    # ── МНОГОРАУНДОВЫЕ вызовы Gemini ──────────────────────────────────────
    # собираем недостающие сущности
    pending_advice_days = sorted(cal.keys())
    pending_phase_names = sorted({rec["phase_name"] for rec in cal.values()})

    advice_results: Dict[str, List[str]] = {}
    long_results: Dict[str, str] = {}

    for round_idx in range(GEMINI_MAX_ROUNDS):
        # a) советы по дням (только те, которых нет)
        advice_tasks = []
        for day in pending_advice_days:
            rec = cal[day]
            phase_name = rec["phase_name"]
            advice_tasks.append(asyncio.create_task(ai_get_advice_from_gemini(day, phase_name)))
        if advice_tasks:
            advice_lists = await asyncio.gather(*advice_tasks)
            for day, lines in zip(pending_advice_days, advice_lists):
                if lines:
                    advice_results[day] = lines
            pending_advice_days = [d for d in pending_advice_days if d not in advice_results]

        # b) длинные тексты по фазам (только недостающие)
        long_tasks = []
        for ph in pending_phase_names:
            # найдём любой день этой фазы для передачи месяца
            any_day = next(k for k, v in cal.items() if v["phase_name"] == ph)
            month_ru = pendulum.parse(any_day).format("MMMM")
            long_tasks.append(asyncio.create_task(ai_get_phase_long_from_gemini(ph, month_ru)))
        if long_tasks:
            long_texts = await asyncio.gather(*long_tasks)
            for ph, txt in zip(pending_phase_names, long_texts):
                if txt:
                    long_results[ph] = txt
            pending_phase_names = [ph for ph in pending_phase_names if ph not in long_results]

        # если всё получили — выходим раньше
        if not pending_advice_days and not pending_phase_names:
            break

        # иначе — небольшая пауза между раундами
        await asyncio.sleep(GEMINI_ROUND_PAUSE)

    # Записываем полученное от Gemini
    for day, lines in advice_results.items():
        cal[day]["advice"] = lines
    for ph, txt in long_results.items():
        for rec in cal.values():
            if rec["phase_name"] == ph:
                cal_next = txt
                if not GEMINI_STRICT:
                    # мягкая подстраховка длины в НЕстрогом режиме
                    if len(cal_next) < 80:
                        fb = FALLBACK_LONG.get(ph, "")
                        if cal_next:
                            sep = "" if cal_next.endswith(("!", "?", ".")) else ". "
                            cal_next = cal_next + sep + fb
                        else:
                            cal_next = fb
                rec["long_desc"] = cal_next

    # Добиваем остатки: strict → оставляем пусто; иначе — мягкие фоллбэки
    if not GEMINI_STRICT:
        # советы
        for day in cal.keys():
            if not cal[day]["advice"]:
                cal[day]["advice"] = [
                    "💼 Сфокусируйтесь на главном.",
                    "⛔ Отложите крупные решения.",
                    "🪄 Пять минут тишины и дыхания.",
                ]
        # длинные тексты
        for ph in {rec["phase_name"] for rec in cal.values()}:
            have = any(rec["long_desc"] for rec in cal.values() if rec["phase_name"] == ph)
            if not have:
                fb = FALLBACK_LONG.get(ph, "")
                for rec in cal.values():
                    if rec["phase_name"] == ph:
                        rec["long_desc"] = fb

    return cal

# ─────────────────────────── entry-point ─────────────────────────────────
async def _main():
    # WORK_DATE из env может переопределять «сегодня» (как в workflow)
    today = pendulum.today()
    data = await generate(today.year, today.month)
    Path("lunar_calendar.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    print("✅ lunar_calendar.json сформирован")

if __name__ == "__main__":
    asyncio.run(_main())