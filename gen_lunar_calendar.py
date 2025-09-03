#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_lunar_calendar.py
──────────────────────────────────────────────────────────────────────────────
Формирует файл lunar_calendar.json со всеми полями, нужными и для ежедневных
постов (короткие советы) и для месячного (длинные описания фаз + VoC).

• phase, percent, sign, phase_time
• advice      – 3 строки «💼 …», «⛔ …», «🪄 …»
• long_desc   – 1-2 предложения на фазу (разово на месяц)
• void_of_course: {start, end}  (UTC → Asia/Nicosia в JSON)
• favorable_days / unfavorable_days – словари CATS

ENV:
  GEN_SKIP_SHORT = "1"  → пропускать генерацию ежедневных коротких советов (для месячного прогона)
"""

import os, json, math, asyncio, random, re
from pathlib import Path
from typing  import Dict, Any, List, Tuple

import pendulum, swisseph as swe
from gpt import gpt_complete  # используем общую обёртку

TZ = pendulum.timezone("Asia/Nicosia")
SKIP_SHORT = os.getenv("GEN_SKIP_SHORT", "0") == "1"

EMO = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

FALLBACK_LONG: Dict[str,str] = {
    "Новолуние"        :"Нулевая точка цикла — закладывайте мечты и намерения.",
    "Растущий серп"    :"Энергия прибавляется — время запускать новые задачи.",
    "Первая четверть"  :"Первые трудности проявились, корректируйте курс и действуйте.",
    "Растущая Луна"    :"Ускорение: расширяйте проекты, укрепляйте связи.",
    "Полнолуние"       :"Кульминация: максимум эмоций и результатов.",
    "Убывающая Луна"   :"Отпускаем лишнее, завершаем дела, наводим порядок.",
    "Последняя четверть":"Аналитика, ретроспектива и пересмотр стратегии.",
    "Убывающий серп"   :"Отдых, ретриты, подготовка к новому циклу.",
}

# «карманные» даты (пример — замените на реальные свои таблицы)
CATS = {
    "general" :{"favorable":[2,3,9,27],   "unfavorable":[13,14,24]},
    "haircut" :{"favorable":[2,3,9],      "unfavorable":[]},
    "travel"  :{"favorable":[4,5],        "unfavorable":[]},
    "shopping":{"favorable":[1,2,7],      "unfavorable":[]},
    "health"  :{"favorable":[20,21,27],   "unfavorable":[]},
}

# ───── helpers ─────────────────────────────────────────────────────────────
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
ASPECTS = {0,60,90,120,180}          # основные мажоры
ORBIS   = 1.5                        # ±градусы для аспекта
PLANETS = [swe.SUN,swe.MERCURY,swe.VENUS,swe.MARS,
           swe.JUPITER,swe.SATURN,swe.URANUS,swe.NEPTUNE,swe.PLUTO]

def _has_major_lunar_aspect(jd: float) -> bool:
    """Есть ли точный лунный мажорный аспект к планете в данный момент?"""
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    for p in PLANETS:
        lon_p = swe.calc_ut(jd, p)[0][0]
        a = abs((lon_m - lon_p + 180) % 360 - 180)
        for asp in ASPECTS:
            if abs(a - asp) <= ORBIS:
                return True
    return False

def compute_voc_for_day(jd_start: float) -> Dict[str,str]:
    """
    Находит интервал Void-of-Course внутри суток jd_start (00:00 UT).
    Алгоритм:
      1) ищем ближайший переход Луны в следующий знак (sign_change_jd) вплоть до нескольких суток вперёд,
         с более частым шагом и уточняем границу бинарным поиском;
      2) от него идём назад с шагом 10 мин, пока снова встречаем аспект —
         это конец последнего аспекта → начало VoC.
    Если оба края интервала не попадают в текущие календарные сутки (локальные), возвращаем start/end = None.
    """
    # 1) ближайший переход знака (скан вперёд до 3 суток, шаг 5 минут)
    sign0 = int(swe.calc_ut(jd_start, swe.MOON)[0][0] // 30)
    step = 5 / 1440.0          # 5 минут в днях
    max_ahead_days = 3.0       # защитный предел
    jd = jd_start
    sign_change = None
    while True:
        jd += step
        if jd - jd_start > max_ahead_days:
            return {"start": None, "end": None}
        if int(swe.calc_ut(jd, swe.MOON)[0][0] // 30) != sign0:
            # уточняем границу смены знака бинарным поиском до ~1 минуты
            lo, hi = jd - step, jd
            for _ in range(10):
                mid = (lo + hi) / 2
                if int(swe.calc_ut(mid, swe.MOON)[0][0] // 30) != sign0:
                    hi = mid
                else:
                    lo = mid
            sign_change = hi
            break

    # 2) идём назад до последнего аспекта
    jd_back = sign_change
    step_b  = 10 / 1440.0      # 10 минут
    limit_back = sign_change - 1.5  # не дальше 1.5 суток назад
    while jd_back > max(jd_start, limit_back) and not _has_major_lunar_aspect(jd_back):
        jd_back -= step_b
    voc_start = jd_back
    voc_end   = sign_change

    start_dt = jd2dt(voc_start).in_tz(TZ)
    end_dt   = jd2dt(voc_end).in_tz(TZ)

    cur_day = jd2dt(jd_start).in_tz(TZ).date()
    if start_dt.date() != cur_day and end_dt.date() != cur_day:
        return {"start": None, "end": None}

    return {
        "start": start_dt.format("DD.MM HH:mm"),
        "end"  : end_dt.format("DD.MM HH:mm")
    }

# ───── санитизация текста ─────────────────────────────────────────────────
_LATIN = re.compile(r"[A-Za-z]+")
def _sanitize_ru(s: str) -> str:
    # вычищаем латиницу и лишние пробелы
    s = _LATIN.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ───── GPT-helpers (через обёртку) ────────────────────────────────────────
async def gpt_short(date: str, phase: str) -> List[str]:
    """3 одно-строчных совета с emoji или fallback"""
    system = (
        "Ты пишешь очень краткие практичные рекомендации на русском языке. "
        "Без англицизмов и штампов. Каждая рекомендация ровно в одной строке, "
        "начинай с нужного эмодзи и не добавляй префиксов типа 'Совет:'."
    )
    prompt = (
        f"Дата {date}, фаза {phase}. "
        "Дай 3 лаконичных рекомендации, каждая — в одной строке, с emoji: "
        "💼 (работа), ⛔ (отложить), 🪄 (ритуал). "
        "Пиши по-русски. Не упоминай название месяца."
    )
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.65, max_tokens=300)
        lines = [ _sanitize_ru(l).strip() for l in (txt or "").splitlines() if _sanitize_ru(l).strip() ]
        if len(lines) >= 2:
            return lines[:3]
    except Exception:
        pass
    # fallback
    return [
        "💼 Сфокусируйся на главном.",
        "⛔ Отложи крупные решения.",
        "🪄 5-минутная медитация.",
    ]

async def gpt_long(name: str, month: str) -> str:
    """Общее описание периода (1-2 предложения)"""
    system = (
        "Ты пишешь краткие (1–2 предложения) пояснения на русском. "
        "Без англицизмов, без общих фраз типа «энергия Вселенной». "
        "Не упоминай название месяца; говори нейтрально: «в этот период», «эта фаза»."
    )
    prompt = (
        f"Фаза: {name}. "
        "Дай 1–2 коротких предложения, описывающих энергетику периода. "
        "Тон спокойный, уверенный, конкретный. Без клише, всё по-русски."
    )
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.7, max_tokens=400)
        if txt:
            return _sanitize_ru(txt.strip())
    except Exception:
        pass
    return FALLBACK_LONG[name]

# ───── основной генератор ─────────────────────────────────────────────────
async def generate(year: int, month: int) -> Dict[str,Any]:
    swe.set_ephe_path(".")                      # где лежат efemeris
    first = pendulum.date(year, month, 1)
    last  = first.end_of('month')

    cal: Dict[str,Any] = {}
    long_tasks, short_tasks = {}, []

    d = first
    while d <= last:
        jd = swe.julday(d.year, d.month, d.day, 0.0)

        # лунные данные
        name, illum, sign = compute_phase(jd)
        emoji       = EMO[name]
        phase_time  = jd2dt(jd).in_tz(TZ).to_iso8601_string()

        # GPT async-задачи
        if not SKIP_SHORT:
            short_tasks.append(asyncio.create_task(gpt_short(d.to_date_string(), name)))
        if name not in long_tasks:
            # месяц в подсказку больше не передаём (чтобы не вызывал англицизмы)
            long_tasks[name] = asyncio.create_task(gpt_long(name, ""))

        # Void-of-Course (приближённо, внутри даты d)
        voc = compute_voc_for_day(jd)

        cal[d.to_date_string()] = {
            "phase_name"     : name,
            "phase"          : f"{emoji} {name} , {sign}",  # прежний формат
            "percent"        : illum,
            "sign"           : sign,
            "phase_time"     : phase_time,
            "advice"         : [],          # позже (или пусто при SKIP_SHORT)
            "long_desc"      : "",          # позже
            "void_of_course" : voc,
            "favorable_days" : CATS,
            "unfavorable_days": CATS,
        }
        d = d.add(days=1)

    # ждём GPT (короткие советы) — только если не отключали
    if not SKIP_SHORT and short_tasks:
        short_ready = await asyncio.gather(*short_tasks)
        for idx, day in enumerate(sorted(cal)):
            cal[day]["advice"] = short_ready[idx]

    # длинные описания по одной задаче на фазу
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
        json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    print("✅ lunar_calendar.json сформирован")

if __name__ == "__main__":
    asyncio.run(_main())