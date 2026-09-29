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
• favorable_days / unfavorable_days – словари категорий месяца
• month_voc   – список всех VoC месяца (локальное время)
"""

import os, json, math, asyncio, re
from pathlib import Path
from typing  import Dict, Any, List, Tuple

import pendulum, swisseph as swe
from gpt import gpt_complete  # общая обёртка LLM

# ───── настройки ────────────────────────────────────────────────────────────
TZ = pendulum.timezone("Asia/Nicosia")
SKIP_SHORT = os.getenv("GEN_SKIP_SHORT", "").strip().lower() in ("1","true","yes","on")
DEBUG_VOC  = os.getenv("DEBUG_VOC",   "").strip().lower() in ("1","true","yes","on")
MIN_VOC_MIN = int(os.getenv("MIN_VOC_MINUTES", "0") or 0)   # порог для вывода месячного списка

def _dbg(*args: Any) -> None:
    if DEBUG_VOC:
        print("[VoC]", *args)

# ───── справочники ──────────────────────────────────────────────────────────
EMO = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

SIGNS = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
         "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]

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

# ───── helpers времени/эфемерид ─────────────────────────────────────────────
def jd2dt(jd: float) -> pendulum.DateTime:
    """Julian Day (UT) → pendulum UTC"""
    return pendulum.from_timestamp((jd - 2440587.5) * 86400, tz="UTC")

def dt2jd(dt: pendulum.DateTime) -> float:
    """pendulum DateTime (UTC) → Julian Day"""
    ts = dt.int_timestamp
    return ts/86400 + 2440587.5

def phase_name(angle: float) -> str:
    idx = int(((angle + 22.5) % 360) // 45)
    return [
        "Новолуние","Растущий серп","Первая четверть","Растущая Луна",
        "Полнолуние","Убывающая Луна","Последняя четверть","Убывающий серп"
    ][idx]

def moon_lon(jd: float) -> float:
    return swe.calc_ut(jd, swe.MOON)[0][0]

def sun_lon(jd: float) -> float:
    return swe.calc_ut(jd, swe.SUN)[0][0]

def moon_sign_idx(jd: float) -> int:
    return int(moon_lon(jd) // 30) % 12

def compute_phase(jd: float) -> Tuple[str,int,str]:
    lon_s = sun_lon(jd)
    lon_m = moon_lon(jd)
    ang   = (lon_m - lon_s) % 360
    illum = int(round((1 - math.cos(math.radians(ang))) / 2 * 100))
    name  = phase_name(ang)
    sign  = SIGNS[int(lon_m // 30) % 12]
    return name, illum, sign

# ───── Void-of-Course (по сменам знаков) ────────────────────────────────────
ASPECTS = {0,60,90,120,180}   # мажоры
ORBIS   = 1.5                  # ±градусы
PLANETS = [swe.SUN,swe.MERCURY,swe.VENUS,swe.MARS,
           swe.JUPITER,swe.SATURN,swe.URANUS,swe.NEPTUNE,swe.PLUTO]

def _has_major_lunar_aspect(jd: float) -> bool:
    """Есть ли лунный мажорный аспект к планете в данный момент?"""
    lon_m = moon_lon(jd)
    for p in PLANETS:
        lon_p = swe.calc_ut(jd, p)[0][0]
        a = abs((lon_m - lon_p + 180) % 360 - 180)
        for asp in ASPECTS:
            if abs(a - asp) <= ORBIS:
                return True
    return False

def _next_sign_change(jd_from: float) -> float:
    """Следующая смена знака после jd_from (UT). Поиск + бинарное уточнение до ~1 мин."""
    start_sign = moon_sign_idx(jd_from)
    step = 1/96  # 15 минут
    jd = jd_from
    # грубый проход
    while moon_sign_idx(jd) == start_sign:
        jd += step
    # бинарное уточнение на отрезке [jd-step, jd]
    lo, hi = jd - step, jd
    while (hi - lo) * 1440 > 1.0:   # точность ~1 мин
        mid = (lo + hi) / 2
        if moon_sign_idx(mid) == start_sign:
            lo = mid
        else:
            hi = mid
    return hi

def _last_aspect_before(jd_end: float, search_hours: int = 48) -> float | None:
    """
    Идём назад от jd_end (обычно момент смены знака) и ищем последнюю точку,
    где аспект ещё был. Возвращаем jd этой точки (внутри окна аспекта),
    либо None, если в пределах окна аспект не найден.
    """
    step = 5/1440  # 5 минут
    jd = jd_end - step
    limit = jd_end - search_hours/24
    while jd > limit:
        if _has_major_lunar_aspect(jd):
            # нашли участок с аспектом; откатимся до границы «не было аспекта»
            while _has_major_lunar_aspect(jd) and jd > limit:
                jd -= step
            return jd  # это уже точка «без аспекта» перед окном
        jd -= step
    return None

def find_voc_intervals_for_month(first_day: pendulum.DateTime, last_day: pendulum.DateTime) -> List[Tuple[pendulum.DateTime, pendulum.DateTime]]:
    """
    Находит *все* интервалы VoC, которые начинаются/заканчиваются рядом с границами месяца.
    Возвращает список пар (start_utc_dt, end_utc_dt) в UTC.
    """
    # берём запас по 2 суток до/после, чтобы захватить переходы вокруг границ
    # стало (Date.at -> DateTime с нужным временем и TZ)
    fd = first_day.subtract(days=2)
    ld = last_day.add(days=2)
    start_utc = pendulum.datetime(fd.year, fd.month, fd.day, 0, 0, 0, tz="UTC")
    end_utc   = pendulum.datetime(ld.year, ld.month, ld.day, 23, 59, 59, tz="UTC")


    jd = dt2jd(start_utc)
    out: List[Tuple[pendulum.DateTime, pendulum.DateTime]] = []

    while True:
        sc = _next_sign_change(jd)                     # JD смены знака
        sc_dt = jd2dt(sc)                               # UTC
        if sc_dt > end_utc:
            break

        la_jd = _last_aspect_before(sc)                 # JD перед началом VoC (точка «без аспекта»)
        if la_jd is None:
            voc_start_jd = sc                           # деградация, нулевой интервал (не должно быть часто)
        else:
            voc_start_jd = la_jd + 5/1440              # старт VoC сразу после последнего аспекта

        voc_end_jd = sc
        s_dt = jd2dt(voc_start_jd)                      # UTC
        e_dt = jd2dt(voc_end_jd)                        # UTC

        if (e_dt - s_dt).total_seconds() >= max(0, MIN_VOC_MIN*60):
            out.append((s_dt, e_dt))
            _dbg(f"VoC найден: {s_dt.in_tz(TZ).format('DD.MM HH:mm')} → {e_dt.in_tz(TZ).format('DD.MM HH:mm')}")
        else:
            _dbg("VoC слишком короткий, пропущен")

        # идём дальше за смену знака
        jd = sc + 1/24  # +1 час
    return out

def _intersect_with_local_day(s: pendulum.DateTime, e: pendulum.DateTime, day_local: pendulum.DateTime) -> Tuple[pendulum.DateTime | None, pendulum.DateTime | None]:
    """Пересечение [s,e] (UTC) с локальными сутками day_local@00:00..+24:00 в TZ."""
    start_day = day_local.in_tz(TZ).start_of("day")
    end_day   = start_day.add(days=1)
    s_loc = s.in_tz(TZ)
    e_loc = e.in_tz(TZ)
    if not (s_loc < end_day and e_loc > start_day):
        return None, None
    a = max(s_loc, start_day)
    b = min(e_loc, end_day)
    if b <= a:
        return None, None
    return a, b

# ───── санитизация текста ─────────────────────────────────────────────────
_LATIN = re.compile(r"[A-Za-z]+")
def _sanitize_ru(s: str) -> str:
    s = _LATIN.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ───── GPT-helpers ────────────────────────────────────────────────────────
async def gpt_short(date: str, phase: str) -> List[str]:
    system = (
        "Ты пишешь очень краткие практичные рекомендации на русском языке. "
        "Без англицизмов и штампов. Каждая рекомендация в одной строке, "
        "с нужным эмодзи в начале. Без префиксов типа 'Совет:'."
    )
    prompt = (
        f"Дата {date}, фаза {phase}.Действуй как профессиональный астролог, который хорошо знает как звезды и луна влияют на человека, ты очень хочешь помогать людям делать их жизнь лучше, но при этом ты ценишь каждое слово, ты краток будто каждое слово дорого стоит."
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
    return FALLBACK_SHORT[:]

async def gpt_long(name: str, month: str) -> str:
    system = (
        "Ты пишешь краткие (1–2 предложения) пояснения на русском. "
        "Без англицизмов и общих слов. "
        "Не упоминай месяц; используй формулировки «в этот период», «эта фаза»."
    )
    prompt = (
        f"Фаза: {name}. Действуй как профессиональный астролог, который хорошо знает как звезды и луна влияют на человека, ты очень хочешь помогать людям делать их жизнь лучше, но при этом ты ценишь каждое слово, ты краток будто каждое слово дорого стоит."
        "Дай 2 коротких предложения, описывающих энергетику периода. "
        "Тон экспертный, вдохновляющий, уверенный, конкретный."
    )
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.7, max_tokens=400)
        if txt:
            return _sanitize_ru(txt.strip())
    except Exception:
        pass
    return FALLBACK_LONG[name]

# ───── категории месяца (детерминированные правила) ───────────────────────
GROWING = {"Растущий серп","Первая четверть","Растущая Луна"}
WANING  = {"Убывающая Луна","Последняя четверть","Убывающий серп"}

def _voc_minutes_pair(s: pendulum.DateTime | None, e: pendulum.DateTime | None) -> int:
    if not s or not e:
        return 0
    return int((e - s).total_seconds() // 60)

def calc_month_categories(cal: Dict[str, Any]) -> Dict[str, Dict[str, List[int]]]:
    cats = {
        "general":  {"favorable": [], "unfavorable": []},
        "haircut":  {"favorable": [], "unfavorable": []},
        "travel":   {"favorable": [], "unfavorable": []},
        "shopping": {"favorable": [], "unfavorable": []},
        "health":   {"favorable": [], "unfavorable": []},
    }
    for day in sorted(cal.keys()):
        rec  = cal[day]
        dnum = int(day[-2:])

        phase = rec["phase_name"]
        sign  = rec["sign"]
        # посчитанные при генерации локальные строки → обратно в даты
        s_str = rec["void_of_course"]["start"]
        e_str = rec["void_of_course"]["end"]
        s_dt = e_dt = None
        if s_str and e_str:
            s_dt = pendulum.from_format(s_str, "DD.MM HH:mm", tz=TZ)
            e_dt = pendulum.from_format(e_str, "DD.MM HH:mm", tz=TZ)
        voc_min = _voc_minutes_pair(s_dt, e_dt)

        # правила
        if phase in GROWING and sign not in {"Скорпион"}:
            cats["general"]["favorable"].append(dnum)
        if phase in WANING or voc_min >= 60:
            cats["general"]["unfavorable"].append(dnum)

        if sign in {"Телец","Лев","Дева"} and phase != "Полнолуние":
            cats["haircut"]["favorable"].append(dnum)
        if sign in {"Рак","Рыбы","Водолей"} or phase == "Полнолуние":
            cats["haircut"]["unfavorable"].append(dnum)

        if sign in {"Стрелец","Близнецы"} and voc_min < 120:
            cats["travel"]["favorable"].append(dnum)
        if sign in {"Скорпион","Телец"} or voc_min >= 180:
            cats["travel"]["unfavorable"].append(dnum)

        if sign in {"Весы","Телец"} and voc_min < 120:
            cats["shopping"]["favorable"].append(dnum)
        if sign in {"Овен","Скорпион"} or voc_min >= 180:
            cats["shopping"]["unfavorable"].append(dnum)

        if sign in {"Дева","Козерог"} and phase in GROWING:
            cats["health"]["favorable"].append(dnum)
        if sign == "Рыбы" and phase in WANING:
            cats["health"]["unfavorable"].append(dnum)

    # удалим дубликаты/отсортируем
    for c in cats.values():
        for k in ("favorable","unfavorable"):
            c[k] = sorted(sorted(set(c[k])))
    return cats

# ───── основной генератор ─────────────────────────────────────────────────
async def generate(year: int, month: int) -> Dict[str,Any]:
    swe.set_ephe_path(".")   # где лежат efemeris
    first = pendulum.date(year, month, 1)
    last  = first.end_of('month')

    # список всех VoC (UTC), затем используем для каждого дня
    all_voc = find_voc_intervals_for_month(first, last)

    cal: Dict[str,Any] = {}
    long_tasks, short_tasks = {}, []

    d = first
    while d <= last:
        # UT-полночь выбранной даты
        jd = swe.julday(d.year, d.month, d.day, 0.0)

        # лунные данные
        name, illum, sign = compute_phase(jd)
        emoji      = EMO[name]
        phase_time = jd2dt(jd).in_tz(TZ).to_iso8601_string()

        # советы
        short = FALLBACK_SHORT[:] if SKIP_SHORT else []
        if not SKIP_SHORT:
            short_tasks.append(asyncio.create_task(gpt_short(d.to_date_string(), name)))
        if name not in long_tasks:
            long_tasks[name] = asyncio.create_task(gpt_long(name, ""))

        # пересечение VoC с сутками даты d
        day_local = pendulum.datetime(d.year, d.month, d.day, 0, 0, tz=TZ)
        voc_s = voc_e = None
        for s_utc, e_utc in all_voc:
            s, e = _intersect_with_local_day(s_utc, e_utc, day_local)
            if s and e:
                voc_s, voc_e = s, e
                break
        voc_obj = {
            "start": voc_s.format("DD.MM HH:mm") if voc_s else None,
            "end"  : voc_e.format("DD.MM HH:mm") if voc_e else None
        }

        cal[d.to_date_string()] = {
            "phase_name"     : name,
            "phase"          : f"{emoji} {name} , {sign}",
            "percent"        : illum,
            "sign"           : sign,
            "phase_time"     : phase_time,
            "advice"         : short,       # либо LLM позже, либо фолбэк
            "long_desc"      : "",          # позже
            "void_of_course" : voc_obj,
            # временно заполним, позже перезапишем результатом calc_month_categories
            "favorable_days" : {},
            "unfavorable_days": {},
        }
        d = d.add(days=1)

    # собрать короткие советы (если не отключены)
    if not SKIP_SHORT and short_tasks:
        short_ready = await asyncio.gather(*short_tasks)
        for idx, day in enumerate(sorted(cal)):
            cal[day]["advice"] = short_ready[idx]

    # раздать длинные описания по всем дням одной фазы
    for ph_name, tsk in long_tasks.items():
        try:
            long_txt = await tsk
        except Exception:
            long_txt = FALLBACK_LONG[ph_name]
        for rec in cal.values():
            if rec["phase_name"] == ph_name:
                rec["long_desc"] = long_txt

    # категории месяца
    cats = calc_month_categories(cal)
    for rec in cal.values():
        rec["favorable_days"]   = cats
        rec["unfavorable_days"] = cats  # для совместимости со старыми скриптами

    # верхнеуровневый список VoC за месяц (локальное время)
    month_voc = [
        {
            "start": s.in_tz(TZ).format("DD.MM HH:mm"),
            "end"  : e.in_tz(TZ).format("DD.MM HH:mm"),
        }
        for (s, e) in all_voc
        if (e - s).total_seconds() >= max(0, MIN_VOC_MIN*60)
    ]

    return {"days": cal, "month_voc": month_voc}

# ───── entry-point ────────────────────────────────────────────────────────
async def _main():
    today = pendulum.today()
    data  = await generate(today.year, today.month)
    Path("lunar_calendar.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    print("✅ lunar_calendar.json сформирован")

if __name__ == "__main__":
    asyncio.run(_main())
