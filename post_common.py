#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

• Море, прогноз Кёнига (день/ночь, м/с, RH min–max, давление)
• Рейтинги городов (d/n, код погоды словами + 🌊)
• Air (IQAir/ваш источник) + Safecast (PM и CPM), пыльца, радиация
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
from settings_klg import SEA_SST_COORD  # not used directly, но оставим импорт

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

def get_schumann_with_fallback() -> Dict[str, Any]:
    arr = _read_schumann_history()
    if not arr:
        return {"freq": None, "amp": None, "trend": "→", "h7_amp": None, "h7_spike": None, "cached": True}

    amps: List[float] = []
    last: Optional[Dict[str, Any]] = None
    for rec in arr:
        if not isinstance(rec, dict):
            continue
        if "freq" in rec and ("amp" in rec or "h7_amp" in rec):
            if isinstance(rec.get("amp"), (int, float)):
                amps.append(float(rec["amp"]))
            last = rec
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
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = s["freq"]; amp = s.get("amp"); trend = s.get("trend", "→")
    h7_amp = s.get("h7_amp"); h7_spike = s.get("h7_spike")
    e = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    base = f"{e} Шуман: {float(f):.2f} Гц"
    if isinstance(amp, (int, float)): base += f" / {float(amp):.2f} pT {trend}"
    else: base += f" / н/д {trend}"
    if isinstance(h7_amp, (int, float)):
        base += f" · H7 {h7_amp:.2f}"
        if isinstance(h7_spike, bool) and h7_spike: base += " ⚡"
    return base

# ───────────── Safecast ─────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists(): return None
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
    if os.getenv("SAFECAST_FILE"): paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths.append(here / "data" / "safecast_kaliningrad.json")

    sc: Optional[Dict[str, Any]] = None
    for p in paths:
        sc = _read_json(p)
        if sc: break
    if not sc: return None

    # staleness: считаем свежими данные не старше 24 часов
    ts = sc.get("ts")
    if not isinstance(ts, (int, float)): return None
    now_ts = pendulum.now("UTC").int_timestamp
    if now_ts - int(ts) > 24*3600:  # устарело
        return None
    return sc

def safecast_pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    """
    Возвращает (emoji, label) по худшему из PM₂.₅/PM₁₀.
    Пороги — усреднённые: PM2.5 [0-15/15-35/35-55/55+], PM10 [0-30/30-50/50-100/100+]
    """
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
    """
    Формирует строки SafeCast для раздела «Качество воздуха».
    Ничего не возвращает ([]) если данных нет/устарели.
    """
    sc = load_safecast()
    if not sc: return []

    pm25 = sc.get("pm25"); pm10 = sc.get("pm10")
    lines: List[str] = []
    if isinstance(pm25, (int, float)) or isinstance(pm10, (int, float)):
        em, lbl = safecast_pm_level(pm25, pm10)
        parts = []
        if isinstance(pm25, (int, float)): parts.append(f"PM₂.₅ {pm25:.0f}")
        if isinstance(pm10, (int, float)): parts.append(f"PM₁₀ {pm10:.0f}")
        lines.append(f"🧪 Safecast: {em} {lbl} · " + " | ".join(parts))

    # Отдельной строкой CPM (если пришёл)
    if isinstance(sc.get("cpm"), (int, float)):
        lines.append(f"📟 CPM: {sc['cpm']:.0f} (медиана 6 ч)")

    return lines

# ───────────── Радиация ─────────────
def _read_local_radiation_usvh() -> Optional[Tuple[float, str]]:
    """
    Пытаемся взять «официальные» локальные данные (μSv/h):
      • ENV RADIATION_FILE  (объект или список)
      • data/radiation.json
      • radiation_hourly.json (список, берём последнюю)
    Считаем свежим ≤ 12ч.
    """
    now_ts = pendulum.now("UTC").int_timestamp
    cand: List[Path] = []
    if os.getenv("RADIATION_FILE"):
        cand.append(Path(os.getenv("RADIATION_FILE")))
    here = Path(__file__).parent
    cand += [here / "data" / "radiation.json",
             here / "radiation.json",
             here / "radiation_hourly.json"]

    for p in cand:
        try:
            if not p.exists():
                continue
            txt = p.read_text("utf-8").strip()
            if not txt:
                continue
            data = json.loads(txt)
        except Exception:
            continue

        # формат: объект
        if isinstance(data, dict):
            ts = data.get("ts")
            val = data.get("usvh") or data.get("dose")
            if isinstance(val, (int, float)) and (not isinstance(ts, (int, float)) or now_ts - int(ts) <= 12*3600):
                return float(val), "официальные"

        # формат: список
        if isinstance(data, list):
            for rec in reversed(data):
                if not isinstance(rec, dict):
                    continue
                ts = rec.get("ts")
                val = rec.get("usvh") or rec.get("dose")
                if isinstance(val, (int, float)) and (not isinstance(ts, (int, float)) or now_ts - int(ts) <= 12*3600):
                    return float(val), "официальные"

    return None

def _format_radiation_line(usvh: float, src_label: str = "") -> str:
    if usvh <= 0.15:  emoji, lvl = "🟢", "низкий"
    elif usvh <= 0.30: emoji, lvl = "🟡", "повышенный"
    else:              emoji, lvl = "🔴", "высокий"
    suffix = f" ({src_label}, {lvl})" if src_label else f" ({lvl})"
    return f"{emoji} Радиация: {usvh:.3f} μSv/h{suffix}"

def radiation_line(lat: float, lon: float) -> str | None:
    """
    1) Пробуем «официальные» (локальные файлы / модуль get_radiation)
    2) Фоллбэк — SafeCast:
       • если есть radiation_usvh — берём её;
       • иначе, если есть cpm — конвертируем: μSv/h = cpm * CPM_TO_USVH
         (ENV CPM_TO_USVH, по умолчанию 0.000571).
    """
    # 1) локальные «официальные»
    lr = _read_local_radiation_usvh()
    if lr:
        return _format_radiation_line(lr[0], lr[1])

    # 1b) онлайн «официальные»
    try:
        rd = get_radiation(lat, lon) or {}
        if isinstance(rd.get("dose"), (int, float)):
            return _format_radiation_line(float(rd["dose"]), "официальные")
    except Exception:
        pass

    # 2) Safecast
    sc = load_safecast()
    if not sc:
        return None

    # прямая μSv/h из Safecast (если collector положил 'radiation_usvh')
    if isinstance(sc.get("radiation_usvh"), (int, float)):
        return _format_radiation_line(float(sc["radiation_usvh"]), "Safecast")

    # конвертация из CPM → μSv/h
    if isinstance(sc.get("cpm"), (int, float)):
        coeff = float(os.getenv("CPM_TO_USVH", "0.000571"))
        usvh = float(sc["cpm"]) * coeff
        return _format_radiation_line(usvh, "Safecast (из CPM)")

    # вдруг в Safecast пришло значение с единицей μSv/h напрямую
    unit = (sc.get("unit") or "").lower()
    val  = sc.get("value")
    if isinstance(val, (int, float)) and ("usv/h" in unit or "µsv/h" in unit or "μsv/h" in unit):
        return _format_radiation_line(float(val), "Safecast")

    return None

# ───────────── Давление: локальный тренд (чувствит. 0.3 гПа) ─────────────
def local_pressure_and_trend(wm: Dict[str, Any], threshold_hpa: float = 0.3) -> Tuple[Optional[int], str]:
    cur_p = (wm.get("current") or {}).get("pressure")
    if not isinstance(cur_p, (int, float)):
        hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
        if isinstance(hp, list) and hp:
            cur_p = hp[-1]; prev = hp[-2] if len(hp) > 1 else None
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
    "Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌",
    ", Дева":"♍","Весы":"♎",", Скорпион":"♏","Стрелец":"♐",
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

    # Калининград — день/ночь, ветер, RH, давление
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz.name)
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

    # Тёплые/холодные
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
    P.append("🏭 <b>Качество воздуха</b>")
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    # Safecast (если свежие данные есть — по «варианту A»)
    P.extend(safecast_block_lines())

    # дымовой индекс
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
    astro = astro_events(offset_days=1, show_all_voc=True)
    filtered: List[str] = []
    for line in (astro or []):
        m = re.search(r"(VoC|VOC|Луна.*без курса).*?(\d+)\s*мин", line, re.IGNORECASE)
        if m:
            mins = int(m.group(2))
            if mins <= 5:
                continue
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
                           sea_cities, other_cities, tz)