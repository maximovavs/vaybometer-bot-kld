#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

• Море, прогноз Кёнига (день/ночь, м/с, RH min–max, давление)
• Рейтинги городов (d/n, код погоды словами + 🌊)
• Air (+ 🔥 Задымление, если не низкое), пыльца, радиация, Safecast
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

# ───────────── Safecast (гибкий парсер локальных JSON) ─────────────
def _pick_latest_record(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        if all(k in obj for k in ("pm25", "pm10")):
            return obj
        if "records" in obj and isinstance(obj["records"], list) and obj["records"]:
            return _pick_latest_record(obj["records"][-1])
    if isinstance(obj, list) and obj:
        return _pick_latest_record(obj[-1])
    return None

def _read_safecast_any(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text("utf-8"))
        rec = _pick_latest_record(data)
        if not isinstance(rec, dict):
            return None
        out: Dict[str, Any] = {}
        for k in ("pm25", "pm10", "aqi", "voc_minutes", "voc", "time", "ts"):
            if k in rec:
                out[k] = rec[k]
        # иногда числа приходят строками
        for k in ("pm25", "pm10", "aqi", "voc_minutes"):
            if k in out and isinstance(out[k], str):
                try:
                    out[k] = float(out[k])
                except Exception:
                    pass
        return out or None
    except Exception as e:
        logging.warning("Safecast read error from %s: %s", path, e)
        return None

def get_safecast() -> Optional[Dict[str, Any]]:
    """
    Источник:
      1) env SAFECAST_FILE
      2) data/safecast_kaliningrad.json
      3) data/safecast_cyprus.json
    Возвращает словарь с ключами: pm25, pm10, aqi (если есть), voc_minutes/voc (если есть).
    """
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths += [here / "data" / "safecast_kaliningrad.json",
              here / "data" / "safecast_cyprus.json"]
    for p in paths:
        rec = _read_safecast_any(p)
        if rec:
            return rec
    return None

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

    # Air + пыльца + радиация + Safecast
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    # добавка из Safecast (если файл есть и есть данные)
    sc = get_safecast()
    if sc:
        parts = []
        if isinstance(sc.get("pm25"), (int, float)):
            parts.append(f"PM₂.₅ {float(sc['pm25']):.0f}")
        if isinstance(sc.get("pm10"), (int, float)):
            parts.append(f"PM₁₀ {float(sc['pm10']):.0f}")
        if isinstance(sc.get("aqi"), (int, float)):
            parts.append(f"AQI {int(round(sc['aqi']))}")
        if parts:
            P.append("🧪 Safecast: " + " | ".join(parts))

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

# ───────────── отправка ─────────────
async def send_common_post(bot: Bot, chat_id: int, region_name: str,
                           sea_label: str, sea_cities, other_label: str,
                           other_cities, tz):
    msg = build_message(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    await bot.send_message(chat_id=chat_id, text=msg,
                           parse_mode=constants.ParseMode.HTML,
                           disable_web_page_preview=True)

async def main_common(bot: Bot, chat_id: int, region_name: str,
                      sea_label: str, sea_cities, other_label: str,
                      other_cities, tz):
    await send_common_post(bot, chat_id, region_name, sea_label,
                           sea_cities, other_label, other_cities, tz)
