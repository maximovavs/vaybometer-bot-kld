#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — collector for Schumann-esque hourly series (v2 JSON)
- freq is fixed 7.83 Hz
- amp is "power" proxy from HeartMath GCMS Magnetometer (GCI003 Lithuania)
- robust HTML/iframe/JS scraping with regex + tolerant JSON cleaner
- optional H7 spectrum hook (kept as stub if you don't use it yet)
- cache-safe: won't fail the workflow if live source is down (env flag)

Env (all optional):
  SCHU_FILE=schumann_hourly.json
  SCHU_MAX_LEN=5000
  SCHU_AMP_SCALE=1
  SCHU_TREND_WINDOW=24
  SCHU_TREND_DELTA=0.1
  SCHU_ALLOW_CACHE_ON_FAIL=1

  # override (for tests) with saved page or custom endpoint:
  SCHU_HEARTMATH_HTML=/path/to/"GCMS Magnetometer _ HeartMath Institute.html"
  SCHU_CUSTOM_URL=https://example/json  # returns {"freq":7.83,"amp":1.2} (any nesting ok)

  # H7 spectrum (optional / stub-friendly)
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
import datetime as dt

try:
    import requests
except Exception as e:
    print("You need 'requests' package", file=sys.stderr)
    raise

FREQ = 7.83
GCI_STATION_KEY = "GCI003"   # Lithuania
GCI_STATION_NAME = "Lithuania"

HEARTMATH_PAGE = "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/"

# --------------------------- utils ---------------------------

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

def http_get(url: str, timeout: int = 20, headers: Optional[Dict[str,str]] = None) -> str:
    h = {
        "User-Agent": "Mozilla/5.0 (compatible; SchuBot/2.0; +github-actions)"
    }
    if headers:
        h.update(headers)
    r = requests.get(url, timeout=timeout, headers=h)
    r.raise_for_status()
    # gunzip if needed
    if r.headers.get("content-encoding","").lower() == "gzip":
        return gzip.decompress(r.content).decode(r.encoding or "utf-8", errors="replace")
    return r.text

def load_local_file(path: str) -> str:
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def json_tidy(s: str) -> str:
    """
    Make JS-ish object/array closer to JSON:
      - replace single quotes with double (careful, but ok for numeric arrays/keys)
      - remove trailing commas
      - unquote bare keys
    This is best-effort for chart configs; falls back gracefully.
    """
    t = s

    # unescape \x style to keep numerics intact
    # 1) replace single quotes around strings/keys -> double quotes
    t = re.sub(r"'", r'"', t)

    # 2) quote bare keys: foo: -> "foo":
    t = re.sub(r'(?m)([{\s,])([A-Za-z_]\w*)\s*:', r'\1"\2":', t)

    # 3) remove trailing commas before ] or }
    t = re.sub(r',\s*([}\]])', r'\1', t)

    return t

def parse_any_json(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except Exception:
        try:
            return json.loads(json_tidy(s))
        except Exception:
            return None

# ---------------- HeartMath scraping (GCI power) --------------

IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+class="[^"]*hm-gcms-src[^"]*"[^>]+src="([^"]+)"',
    re.IGNORECASE
)

# Common patterns seen in embedded chart pages
# We will try multiple strategies:

# 1) Highcharts-like: series: [{name:"GCI003 ...", data:[[ts,val],...]}]
SERIES_BLOCK_RE = re.compile(
    r'series\s*:\s*\[(.+?)\]\s*[),;}]',
    re.IGNORECASE | re.DOTALL
)

# 2) Some pages embed a global var with datasets
#    Try to catch JSON-ish arrays directly: [[ts,val], ...]
PAIR_ARRAY_RE = re.compile(
    r'\[\s*\[\s*(\d{10,13})\s*,\s*([-+]?\d+(?:\.\d+)?)\s*](?:\s*,\s*\[\s*(?:\d{10,13})\s*,\s*[-+]?\d+(?:\.\d+)?\s*])+\s*]',
    re.DOTALL
)

# 3) Name/GCI key near data block
SERIES_ITEM_RE = re.compile(
    r'\{\s*("name"|"label")\s*:\s*"([^"]*GCI003[^"]*|[^"]*Lithuania[^"]*)"\s*,\s*("data"|"series"|"values")\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)',
    re.IGNORECASE | re.DOTALL
)

NAME_NEAR_DATA_RE = re.compile(
    r'(GCI003|Lithuania)[^]{]{0,500}?data\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)',
    re.IGNORECASE | re.DOTALL
)

def extract_iframe_src(html: str) -> Optional[str]:
    m = IFRAME_SRC_RE.search(html)
    if m:
        return m.group(1)
    return None

def pick_latest_pair(pairs: Iterable[Tuple[int, float]]) -> Optional[Tuple[int,float]]:
    latest = None
    for ts, val in pairs:
        if not math.isfinite(val):
            continue
        if ts > 1e12:  # ms -> s
            ts = int(ts / 1000)
        else:
            ts = int(ts)
        if latest is None or ts > latest[0]:
            latest = (ts, float(val))
    return latest

def parse_pairs_block(s: str) -> List[Tuple[int, float]]:
    """
    Accepts a string like [[ts,val],[ts2,val2],...]
    Returns list of (ts, val)
    """
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
    """
    Try several patterns to find the series for GCI003/Lithuania and return pairs.
    """
    # Strategy A: item with explicit "name": "...GCI003..." and "data": [...]
    for m in SERIES_ITEM_RE.finditer(iframe_html):
        arr = parse_pairs_block(m.group(4))
        if arr:
            return arr

    # Strategy B: look for the whole "series: [ ... ]" block and search inside for GCI003
    sb = SERIES_BLOCK_RE.search(iframe_html)
    if sb:
        block = sb.group(1)
        # Split rough objects by "},{" boundaries safest-ish
        chunks = re.split(r'\}\s*,\s*\{', block)
        for ch in chunks:
            if re.search(r'(GCI003|Lithuania)', ch, re.IGNORECASE):
                m = re.search(r'data\s*:\s*(\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*)', ch, re.IGNORECASE | re.DOTALL)
                if m:
                    arr = parse_pairs_block(m.group(1))
                    if arr:
                        return arr

    # Strategy C: near-name heuristic
    for m in NAME_NEAR_DATA_RE.finditer(iframe_html):
        arr = parse_pairs_block(m.group(2))
        if arr:
            return arr

    # Strategy D: last resort — pick any big pair-array and hope it’s the right one,
    #             but prefer ones preceded by GCI003 within 1k chars to the left.
    candidates: List[Tuple[int,float]] = []
    for m in PAIR_ARRAY_RE.finditer(iframe_html):
        s = m.group(0)
        start = m.start()
        left = iframe_html[max(0, start - 1000):start]
        bias = 1 if re.search(r'(GCI003|Lithuania)', left, re.IGNORECASE) else 0
        arr = parse_pairs_block(s)
        if arr:
            # keep best length + bias
            candidates.append((len(arr) + 1000 * bias, arr))  # type: ignore

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]  # best guess

    return None

def get_gci_power(station_key: str = GCI_STATION_KEY,
                  page_html: Optional[str] = None) -> Optional[Tuple[int, float]]:
    """
    Returns (ts, power) for given station, or None.
    Will fetch HEARTMATH_PAGE (or use provided page_html), then load iframe and parse.
    """
    # 0) If the user passed a saved page via env, use that first
    page_html_path = read_env("SCHU_HEARTMATH_HTML", "").strip()
    if page_html is None and page_html_path:
        try:
            page_html = load_local_file(page_html_path)
        except Exception:
            page_html = None

    # 1) Fetch outer page if needed
    if page_html is None:
        try:
            page_html = http_get(HEARTMATH_PAGE, timeout=25)
        except Exception:
            page_html = None

    if not page_html:
        return None

    # 2) Find iframe src
    iframe_url = extract_iframe_src(page_html)
    if not iframe_url:
        # sometimes the page is the chart itself (if saved as inner doc)
        iframe_html = page_html
    else:
        # 3) Fetch iframe
        try:
            iframe_html = http_get(iframe_url, timeout=25)
        except Exception:
            return None

    # 4) Parse series for this station
    pairs = find_gci_series_block(iframe_html)
    if not pairs:
        return None

    latest = pick_latest_pair(pairs)
    if not latest:
        return None

    return latest  # (ts, power)

# --------------- optional H7 spectrum hook (stub-safe) ---------------

def try_h7_spike() -> Tuple[Optional[float], Optional[bool]]:
    """
    Placeholder: returns (h7_amp, h7_spike) if you later connect a spectrum endpoint.
    For now, read env H7_URL; otherwise return (None, None).
    """
    h7_url = read_env("H7_URL", "").strip()
    if not h7_url:
        return (None, None)
    try:
        # Expect JSON like [{"freq":..,"amp":..}, ...] or any nest with arrays
        txt = http_get(h7_url, timeout=20)
        # find best amp near target Hz
        target = to_float(read_env("H7_TARGET_HZ", "54.81"), 54.81)
        data = parse_any_json(txt)
        vals: List[Tuple[float,float]] = []
        def dig(x):
            if isinstance(x, dict):
                # common keys
                f = None; a = None
                if "freq" in x: f = to_float(x["freq"], math.nan)
                if "frequency" in x: f = to_float(x["frequency"], math.nan) if f is math.nan else f
                if "amp" in x: a = to_float(x["amp"], math.nan)
                if "amplitude" in x: a = to_float(x["amplitude"], math.nan) if a is math.nan else a
                if f is not math.nan and a is not math.nan:
                    vals.append((f,a))
                for v in x.values(): dig(v)
            elif isinstance(x, list):
                for v in x: dig(v)
        dig(data)
        if not vals:
            return (None, None)
        # pick closest to target
        vals.sort(key=lambda t: abs(t[0]-target))
        h7_amp = vals[0][1]
        # crude spike flag vs neighbor median (not implemented — return None)
        return (h7_amp, None)
    except Exception:
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
    if max_len > 0 and len(arr) > max_len:
        return arr[-max_len:]
    return arr

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
    """
    Accepts any JSON; will search for "freq"≈7.83 & "amp" value, or just "amp".
    Returns (ts_now, amp) if found.
    """
    try:
        txt = http_get(url, timeout=20)
        data = parse_any_json(txt)
        found_amp = []

        def dig(x):
            if isinstance(x, dict):
                # prioritize freq~7.83 + amp
                f = x.get("freq", x.get("frequency"))
                a = x.get("amp", x.get("power", x.get("value")))
                if f is not None and abs(to_float(f, 0.0) - FREQ) < 0.2 and a is not None:
                    found_amp.append(to_float(a, math.nan))
                # also traverse deeper
                for v in x.values(): dig(v)
            elif isinstance(x, list):
                for v in x: dig(v)

        dig(data)
        amp = None
        for v in found_amp:
            if math.isfinite(v):
                amp = v
                break
        if amp is None:
            # fallback: first numeric "amp" anywhere
            nums = []
            def dig2(x):
                if isinstance(x, dict):
                    if "amp" in x: nums.append(to_float(x["amp"], math.nan))
                    for v in x.values(): dig2(v)
                elif isinstance(x, list):
                    for v in x: dig2(v)
            dig2(data)
            for v in nums:
                if math.isfinite(v):
                    amp = v
                    break
        if amp is None:
            return None
        return (now_ts(), float(amp))
    except Exception:
        return None

# -------------------------- main collect --------------------------

def collect() -> int:
    out_path = read_env("SCHU_FILE", "schumann_hourly.json")
    allow_cache = read_env("SCHU_ALLOW_CACHE_ON_FAIL", "1") in ("1","true","yes","on")

    # Order of attempts:
    # 1) SCHU_CUSTOM_URL (if provided)
    # 2) HeartMath GCMS (GCI003) page+iframe scrape
    # 3) cache-only (no change), if allowed
    ts = None
    amp = None
    src = None

    # H7 (optional)
    h7_amp, h7_spike = try_h7_spike()

    # Try custom URL first
    custom_url = read_env("SCHU_CUSTOM_URL","").strip()
    if custom_url:
        r = fetch_custom_url(custom_url)
        if r:
            ts, amp = r
            src = "custom"

    # Try HeartMath GCI
    if ts is None or amp is None:
        r = get_gci_power(GCI_STATION_KEY)
        if r:
            ts, amp = r
            src = "gci"

    # If still none — we either keep last ts and fill None amp, or use cache semantics.
    if ts is None:
        # as a safe default, use current time bucket (hour floor)
        tnow = now_ts()
        ts = int(tnow - (tnow % 3600))
        amp = None
        if allow_cache:
            # we will mark src=cache; amp stays None
            src = "cache"
        else:
            print("collect: no live source available", file=sys.stderr)
            return 2

    # Scaling if needed
    scale = to_float(read_env("SCHU_AMP_SCALE","1"), 1.0)
    if amp is not None and math.isfinite(scale):
        amp = amp * scale

    # Append to file
    arr = append_record(out_path, ts, amp, src or "cache", h7_amp=h7_amp, h7_spike=h7_spike)

    # trend window/delta kept for possible downstream (no computation here)
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
