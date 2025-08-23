#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — collector for Schumann-like hourly series (v2 JSON)

Что собираем:
- freq: фикс 7.83 Гц (якорь)
- amp: по умолчанию маппим HeartMath GCMS "Power" станции GCI003 (Lithuania) → amp
- h7_amp / h7_spike: опционально (если есть отдельный спектральный источник)

Источники (по приоритету):
1) SCHU_CUSTOM_URL — любой JSON/HTML, из которого можно выковырять freq≈7.83 или просто amp
2) HeartMath GCMS:
   - если задан SCHU_HEARTMATH_HTML — парсим сохранённую страницу (офлайн)
   - иначе качаем лайв-страницу + вложенный iframe (tolerant regex)
3) cache-safe: если лайв не доступен, создаём запись с src="cache" и amp=None (job не падает)

ENV (все опционально):
  SCHU_FILE=schumann_hourly.json
  SCHU_MAX_LEN=5000
  SCHU_AMP_SCALE=1
  SCHU_TREND_WINDOW=24
  SCHU_TREND_DELTA=0.1
  SCHU_ALLOW_CACHE_ON_FAIL=1

  # HeartMath:
  SCHU_GCI_ENABLE=1
  SCHU_GCI_STATION=GCI003
  SCHU_HEARTMATH_HTML=path/to/gcms_magnetometer_heartmath.html
  SCHU_GCI_URL=https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/
  SCHU_GCI_IFRAME=https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html
  SCHU_MAP_GCI_POWER_TO_AMP=1   # маппить power→amp (по умолчанию ВКЛ)
  SCHU_DEBUG=1                  # 1 — подробный лог в stdout

  # Кастомный эндпоинт:
  SCHU_CUSTOM_URL=

  # Спектр для 7-й гармоники (если найдёшь источник):
  H7_URL=
  H7_TARGET_HZ=54.81
  H7_WINDOW_H=48
  H7_Z=2.5
  H7_MIN_ABS=0.2

CLI:
  python schumann.py --collect
"""

from __future__ import annotations

import os, sys, io, time, json, math, re, gzip
from typing import Any, Dict, List, Optional, Tuple, Iterable

try:
    import requests
except Exception:
    print("You need 'requests' package", file=sys.stderr)
    raise

FREQ = 7.83
# Можно задать несколько станций через запятую — будет перебор:
# Примеры кодов: GCI001=California (USA), GCI003=Lithuania, GCI006=Alberta (Canada), GCI004=New Zealand, GCI005=South Africa
GCI_STATIONS_ENV = os.getenv("SCHU_GCI_STATIONS", "").strip()
if GCI_STATIONS_ENV:
    GCI_STATION_KEYS = [s.strip().upper() for s in GCI_STATIONS_ENV.split(",") if s.strip()]
else:
    GCI_STATION_KEYS = [os.getenv("SCHU_GCI_STATION", "GCI003").strip().upper()]

# --------------------------- utils ---------------------------

def dbg(msg: str) -> None:
    if os.environ.get("SCHU_DEBUG", "0").lower() in ("1","true","yes","on"):
        print(f"[DEBUG] {msg}")

def now_ts() -> int:
    return int(time.time())

def read_env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else v

def to_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def to_float(x: Any, default: float = math.nan) -> float:
    try:
        f = float(x)
        if math.isfinite(f):
            return f
        return default
    except Exception:
        return default

def http_get(url: str, timeout: int = 25, headers: Optional[Dict[str,str]] = None) -> str:
    h = {"User-Agent": "Mozilla/5.0 (compatible; Vaybometer-SchuBot/2.3; +github-actions)"}
    if headers:
        h.update(headers)
    r = requests.get(url, timeout=timeout, headers=h)
    r.raise_for_status()
    if r.headers.get("content-encoding","").lower() == "gzip":
        return gzip.decompress(r.content).decode(r.encoding or "utf-8", errors="replace")
    return r.text

def load_local_file(path: str) -> str:
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def json_tidy(s: str) -> str:
    t = s
    t = re.sub(r"'", r'"', t)                                 # одинарные → двойные
    t = re.sub(r'(?m)([{\s,])([A-Za-z_]\w*)\s*:', r'\1"\2":', t)  # некавыченные ключи
    t = re.sub(r',\s*([}\]])', r'\1', t)                      # висячие запятые
    return t

def parse_any_json(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except Exception:
        try:
            return json.loads(json_tidy(s))
        except Exception:
            return None

# ---------------- HeartMath scraping (GCI Power) --------------

HEARTMATH_PAGE = read_env(
    "SCHU_GCI_URL",
    "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/"
)

# допускаем любой порядок атрибутов class/src
IFRAME_SRC_RES = [
    re.compile(r'<iframe[^>]+class="[^"]*hm-gcms-src[^"]*"[^>]+src="([^"]+)"', re.I),
    re.compile(r'<iframe[^>]+src="([^"]+)"[^>]+class="[^"]*hm-gcms-src[^"]*"', re.I),
]

# Highcharts-подобные куски
SERIES_BLOCK_RE = re.compile(r'series\s*:\s*\[(.+?)\]\s*[),;}]', re.I | re.DOTALL)

# массив пар [[ts,val], ...]
PAIR_ARRAY_RE = re.compile(
    r'\[\s*\[\s*(\d{10,13})\s*,\s*([-+]?\d+(?:\.\d+)?)\s*](?:\s*,\s*\[\s*(?:\d{10,13})\s*,\s*[-+]?\d+(?:\.\d+)?\s*])+\s*]',
    re.DOTALL
)

# серия вида {"name":"...GCI003...","data":[...]}
SERIES_ITEM_RE = re.compile(
    r'\{\s*("name"|"label")\s*:\s*"([^"]*GCI003[^"]*|[^"]*Lithuania[^"]*)"\s*,\s*("data"|"series"|"values")\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)',
    re.I | re.DOTALL
)

# 🔧 FIX: безопасный «любой символ» — [\s\S], а не некорректный [^]
NAME_NEAR_DATA_RE = re.compile(
    r'(GCI003|Lithuania)[\s\S]{0,800}?data\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)',
    re.I | re.DOTALL
)

def extract_iframe_src(html: str) -> Optional[str]:
    for rx in IFRAME_SRC_RES:
        m = rx.search(html)
        if m:
            return m.group(1)
    return None

def pick_latest_pair(pairs: Iterable[Tuple[int, float]]) -> Optional[Tuple[int,float]]:
    latest = None
    for ts, val in pairs:
        if not math.isfinite(val):
            continue
        ts = int(ts / 1000) if ts > 1e12 else int(ts)
        if latest is None or ts > latest[0]:
            latest = (ts, float(val))
    return latest

def parse_pairs_block(s: str) -> List[Tuple[int, float]]:
    data = parse_any_json(s)
    out: List[Tuple[int,float]] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                ts = to_int(row[0], 0)
                val = to_float(row[1], math.nan)
                if ts and math.isfinite(val):
                    out.append((ts, val))
    return out

def find_gci_series_block(iframe_html: str) -> Optional[List[Tuple[int,float]]]:
    # A) предметный матч по имени
    for m in SERIES_ITEM_RE.finditer(iframe_html):
        arr = parse_pairs_block(m.group(4))
        if arr:
            return arr
    # B) общий блок series: [...]
    sb = SERIES_BLOCK_RE.search(iframe_html)
    if sb:
        block = sb.group(1)
        chunks = re.split(r'\}\s*,\s*\{', block)
        for ch in chunks:
            if re.search(r'(GCI003|Lithuania)', ch, re.I):
                m = re.search(r'data\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)', ch, re.I | re.DOTALL)
                if m:
                    arr = parse_pairs_block(m.group(1))
                    if arr:
                        return arr
    # C) эвристика «имя рядом с data»
    for m in NAME_NEAR_DATA_RE.finditer(iframe_html):
        arr = parse_pairs_block(m.group(2))
        if arr:
            return arr
    # D) last resort: самые «увесистые» массивы пар; плюс биас, если слева подсказка GCI003
    candidates: List[Tuple[int, List[Tuple[int,float]]]] = []
    for m in PAIR_ARRAY_RE.finditer(iframe_html):
        s = m.group(0)
        left = iframe_html[max(0, m.start()-1000):m.start()]
        bias = 100000 if re.search(r'(GCI003|Lithuania)', left, re.I) else 0
        arr = parse_pairs_block(s)
        if arr:
            candidates.append((len(arr) + bias, arr))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def get_gci_power(station_key: str = GCI_STATION_KEY,
                  page_html: Optional[str] = None) -> Optional[Tuple[int, float]]:
    """
    Возвращает (ts, power) для станции HeartMath (по умолчанию GCI003).
    """
    # 0) локальная сохранёнка?
    page_html_path = read_env("SCHU_HEARTMATH_HTML", "").strip()
    if page_html is None and page_html_path:
        try:
            page_html = load_local_file(page_html_path)
            dbg(f"Loaded local HeartMath HTML: {page_html_path} ({len(page_html)} bytes)")
        except Exception as e:
            dbg(f"Local HTML read error: {e}")
            page_html = None

    # 1) если нет — грузим лайв-страницу
    if page_html is None:
        try:
            page_html = http_get(HEARTMATH_PAGE, timeout=25)
            dbg(f"Fetched HeartMath page ok ({len(page_html)} bytes)")
        except Exception as e:
            dbg(f"Fetch HeartMath page error: {e}")
            page_html = None

    if not page_html:
        return None

    # 2) ищем iframe
    iframe_url = extract_iframe_src(page_html)
    if not iframe_url:
        dbg("No iframe found — assuming given HTML IS the iframe")
        iframe_html = page_html
    else:
        # относительный путь? → на дефолтный live iframe
        if not iframe_url.lower().startswith(("http://", "https://")):
            iframe_url = read_env(
                "SCHU_GCI_IFRAME",
                "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html"
            )
            dbg(f"Using fallback iframe URL: {iframe_url}")
        try:
            iframe_html = http_get(iframe_url, timeout=25)
            dbg(f"Fetched iframe ok ({len(iframe_html)} bytes)")
        except Exception as e:
            dbg(f"Fetch iframe error: {e}")
            return None

    # 3) извлекаем массив пар для GCI003
    pairs = find_gci_series_block(iframe_html)
    if not pairs:
        dbg("No GCI003 series found in iframe")
        return None

    latest = pick_latest_pair(pairs)
    if not latest:
        dbg("No latest pair after parsing")
        return None

    return latest  # (ts, power)

# --------------- optional H7 spectrum hook (stub-safe) ---------------

def try_h7_spike() -> Tuple[Optional[float], Optional[bool]]:
    h7_url = read_env("H7_URL", "").strip()
    if not h7_url:
        return (None, None)
    try:
        txt = http_get(h7_url, timeout=20)
        target = to_float(read_env("H7_TARGET_HZ", "54.81"), 54.81)
        data = parse_any_json(txt)
        vals: List[Tuple[float,float]] = []
        def dig(x):
            if isinstance(x, dict):
                f = x.get("freq", x.get("frequency"))
                a = x.get("amp", x.get("amplitude"))
                if f is not None and a is not None:
                    ff = to_float(f, math.nan); aa = to_float(a, math.nan)
                    if math.isfinite(ff) and math.isfinite(aa): vals.append((ff, aa))
                for v in x.values(): dig(v)
            elif isinstance(x, list):
                for v in x: dig(v)
        dig(data)
        if not vals:
            return (None, None)
        vals.sort(key=lambda t: abs(t[0]-target))
        h7_amp = vals[0][1]
        return (h7_amp, None)
    except Exception as e:
        dbg(f"H7 fetch/parse error: {e}")
        return (None, None)

# ------------------------- storage v2 -------------------------

def load_series(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            if not txt:
                return []
            data = json.loads(txt)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def save_series(path: str, arr: List[Dict[str,Any]]) -> None:
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, separators=(",",":"))
    os.replace(tmp, path)

def clamp_len(arr: List[Dict[str,Any]], max_len: int) -> List[Dict[str,Any]]:
    return arr[-max_len:] if max_len > 0 and len(arr) > max_len else arr

def append_record(path: str,
                  ts: int,
                  amp: Optional[float],
                  src: str,
                  h7_amp: Optional[float] = None,
                  h7_spike: Optional[bool] = None) -> List[Dict[str,Any]]:
    arr = load_series(path)
    rec = {
        "ts": ts,
        "freq": FREQ,
        "amp": None if amp is None or not math.isfinite(amp) else round(float(amp), 2),
        "h7_amp": None if (h7_amp is None or not math.isfinite(h7_amp)) else round(float(h7_amp), 2),
        "ver": 2,
        "src": src,
        "h7_spike": h7_spike if isinstance(h7_spike, bool) else None
    }
    if arr and isinstance(arr[-1], dict) and int(arr[-1].get("ts",0)) == ts:
        arr[-1] = rec
    else:
        arr.append(rec)
    max_len = to_int(read_env("SCHU_MAX_LEN", "5000"), 5000)
    arr = clamp_len(arr, max_len)
    save_series(path, arr)
    return arr

# --------------------- CUSTOM_URL support ---------------------

def fetch_custom_url(url: str) -> Optional[Tuple[int, float]]:
    try:
        txt = http_get(url, timeout=20)
        data = parse_any_json(txt)
        found_amp: List[float] = []
        def dig(x):
            if isinstance(x, dict):
                f = x.get("freq", x.get("frequency"))
                a = x.get("amp", x.get("power", x.get("value")))
                if f is not None and abs(to_float(f, 0.0) - FREQ) < 0.2 and a is not None:
                    found_amp.append(to_float(a, math.nan))
                for v in x.values(): dig(v)
            elif isinstance(x, list):
                for v in x: dig(v)
        dig(data)
        amp = next((v for v in found_amp if math.isfinite(v)), math.nan)
        if not math.isfinite(amp):
            # fallback: любая числовая 'amp'
            nums: List[float] = []
            def dig2(x):
                if isinstance(x, dict):
                    if "amp" in x: nums.append(to_float(x["amp"], math.nan))
                    for v in x.values(): dig2(v)
                elif isinstance(x, list):
                    for v in x: dig2(v)
            dig2(data)
            amp = next((v for v in nums if math.isfinite(v)), math.nan)
        if not math.isfinite(amp):
            return None
        return (now_ts(), float(amp))
    except Exception as e:
        dbg(f"Custom URL error: {e}")
        return None

# -------------------------- main collect --------------------------

def collect() -> int:
    out_path = read_env("SCHU_FILE", "schumann_hourly.json")
    allow_cache = read_env("SCHU_ALLOW_CACHE_ON_FAIL", "1").lower() in ("1","true","yes","on")
    map_power_to_amp = read_env("SCHU_MAP_GCI_POWER_TO_AMP", "1").lower() in ("1","true","yes","on")

    ts = None
    amp = None
    src = None

    h7_amp, h7_spike = try_h7_spike()

    # 1) кастомный JSON/HTML
    custom_url = read_env("SCHU_CUSTOM_URL","").strip()
    if custom_url:
        r = fetch_custom_url(custom_url)
        if r:
            ts, amp = r
            src = "custom"
            dbg(f"Taken from custom URL: ts={ts}, amp={amp}")

    # 2) HeartMath GCMS
    if ts is None or amp is None:
        if read_env("SCHU_GCI_ENABLE","1").lower() in ("1","true","yes","on"):
            r = get_gci_power(GCI_STATION_KEY)
            if r:
                ts, power = r
                amp = float(power) if map_power_to_amp else None
                src = GCI_STATION_KEY.lower()
                dbg(f"Taken from HeartMath: ts={ts}, power={power}, map_to_amp={map_power_to_amp}")

    # 3) Cache-safe fallback
    if ts is None:
        tnow = now_ts()
        ts = int(tnow - (tnow % 3600))
        amp = None
        if not allow_cache:
            print("collect: no live source available", file=sys.stderr)
            return 2
        src = "cache"
        dbg("Fallback to cache-safe record")

    # масштабирование
    scale = to_float(read_env("SCHU_AMP_SCALE","1"), 1.0)
    if amp is not None and math.isfinite(scale):
        amp = amp * scale

    append_record(out_path, ts, amp, src or "cache", h7_amp=h7_amp, h7_spike=h7_spike)
    print(f"collect: ok ts={ts} src={src} freq={FREQ} amp={amp if amp is not None else 'None'} h7={h7_amp if h7_amp is not None else 'None'} spike={h7_spike if isinstance(h7_spike,bool) else 'None'} -> {os.path.abspath(out_path)}")
    return 0

# --------------------------- entry ---------------------------

def main(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("--collect","collect"):
        return collect()
    print("Usage: python schumann.py --collect", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))
