#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор «Шумана» для VayboMeter.

Изменения в этой ревизии:
• Если в iframe нет JSON, ищем <script src="..."> и тянем внешний JS (power_levels*.js),
  внутри него находим массивы с сериями (Highcharts) и достаем power по name=GCIxxx.
• Дедуп по часам: если последняя запись с тем же ts — заменяем её.
• Более подробные debug-лог-сообщения по веткам отказа (печатаются в stdout).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

# ───────────────────────── Конфиг из ENV ─────────────────────────

DEF_FILE = os.getenv("SCHU_FILE", "schumann_hourly.json")
MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"
AMP_SCALE = float(os.getenv("SCHU_AMP_SCALE", "1"))

CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

# HeartMath GCI
GCI_ENABLE = os.getenv("SCHU_GCI_ENABLE", "1") == "1"
GCI_PAGE_URL = os.getenv(
    "SCHU_GCI_URL",
    "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/"
).strip()
GCI_IFRAME_URL = os.getenv(
    "SCHU_GCI_IFRAME",
    "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html"
).strip()

GCI_STATIONS_ENV = os.getenv("SCHU_GCI_STATIONS", "").strip()
if GCI_STATIONS_ENV:
    GCI_STATION_KEYS = [s.strip().upper() for s in GCI_STATIONS_ENV.split(",") if s.strip()]
else:
    GCI_STATION_KEYS = [os.getenv("SCHU_GCI_STATION", "GCI003").strip().upper()]

HEARTMATH_HTML = os.getenv("SCHU_HEARTMATH_HTML", "").strip()

# H7
H7_URL = os.getenv("H7_URL", "").strip()
H7_TARGET = float(os.getenv("H7_TARGET_HZ", "54.81"))
H7_WINDOW_H = int(os.getenv("H7_WINDOW_H", "48"))
H7_Z = float(os.getenv("H7_Z", "2.5"))
H7_MIN_ABS = float(os.getenv("H7_MIN_ABS", "0.2"))

MAP_POWER_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "1") == "1"

# ───────────────────────── Регексы ─────────────────────────

# 1) iframe со страницей power_levels
IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*power_levels[^"\']*)["\']',
    re.IGNORECASE
)

# 2) внутри iframe ищем JSON-массив:
DATA_ARRAY_RE = re.compile(
    r'(?:var|let|const)\s+data\s*=\s*(\[[\s\S]*?\])\s*;',
    re.IGNORECASE
)
DATA_FIELD_RE = re.compile(
    r'"data"\s*:\s*(\[[\s\S]*?\])',
    re.IGNORECASE
)
ROOT_ARRAY_RE = re.compile(
    r'^\s*(\[[\s\S]*\])\s*$'
)

# 3) «серии» Highcharts, упрощённо: {name:"GCI003", y: 12.3}
SERIES_OBJ_RE = re.compile(
    r'\{[^{}]*"name"\s*:\s*"(?P<name>GCI00[1-6])"[^{}]*"y"\s*:\s*(?P<val>-?\d+(?:\.\d+)?)',
    re.IGNORECASE
)

# 4) script src внутри iframe
SCRIPT_SRC_RE = re.compile(
    r'<script[^>]+src=["\']([^"\']+power[^"\']*\.js)["\']',
    re.IGNORECASE
)

# ───────────────────────── Утилиты ─────────────────────────

def _now_hour_ts() -> int:
    return int(time.time() // 3600 * 3600)

def _http_get(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "VayboMeter/1.1"})
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[debug] GET text failed {url}: {e}")
        return None

def _http_get_json(url: str, timeout: int = 20) -> Optional[Any]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "VayboMeter/1.1"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[debug] GET json failed {url}: {e}")
        return None

def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None

def _extract_any_number(d: Any, keys=("amp","amplitude","power","value","val")) -> Optional[float]:
    if isinstance(d, dict):
        for k in keys:
            if k in d:
                val = _safe_float(d[k])
                if val is not None:
                    return val
        for v in d.values():
            val = _extract_any_number(v, keys)
            if val is not None:
                return val
    elif isinstance(d, list):
        for v in d:
            val = _extract_any_number(v, keys)
            if val is not None:
                return val
    return None

def _read_json_array(path: Union[str, Path]) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        arr = json.loads(p.read_text(encoding="utf-8"))
        return arr if isinstance(arr, list) else []
    except Exception:
        return []

def _write_json_array(path: Union[str, Path], arr: List[Dict[str, Any]]) -> None:
    p = Path(path)
    p.write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")

def _append_or_replace_by_ts(path: Union[str, Path], rec: Dict[str, Any], max_len: int = MAX_LEN) -> None:
    """Если последний элемент с тем же ts — заменяем; иначе дописываем."""
    arr = _read_json_array(path)
    if arr and isinstance(arr[-1], dict) and arr[-1].get("ts") == rec.get("ts"):
        arr[-1] = rec
    else:
        arr.append(rec)
    if max_len > 0 and len(arr) > max_len:
        arr = arr[-max_len:]
    _write_json_array(path, arr)

def _last_amp_from_file(path: Union[str, Path]) -> Optional[float]:
    arr = _read_json_array(path)
    if not arr:
        return None
    for item in reversed(arr):
        amp = item.get("amp")
        if isinstance(amp, (int, float)):
            return float(amp)
    return None

# ───────────────────────── Парсинг HeartMath ─────────────────────────

def extract_iframe_src(html: str) -> Optional[str]:
    m = IFRAME_SRC_RE.search(html or "")
    if not m:
        print("[debug] iframe not found in page HTML")
        return None
    src = m.group(1)
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        try:
            from urllib.parse import urlparse, urljoin
            base = urlparse(GCI_PAGE_URL)
            return urljoin(f"{base.scheme}://{base.netloc}", src)
        except Exception:
            return None
    return src

def _extract_data_array_from_text(txt: str) -> Optional[str]:
    m = DATA_ARRAY_RE.search(txt or "")
    if m:
        return m.group(1)
    m = DATA_FIELD_RE.search(txt or "")
    if m:
        return m.group(1)
    m = ROOT_ARRAY_RE.search(txt or "")
    if m:
        return m.group(1)
    return None

def _parse_series_array(json_text: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(json_text)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        out: List[Dict[str, Any]] = []
        for m in SERIES_OBJ_RE.finditer(json_text or ""):
            name = m.group("name")
            val = _safe_float(m.group("val"))
            out.append({"name": name, "y": val})
        return out

def _pick_series_value(series: List[Dict[str, Any]], station: str) -> Optional[float]:
    for s in series:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if isinstance(name, str) and name.upper() == station.upper():
            return _safe_float(s.get("y"))
    return None

def _join_relative(base_url: str, rel: str) -> str:
    from urllib.parse import urlparse, urljoin
    if rel.startswith("//"):
        return "https:" + rel
    if rel.startswith("/"):
        b = urlparse(base_url)
        return urljoin(f"{b.scheme}://{b.netloc}", rel)
    if rel.startswith("http"):
        return rel
    return urljoin(base_url, rel)

def _fetch_series_from_iframe_or_js(iframe_url: str) -> Optional[List[Dict[str, Any]]]:
    """Пытаемся достать массив серий: сначала из iframe HTML, если нет — из подключенного JS."""
    iframe_text = _http_get(iframe_url)
    if not iframe_text:
        print("[debug] iframe GET failed")
        # Иногда iframe может отдавать JSON напрямую
        data = _http_get_json(iframe_url)
        if isinstance(data, list):
            return data
        return None

    arr_txt = _extract_data_array_from_text(iframe_text)
    if arr_txt:
        return _parse_series_array(arr_txt)

    # Ищем <script src="...power*.js">
    scripts = SCRIPT_SRC_RE.findall(iframe_text)
    if not scripts:
        print("[debug] iframe has no data array and no matching script src")
        return None

    for s in scripts:
        js_url = _join_relative(iframe_url, s)
        js_text = _http_get(js_url)
        if not js_text:
            continue
        arr_txt = _extract_data_array_from_text(js_text)
        if arr_txt:
            series = _parse_series_array(arr_txt)
            if series:
                return series

        # fallback: попробуем выдернуть пары {name:"GCIxxx",y:number} напрямую из JS
        series = []
        for m in SERIES_OBJ_RE.finditer(js_text or ""):
            name = m.group("name")
            val = _safe_float(m.group("val"))
            series.append({"name": name, "y": val})
        if series:
            return series

    print("[debug] scripts fetched but no series found")
    return None

def get_gci_power(station_key: str) -> Optional[Tuple[float, str]]:
    """Возвращает (power, 'gci') для одной станции."""
    # 1) сохранённый HTML
    html = None
    if HEARTMATH_HTML:
        try:
            html = Path(HEARTMATH_HTML).read_text(encoding="utf-8")
            print(f"[debug] using saved HTML: {HEARTMATH_HTML}")
        except Exception as e:
            print(f"[debug] saved HTML read failed: {e}")
            html = None

    # 2) live-страница
    if html is None:
        html = _http_get(GCI_PAGE_URL)

    if not html:
        print("[debug] page HTML unavailable")
        return None

    iframe_url = extract_iframe_src(html)
    if not iframe_url:
        # пробуем ENV-iframe
        if GCI_IFRAME_URL:
            iframe_url = GCI_IFRAME_URL
            print(f"[debug] fallback to ENV iframe: {iframe_url}")
        else:
            return None

    series = _fetch_series_from_iframe_or_js(iframe_url)
    if not series:
        print("[debug] no series extracted from iframe/js")
        return None

    val = _pick_series_value(series, station_key)
    if val is None:
        print(f"[debug] station {station_key} not found in series")
        return None
    return float(val), "gci"

# ───────────────────────── Кастомный URL ─────────────────────────

def get_custom() -> Optional[Tuple[Optional[float], Optional[float], str]]:
    if not CUSTOM_URL:
        return None
    data = _http_get_json(CUSTOM_URL)
    if data is None:
        return None

    freq = None
    if isinstance(data, dict):
        for k in ("freq", "frequency", "f"):
            if k in data:
                freq = _safe_float(data[k]); break
        if freq is None:
            freq = _extract_any_number(data, keys=("freq","frequency","f"))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                freq = _extract_any_number(item, keys=("freq","frequency","f"))
                if freq is not None:
                    break

    amp = None
    if isinstance(data, dict):
        amp = _extract_any_number(data, keys=("amp","amplitude","power","value"))
    elif isinstance(data, list):
        for item in data:
            amp = _extract_any_number(item, keys=("amp","amplitude","power","value"))
            if amp is not None:
                break

    if amp is not None:
        amp *= AMP_SCALE

    return freq, amp, "custom"

# ───────────────────────── H7 ─────────────────────────

def get_h7_features() -> Tuple[Optional[float], Optional[bool]]:
    if not H7_URL:
        return None, None
    data = _http_get_json(H7_URL)
    if data is None:
        return None, None

    points: List[Tuple[float, float]] = []

    def push(f: Any, a: Any):
        f1 = _safe_float(f); a1 = _safe_float(a)
        if f1 is not None and a1 is not None:
            points.append((f1, a1))

    def harvest(obj: Any):
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, list) and len(it) >= 2:
                    push(it[0], it[1])
                elif isinstance(it, dict):
                    f = it.get("freq") or it.get("f") or it.get("hz")
                    a = it.get("amp") or it.get("amplitude") or it.get("pwr") or it.get("power") or it.get("a")
                    if f is not None and a is not None:
                        push(f, a)
                    else:
                        harvest(it.values())
        elif isinstance(obj, dict):
            for v in obj.values():
                harvest(v)

    harvest(data)
    if not points:
        return None, None

    best = None
    for f, a in points:
        if best is None or abs(f - H7_TARGET) < abs(best[0] - H7_TARGET):
            best = (f, a)
    if best is None:
        return None, None

    h7_amp = float(best[1])
    spike = bool(h7_amp >= max(H7_MIN_ABS, H7_Z))
    return h7_amp, spike

# ───────────────────────── Основной сбор ─────────────────────────

def collect() -> int:
    ts = _now_hour_ts()
    freq = 7.83
    amp: Optional[float] = None
    src = "cache"
    station_used: Optional[str] = None

    # 1) кастомный эндпоинт
    if CUSTOM_URL:
        r = get_custom()
        if r:
            f, a, s = r
            if isinstance(f, (int, float)):
                freq = float(f)
            if isinstance(a, (int, float)):
                amp = float(a)
            src = s

    # 2) HeartMath (перебор станций)
    if src == "cache" and GCI_ENABLE:
        for station in GCI_STATION_KEYS:
            gci = get_gci_power(station)
            if gci:
                power, _ = gci
                station_used = station
                if MAP_POWER_TO_AMP and power is not None:
                    amp = power * AMP_SCALE
                src = "gci"
                break

    # 3) cache fallback
    if amp is None and ALLOW_CACHE:
        last_amp = _last_amp_from_file(DEF_FILE)
        if last_amp is not None:
            amp = last_amp

    h7_amp, h7_spike = get_h7_features()

    rec: Dict[str, Any] = {
        "ts": ts,
        "freq": float(freq),
        "amp": float(amp) if isinstance(amp, (int, float)) else None,
        "h7_amp": float(h7_amp) if isinstance(h7_amp, (int, float)) else None,
        "h7_spike": bool(h7_spike) if h7_spike is not None else None,
        "ver": 2,
        "src": src
    }
    if station_used:
        rec["station"] = station_used

    _append_or_replace_by_ts(DEF_FILE, rec, MAX_LEN)

    print(f"collect: ok ts={ts} src={src} freq={freq} amp={amp} h7={h7_amp} spike={h7_spike} -> {Path(DEF_FILE).resolve()}")
    try:
        tail = _read_json_array(DEF_FILE)[-1:]
        if tail:
            print("Last record JSON:", json.dumps(tail[0], ensure_ascii=False))
    except Exception:
        pass
    return 0

# ───────────────────────── CLI ─────────────────────────

def main(argv: List[str]) -> int:
    if len(argv) > 1 and argv[1] in ("--collect", "collect"):
        return collect()
    print("Usage: python schumann.py --collect")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))