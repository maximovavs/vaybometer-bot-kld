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
• Астрособытия (знак как ♈ … ♓; VoC > 5 мин)
• «Вините …», рекомендации, факт дня
"""

from __future__ import annotations
import os
import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather      import get_weather, fetch_tomorrow_temps, day_night_stats
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from astro        import astro_events
from gpt          import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214

# коэффициент перевода CPM -> μSv/h (можно переопределить ENV CPM_TO_USVH)
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

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
    return WMO_DESC.get(int(c)) if isinstance(c, (int, float)) and int(c) in WMO_DESC else None

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
    """
    freq = s.get("freq")
    amp  = s.get("amp")
    trend_text = s.get("trend_text") or _trend_text(s.get("trend", "→"))
    status = s.get("status") or _freq_status(freq)[0]
    h7line = s.get("h7_text") or _h7_text(s.get("h7_amp"), s.get("h7_spike"))
    interp = s.get("interpretation") or _gentle_interpretation(s.get("status_code") or _freq_status(freq)[1])

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
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose, (int, float)):
        em, lbl = official_usvh_risk(float(dose))
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ───────────── Давление: локальный тренд ─────────────
def local_pressure_and_trend(wm: Dict[str, Any], threshold_hpa: float = 0.3) -> Tuple[Optional[int], str]:
    cur_p = (wm.get("current") or {}).get("pressure")
    if not isinstance(cur_p, (int, float)):
        hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
        if isinstance(hp, list) and hp:
            cur_p = hp[-1]
            prev = hp[-2] if len(hp) > 1 else None
        else:
            prev = None
    else:
        hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
        prev = hp[-1] if isinstance(hp, list) and hp else None
    arrow = "→"
    if isinstance(cur_p, (int, float)) and isinstance(prev, (int, float)):
        diff = float(cur_p) - float(prev)
        if diff >= threshold_hpa: arrow = "↑"
        elif diff <= -threshold_hpa: arrow = "↓"
    return (int(round(cur_p)) if isinstance(cur_p, (int, float)) else None, arrow)

# ───────────── Зодиаки → символы ─────────────
ZODIAC = {
    "Овен": "♈","Телец": "♉","Близнецы": "♊","Рак": "♋","Лев": "♌",
    ", Дева": "♍","Весы": "♎",", Скорпион": "♏","Стрелец": "♐",
    "Козерог": "♑","Водолей": "♒","Рыбы": "♓",
}
def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s

def build_astro_section(
    date_local: Optional[pendulum.Date] = None,
    tz_post: Union[str, pendulum.Timezone] = "Europe/Kaliningrad",
    tz_calendar: str = "Asia/Nicosia",
) -> str:
    """
    «Астрособытия» для ежедневного поста на конкретную дату.
    Источник — lunar_calendar.json (советы + VoC), фолбэк — astro_events().
    Правила:
      • показываем фазу/знак (если есть) + до 3 советов из календаря;
      • если VoC пересекает 06:00–22:00 локального TZ поста — добавляем строку '⚫️ VoC HH:mm–HH:mm';
      • если календарь пуст — fallback к astro_events (и фильтруем VoC ≤ 5 мин).
    """
    tzp = _as_tz(tz_post)
    date_local = date_local or pendulum.today(tzp)
    date_key = date_local.to_date_string()

    cal = load_calendar("lunar_calendar.json")
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}

    lines: List[str] = ["🌌 <b>Астрособытия</b>"]
    added = False

    # 1) Фаза/знак
    raw_phase = (rec.get("phase") or rec.get("phase_name") or "").strip()
    if raw_phase:
        lines.append(zsym(raw_phase))
        added = True

    # 2) Советы (до 3), срезаем нумерацию типа "1. " / "2) "
    adv = rec.get("advice") or []
    if isinstance(adv, list):
        for a in adv[:3]:
            t = re.sub(r'^\s*\d+[\.\)]\s*', '', str(a).strip())
            if t:
                lines.append(t)
                added = True

    # 3) VoC: если пересекает 06:00–22:00 локального TZ поста
    voc = voc_interval_for_date(rec, tz_local=tz_calendar)
    if voc:
        t1, t2 = voc
        # Переводим во временную зону поста (Калининград)
        t1p, t2p = t1.in_tz(tzp), t2.in_tz(tzp)
        day_start = date_local.at(6, 0)
        day_end   = date_local.at(22, 0)
        if t2p > day_start and t1p < day_end:
            s = max(t1p, day_start).format("HH:mm")
            e = min(t2p, day_end).format("HH:mm")
            lines.append(f"⚫️ VoC {s}–{e}")
            added = True

    # 4) Фолбэк: старый генератор astro_events (и фильтр VoC ≤ 5 мин)
    if not added:
        try:
            astro = astro_events(offset_days=1, show_all_voc=True, tz=tz_calendar)
            filtered: List[str] = []
            for line in (astro or []):
                m = re.search(r"(VoC|VOC|Луна.*без курса).*?(\d+)\s*мин", line, re.IGNORECASE)
                if m and int(m.group(2)) <= 5:
                    continue
                filtered.append(zsym(line))
            if filtered:
                lines.extend(filtered)
                added = True
        except Exception:
            pass

    if not added:
        lines.append("— нет данных —")

    return "\n".join(lines)

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
    cur   = wm.get("current", {}) or {}
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc    = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else cur.get("weathercode")
    wind_ms = kmh_to_ms(cur.get("windspeed"))
    rh_min = stats.get("rh_min"); rh_max = stats.get("rh_max")
    t_day_max = stats.get("t_day_max"); t_night_min = stats.get("t_night_min")

    p_val, p_trend = local_pressure_and_trend(wm, threshold_hpa=0.3)
    press_part = f"{p_val} гПа {p_trend}" if isinstance(p_val, int) else "н/д"

    desc = code_desc(wc)
    kal_parts = [
        f"🏙️ Калининград: дн/ночь {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None)
        else "🏙️ Калининград: дн/ночь н/д",
        desc or None,
        f"💨 {wind_ms:.1f} м/с ({compass(cur.get('winddirection', 0))})" if wind_ms is not None else f"💨 н/д ({compass(cur.get('winddirection', 0))})",
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

    # Солнечный ветер (Bz/Bt/v/n) — показываем только если есть хоть что-то
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

    # если Kp высокий, но ветер спокойный — пояснение
    try:
        if (isinstance(kp, (int, float)) and kp >= 5) and isinstance(wind_status, str) and ("спокой" in wind_status.lower()):
            P.append("ℹ️ По ветру сейчас спокойно; Kp — глобальный индекс за 3 ч.")
    except Exception:
        pass

    # Шуман
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # Астрособытия (из календаря с VoC, фолбэк — astro_events)
    P.append(build_astro_section(
        date_local=tom,
        tz_post=tz_name,
        tz_calendar="Asia/Nicosia",
    ))
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

# ==== lunar helpers for daily posts =========================================

def load_calendar(path: str = "lunar_calendar.json") -> dict:
    """Безопасно читает lunar_calendar.json. Возвращает {} при ошибке/пустоте."""
    try:
        txt = Path(path).read_text("utf-8")
        data = json.loads(txt)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _parse_voc_dt(s: str, tz: pendulum.Timezone):
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
    voc = rec.get("void_of_course") or {}
    s, e = voc.get("start"), voc.get("end")
    if not s or not e:
        return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz)
    t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2:
        return None
    return (t1, t2)

def format_voc_for_post(start: pendulum.DateTime, end: pendulum.DateTime, label: str = "сегодня") -> str:
    """Формат: '⚫️ VoC сегодня 09:10–13:25.'"""
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
