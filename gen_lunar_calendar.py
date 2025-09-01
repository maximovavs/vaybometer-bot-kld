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
"""

import os, json, math, asyncio, random
from pathlib import Path
from typing  import Dict, Any, List, Tuple

import pendulum, swisseph as swe

TZ = pendulum.timezone("Asia/Nicosia")

# ───── GPT (по возможности) ────────────────────────────────────────────────
try:
    from openai import OpenAI
    GPT = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) #здесь исправить? 
except Exception:
    GPT = None
# ───────────────────────────────────────────────────────────────────────────

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
      1) ищем ближайший переход Луны в следующий знак (sign_change_jd);
      2) от него «идём назад» с шагом 10 мин, пока снова встречаем аспект —
         это конец последнего аспекта → начало VoC.
    Если начало/конец лежат не в текущих календарных сутках, возвращаем None.
    """
    # 1) ближайший переход знака
    sign0 = int(swe.calc_ut(jd_start, swe.MOON)[0][0] // 30)
    jd = jd_start
    step = 1/24  # час
    while True:
        jd += step
        if int(swe.calc_ut(jd, swe.MOON)[0][0] // 30) != sign0:
            sign_change = jd
            break

    # 2) идём назад до последнего аспекта
    jd_back = sign_change
    step_b  = 10 / 1440   # 10 минут
    while jd_back > jd_start and not _has_major_lunar_aspect(jd_back):
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

# ───── GPT-helpers ─────────────────────────────────────────────────────────
async def gpt_short(date: str, phase: str) -> List[str]:
    """3 одно-строчных совета с emoji или fallback"""
    if GPT:
        prompt = (
            f"Дата {date}, фаза {phase}. Действуй как профессиональный астролог, который хорошо знает как звезды и луна влияют на человека, ты очень хочешь помогать людям делать их жизнь лучше, но при этом ты ценишь каждое слово, ты краток будто каждое слово дорого стоит."
            " Дай 3 лаконичных совета, каждый в одной строке, с emoji: 💼 (работа), ⛔ (отложить), 🪄 (ритуал)."
        )
        try:
            r = GPT.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role":"user","content":prompt}],
                    temperature=0.65)
            return [l.strip() for l in r.choices[0].message.content.splitlines() if l.strip()][:3]
        except Exception:
            pass
    # fallback
    return ["💼 Сфокусируйся на главном.",
            "⛔ Отложи крупные решения.",
            "🪄 5-минутная медитация."]

async def gpt_long(name: str, month: str) -> str:
    """Общее описание периода (1-2 предложения)"""
    if GPT:
        prompt = (
            f"Месяц {month}. Фаза {name}. Действуй как профессиональный астролог, который хорошо знает как звезды и луна влияют на человека, ты очень хочешь помогать людям делать их жизнь лучше, но при этом ты ценишь каждое слово, ты краток будто каждое слово дорого стоит."
            " Дай 2 коротких предложения, описывающих энергетику периода. Тон экспертный, вдохновляющий."
        )
        try:
            r = GPT.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role":"user","content":prompt}],
                    temperature=0.7)
            return r.choices[0].message.content.strip()
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
        short_tasks.append(asyncio.create_task(gpt_short(d.to_date_string(), name)))
        if name not in long_tasks:
            long_tasks[name] = asyncio.create_task(gpt_long(name, d.format('MMMM')))

        # Void-of-Course (приближённо, внутри даты d)
        voc = compute_voc_for_day(jd)

        cal[d.to_date_string()] = {
            "phase_name"     : name,
            "phase"          : f"{emoji} {name} , {sign}", #"phase"          : f"{emoji} {name} в {sign} ({illum}% освещ.)",
            "percent"        : illum,
            "sign"           : sign,
            "phase_time"     : phase_time,
            "advice"         : [],          # позже
            "long_desc"      : "",          # позже
            "void_of_course" : voc,
            "favorable_days" : CATS,
            "unfavorable_days": CATS,
        }
        d = d.add(days=1)

    # ждём GPT
    short_ready = await asyncio.gather(*short_tasks)
    for idx, day in enumerate(sorted(cal)):
        cal[day]["advice"] = short_ready[idx]

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
