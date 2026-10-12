#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources() — объединяет словари с приоритетом IQAir → Open-Meteo
• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему часу)
• get_kp()               — (kp, state, ts_unix, src) — индекс Kp с «свежестью»
• get_kp_observed_3h()   — только подтверждённый NOAA observed 3-hour Kp (или None)
• get_solar_wind()       — {'bz','bt','speed_kms','density','ts','status','src'}

Особенности:
- Open-Meteo: берём значения по ближайшему прошедшему часу (UTC).
- SST: то же правило ближайшего часа.
- Kp: NOAA SWPC observed 3-hour (products/noaa-planetary-k-index.json, поля
  time_tag/Kp) → оперативная 1-минутная оценка → кэш. Источник всегда помечен:
  observed 3-hour, estimated 1-minute и cache никогда не смешиваются.
- Солнечный ветер: SWPC 5-минутные продукты (mag/plasma); кэш 10 мин.
- Источник AQI возвращаем как:
    'src' ∈ {'iqair','openmeteo','n/d'},
    'src_emoji' ∈ {'📡','🛰','⚪'},
    'src_icon'  ∈ {'📡 IQAir','🛰 OM','⚪ н/д'}.
"""

from __future__ import annotations
import os
import time
import json
import math
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, List

import pendulum

from utils import _get  # HTTP-обёртка (_get_retry внутри)

__all__ = (
    "get_air",
    "get_sst",
    "get_kp",
    "get_kp_observed_3h",
    "get_solar_wind",
    "kp_g_scale",
    "remember_kp_observed_3h",
    "parse_noaa_kp_estimated_1m",
    "parse_noaa_kp_observed_3h",
)

# ───────────────────────── Константы / лог / кеш ─────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

AIR_KEY = os.getenv("AIRVISUAL_KEY")

# Единый сетевой таймаут (сек) — можно переопределить переменной окружения HTTP_TIMEOUT
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Kp cache: one slot per provenance kind, so an estimate never overwrites
# (or masquerades as) an observed 3-hour value.
KP_CACHE = CACHE_DIR / "kp.json"
KP_CACHE_SCHEMA = 2

# Солнечный ветер — кэш 10 мин
SW_CACHE = CACHE_DIR / "solar_wind.json"
SW_TTL_SEC = 10 * 60

# NOAA SWPC planetary K-index.
# Official 3-hour Kp: a JSON list of {"time_tag", "Kp", ...}; time_tag (UTC) is
# the START of the 3-hour interval the value describes.
KP_OBSERVED_3H_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
# Operational 1-minute estimate: useful as a live hint, never a confirmed
# 3-hour observation.
KP_ESTIMATED_1M_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
KP_URLS = [KP_OBSERVED_3H_URL, KP_ESTIMATED_1M_URL]

KP_SRC_OBSERVED_3H = "swpc_observed_3h"
KP_SRC_ESTIMATED_1M = "swpc_estimated_1m"
KP_SRC_CACHE_PREFIX = "cache:"

KP_INTERVAL_SEC = 3 * 3600
# A closed bar is at most one interval old when it is published. Tolerate one
# missed publication cycle plus 30 min latency; anything older is stale.
KP_OBSERVED_MAX_AGE_SEC = 2 * KP_INTERVAL_SEC + 30 * 60
KP_ESTIMATED_MAX_AGE_SEC = 30 * 60
# Runner/NOAA clocks may disagree slightly; a timestamp further ahead than this
# is not a real observation.
KP_FUTURE_SKEW_SEC = 10 * 60

# 5-минутные продукты DSCOVR/ACE
SWP_MAG_5M = "https://services.swpc.noaa.gov/products/solar-wind/mag-5-minute.json"
SWP_PLA_5M = "https://services.swpc.noaa.gov/products/solar-wind/plasma-5-minute.json"

SRC_EMOJI = {"iqair": "📡", "openmeteo": "🛰", "n/d": "⚪"}
SRC_ICON  = {"iqair": "📡 IQAir", "openmeteo": "🛰 OM", "n/d": "⚪ н/д"}

# ───────────────────────── Безопасная HTTP-обёртка ─────────────────────────

def _safe_http_get(url: str, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Пытается вызвать utils._get с таймаутом. Если у _get нет аргумента timeout,
    повторно вызывает без него. Любые исключения логируются и возвращается None.
    """
    try:
        try:
            return _get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except TypeError:
            # если твой _get не поддерживает timeout
            return _get(url, **kwargs)
    except Exception as e:
        logging.warning("_safe_http_get — HTTP error: %s", e)
        return None

# ───────────────────────── Утилиты AQI/Kp ──────────────────────────

def _aqi_level(aqi: Union[int, float, str, None]) -> str:
    if aqi in (None, "н/д"):
        return "н/д"
    try:
        v = float(aqi)
    except (TypeError, ValueError):
        return "н/д"
    if v <= 50: return "хороший"
    if v <= 100: return "умеренный"
    if v <= 150: return "вредный"
    if v <= 200: return "оч. вредный"
    return "опасный"

def _pick_nearest_hour(arr_time: List[str], arr_val: List[Any]) -> Optional[float]:
    if not arr_time or not arr_val or len(arr_time) != len(arr_val):
        return None
    try:
        now_iso = time.strftime("%Y-%m-%dT%H:00", time.gmtime())
        idxs = [i for i, t in enumerate(arr_time) if isinstance(t, str) and t <= now_iso]
        idx = max(idxs) if idxs else 0
        v = arr_val[idx]
        if not isinstance(v, (int, float)):
            return None
        v = float(v)
        return v if (math.isfinite(v) and v >= 0) else None
    except Exception:
        return None

# ───────────────────────── Источники AQI ───────────────────────────

def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        return None
    resp = _safe_http_get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=lat, lon=lon, key=AIR_KEY,
    )
    if not resp or "data" not in resp:
        return None
    try:
        pol = (resp.get("data", {}) or {}).get("current", {}).get("pollution", {}) or {}
        aqi_val  = pol.get("aqius")
        # В публичном API обычно нет микрограммов PM, оставляем None если нет
        pm25_val = pol.get("p2")   # если ключа нет — будет None (ок)
        pm10_val = pol.get("p1")
        return {
            "aqi":  float(aqi_val)  if isinstance(aqi_val,  (int, float)) else None,
            "pm25": float(pm25_val) if isinstance(pm25_val, (int, float)) else None,
            "pm10": float(pm10_val) if isinstance(pm10_val, (int, float)) else None,
            "src": "iqair",
        }
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None

def _src_openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    resp = _safe_http_get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=lat, longitude=lon,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC",
    )
    if not resp or "hourly" not in resp:
        return None
    try:
        h = resp["hourly"]
        times = h.get("time", []) or []
        aqi_val  = _pick_nearest_hour(times, h.get("us_aqi", []) or [])
        pm25_val = _pick_nearest_hour(times, h.get("pm2_5", []) or [])
        pm10_val = _pick_nearest_hour(times, h.get("pm10", [])  or [])
        aqi_norm: Union[float, str] = float(aqi_val)  if isinstance(aqi_val,  (int, float)) and math.isfinite(aqi_val)  and aqi_val  >= 0 else "н/д"
        pm25_norm = float(pm25_val) if isinstance(pm25_val, (int, float)) and math.isfinite(pm25_val) and pm25_val >= 0 else None
        pm10_norm = float(pm10_val) if isinstance(pm10_val, (int, float)) and math.isfinite(pm10_val) and pm10_val >= 0 else None
        return {"aqi": aqi_norm, "pm25": pm25_norm, "pm10": pm10_norm, "src": "openmeteo"}
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None

# ───────────────────────── Merge AQI ───────────────────────────────

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Соединяет данные двух источников AQI (приоритет src1 → src2).
    Возвращает {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}.
    """
    aqi_val: Union[float, str, None] = "н/д"
    src_tag: str = "n/d"

    # AQI источник
    for s in (src1, src2):
        if not s:
            continue
        v = s.get("aqi")
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            aqi_val = float(v)
            src_tag = s.get("src") or src_tag
            break

    # PM first-non-null
    pm25 = None
    pm10 = None
    for s in (src1, src2):
        if not s:
            continue
        if pm25 is None and isinstance(s.get("pm25"), (int, float)) and math.isfinite(s["pm25"]):
            pm25 = float(s["pm25"])
        if pm10 is None and isinstance(s.get("pm10"), (int, float)) and math.isfinite(s["pm10"]):
            pm10 = float(s["pm10"])

    lvl = _aqi_level(aqi_val)
    src_emoji = SRC_EMOJI.get(src_tag, SRC_EMOJI["n/d"])
    src_icon  = SRC_ICON.get(src_tag,  SRC_ICON["n/d"])

    return {
        "lvl": lvl,
        "aqi": aqi_val,
        "pm25": pm25,
        "pm10": pm10,
        "src": src_tag,
        "src_emoji": src_emoji,
        "src_icon": src_icon,
    }

def get_air(lat: float, lon: float) -> Dict[str, Any]:
    try:
        src1 = _src_iqair(lat, lon)
    except Exception:
        src1 = None
    try:
        src2 = _src_openmeteo(lat, lon)
    except Exception:
        src2 = None
    return merge_air_sources(src1, src2)

# ───────────────────────── SST (по ближайшему часу) ─────────────────

def get_sst(lat: float, lon: float) -> Optional[float]:
    resp = _safe_http_get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature", timezone="UTC",
    )
    if not resp or "hourly" not in resp:
        return None
    try:
        h = resp["hourly"]
        times = h.get("time", []) or []
        vals  = h.get("sea_surface_temperature", []) or []
        v = _pick_nearest_hour(times, vals)
        return float(v) if isinstance(v, (int, float)) else None
    except Exception as e:
        logging.warning("Marine SST parse error: %s", e)
        return None

# ───────────────────────── Kp + кеш (TTL 120 мин) ───────────────────

def _now_ts() -> int:
    return int(time.time())


def _finite_kp(raw: Any) -> Optional[float]:
    """A planetary Kp is a finite number in [0, 9]; 0.0 is a valid value, not "missing"."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw.strip().replace(",", ".")) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError, AttributeError):
        return None
    if not math.isfinite(value) or value < 0.0 or value > 9.0:
        return None
    return value


def _parse_noaa_time_tag(raw: Any) -> Optional[int]:
    """UTC unix timestamp for a NOAA time_tag; None (never "now") when unparseable."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def kp_g_scale(kp: Any) -> Optional[str]:
    """NOAA G-scale for an unrounded Kp (G1 from 5, ... G5 at 9); None below storm level."""
    value = _finite_kp(kp)
    if value is None or value < 5.0:
        return None
    if value >= 9.0:
        return "G5"
    if value >= 8.0:
        return "G4"
    if value >= 7.0:
        return "G3"
    if value >= 6.0:
        return "G2"
    return "G1"


def _select_kp_observation(
    candidates: List[Tuple[Any, Any]],
    *,
    now_ts: int,
    max_age_sec: int,
    src: str,
) -> tuple[Optional[Dict[str, Any]], Dict[str, int]]:
    """Newest valid (time_tag, value) by timestamp, never by array position."""
    rejected = {"invalid_kp": 0, "invalid_time": 0, "future": 0, "stale": 0}
    best: Optional[tuple[int, float]] = None
    for raw_time, raw_kp in candidates:
        ts = _parse_noaa_time_tag(raw_time)
        if ts is None:
            rejected["invalid_time"] += 1
            continue
        kp = _finite_kp(raw_kp)
        if kp is None:
            rejected["invalid_kp"] += 1
            continue
        if ts > now_ts + KP_FUTURE_SKEW_SEC:
            rejected["future"] += 1
            continue
        if now_ts - ts > max_age_sec:
            rejected["stale"] += 1
            continue
        if best is None or ts >= best[0]:
            best = (ts, kp)
    if best is None:
        return None, rejected
    ts, kp = best
    # Within the skew tolerance a timestamp may sit slightly ahead: that is clock
    # disagreement, not a negative age.
    age_min = max(0, (now_ts - ts) // 60)
    return {"kp": kp, "ts": ts, "age_min": int(age_min), "src": src, "g_scale": kp_g_scale(kp)}, rejected


def _log_kp_rejections(src: str, rejected: Dict[str, int]) -> None:
    if any(rejected.values()):
        logging.info("Kp %s rows rejected: %s", src, ", ".join(f"{k}={v}" for k, v in rejected.items() if v))


def parse_noaa_kp_observed_3h(data: Any, *, now_ts: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Newest valid official 3-hour Kp from products/noaa-planetary-k-index.json.

    Current schema: [{"time_tag": "2026-10-10T06:00:00", "Kp": 7.0, ...}, ...].
    The legacy table schema ([["time_tag", "Kp", ...], [...], ...]) is read by
    header name, never by column position.
    """
    now = _now_ts() if now_ts is None else int(now_ts)
    if not isinstance(data, list) or not data:
        return None
    candidates: List[Tuple[Any, Any]] = []
    if all(isinstance(row, dict) for row in data):
        candidates = [(row.get("time_tag"), row.get("Kp")) for row in data]
    elif isinstance(data[0], list):
        header = [str(name).strip() for name in data[0]]
        if "time_tag" not in header:
            return None
        kp_column = next((name for name in ("Kp", "kp_index") if name in header), None)
        if kp_column is None:
            return None
        time_i, kp_i = header.index("time_tag"), header.index(kp_column)
        for row in data[1:]:
            if isinstance(row, list) and len(row) > max(time_i, kp_i):
                candidates.append((row[time_i], row[kp_i]))
            else:
                candidates.append((None, None))
    else:
        return None
    obs, rejected = _select_kp_observation(
        candidates, now_ts=now, max_age_sec=KP_OBSERVED_MAX_AGE_SEC, src=KP_SRC_OBSERVED_3H
    )
    _log_kp_rejections(KP_SRC_OBSERVED_3H, rejected)
    return obs


def parse_noaa_kp_estimated_1m(data: Any, *, now_ts: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Newest valid operational 1-minute estimate (estimated_kp, else kp_index)."""
    now = _now_ts() if now_ts is None else int(now_ts)
    if not isinstance(data, list) or not data or not all(isinstance(row, dict) for row in data):
        return None
    candidates: List[Tuple[Any, Any]] = []
    for row in data:
        raw = row.get("estimated_kp")
        if _finite_kp(raw) is None:
            raw = row.get("kp_index")
        candidates.append((row.get("time_tag"), raw))
    obs, rejected = _select_kp_observation(
        candidates, now_ts=now, max_age_sec=KP_ESTIMATED_MAX_AGE_SEC, src=KP_SRC_ESTIMATED_1M
    )
    _log_kp_rejections(KP_SRC_ESTIMATED_1M, rejected)
    return obs


_KP_MAX_AGE_BY_SRC = {
    KP_SRC_OBSERVED_3H: KP_OBSERVED_MAX_AGE_SEC,
    KP_SRC_ESTIMATED_1M: KP_ESTIMATED_MAX_AGE_SEC,
}


def _load_kp_cache() -> Dict[str, Any]:
    try:
        data = json.loads(KP_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Pre-schema-2 caches did not record provenance reliably: ignore them.
    if not isinstance(data, dict) or data.get("schema") != KP_CACHE_SCHEMA:
        return {}
    return data


def _save_kp_cache(obs: Dict[str, Any]) -> None:
    data = _load_kp_cache() or {"schema": KP_CACHE_SCHEMA}
    previous = data.get(str(obs["src"]))
    # Newest observation wins: an older response never replaces a newer record.
    if isinstance(previous, dict) and isinstance(previous.get("ts"), int) and previous["ts"] > int(obs["ts"]):
        return
    data[str(obs["src"])] = {"kp": float(obs["kp"]), "ts": int(obs["ts"])}
    try:
        KP_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("Kp cache write error: %s", e)


def remember_kp_observed_3h(obs: Optional[Dict[str, Any]]) -> bool:
    """
    Persist a confirmed live observed 3-hour Kp parsed elsewhere (post_common's
    primary request) into the same provenance-aware cache slot get_kp() uses.
    Estimates, cache-derived records and malformed values are refused.
    """
    if not isinstance(obs, dict) or obs.get("src") != KP_SRC_OBSERVED_3H:
        return False
    ts = obs.get("ts")
    if _finite_kp(obs.get("kp")) is None or not isinstance(ts, int) or isinstance(ts, bool):
        return False
    _save_kp_cache(obs)
    return True


def _cached_kp(src: str, *, now_ts: int) -> Optional[Dict[str, Any]]:
    """A cached value keeps its original kind and its original freshness bound."""
    entry = _load_kp_cache().get(src)
    if not isinstance(entry, dict):
        return None
    ts = entry.get("ts")
    if not isinstance(ts, int) or isinstance(ts, bool):
        return None
    obs, _rejected = _select_kp_observation(
        [(datetime.fromtimestamp(ts, timezone.utc).isoformat(), entry.get("kp"))],
        now_ts=now_ts,
        max_age_sec=_KP_MAX_AGE_BY_SRC[src],
        src=KP_SRC_CACHE_PREFIX + src,
    )
    return obs


def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    data = None
    for i in range(attempts):
        data = _safe_http_get(url)
        if data:
            break
        try:
            time.sleep(backoff ** i)
        except Exception:
            pass
    return data


def _kp_state(kp: float) -> str:
    if kp < 3.0: return "спокойно"
    if kp < 5.0: return "неспокойно"
    return "буря"


def _fetch_kp_observed_3h(now_ts: int) -> Optional[Dict[str, Any]]:
    obs = parse_noaa_kp_observed_3h(_fetch_kp_data(KP_OBSERVED_3H_URL), now_ts=now_ts)
    if obs is not None:
        _save_kp_cache(obs)
    return obs


def get_kp_observed_3h() -> Optional[Dict[str, Any]]:
    """
    Confirmed NOAA 3-hour Kp only: live, else a cached observed value within the
    same freshness bound. Never an estimate. Returns
    {"kp", "ts", "age_min", "src", "g_scale"} or None.
    """
    now_ts = _now_ts()
    return _fetch_kp_observed_3h(now_ts) or _cached_kp(KP_SRC_OBSERVED_3H, now_ts=now_ts)


def get_kp() -> tuple[Optional[float], str, Optional[int], str]:
    """
    Возвращает (kp_value, state, ts_unix, src_tag).

    src_tag ∈ {"swpc_observed_3h", "swpc_estimated_1m",
               "cache:swpc_observed_3h", "cache:swpc_estimated_1m", "n/d"}.
    Только swpc_observed_3h (в том числе из кэша) — подтверждённое трёхчасовое
    наблюдение NOAA; оценка 1m и кэш всегда помечены своим источником.
    """
    now_ts = _now_ts()

    # 1) Официальный 3-часовой Kp
    obs = _fetch_kp_observed_3h(now_ts)

    # 2) Оперативная 1-минутная оценка (явно помечена как оценка)
    if obs is None:
        obs = parse_noaa_kp_estimated_1m(_fetch_kp_data(KP_ESTIMATED_1M_URL), now_ts=now_ts)
        if obs is not None:
            _save_kp_cache(obs)

    # 3) Кэш: сначала наблюдение, затем оценка — каждое в своих границах свежести
    if obs is None:
        obs = _cached_kp(KP_SRC_OBSERVED_3H, now_ts=now_ts) or _cached_kp(KP_SRC_ESTIMATED_1M, now_ts=now_ts)

    if obs is None:
        return None, "н/д", None, "n/d"
    return obs["kp"], _kp_state(obs["kp"]), obs["ts"], obs["src"]

# ───────────────────────── Солнечный ветер (5-мин) ─────────────────

def _load_sw_cache() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(SW_CACHE.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None

def _save_sw_cache(obj: Dict[str, Any]) -> None:
    try:
        SW_CACHE.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("SW cache write error: %s", e)

def _parse_table_latest(rowset: Any, want: List[str]) -> tuple[Optional[Dict[str, float]], Optional[int]]:
    """
    Универсальный парсер табличных продуктов SWPC:
    Первый элемент — список названий колонок; далее — строки.
    Возвращает словарь {col:value} и ts (UTC) по последней валидной строке.
    """
    try:
        if not isinstance(rowset, list) or len(rowset) < 2 or not isinstance(rowset[0], list):
            return None, None
        header = rowset[0]
        idx = {name: header.index(name) for name in want if name in header}
        for row in reversed(rowset[1:]):
            if not isinstance(row, list) or len(row) < len(header):
                continue
            tstr = row[idx.get("time_tag")] if "time_tag" in idx else row[0]
            try:
                dt = pendulum.parse(str(tstr).replace(" ", "T"), tz="UTC")
                ts = int(dt.int_timestamp)
            except Exception:
                ts = int(time.time())
            values: Dict[str, float] = {}
            ok = False
            for col in want:
                if col == "time_tag":
                    continue
                j = idx.get(col)
                if j is None or j >= len(row):
                    continue
                try:
                    val = float(str(row[j]).replace(",", "."))
                    if math.isfinite(val):
                        values[col] = val
                        ok = True
                except Exception:
                    continue
            if ok:
                return values, ts
    except Exception:
        pass
    return None, None

def _solar_wind_status(bz: Optional[float], v: Optional[float], n: Optional[float]) -> str:
    """
    Примитивная эвристика:
      - опаснее всего Bz < -6 nT
      - скорость > 600 км/с повышенная
      - плотность > 15 см^-3 добавляет «напряжённости»
    """
    flags = 0
    if isinstance(bz, (int, float)):
        if bz < -6: flags += 2
        elif bz < -2: flags += 1
    if isinstance(v, (int, float)):
        if v > 700: flags += 2
        elif v > 600: flags += 1
    if isinstance(n, (int, float)):
        if n > 20: flags += 2
        elif n > 15: flags += 1

    if flags >= 4: return "напряжённо"
    if flags >= 2: return "умеренно"
    return "спокойно"

def get_solar_wind() -> Dict[str, Any]:
    """
    Возвращает: {'bz','bt','speed_kms','density','ts','status','src'}
    Источник — SWPC 5-minute (mag/plasma). Кэш 10 мин.
    """
    now_ts = int(time.time())

    # 1) читаем оба продукта
    mag = _safe_http_get(SWP_MAG_5M)
    pla = _safe_http_get(SWP_PLA_5M)

    bz = bt = v = n = None
    ts_list: List[int] = []
    src = "swpc_5m"

    # магнетометр: интересны time_tag, bz_gsm, bt
    if mag:
        vals, ts = _parse_table_latest(mag, ["time_tag", "bz_gsm", "bt"])
        if vals:
            bz = vals.get("bz_gsm", bz)
            bt = vals.get("bt", bt)
        if ts: ts_list.append(ts)

    # плазма: speed, density
    if pla:
        vals, ts = _parse_table_latest(pla, ["time_tag", "speed", "density"])
        if vals:
            v = vals.get("speed", v)
            n = vals.get("density", n)
        if ts: ts_list.append(ts)

    if ts_list:
        ts = max(ts_list)
        status = _solar_wind_status(bz, v, n)
        obj = {"bz": bz, "bt": bt, "speed_kms": v, "density": n, "ts": ts, "status": status, "src": src}
        _save_sw_cache(obj)
        return obj

    # 2) кэш (10 мин)
    cached = _load_sw_cache()
    if cached and isinstance(cached.get("ts"), int) and (now_ts - int(cached["ts"]) <= SW_TTL_SEC):
        cached["src"] = "cache"
        return cached

    return {}

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Калининград) ===")
    pprint(get_air(54.710426, 20.452214))
    print("\n=== Пример get_sst (Калининград) ===")
    print(get_sst(54.710426, 20.452214))
    print("\n=== Пример get_kp ===")
    print(get_kp())
    print("\n=== Пример get_solar_wind ===")
    pprint(get_solar_wind())