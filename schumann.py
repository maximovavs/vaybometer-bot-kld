#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — collector for Schumann-like hourly series (v2 JSON)

Что собираем:
- freq: фикс 7.83 Гц (якорь)
- amp: по умолчанию маппим HeartMath GCMS "Power" → amp
- h7_amp / h7_spike: опционально (если будет отдельный спектральный источник)

Источники (по приоритету):
1) SCHU_CUSTOM_URL — любой JSON/HTML, где можно выкопать freq≈7.83 или amp
2) HeartMath GCMS:
   • если задан SCHU_HEARTMATH_HTML — парсим сохранённую страницу (офлайн)
   • иначе качаем live‑страницу + вложенный iframe (толерантные regex)
3) cache-safe: если лайв недоступен, создаём запись с src="cache" (job не падает)

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
  SCHU_GCI_STATIONS=GCI003,GCI001    # ← приоритетный список (Литва, затем Калифорния)
  SCHU_HEARTMATH_HTML=data/gcms_magnetometer_heartmath.html
  SCHU_GCI_URL=https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/
  SCHU_GCI_IFRAME=https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html
  SCHU_MAP_GCI_POWER_TO_AMP=1
  SCHU_DEBUG=1

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

# --------------------------- stations ---------------------------

# Доп. алиасы по географии (помогают, если в series вместо GCIxxx стоит «Lithuania», «California» и т.п.)
GCI_ALIASES: Dict[str, List[str]] = {
    "GCI001": ["GCI001", "California", "USA"],
    "GCI003": ["GCI003", "Lithuania"],
    "GCI004": ["GCI004", "New Zealand", "NZ"],
    "GCI005": ["GCI005", "South Africa"],
    "GCI006": ["GCI006", "Alberta", "Canada"],
}

def env_station_list() -> List[str]:
    s = os.getenv("SCHU_GCI_STATIONS", "") or os.getenv("SCHU_GCI_STATION", "GCI003")
    keys = [k.strip().upper() for k in s.split(",") if k.strip()]
    # фильтр по известным GCIxxx + нормализация
    out = []
    for k in keys:
        if re.fullmatch(r"GCI00[1-6]", k):
            out.append(k)
    if not out:
        out = ["GCI003"]
    return out

GCI_STATION_KEYS: List[str] = env_station_list()

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
    h = {"User-Agent": "Mozilla/5.0 (compatible; Vaybometer-SchuBot/2.4; +github-actions)"}
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

# Highcharts‑подобные куски
SERIES_BLOCK_RE = re.compile(r'series\s*:\s*\[(.+?)\]\s*[),;}]', re.I | re.DOTALL)
PAIR_ARRAY_RE   = re.compile(r'\[\s*\[\s*(\d{10,13})\s*,\s*([-+]?\d+(?:\.\d+)?)\s*](?:\s*,\s*\[\s*(?:\d{10,13})\s*,\s*[-+]?\d+(?:\.\d+)?\s*])+\s*]', re.DOTALL)

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

def build_alias_regex(keys: List[str]) -> re.Pattern:
    aliases: List[str] = []
    for k in keys:
        aliases.extend(GCI_ALIASES.get(k, [k]))
    # Экранируем и собираем в одну группу
    alt = "|".join(sorted({re.escape(a) for a in aliases}, key=len, reverse=True))
    return re.compile(alt, re.I)

def extract_series_for_keys(iframe_html: str, keys: List[str]) -> Dict[str, List[Tuple[int,float]]]:
    """Возвращает словарь {station_key: [(ts,val), ...]} для указанных станций."""
    res: Dict[str, List[Tuple[int,float]]] = {}
    alias_re = build_alias_regex(keys)

    # A) «series: [...]» — режем на части по объектам
    sb = SERIES_BLOCK_RE.search(iframe_html)
    if sb:
        block = sb.group(1)
        chunks = re.split(r'\}\s*,\s*\{', block)
        for ch in chunks:
            m_name = alias_re.search(ch)
            if not m_name:
                continue
            # data: [...]
            m_data = re.search(r'data\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)', ch, re.I | re.DOTALL)
            if not m_data:
                continue
            arr = parse_pairs_block(m_data.group(1))
            if not arr:
                continue
            # понять ключ станции по сработавшему алиасу
            alias = m_name.group(0)
            key = next((k for k, al in GCI_ALIASES.items() if alias.lower() in [a.lower() for a in al]), None)
            if key and key in keys:
                res[key] = arr

    # B) Если не нашли — эвристика по «ближайшему тексту»
    if not res:
        for m in PAIR_ARRAY_RE.finditer(iframe_html):
            arr = parse_pairs_block(m.group(0))
            if not arr:
                continue
            left = iframe_html[max(0, m.start()-1000):m.start()]
            m_name = alias_re.search(left)
            if not m_name:
                continue
            alias = m_name.group(0)
            key = next((k for k, al in GCI_ALIASES.items() if alias.lower() in [a.lower() for a in al]), None)
            if key and key in keys and key not in res:
                res[key] = arr

    return res

def get_gci_power(station_keys: Optional[List[str]] = None,
                  page_html: Optional[str] = None) -> Optional[Tuple[int, float, str]]:
    """
    Возвращает (ts, power, station_key) для HeartMath. Перебирает станции по приоритету.
    """
    keys = station_keys or GCI_STATION_KEYS

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

    # 3) извлекаем серии сразу для нескольких станций
    found = extract_series_for_keys(iframe_html, keys)
    if not found:
        dbg("No GCI series for requested stations")
        return None

    # 4) выбираем по приоритету
    for k in keys:
        if k in found:
            latest = pick_latest_pair(found[k])
            if latest:
                return (latest[0], latest[1], k)

    return None

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

    ts: Optional[int] = None
    amp: Optional[float] = None
    src: Optional[str] = None

    h7_amp, h7_spike = try_h7_spike()

    # 1) кастомный JSON/HTML
    custom_url = read_env("SCHU_CUSTOM_URL","").strip()
    if custom_url:
        r = fetch_custom_url(custom_url)
        if r:
            ts, amp = r
            src = "custom"
            dbg(f"Taken from custom URL: ts={ts}, amp={amp}")

    # 2) HeartMath GCMS — перебор станций по приоритету
    if (ts is None or amp is None) and read_env("SCHU_GCI_ENABLE","1").lower() in ("1","true","yes","on"):
        r = get_gci_power(GCI_STATION_KEYS)
        if r:
            ts, power, key_used = r
            amp = float(power) if map_power_to_amp else None
            src = key_used.lower()
            dbg(f"Taken from HeartMath: ts={ts}, power={power}, station={key_used}, map_to_amp={map_power_to_amp}")

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
    print(
        "collect: ok ts={ts} src={src} freq={freq} amp={amp} h7={h7} spike={spike} -> {path}".format(
            ts=ts, src=src, freq=FREQ,
            amp=("None" if amp is None else amp),
            h7=("None" if h7_amp is None else h7_amp),
            spike=("None" if not isinstance(h7_spike, bool) else h7_spike),
            path=os.path.abspath(out_path),
        )
    )
    return 0

# --------------------------- entry ---------------------------

def main(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("--collect","collect"):
        return collect()
    print("Usage: python schumann.py --collect", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))