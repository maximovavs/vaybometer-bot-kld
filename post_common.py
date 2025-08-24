#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).
...
"""

from __future__ import annotations
import os, re, json, math, asyncio, logging
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
from settings_klg import SEA_SST_COORD

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

KLD_LAT, KLD_LON = 54.710426, 20.452214
WMO_DESC = {0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",48:"🌫 изморозь",51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"}
def code_desc(c: Any) -> str | None:
    return WMO_DESC.get(int(c)) if isinstance(c,(int,float)) and int(c) in WMO_DESC else None

# -------- SafeCast: гибкий ридер summary-файла --------
def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None

def get_safecast() -> Optional[Dict[str, Any]]:
    """
    Ищет summary-файл с ключами: pm25?, pm10?, radiation? (μSv/h), ts?.
    1) env SAFECAST_FILE
    2) data/safecast_kaliningrad.json
    3) data/safecast_cyprus.json
    """
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths += [here / "data" / "safecast_kaliningrad.json",
              here / "data" / "safecast_cyprus.json"]
    for p in paths:
        if p.exists():
            obj = _read_json(p)
            if isinstance(obj, dict):
                # фильтр устаревших: не старше 24 ч
                ts = obj.get("ts")
                if isinstance(ts, (int, float)):
                    import time
                    if (time.time() - float(ts)) > 24*3600:
                        continue
                # возьмём только полезные поля
                out: Dict[str, Any] = {}
                for k in ("pm25","pm10","aqi","radiation","voc_minutes"):
                    v = obj.get(k)
                    if isinstance(v,(int,float)):
                        out[k] = float(v)
                if out:
                    return out
    return None

# --------- Шуман (как было у нас) ---------
def _read_schumann_history() -> List[Dict[str, Any]]:
    env_path = os.getenv("SCHU_FILE")
    candidates: List[Path] = []
    if env_path: candidates.append(Path(env_path))
    here = Path(__file__).parent
    candidates += [here/"schumann_hourly.json", here.parent/"schumann_hourly.json"]
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
    if not values: return "→"
    tail = values[-24:] if len(values)>24 else values
    if len(tail)<2: return "→"
    avg_prev = sum(tail[:-1])/(len(tail)-1)
    d = tail[-1]-avg_prev
    return "↑" if d>=delta else "↓" if d<=-delta else "→"

def get_schumann_with_fallback() -> Dict[str, Any]:
    arr = _read_schumann_history()
    if not arr:
        return {"freq":None,"amp":None,"trend":"→","h7_amp":None,"h7_spike":None,"cached":True}
    amps: List[float] = []; last: Optional[Dict[str,Any]] = None
    for rec in arr:
        if not isinstance(rec, dict): continue
        if "freq" in rec and ("amp" in rec or "h7_amp" in rec):
            if isinstance(rec.get("amp"),(int,float)): amps.append(float(rec["amp"]))
            last = rec
        elif "amp" in rec:
            try: amps.append(float(rec["amp"]))
            except: pass
            last = rec
    trend = _schumann_trend(amps)
    if last is None:
        return {"freq":None,"amp":None,"trend":trend,"h7_amp":None,"h7_spike":None,"cached":True}
    freq = last.get("freq",7.83) if isinstance(last.get("freq"),(int,float)) else 7.83
    amp  = last.get("amp") if isinstance(last.get("amp"),(int,float)) else None
    h7a  = last.get("h7_amp") if isinstance(last.get("h7_amp"),(int,float)) else None
    h7s  = last.get("h7_spike") if isinstance(last.get("h7_spike"),bool) else None
    src  = (last.get("src") or "").lower()
    return {"freq":freq,"amp":amp,"trend":trend,"h7_amp":h7a,"h7_spike":h7s,"cached":(src=="cache")}

def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None: return "🎵 Шуман: н/д"
    f = s["freq"]; amp = s.get("amp"); tr = s.get("trend","→")
    h7a = s.get("h7_amp"); h7s = s.get("h7_spike")
    e = "🔴" if f<7.6 else "🟣" if f>8.1 else "🟢"
    base = f"{e} Шуман: {float(f):.2f} Гц"
    base += f" / {float(amp):.2f} pT {tr}" if isinstance(amp,(int,float)) else f" / н/д {tr}"
    if isinstance(h7a,(int,float)):
        base += f" · H7 {h7a:.2f}" + (" ⚡" if isinstance(h7s,bool) and h7s else "")
    return base

# -------- Радиоактивность, fallback на наш модуль --------
def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if dose is None: return None
    if dose <= 0.15: emoji, lvl = "🟢", "низкий"
    elif dose <= 0.30: emoji, lvl = "🟡", "повышенный"
    else: emoji, lvl = "🔴", "высокий"
    return f"{emoji} Радиация: {dose:.3f} μSv/h ({lvl})"

# -------- Давление: локальный тренд --------
def local_pressure_and_trend(wm: Dict[str, Any], threshold_hpa: float = 0.3) -> Tuple[Optional[int], str]:
    cur_p = (wm.get("current") or {}).get("pressure")
    hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
    prev = hp[-1] if isinstance(hp,list) and hp else None
    if not isinstance(cur_p,(int,float)) and isinstance(hp,list) and hp:
        cur_p = hp[-1]; prev = hp[-2] if len(hp)>1 else None
    arrow = "→"
    if isinstance(cur_p,(int,float)) and isinstance(prev,(int,float)):
        diff = float(cur_p)-float(prev)
        arrow = "↑" if diff>=threshold_hpa else "↓" if diff<=-threshold_hpa else "→"
    return (int(round(cur_p)) if isinstance(cur_p,(int,float)) else None, arrow)

# -------- Знаки зодиака --------
ZODIAC = {"Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌","Дева":"♍","Весы":"♎","Скорпион":"♏","Стрелец":"♐","Козерог":"♑","Водолей":"♒","Рыбы":"♓"}
def zsym(s: str) -> str:
    for name, sym in ZODIAC.items(): s = s.replace(name, sym)
    return s

# -------- Сообщение --------
def build_message(region_name: str, sea_label: str, sea_cities, other_label: str, other_cities, tz: pendulum.Timezone) -> str:
    P: List[str] = []
    today = pendulum.now(tz).date(); tom = today.add(days=1)

    P.append(f"<b>🌅 {region_name}: погода на завтра ({tom.format('DD.MM.YYYY')})</b>")

    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz.name)
    wm = get_weather(KLD_LAT, KLD_LON) or {}; cur = wm.get("current", {}) or {}
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc = wcarr[1] if isinstance(wcarr, list) and len(wcarr)>1 else cur.get("weathercode")
    wind_ms = kmh_to_ms(cur.get("windspeed"))
    rh_min = stats.get("rh_min"); rh_max = stats.get("rh_max")
    t_day_max = stats.get("t_day_max"); t_night_min = stats.get("t_night_min")
    p_val, p_trend = local_pressure_and_trend(wm, threshold_hpa=0.3)
    press_part = f"{p_val} гПа {p_trend}" if isinstance(p_val,int) else "н/д"
    desc = code_desc(wc)

    kal_parts = [
        f"🏙️ Калининград: дн/ночь {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None) else "🏙️ Калининград: дн/ночь н/д",
        desc or None,
        f"💨 {wind_ms:.1f} м/с ({compass(cur.get('winddirection', 0))})" if wind_ms is not None else f"💨 н/д ({compass(cur.get('winddirection', 0))})",
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        f"🔹 {press_part}",
    ]
    P.append(" • ".join([x for x in kal_parts if x]))
    P.append("———")

    # (рейтинги городов — без изменений)
    temps_sea: Dict[str, Tuple[float,float,int,float|None]] = {}
    for city, (la, lo) in sea_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if tmax is None: continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx)>1 else 0
        temps_sea[city] = (tmax, tmin or tmax, wcx, get_sst(la, lo))
    if temps_sea:
        P.append(f"🎖️ <b>{sea_label}</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(city,(d,n,wcx,sst)) in enumerate(sorted(temps_sea.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            line = f"{medals[i]} {city}: {d:.1f}/{n:.1f}"
            if (dd:=code_desc(wcx)): line += f", {dd}"
            if sst is not None: line += f" 🌊 {sst:.1f}"
            P.append(line)
        P.append("———")

    temps_oth: Dict[str, Tuple[float,float,int]] = {}
    for city,(la,lo) in other_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if tmax is None: continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx)>1 else 0
        temps_oth[city] = (tmax, tmin or tmax, wcx)
    if temps_oth:
        P.append("🔥 <b>Тёплые города, °C</b>")
        for city,(d,n,wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0], reverse=True)[:3]:
            P.append(f"   • {city}: {d:.1f}/{n:.1f}" + (f" {code_desc(wcx)}" if code_desc(wcx) else ""))
        P.append("❄️ <b>Холодные города, °C</b>")
        for city,(d,n,wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0])[:3]:
            P.append(f"   • {city}: {d:.1f}/{n:.1f}" + (f" {code_desc(wcx)}" if code_desc(wcx) else ""))
        P.append("———")

    # -------- Air + Safecast + Пыльца + Радиация --------
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl","н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    sc = get_safecast()
    if sc:
        parts = []
        if isinstance(sc.get("pm25"), (int,float)): parts.append(f"PM₂.₅ {int(round(sc['pm25']))} {pm_color(sc['pm25'])}")
        if isinstance(sc.get("pm10"), (int,float)): parts.append(f"PM₁₀ {int(round(sc['pm10']))} {pm_color(sc['pm10'])}")
        if isinstance(sc.get("radiation"), (int,float)):
            r = float(sc["radiation"])
            if r <= 0.15: rmark = "🟢"
            elif r <= 0.30: rmark = "🟡"
            else: rmark = "🔴"
            parts.append(f"γ {r:.3f} μSv/h {rmark}")
        if parts:
            P.append("🧪 Safecast: " + " | ".join(parts))

    em, lbl = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl != "низкое":
        P.append(f"🔥 Задымление: {em} {lbl}")

    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    # если из SafeCast радиации нет — оставим фоллбэк по нашему источнику
    if not (sc and isinstance(sc.get("radiation"), (int,float))):
        if (rl := radiation_line(KLD_LAT, KLD_LON)):
            P.append(rl)
    P.append("———")

    kp, ks = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})" if kp is not None else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events(offset_days=1, show_all_voc=True) or []
    filtered: List[str] = []
    for line in astro:
        m = re.search(r"(VoC|VOC|Луна.*без курса).*?(\d+)\s*мин", line, re.IGNORECASE)
        if m and int(m.group(2)) <= 5:
            continue
        filtered.append(line)
    P.extend([zsym(l) for l in filtered] if filtered else ["— нет данных —"])
    P.append("———")

    culprit = "магнитные бури" if kp is not None and ks and ks.lower()=="буря" else "неблагоприятный прогноз погоды"
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    try:
        _, tips = gpt_blurb(culprit)
        for t in tips[:3]:
            t = t.strip()
            if t: P.append(t)
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")
    P.append("———")
    P.append(f"📚 {get_fact(tom, region_name)}")
    return "\n".join(P)

async def send_common_post(bot: Bot, chat_id: int, region_name: str, sea_label: str, sea_cities, other_label: str, other_cities, tz):
    msg = build_message(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)

async def main_common(bot: Bot, chat_id: int, region_name: str, sea_label: str, sea_cities, other_label: str, other_cities, tz):
    await send_common_post(bot, chat_id, region_name, sea_label, sea_cities, other_label, other_cities, tz)