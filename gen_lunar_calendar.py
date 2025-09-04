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
• favorable_days / unfavorable_days – словари CATS (рассчитываются)
"""

import os, json, math, asyncio, re
from pathlib import Path
from typing  import Dict, Any, List, Tuple

import pendulum, swisseph as swe
from gpt import gpt_complete  # общая обёртка для LLM

TZ = pendulum.timezone("Asia/Nicosia")
SKIP_SHORT = os.getenv("GEN_SKIP_SHORT", "").strip().lower() in ("1","true","yes","on")
DEBUG_VOC  = os.getenv("DEBUG_VOC", "").strip().lower() in ("1","true","yes","on")

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

FALLBACK_SHORT = [
    "💼 Сфокусируйся на главном.",
    "⛔ Отложи крупные решения.",
    "🪄 5-минутная медитация.",
]

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
    Находит интервал VoC, который пересекает локальные сутки jd_start (00:00 UTC).
    1) Идём вперёд до смены знака Луны (получаем voc_end).
    2) От этой точки идём назад шагом 5 минут, пока НЕ встретим мажорный аспект.
       Первая «без аспектов» точка после последнего аспекта — старт VoC.
    3) Возвращаем пересечение [voc_start, voc_end] с локальными сутками.
    """
    MAX_HOURS_LOOKAHEAD = 96

    # 1) поиск перехода знака
    sign0 = int(swe.calc_ut(jd_start, swe.MOON)[0][0] // 30)
    jd = jd_start
    step_f = 1/48  # 30 минут
    hours = 0.0
    sign_change = None
    while hours <= MAX_HOURS_LOOKAHEAD:
        jd += step_f
        hours += 0.5
        if int(swe.calc_ut(jd, swe.MOON)[0][0] // 30) != sign0:
            sign_change = jd
            break
    if sign_change is None:
        if DEBUG_VOC:
            print("[VoC] ✖ переход знака не найден в окне 96 ч")
        return {"start": None, "end": None}

    # 2) шаг назад от смены знака
    step_b  = 5 / 1440
    jd_back = sign_change - step_b
    found_aspect = False
    while jd_back > jd_start:
        if _has_major_lunar_aspect(jd_back):
            found_aspect = True
            break
        jd_back -= step_b

    voc_start = jd_back + step_b if found_aspect else jd_start
    voc_end   = sign_change

    # 3) пересечение с локальными сутками
    start_dt = jd2dt(voc_start).in_tz(TZ)
    end_dt   = jd2dt(voc_end).in_tz(TZ)
    day_start = jd2dt(jd_start).in_tz(TZ).start_of("day")
    day_end   = day_start.add(days=1)

    if not (start_dt < day_end and end_dt > day_start):
        if DEBUG_VOC:
            print(f"[VoC] ✗ интервал VoC не пересекает локальные сутки: {day_start.to_datetime_string()}")
        return {"start": None, "end": None}

    s = max(start_dt, day_start)
    e = min(end_dt,   day_end)
    if e <= s:
        if DEBUG_VOC:
            print(f"[VoC] ✗ пустое пересечение VoC с сутками")
        return {"start": None, "end": None}

    if DEBUG_VOC:
        print(f"[VoC] ▶ {day_start.format('DD.MM.YYYY')}  start {s.format('DD.MM HH:mm')}  →  end {e.format('DD.MM HH:mm')}")
    return {"start": s.format("DD.MM HH:mm"), "end": e.format("DD.MM HH:mm")}

# ───── санитизация текста ─────────────────────────────────────────────────
_LATIN = re.compile(r"[A-Za-z]+")
def _sanitize_ru(s: str) -> str:
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
        lines = [_sanitize_ru(l).strip() for l in (txt or "").splitlines() if _sanitize_ru(l).strip()]
        if len(lines) >= 2:
            return lines[:3]
    except Exception:
        pass
    return FALLBACK_SHORT[:]

async def gpt_long(name: str, month: str) -> str:
    """Общее описание периода (1–2 предложения)"""
    system = (
        "Ты пишешь краткие (1–2 предложения) пояснения на русском. "
        "Без англицизмов и клише. "
        "Не упоминай название месяца; говори нейтрально: «в этот период», «эта фаза»."
    )
    prompt = (
        f"Фаза: {name}. "
        "Дай 1–2 коротких предложения, описывающих энергетику периода. "
        "Тон спокойный, уверенный, конкретный."
    )
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.7, max_tokens=400)
        if txt:
            return _sanitize_ru(txt.strip())
    except Exception:
        pass
    return FALLBACK_LONG[name]

# ───── «дни особых категорий» (простейшие правила) ────────────────────────
GROWTH_PHASES = {"Растущий серп","Первая четверть","Растущая Луна"}
WANING_PHASES = {"Убывающая Луна","Последняя четверть","Убывающий серп"}

SIGN_GROUPS = {
    "earth": {"Телец","Дева","Козерог"},
    "air":   {"Близнецы","Весы","Водолей"},
    "fire":  {"Овен","Лев","Стрелец"},
    "water": {"Рак","Скорпион","Рыбы"},
}

def _voc_minutes(voc: Dict[str,str]) -> int:
    try:
        if not voc or not voc.get("start") or not voc.get("end"):
            return 0
        s = pendulum.from_format(voc["start"]+" +0200", "DD.MM HH:mm Z")  # смещение не важно, берём локально
        e = pendulum.from_format(voc["end"]  +" +0200", "DD.MM HH:mm Z")
        return max(0, int((e - s).total_minutes()))
    except Exception:
        return 0

def calc_month_categories(cal: Dict[str,Any]) -> Dict[str, Dict[str, List[int]]]:
    """Возвращает словарь категорий с днями месяца (простые эвристики)."""
    cat: Dict[str, Dict[str, List[int]]] = {
        "general":  {"favorable": [], "unfavorable": []},
        "haircut":  {"favorable": [], "unfavorable": []},
        "travel":   {"favorable": [], "unfavorable": []},
        "shopping": {"favorable": [], "unfavorable": []},
        "health":   {"favorable": [], "unfavorable": []},
    }

    for day_str in sorted(cal):
        rec = cal[day_str]
        dnum = int(day_str[-2:])
        sign = rec.get("sign")
        phase = rec.get("phase_name")
        vocm = _voc_minutes(rec.get("void_of_course") or {})

        # general
        if phase in GROWTH_PHASES and (sign in SIGN_GROUPS["earth"] | SIGN_GROUPS["air"] | SIGN_GROUPS["fire"]):
            cat["general"]["favorable"].append(dnum)
        if phase in WANING_PHASES and vocm >= 60:
            cat["general"]["unfavorable"].append(dnum)

        # haircut
        if sign in {"Телец","Лев","Дева"} and ("Полнолуние" not in phase):
            cat["haircut"]["favorable"].append(dnum)
        if sign in {"Рак","Рыбы","Водолей"} or phase == "Полнолуние":
            cat["haircut"]["unfavorable"].append(dnum)

        # travel
        if sign in {"Стрелец","Близнецы"} and vocm < 120:
            cat["travel"]["favorable"].append(dnum)
        if sign in {"Скорпион","Телец"} or vocm >= 180:
            cat["travel"]["unfavorable"].append(dnum)

        # shopping
        if sign in {"Весы","Телец"} and vocm < 120:
            cat["shopping"]["favorable"].append(dnum)
        if sign in {"Овен","Скорпион"} or vocm >= 180:
            cat["shopping"]["unfavorable"].append(dnum)

        # health
        if sign in {"Дева","Козерог"} and phase in GROWTH_PHASES:
            cat["health"]["favorable"].append(dnum)
        if sign in {"Рыбы"} and phase in WANING_PHASES:
            cat["health"]["unfavorable"].append(dnum)

    # сортировка и удаление дублей
    for k in cat:
        cat[k]["favorable"]   = sorted(sorted(set(cat[k]["favorable"])))
        cat[k]["unfavorable"] = sorted(sorted(set(cat[k]["unfavorable"])))
    return cat

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

        # короткие советы
        if SKIP_SHORT:
            short = FALLBACK_SHORT[:]
        else:
            short = []
            short_tasks.append(asyncio.create_task(gpt_short(d.to_date_string(), name)))

        # длинные (по одной на фазу)
        if name not in long_tasks:
            long_tasks[name] = asyncio.create_task(gpt_long(name, ""))

        # VoC
        voc = compute_voc_for_day(jd)

        cal[d.to_date_string()] = {
            "phase_name"     : name,
            "phase"          : f"{emoji} {name} , {sign}",
            "percent"        : illum,
            "sign"           : sign,
            "phase_time"     : phase_time,
            "advice"         : short,       # позже подменится LLM-ом, если не SKIP_SHORT
            "long_desc"      : "",          # позже
            "void_of_course" : voc,
            # заглушка; позже перезапишем рассчитанными словами
            "favorable_days" : {},
            "unfavorable_days": {},
        }
        d = d.add(days=1)

    # дожидаемся коротких советов
    if not SKIP_SHORT and short_tasks:
        short_ready = await asyncio.gather(*short_tasks)
        for idx, day in enumerate(sorted(cal)):
            cal[day]["advice"] = short_ready[idx]

    # тянем длинные тексты в каждую дату своей фазы
    for ph_name, tsk in long_tasks.items():
        try:
            long_txt = await tsk
        except Exception:
            long_txt = FALLBACK_LONG[ph_name]
        for rec in cal.values():
            if rec["phase_name"] == ph_name:
                rec["long_desc"] = long_txt

    # считаем категории по месяцу и кладём одинаково во все записи
    month_cats = calc_month_categories(cal)
    for rec in cal.values():
        rec["favorable_days"]   = month_cats
        rec["unfavorable_days"] = month_cats

    return cal

# ───── entry-point ────────────────────────────────────────────────────────
async def _main():
    today = pendulum.today()
    if DEBUG_VOC:
        print(f"Run ▸ В env WORK_DATE задан → «переопределяем» pendulum.today()")
    data  = await generate(today.year, today.month)
    Path("lunar_calendar.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    print("✅ lunar_calendar.json сформирован")

if __name__ == "__main__":
    asyncio.run(_main())
