#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

• Море, прогноз Кёнига (день/ночь, м/с, RH min–max, давление)
• Рейтинги городов (d/n, код погоды словами + 🌊)
• Air (IQAir/ваш источник) + Safecast (PM и CPM→μSv/h, мягкая шкала 🟢🟡🔵), пыльца
• Радиация из офиц. источника (строгая шкала 🟢🟡🔴)
• Геомагнитка: Kp со «свежестью» + Солнечный ветер (Bz/Bt/v/n + статус)
• Шуман (фоллбэк чтения JSON; либо прямой импорт schumann.get_schumann())
• Астрособытия (микро-LLM 2–3 строки + VoC, извлекаем из lunar_calendar.json)
• «Вините …», рекомендации, факт дня
"""

from __future__ import annotations
import os
import re
import json
import asyncio
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather      import get_weather, fetch_tomorrow_temps, day_night_stats
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete  # gpt_complete — для микро-LLM в «Астрособытиях»

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214

# коэффициент перевода CPM -> μSv/h (можно переопределить ENV CPM_TO_USVH)
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

# Кэш для микро-LLM «Астрособытий»
CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")

# ──────────── утилита: принять tz как объект или как строку ────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    if isinstance(tz, str):
        return pendulum.timezone(tz)
    return tz

# Мэппинг WMO-кодов в короткие текст+эмодзи
WMO_DESC = {
    0: "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
    61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
def code_desc(c: Any) -> Optional[str]:
    try:
        i = int(c)
    except Exception:
        return None
    return WMO_DESC.get(i)

# ───────────── Шуман: чтение JSON-истории ─────────────
def _read_schumann_history() -> List[Dict[str, Any]]:
    candidates: List[Path] = []
    env_path = os.getenv("SCHU_FILE")
    if env_path:
        candidates.append(Path(env_path))
    here = Path(__file__).parent
    candidates += [here / "schumann_hourly.json", here.parent / "schumann_hourly.json"]

    for p in candidates:
        try:
            if p.exists():
                txt = p.read_text("utf-8").strip()
                data = json.loads(txt) if txt else []
                if isinstance(data, list):
                    return data
        except Exception as e:
            logging.warning("Schumann history read error from %s: %s", p, e)
    return []

def _schumann_trend(values: List[float], delta: float = 0.1) -> str:
    if not values:
        return "→"
    tail = values[-24:] if len(values) > 24 else values
    if len(tail) < 2:
        return "→"
    avg_prev = sum(tail[:-1]) / (len(tail) - 1)
    d = tail[-1] - avg_prev
    return "↑" if d >= delta else "↓" if d <= -delta else "→"

# ───────────── Шуман: вспомогалки ─────────────
def _freq_status(freq: Optional[float]) -> tuple[str, str]:
    """
    (label, code):
      🟢 в норме — 7.7..8.1
      🟡 колебания — 7.4..8.4, но вне зелёного коридора
      🔴 сильное отклонение — <7.4 или >8.4
    """
    if not isinstance(freq, (int, float)):
        return "🟡 колебания", "yellow"
    f = float(freq)
    if 7.4 <= f <= 8.4:
        return ("🟢 в норме", "green") if (7.7 <= f <= 8.1) else ("🟡 колебания", "yellow")
    return "🔴 сильное отклонение", "red"

def _trend_text(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def _h7_text(h7_amp: Optional[float], h7_spike: Optional[bool]) -> str:
    if isinstance(h7_amp, (int, float)):
        return f"· H7: {h7_amp:.1f} (⚡ всплеск)" if h7_spike else f"· H7: {h7_amp:.1f} — спокойно"
    return "· H7: — нет данных"

def _gentle_interpretation(code: str) -> str:
    if code == "green":
        return "Волны Шумана близки к норме — организм реагирует как на обычный день."
    if code == "yellow":
        return "Заметны колебания — возможна лёгкая чувствительность к погоде и настроению."
    return "Сильные отклонения — прислушивайтесь к самочувствию и снижайте перегрузки."

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Пытаемся взять состояние из schumann.get_schumann(), иначе читаем JSON.
    Возвращаем унифицированный словарь.
    """
    try:
        import schumann  # локальный модуль
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            return {
                "freq": payload.get("freq"),
                "amp": payload.get("amp"),
                "trend": payload.get("trend", "→"),
                "trend_text": payload.get("trend_text") or _trend_text(payload.get("trend", "→")),
                "status": payload.get("status") or _freq_status(payload.get("freq"))[0],
                "status_code": payload.get("status_code") or _freq_status(payload.get("freq"))[1],
                "h7_text": payload.get("h7_text") or _h7_text(payload.get("h7_amp"), payload.get("h7_spike")),
                "h7_amp": payload.get("h7_amp"),
                "h7_spike": payload.get("h7_spike"),
                "interpretation": payload.get("interpretation") or _gentle_interpretation(
                    payload.get("status_code") or _freq_status(payload.get("freq"))[1]
                ),
                "cached": bool(payload.get("cached")),
            }
    except Exception:
        pass

    # фоллбэк: локальная история
    arr = _read_schumann_history()
    if not arr:
        return {"freq": None, "amp": None, "trend": "→",
                "trend_text": "стабильно", "status": "🟡 колебания", "status_code": "yellow",
                "h7_text": _h7_text(None, None), "h7_amp": None, "h7_spike": None,
                "interpretation": _gentle_interpretation("yellow"), "cached": True}

    amps: List[float] = []
    last: Optional[Dict[str, Any]] = None
    for rec in arr:
        if not isinstance(rec, dict):
            continue
        if isinstance(rec.get("amp"), (int, float)):
            amps.append(float(rec["amp"]))
        last = rec

    trend = _schumann_trend(amps)
    freq = (last.get("freq") if last else None)
    amp = (last.get("amp") if last else None)
    h7_amp = (last.get("h7_amp") if last else None)
    h7_spike = (last.get("h7_spike") if last else None)
    src = ((last or {}).get("src") or "").lower()
    cached = (src == "cache")

    status, code = _freq_status(freq)
    return {
        "freq": freq if isinstance(freq, (int, float)) else None,
        "amp": amp if isinstance(amp, (int, float)) else None,
        "trend": trend,
        "trend_text": _trend_text(trend),
        "status": status,
        "status_code": code,
        "h7_text": _h7_text(h7_amp, h7_spike),
        "h7_amp": h7_amp if isinstance(h7_amp, (int, float)) else None,
        "h7_spike": h7_spike if isinstance(h7_spike, bool) else None,
        "interpretation": _gentle_interpretation(code),
        "cached": cached,
    }

def schumann_line(s: Dict[str, Any]) -> str:
    """
    Возвращает 2 строки:
    1) статус + числа + тренд + H7
    2) мягкая интерпретация
    — если нет чисел, показываем компактную строку без «н/д Гц / н/д».
    """
    freq = s.get("freq")
    amp  = s.get("amp")
    trend_text = s.get("trend_text") or _trend_text(s.get("trend", "→"))
    status = s.get("status") or _freq_status(freq)[0]
    h7line = s.get("h7_text") or _h7_text(s.get("h7_amp"), s.get("h7_spike"))
    interp = s.get("interpretation") or _gentle_interpretation(s.get("status_code") or _freq_status(freq)[1])

    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        main = f"{status} Шуман: данных нет — используем последнюю оценку; тренд: {trend_text} • {h7line}"
        return main + "\n" + interp

    fstr = f"{freq:.2f}" if isinstance(freq, (int, float)) else "н/д"
    astr = f"{amp:.2f} pT" if isinstance(amp, (int, float)) else "н/д"
    main = f"{status} Шуман: {fstr} Гц / {astr} — тренд: {trend_text} • {h7line}"
    return main + "\n" + interp

# ───────────── Safecast / чтение файла ─────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logging.warning("Safecast read error from %s: %s", path, e)
        return None

def load_safecast() -> Optional[Dict[str, Any]]:
    """
    Ищем JSON:
      1) env SAFECAST_FILE
      2) ./data/safecast_kaliningrad.json
    Возвращаем None, если нет/устарело.
    """
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths.append(here / "data" / "safecast_kaliningrad.json")

    sc: Optional[Dict[str, Any]] = None
    for p in paths:
        sc = _read_json(p)
        if sc:
            break
    if not sc:
        return None

    # свежесть не старше 24 часов
    ts = sc.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    now_ts = pendulum.now("UTC").int_timestamp
    if now_ts - int(ts) > 24 * 3600:
        return None
    return sc

# ───────────── риск/шкалы для радиации ─────────────
def safecast_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15:
        return "🟢", "низкий"
    if x <= 0.30:
        return "🟡", "умеренный"
    return "🔵", "выше нормы"

def official_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15:
        return "🟢", "низкий"
    if x <= 0.30:
        return "🟡", "повышенный"
    return "🔴", "высокий"

def safecast_pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    """По худшему из PM2.5/PM10."""
    def level_pm25(x: float) -> int:
        if x <= 15: return 0
        if x <= 35: return 1
        if x <= 55: return 2
        return 3
    def level_pm10(x: float) -> int:
        if x <= 30: return 0
        if x <= 50: return 1
        if x <= 100: return 2
        return 3
    worst = -1
    if isinstance(pm25, (int, float)): worst = max(worst, level_pm25(float(pm25)))
    if isinstance(pm10, (int, float)): worst = max(worst, level_pm10(float(pm10)))
    if worst < 0: return "⚪", "н/д"
    return (["🟢","🟡","🟠","🔴"][worst],
            ["низкий","умеренный","высокий","очень высокий"][worst])

def safecast_block_lines() -> List[str]:
    """Строки SafeCast для раздела «Качество воздуха»."""
    sc = load_safecast()
    if not sc:
        return []
    lines: List[str] = []
    pm25 = sc.get("pm25"); pm10 = sc.get("pm10")
    if isinstance(pm25, (int, float)) or isinstance(pm10, (int, float)):
        em, lbl = safecast_pm_level(pm25, pm10)
        parts = []
        if isinstance(pm25, (int, float)): parts.append(f"PM₂.₅ {pm25:.0f}")
        if isinstance(pm10, (int, float)): parts.append(f"PM₁₀ {pm10:.0f}")
        lines.append(f"🧪 Safecast: {em} {lbl} · " + " | ".join(parts))
    cpm = sc.get("cpm")
    usvh = sc.get("radiation_usvh")
    if not isinstance(usvh, (int, float)) and isinstance(cpm, (int, float)):
        usvh = float(cpm) * CPM_TO_USVH
    if isinstance(usvh, (int, float)):
        em, lbl = safecast_usvh_risk(float(usvh))
        if isinstance(cpm, (int, float)):
            lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
        else:
            lines.append(f"📟 Радиация (Safecast): ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
    elif isinstance(cpm, (int, float)):
        lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM (медиана 6 ч)")
    return lines

# ───────────── Радиация (официальный источник) ─────────────
def radiation_line(lat: float, lon: float) -> Optional[str]:
    """
    Берём дозу из get_radiation(lat, lon) и возвращаем строку для поста
    со строгой шкалой эмодзи 🟢/🟡/🔴. Возвращает None, если данных нет.
    """
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose, (int, float)):
        em, lbl = official_usvh_risk(float(dose))
        return f"{em} Радиация: {float(dose):.3f} μSv/h ({lbl})"
    return None

# ───────────── Зодиаки → символы ─────────────
ZODIAC = {
    "Овен": "♈","Телец": "♉","Близнецы": "♊","Рак": "♋","Лев": "♌",
    "Дева": "♍","Весы": "♎","Скорпион": "♏","Стрелец": "♐",
    "Козерог": "♑","Водолей": "♒","Рыбы": "♓",
}
def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s

# ───────────── Чтение lunar_calendar.json и VoC ─────────────
def load_calendar(path: str = "lunar_calendar.json") -> dict:
    """
    Читает lunar_calendar.json и возвращает словарь дней {YYYY-MM-DD: rec}.
    Поддерживает и «плоский», и новый формат с обёрткой {"days": ...}.
    """
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}

def _parse_voc_dt(s: str, tz: pendulum.tz.timezone.Timezone):
    """Поддерживает ISO и формат 'DD.MM HH:mm'."""
    if not s:
        return None
    try:
        return pendulum.parse(s).in_tz(tz)
    except Exception:
        pass
    try:
        dmy, hm = s.split()
        d, m = map(int, dmy.split("."))
        hh, mm = map(int, hm.split(":"))
        year = pendulum.today(tz).year
        return pendulum.datetime(year, m, d, hh, mm, tz=tz)
    except Exception:
        return None

def voc_interval_for_date(rec: dict, tz_local: str = "Asia/Nicosia"):
    """
    Возвращает (start_dt, end_dt) для VoC из записи дня или None.
    В JSON VoC хранится как строки "DD.MM HH:mm" (локальная TZ) или ISO.
    """
    if not isinstance(rec, dict):
        return None
    voc = (rec.get("void_of_course")
           or rec.get("voc")
           or rec.get("void")
           or {})
    if not isinstance(voc, dict):
        return None
    s = voc.get("start") or voc.get("from") or voc.get("start_time")
    e = voc.get("end")   or voc.get("to")   or voc.get("end_time")
    if not s or not e:
        return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz)
    t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2:
        return None
    return (t1, t2)

def format_voc_for_post(start: pendulum.DateTime, end: pendulum.DateTime, label: str = "сегодня") -> str:
    """Формат: '⚫️ VoC сегодня 09:10–13:25.' (не используется в текущем блоке, оставим как утилиту)."""
    if not start or not end:
        return ""
    return f"⚫️ VoC {label} {start.format('HH:mm')}–{end.format('HH:mm')}."

def lunar_advice_for_date(cal: dict, date_obj) -> list[str]:
    """
    Достаёт советы из календаря на указанную дату.
    date_obj: pendulum.Date/DateTime или строка 'YYYY-MM-DD'.
    """
    key = date_obj.to_date_string() if hasattr(date_obj, "to_date_string") else str(date_obj)
    rec = (cal or {}).get(key, {}) or {}
    adv = rec.get("advice")
    return [str(x).strip() for x in adv][:3] if isinstance(adv, list) and adv else []

# ───────────── Астрособытия (микро-LLM + VoC) ─────────────
def _astro_llm_bullets(date_str: str, phase: str, percent: int, sign: str, voc_text: str) -> List[str]:
    """
    Возвращает 2–3 короткие строки для блока «Астрособытия».
    Кэш: .cache/astro_YYYY-MM-DD.txt
    """
    cache_file = CACHE_DIR / f"astro_{date_str}.txt"
    if cache_file.exists():
        lines = [l.strip() for l in cache_file.read_text("utf-8").splitlines() if l.strip()]
        if lines:
            return lines[:3]

    if not USE_DAILY_LLM:
        return []

    system = (
        "Действуй как АстроЭксперт, ты лучше всех знаешь как энергии луны и звезд влияют на жизнь человека."
        "Ты делаешь очень короткую сводку астрособытий на указанную дату (2–3 строки). "
        "Пиши по-русски, без клише. Используй ТОЛЬКО данную информацию: "
        "фаза Луны, освещённость, знак Луны и интервал Void-of-Course. "
        "Не придумывай других планет и аспектов. Каждая строка начинается с эмодзи."
    )
    prompt = (
        f"Дата: {date_str}. Фаза Луны: {phase} ({percent}% освещённости), знак: {sign or 'н/д'}. "
        f"Void-of-Course: {voc_text or 'нет'}."
    )
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.5, max_tokens=180)
        lines = [l.strip() for l in (txt or "").splitlines() if l.strip()]
        if lines:
            cache_file.write_text("\n".join(lines[:3]), "utf-8")
            return lines[:3]
    except Exception:
        pass
    return []

def build_astro_section(date_local: Optional[pendulum.Date] = None,
                        tz_local: str = "Asia/Nicosia") -> str:
    """
    Собирает блок «Астрособытия»: 2–3 строки от LLM (если доступен) +
    VoC (полностью, если есть в этот день). Данные берём из lunar_calendar.json (ключи days).
    """
    tz = pendulum.timezone(tz_local)
    date_local = date_local or pendulum.today(tz)
    date_key = date_local.format("YYYY-MM-DD")

    cal = load_calendar("lunar_calendar.json")  # возвращает {YYYY-MM-DD: rec}
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}

    # извлечения полей
    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip()

    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try:
        percent = int(round(float(percent)))
    except Exception:
        percent = 0

    sign = rec.get("sign") or rec.get("zodiac") or ""

    # VoC интервал в локальной TZ — показываем полностью, без «часов бодрствования»
    voc_text = ""
    voc = voc_interval_for_date(rec, tz_local=tz_local)
    if voc:
        t1, t2 = voc
        voc_text = f"{t1.format('HH:mm')}–{t2.format('HH:mm')}"

    # 1) пробуем LLM
    bullets = _astro_llm_bullets(
        date_local.format("DD.MM.YYYY"),
        phase_name,
        int(percent or 0),
        sign,
        voc_text
    )

    # 2) фолбэк – советы из календаря
    if not bullets:
        adv = rec.get("advice") or []
        bullets = [f"• {a}" for a in adv[:3]] if adv else []

    # 3) последний фолбэк – «жёсткий»
    if not bullets:
        base = f"🌙 Фаза: {phase_name}" if phase_name else "🌙 Лунный день в норме"
        prm  = f" ({percent}%)" if isinstance(percent, int) and percent else ""
        bullets = [base + prm, (f"♒ Знак: {sign}" if sign else "— знак Луны н/д")]

    # формируем блок
    lines = ["🌌 <b>Астрособытия</b>"]
    lines += [zsym(x) for x in bullets[:3]]
    if voc_text:
        lines.append(f"⚫️ VoC: {voc_text}")
    return "\n".join(lines)

# ───────────── Помощники: hourly на завтра (ветер/давление) ─────────────
def _pick(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default

def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try:
            out.append(pendulum.parse(str(t)))
        except Exception:
            continue
    return out

def _nearest_index_for_day(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int, tz: pendulum.Timezone) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_dt, best_diff = None, None, None
    for i, dt in enumerate(times):
        # times уже в локальном часовом поясе (как вернул weather.get_weather), но на всякий случай:
        try:
            dt_local = dt.in_tz(tz)
        except Exception:
            dt_local = dt
        if dt_local.date() != date_obj:
            continue
        diff = abs((dt_local - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_dt, best_diff = i, dt_local, diff
    return best_i

def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list:
        return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0:
        return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

def pick_tomorrow_header_metrics(wm: Dict[str, Any], tz: pendulum.Timezone) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """
    Возвращает:
      wind_ms (float|None), wind_dir_deg (int|None),
      pressure_hpa (int|None), pressure_trend ("↑","↓","→")
    Берём ближайшее к 12:00; тренд — относительно 06:00.
    """
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    if not times:
        return None, None, None, "→"

    tomorrow = pendulum.now(tz).add(days=1).date()

    idx_noon = _nearest_index_for_day(times, tomorrow, prefer_hour=12, tz=tz)
    idx_morn = _nearest_index_for_day(times, tomorrow, prefer_hour=6, tz=tz)
    if idx_noon is None:
        # возьмём среднее за день
        idxs = [i for i, t in enumerate(times) if t.in_tz(tz).date() == tomorrow]
        if not idxs:
            return None, None, None, "→"
        # ветер — усреднение, давление — медиана/среднее последнего
        spd_key = _pick(hourly, "windspeed_10m", "windspeed", default=[])
        dir_key = _pick(hourly, "winddirection_10m", "winddirection", default=[])
        prs_key = _pick(hourly, "surface_pressure", default=[])
        try:
            speeds = [float(spd_key[i]) for i in idxs if i < len(spd_key)]
        except Exception:
            speeds = []
        try:
            dirs = [float(dir_key[i]) for i in idxs if i < len(dir_key)]
        except Exception:
            dirs = []
        try:
            prs_vals = [float(prs_key[i]) for i in idxs if i < len(prs_key)]
        except Exception:
            prs_vals = []
        wind_ms = kmh_to_ms(sum(speeds)/len(speeds)) if speeds else None
        wind_dir = int(round(_circular_mean_deg(dirs))) if dirs and _circular_mean_deg(dirs) is not None else None
        press_val = int(round(sum(prs_vals)/len(prs_vals))) if prs_vals else None
        return wind_ms, wind_dir, press_val, "→"

    # точечные значения (около 12:00 и 06:00)
    spd_arr = _pick(hourly, "windspeed_10m", "windspeed", default=[])
    dir_arr = _pick(hourly, "winddirection_10m", "winddirection", default=[])
    prs_arr = hourly.get("surface_pressure", [])
    try:
        spd = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
    except Exception:
        spd = None
    try:
        wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
    except Exception:
        wdir = None
    try:
        p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
    except Exception:
        p_noon = None
    try:
        p_morn = float(prs_arr[idx_morn]) if (idx_morn is not None and idx_morn < len(prs_arr)) else None
    except Exception:
        p_morn = None

    wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else None
    wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None

    trend = "→"
    if isinstance(p_noon, (int, float)) and isinstance(p_morn, (int, float)):
        diff = p_noon - p_morn
        if diff >= 0.3: trend = "↑"
        elif diff <= -0.3: trend = "↓"

    press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
    return wind_ms, wind_dir, press_val, trend

# ───────────── сообщение ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str]) -> str:

    tz_obj = _as_tz(tz)
    tz_name = tz_obj.name

    P: List[str] = []
    today = pendulum.now(tz_obj).date()
    tom   = today.add(days=1)

    # Заголовок
    P.append(f"<b>🌅 {region_name}: погода на завтра ({tom.format('DD.MM.YYYY')})</b>")

    # Калининград — день/ночь, ветер, RH, давление
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz_name)
    wm    = get_weather(KLD_LAT, KLD_LON) or {}

    # Завтрашний код погоды берём из daily[1]
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc    = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None

    # RH и t по нашим helper’ам
    rh_min = stats.get("rh_min"); rh_max = stats.get("rh_max")
    t_day_max = stats.get("t_day_max"); t_night_min = stats.get("t_night_min")

    # Ветер/давление — строго на завтра из hourly (около 12:00)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_tomorrow_header_metrics(wm, tz_obj)
    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms, (int, float)) else "💨 н/д")
    )
    press_part = f"{press_val} гПа {press_trend}" if isinstance(press_val, int) else "н/д"

    desc = code_desc(wc)
    kal_parts = [
        f"🏙️ Калининград: дн/ночь {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None)
        else "🏙️ Калининград: дн/ночь н/д",
        desc or None,
        wind_part,
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        f"🔹 {press_part}",
    ]
    P.append(" • ".join([x for x in kal_parts if x]))
    P.append("———")

    # Морские города (топ-5)
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    for city, (la, lo) in sea_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
        temps_sea[city] = (tmax, tmin or tmax, wcx, get_sst(la, lo))
    if temps_sea:
        P.append(f"🎖️ <b>{sea_label}</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, (city, (d, n, wcx, sst_c)) in enumerate(sorted(temps_sea.items(),
                                                              key=lambda kv: kv[1][0], reverse=True)[:5]):
            line = f"{medals[i]} {city}: {d:.1f}/{n:.1f}"
            descx = code_desc(wcx)
            if descx:
                line += f", {descx}"
            if sst_c is not None:
                line += f" 🌊 {sst_c:.1f}"
            P.append(line)
        P.append("———")

    # Тёплые/холодные
    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in other_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
        temps_oth[city] = (tmax, tmin or tmax, wcx)
    if temps_oth:
        P.append("🔥 <b>Тёплые города, °C</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0], reverse=True)[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.1f}/{n:.1f}" + (f" {descx}" if descx else ""))
        P.append("❄️ <b>Холодные города, °C</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0])[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.1f}/{n:.1f}" + (f" {descx}" if descx else ""))
        P.append("———")

    # Air + Safecast + пыльца + радиация (офиц.)
    P.append("🏭 <b>Качество воздуха</b>")
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    # Safecast (мягкая шкала)
    P.extend(safecast_block_lines())

    # дымовой индекс — показываем ТОЛЬКО если не низкое/н/д
    em_sm, lbl_sm = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl_sm and str(lbl_sm).lower() not in ("низкое", "низкий", "нет", "н/д"):
        P.append(f"🔥 Задымление: {em_sm} {lbl_sm}")

    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    # официальная радиация (строгая шкала)
    if (rl := radiation_line(KLD_LAT, KLD_LON)):
        P.append(rl)
    P.append("———")

    # Геомагнитка: Kp (со свежестью)
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, kp_src = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 1 else "н/д"
        kp_ts, kp_src = None, "n/d"

    age_txt = ""
    if isinstance(kp_ts, int) and kp_ts > 0:
        try:
            age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
            if age_min > 180:
                age_txt = f", 🕓 {age_min // 60}ч назад"
            elif age_min >= 0:
                age_txt = f", {age_min} мин назад"
        except Exception:
            age_txt = ""

    if isinstance(kp, (int, float)):
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks}{age_txt})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    # Солнечный ветер (Bz/Bt/v/n)
    sw = get_solar_wind() or {}
    bz = sw.get("bz"); bt = sw.get("bt"); v = sw.get("speed_kms"); n = sw.get("density")
    wind_status = sw.get("status", "н/д")
    parts = []
    if isinstance(bz, (int, float)): parts.append(f"Bz {bz:.1f} nT")
    if isinstance(bt, (int, float)): parts.append(f"Bt {bt:.1f} nT")
    if isinstance(v,  (int, float)): parts.append(f"v {v:.0f} км/с")
    if isinstance(n,  (int, float)): parts.append(f"n {n:.1f} см⁻³")
    if parts:
        P.append("🌬️ Солнечный ветер: " + ", ".join(parts) + f" — {wind_status}")
    try:
        if (isinstance(kp, (int, float)) and kp >= 5) and isinstance(wind_status, str) and ("спокой" in wind_status.lower()):
            P.append("ℹ️ По ветру сейчас спокойно; Kp — глобальный индекс за 3 ч.")
    except Exception:
        pass

    # Шуман
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # Астрособытия (LLM+VoC из lunar_calendar.json) — на завтра по Asia/Nicosia
    tz_nic = pendulum.timezone("Asia/Nicosia")
    date_for_astro = pendulum.today(tz_nic).add(days=1)
    P.append(build_astro_section(date_local=date_for_astro, tz_local="Asia/Nicosia"))
    P.append("———")

    # Вывод + советы
    culprit = "магнитные бури" if isinstance(kp, (int, float)) and ks and ks.lower() == "буря" else "неблагоприятный прогноз погоды"
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    try:
        _, tips = gpt_blurb(culprit)
        for t in tips[:3]:
            t = t.strip()
            if t:
                P.append(t)
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")

    P.append("———")
    P.append(f"📚 {get_fact(tom, region_name)}")
    return "\n".join(P)

# ───────────── отправка ─────────────
async def send_common_post(bot: Bot, chat_id: int, region_name: str,
                           sea_label: str, sea_cities, other_label: str,
                           other_cities, tz: Union[pendulum.Timezone, str]):
    msg = build_message(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    await bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True
    )

async def main_common(bot: Bot, chat_id: int, region_name: str,
                      sea_label: str, sea_cities, other_label: str,
                      other_cities, tz: Union[pendulum.Timezone, str]):
    await send_common_post(bot, chat_id, region_name, sea_label, sea_cities, other_label, other_cities, tz)
