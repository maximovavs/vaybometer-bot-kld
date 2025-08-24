#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

• Море, прогноз Кёнига (день/ночь, м/с, RH min–max, давление)
• Рейтинги городов (d/n, код погоды словами + 🌊)
• Air (+ 🔥 Задымление, если не низкое), пыльца, радиация, SafeCast
• Kp, Шуман (с фоллбэком чтения JSON; h7_amp/h7_spike)
• Астрособытия (знак как ♈ … ♓; VoC > 5 мин)
• «Вините …», рекомендации, факт дня
"""

from __future__ import annotations
import os
import re
import json
import math
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import pendulum
from telegram import Bot, constants

from utils       import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather     import get_weather, fetch_tomorrow_temps, day_night_stats
from air         import get_air, get_sst, get_kp
from pollen      import get_pollen
from radiation   import get_radiation
from astro       import astro_events
from gpt         import gpt_blurb
from settings_klg import SEA_SST_COORD            # точка в заливе

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214

# Мэппинг WMO-кодов в короткие текст+эмодзи
WMO_DESC = {
    0:"☀️ ясно", 1:"⛅ ч.обл", 2:"☁️ обл", 3:"🌥 пасм",
    45:"🌫 туман", 48:"🌫 изморозь", 51:"🌦 морось",
    61:"🌧 дождь", 71:"❄️ снег", 95:"⛈ гроза",
}
def code_desc(c: Any) -> str | None:
    return WMO_DESC.get(int(c)) if isinstance(c, (int, float)) and int(c) in WMO_DESC else None

# ───────────── Шуман: чтение JSON-истории (оба формата) ─────────────
def _read_schumann_history() -> List[Dict[str, Any]]:
    """
    Возвращает список записей из schumann_hourly.json (может быть v1 или v2).
    Поиск файла:
      1) env SCHU_FILE
      2) ./schumann_hourly.json (рядом с post_common.py)
      3) ../schumann_hourly.json (корень репо)
    """
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
    """Стрелка тренда по сравнению с усреднением предыдущих значений."""
    if not values:
        return "→"
    tail = values[-24:] if len(values) > 24 else values
    if len(tail) < 2:
        return "→"
    avg_prev = sum(tail[:-1]) / (len(tail) - 1)
    d = tail[-1] - avg_prev
    return "↑" if d >= delta else "↓" if d <= -delta else "→"

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Возвращает словарь для рендеринга строки Шумана:
      {"freq": 7.83|None, "amp": float|None, "trend": "↑/→/↓", "h7_amp": float|None, "h7_spike": bool|None, "cached": bool}
    Поддерживает оба формата JSON (старый и v2).
    """
    arr = _read_schumann_history()
    if not arr:
        return {"freq": None, "amp": None, "trend": "→", "h7_amp": None, "h7_spike": None, "cached": True}

    # Собираем амплитуды для тренда, ищем последний валидный рекорд
    amps: List[float] = []
    last: Optional[Dict[str, Any]] = None

    for rec in arr:
        if not isinstance(rec, dict):
            continue
        # v2 формат: {"ts", "freq", "amp", "h7_amp", "h7_spike", "src", ...}
        if "freq" in rec and ("amp" in rec or "h7_amp" in rec):
            if isinstance(rec.get("amp"), (int, float)):
                amps.append(float(rec["amp"]))
            last = rec
        # старый формат — поддержка на всякий случай (ключи могли отличаться)
        elif "amp" in rec:
            try:
                amps.append(float(rec["amp"]))
            except Exception:
                pass
            last = rec

    trend = _schumann_trend(amps)

    if last is None:
        return {"freq": None, "amp": None, "trend": trend, "h7_amp": None, "h7_spike": None, "cached": True}

    freq = last.get("freq", 7.83) if isinstance(last.get("freq"), (int, float)) else 7.83
    amp = last.get("amp") if isinstance(last.get("amp"), (int, float)) else None
    h7_amp = last.get("h7_amp") if isinstance(last.get("h7_amp"), (int, float)) else None
    h7_spike = last.get("h7_spike") if isinstance(last.get("h7_spike"), bool) else None
    src = (last.get("src") or "").lower()
    cached = (src == "cache")

    return {"freq": freq, "amp": amp, "trend": trend, "h7_amp": h7_amp, "h7_spike": h7_spike, "cached": cached}

def schumann_line(s: Dict[str, Any]) -> str:
    """
    Рендер строки для поста.
    Пример: 🟢 Шуман: 7.83 Гц / 4.2 pT ↑  (H7: 0.9 ⚡)
    """
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = s["freq"]
    amp = s.get("amp")
    trend = s.get("trend", "→")
    h7_amp = s.get("h7_amp")
    h7_spike = s.get("h7_spike")

    # эмодзи по частоте (условно)
    e = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"

    base = f"{e} Шуман: {float(f):.2f} Гц"
    if isinstance(amp, (int, float)):
        base += f" / {float(amp):.2f} pT {trend}"
    else:
        base += f" / н/д {trend}"

    if isinstance(h7_amp, (int, float)):
        base += f" · H7 {h7_amp:.2f}"
        if isinstance(h7_spike, bool):
            base += " ⚡" if h7_spike else ""
    return base

# ───────────── Радиация ─────────────
def radiation_line(lat: float, lon: float) -> str | None:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if dose is None:
        return None
    if dose <= 0.15:
        emoji, lvl = "🟢", "низкий"
    elif dose <= 0.30:
        emoji, lvl = "🟡", "повышенный"
    else:
        emoji, lvl = "🔴", "высокий"
    return f"{emoji} Радиация: {dose:.3f} μSv/h ({lvl})"

# ───────────── Давление: локальный тренд (чувствит. 0.3 гПа) ─────────────
def local_pressure_and_trend(wm: Dict[str, Any], threshold_hpa: float = 0.3) -> Tuple[Optional[int], str]:
    """
    Возвращает (давление в гПа округл., стрелка тренда).
    Тренд считаем по последним двум точкам surface_pressure (если есть) с порогом threshold_hpa.
    """
    # текущее из current
    cur_p = (wm.get("current") or {}).get("pressure")
    if not isinstance(cur_p, (int, float)):
        hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
        if isinstance(hp, list) and hp:
            cur_p = hp[-1]
            prev = hp[-2] if len(hp) > 1 else None
        else:
            prev = None
    else:
        # попробуем вытащить предыдущее из hourly
        hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
        prev = hp[-1] if isinstance(hp, list) and hp else None

    arrow = "→"
    if isinstance(cur_p, (int, float)) and isinstance(prev, (int, float)):
        diff = float(cur_p) - float(prev)
        if diff >= threshold_hpa:
            arrow = "↑"
        elif diff <= -threshold_hpa:
            arrow = "↓"

    return (int(round(cur_p)) if isinstance(cur_p, (int, float)) else None, arrow)

# ───────────── SafeCast (локальный кэш Калининграда) ─────────────
SAFECAST_DEFAULT_FILE_KLD = os.getenv("SAFECAST_KLD_FILE", str(Path(__file__).parent / "data" / "safecast_kaliningrad.json"))
SAFECAST_STALE_HOURS      = int(os.getenv("SAFECAST_STALE_HOURS", "6"))

def _safecast_read(path: str) -> Optional[Dict[str, Any]]:
    try:
        p = Path(path)
        if not p.exists():
            return None
        return json.loads(p.read_text("utf-8"))
    except Exception as e:
        logging.warning("SafeCast read error: %s", e)
        return None

def _safecast_pick_latest(obj: Any) -> Optional[Dict[str, Any]]:
    """Гибко находим последний рекорд с полями измерений."""
    if isinstance(obj, dict):
        if any(k in obj for k in ("pm25", "pm2_5", "pm2.5", "pm10", "no2", "so2", "co")):
            return obj
        for k in ("records", "data", "items"):
            if k in obj and isinstance(obj[k], list) and obj[k]:
                return _safecast_pick_latest(obj[k][-1])
    if isinstance(obj, list) and obj:
        return _safecast_pick_latest(obj[-1])
    return None

def _to_dt_utc(t: Any):
    from datetime import datetime, timezone
    if t is None:
        return None
    if isinstance(t, (int, float)):
        try:
            return datetime.fromtimestamp(float(t), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return None
    return None

def _classify(name: str, value: float) -> str:
    """Пороги (примерно US EPA/WHO). Возвращает уровень: good/moderate/unhealthy_sensitive/unhealthy/very_unhealthy/hazardous."""
    x = float(value)
    n = name.lower()
    if n in ("pm25","pm2_5","pm2.5"):
        if x <= 12:   return "good"
        if x <= 35.4: return "moderate"
        if x <= 55.4: return "unhealthy_sensitive"
        if x <= 150:  return "unhealthy"
        if x <= 250:  return "very_unhealthy"
        return "hazardous"
    if n == "pm10":
        if x <= 54:   return "good"
        if x <= 154:  return "moderate"
        if x <= 254:  return "unhealthy_sensitive"
        if x <= 354:  return "unhealthy"
        if x <= 424:  return "very_unhealthy"
        return "hazardous"
    if n == "no2":
        if x <= 40:   return "good"
        if x <= 100:  return "moderate"
        if x <= 200:  return "unhealthy_sensitive"
        if x <= 400:  return "unhealthy"
        if x <= 1000: return "very_unhealthy"
        return "hazardous"
    if n == "so2":
        if x <= 20:   return "good"
        if x <= 50:   return "moderate"
        if x <= 125:  return "unhealthy_sensitive"
        if x <= 350:  return "unhealthy"
        if x <= 500:  return "very_unhealthy"
        return "hazardous"
    if n == "co":  # mg/m³
        if x <= 4:   return "good"
        if x <= 9:   return "moderate"
        if x <= 12:  return "unhealthy_sensitive"
        if x <= 15:  return "unhealthy"
        if x <= 20:  return "very_unhealthy"
        return "hazardous"
    return "good"

def _level_emoji(level: str) -> str:
    return {
        "good":"🟢", "moderate":"🟡", "unhealthy_sensitive":"🟠",
        "unhealthy":"🔴", "very_unhealthy":"🟣", "hazardous":"🟤"
    }.get(level, "⚪")

def build_safecast_block_for_kaliningrad(
    path: str = SAFECAST_DEFAULT_FILE_KLD,
    stale_hours: int = SAFECAST_STALE_HOURS
) -> Optional[str]:
    """
    Читает локальный кэш SafeCast и возвращает готовый блок.
    Если нет/устарело — возвращает None (ничего не выводим).
    Поддерживаются поля: ts|timestamp (unix/ISO), pm25|pm2_5|pm2.5, pm10, no2, so2, co.
    """
    raw = _safecast_read(path)
    if not isinstance(raw, (dict, list)):
        return None
    rec = _safecast_pick_latest(raw)
    if not isinstance(rec, dict):
        return None

    # timestamp
    ts = rec.get("ts") or rec.get("timestamp") or rec.get("time")
    dt = _to_dt_utc(ts)
    if not dt:
        return None
    from datetime import datetime, timezone
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    if age_h > stale_hours:
        return None

    # значения
    def pick(*names):
        for n in names:
            if n in rec and isinstance(rec[n], (int, float, str)):
                try:
                    return float(rec[n])
                except Exception:
                    pass
        return None

    pm25 = pick("pm25","pm2_5","pm2.5")
    pm10 = pick("pm10")
    no2  = pick("no2")
    so2  = pick("so2")
    co   = pick("co")  # mg/m³

    if all(v is None for v in (pm25, pm10, no2, so2, co)):
        return None

    # худший уровень
    levels: List[str] = []
    for name, val in (("pm25", pm25), ("pm10", pm10), ("no2", no2), ("so2", so2), ("co", co)):
        if val is not None:
            levels.append(_classify(name, val))
    order = ["good","moderate","unhealthy_sensitive","unhealthy","very_unhealthy","hazardous"]
    worst = max(levels, key=lambda s: order.index(s)) if levels else "good"
    emoji = _level_emoji(worst)
    label = {
        "good":"good", "moderate":"moderate", "unhealthy_sensitive":"unhealthy (SG)",
        "unhealthy":"unhealthy", "very_unhealthy":"very unhealthy", "hazardous":"hazardous"
    }[worst]

    # блок
    lines: List[str] = []
    lines.append("📡 SafeCast — загрязнение (по городу)")
    lines.append(f"{emoji} Уровень: {label}")
    det: List[str] = []
    if pm25 is not None: det.append(f"PM2.5: {pm25:.1f} µg/m³")
    if pm10 is not None: det.append(f"PM10: {pm10:.1f} µg/m³")
    if no2  is not None: det.append(f"NO₂: {no2:.0f} µg/m³")
    if so2  is not None: det.append(f"SO₂: {so2:.0f} µg/m³")
    if co   is not None: det.append(f"CO: {co:.1f} mg/m³")
    if det:
        lines.append("· " + " | ".join(det))
    when = dt.astimezone().strftime("%H:%M")
    lines.append(f"Источник: SafeCast · {when}")
    return "\n".join(lines)

# ───────────── Зодиаки → символы ─────────────
ZODIAC = {
    "Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌",
    "Дева":"♍","Весы":"♎","Скорпион":"♏","Стрелец":"♐",
    "Козерог":"♑","Водолей":"♒","Рыбы":"♓",
}
def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s

# ───────────── сообщение ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: pendulum.Timezone) -> str:

    P: List[str] = []
    today = pendulum.now(tz).date()
    tom   = today.add(days=1)

    # Заголовок
    P.append(f"<b>🌅 {region_name}: погода на завтра ({tom.format('DD.MM.YYYY')})</b>")

    # (строка «Темп. моря (центр залива)» — убрана по пожеланию)

    # Калининград — день/ночь, код словами (если надёжен), ветер м/с, RH min–max, давление
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz.name)
    wm    = get_weather(KLD_LAT, KLD_LON) or {}
    cur   = wm.get("current", {}) or {}
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc    = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else cur.get("weathercode")
    wind_ms = kmh_to_ms(cur.get("windspeed"))
    rh_min = stats.get("rh_min"); rh_max = stats.get("rh_max")
    t_day_max = stats.get("t_day_max"); t_night_min = stats.get("t_night_min")

    # давление с локальным трендом ↑/↓/→ (0.3 гПа)
    p_val, p_trend = local_pressure_and_trend(wm, threshold_hpa=0.3)
    press_part = f"{p_val} гПа {p_trend}" if isinstance(p_val, int) else "н/д"

    desc = code_desc(wc)  # может вернуть None — тогда не выводим
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

    # Морские города (топ‑5)
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    for city, (la, lo) in sea_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz.name)
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

    # Тёплые/холодные (топ‑3 / топ‑3)
    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in other_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz.name)
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

    # Air + пыльца + радиация + SafeCast
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    # SafeCast (Калининград) — отдельный мини‑блок; скрывается если нет/устарело
    sc_block = build_safecast_block_for_kaliningrad()
    if sc_block:
        P.append(sc_block)

    em, lbl = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl != "низкое":
        P.append(f"🔥 Задымление: {em} {lbl}")
    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
    if (rl := radiation_line(KLD_LAT, KLD_LON)):
        P.append(rl)
    P.append("———")

    # Kp + Шуман
    kp, ks = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})" if kp is not None else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # Астрособытия (скрываем VoC <= 5 минут)
    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events(offset_days=1, show_all_voc=True)  # получаем всё, потом фильтруем
    filtered: List[str] = []
    for line in (astro or []):
        m = re.search(r"(VoC|VOC|Луна.*без курса).*?(\d+)\s*мин", line, re.IGNORECASE)
        if m:
            mins = int(m.group(2))
            if mins <= 5:
                continue  # пропускаем короткие VoC
        filtered.append(line)
    if filtered:
        P.extend([zsym(line) for line in filtered])
    else:
        P.append("— нет данных —")
    P.append("———")

    # Вывод + советы
    culprit = "магнитные бури" if kp is not None and ks and ks.lower() == "буря" else "неблагоприятный прогноз погоды"
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

# ───────────── о