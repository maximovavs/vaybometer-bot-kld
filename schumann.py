#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных для «Шумана» (v2.4, GCI power_levels.php)

Возможности:
• Сбор ежечасной точки с безопасным кеш-фоллбэком (минимум null).
• Источники:
  - CUSTOM JSON (SCHU_CUSTOM_URL) — любой JSON, где удаётся найти freq/amp.
  - HeartMath GCI:
      * страница (GCI_PAGE_URL) → iframe src → power_levels.php JSON
      * прямой iframe (GCI_IFRAME_URL)
      * сохранённый HTML (GCI_SAVED_HTML)
    Можно маппить GCI power → amp (SCHU_MAP_GCI_POWER_TO_AMP=1)
  - (опц.) TSU (страница живости, без чисел)
• Запись в файл истории (SCHU_FILE, по умолчанию schumann_hourly.json).
• Forward-fill амплитуды при src=='cache' (если раньше была валидная amp).
• H7: поля h7_amp/h7_spike оставлены под будущее.
• get_schumann() возвращает freq/amp/trend/status/h7/interpretation.

CLI:
  --collect          собрать одну точку и сохранить в историю
  --fix-history      нормализовать и дедуплицировать историю
  --print            вывести итог get_schumann() (для отладки CI)
"""

from __future__ import annotations
import os, sys, re, json, time, math, calendar
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    requests = None

# ───────────────── Константы и ENV ─────────────────

DEF_FILE    = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE    = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA  = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

# Диапазоны
FREQ_MIN = float(os.getenv("SCHU_FREQ_MIN", "0"))
FREQ_MAX = float(os.getenv("SCHU_FREQ_MAX", "100"))
AMP_MIN  = float(os.getenv("SCHU_AMP_MIN",  "0"))
AMP_MAX  = float(os.getenv("SCHU_AMP_MAX",  "1000000"))

# Пороги статуса частоты
FREQ_GREEN_MIN = 7.7
FREQ_GREEN_MAX = 8.1
FREQ_RED_MIN   = 7.4
FREQ_RED_MAX   = 8.4

# HeartMath / GCI
GCI_ENABLE     = os.getenv("SCHU_GCI_ENABLE", "0") == "1"
GCI_STATIONS   = [s.strip() for s in os.getenv("SCHU_GCI_STATIONS", "GCI003").split(",") if s.strip()]
GCI_PAGE_URL   = os.getenv("SCHU_GCI_URL", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/")
GCI_IFRAME_URL = os.getenv("SCHU_GCI_IFRAME", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html")
GCI_SAVED_HTML = os.getenv("SCHU_HEARTMATH_HTML", "")
MAP_GCI_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "0") == "1"

# TSU (живость, индикатор; чисел нет)
TSU_ENABLE   = os.getenv("SCHU_TSU_ENABLE", "0") == "1"
TSU_URL      = os.getenv("SCHU_TSU_URL", "https://sosrff.tsu.ru/?page_id=502")
TSU_SNAPSHOT = os.getenv("SCHU_TSU_SNAPSHOT", "")

CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

# H7 placeholders (резерв)
H7_URL       = os.getenv("H7_URL", "").strip()
H7_TARGET_HZ = float(os.getenv("H7_TARGET_HZ", "54.81"))

DEBUG = os.getenv("SCHU_DEBUG", "0") == "1"
USER_AGENT = os.getenv("SCHU_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Circuit breaker (для GCI запросов)
BREAKER_FILE      = ".schu_breaker.json"
BREAKER_THRESHOLD = int(os.getenv("SCHU_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN  = int(os.getenv("SCHU_BREAKER_COOLDOWN",  "1800"))

# Директория для дампов
CACHE_DIR = ".cache"
if DEBUG:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        pass

# ───────────────── Регэкспы ─────────────────

IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']', re.I)
DATA_JSON_FROM_RE = re.compile(r'data-load-json-from=["\']([^"\']+)["\']', re.I)
# запасной сырый поиск JSON (почти не используется теперь)
JSON_IN_IFRAME_RE = re.compile(r'(?:postMessage\s*\(\s*(\{.*?\})\s*,|\bvar\s+\w+\s*=\s*(\{.*?\}|\[.*?\]))', re.I | re.S)

# ───────────────── Helpers ─────────────────

def _now_hour_ts_utc() -> int:
    t = time.gmtime()
    return int(calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, 0, 0)))

def _load_history(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _write_history(path: str, items: List[Dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, path)

def _dump(name: str, content: str | bytes):
    if not DEBUG:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
        with open(os.path.join(CACHE_DIR, name), mode) as f:
            f.write(content)
    except Exception:
        pass

# ─────── Приоритет источников ───────

def _src_rank(src: str) -> int:
    return {"gci_json": 4, "live": 3, "custom": 2, "gci_live": 2, "gci_saved": 2, "gci_iframe": 2, "tsu_live": 1, "cache": 1}.get(str(src), 0)

def _better_record(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    ra, rb = _src_rank(a.get("src")), _src_rank(b.get("src"))
    if ra != rb:
        return a if ra > rb else b
    # иначе — выбираем тот, где есть amp
    a_has, b_has = isinstance(a.get("amp"), (int, float)), isinstance(b.get("amp"), (int, float))
    if a_has and not b_has: return a
    if b_has and not a_has: return b
    return b

def upsert_record(path: str, rec: Dict[str, Any], max_len: Optional[int] = None) -> None:
    try:
        ts = int(rec.get("ts"))
    except Exception:
        return
    hist = _load_history(path)
    merged: Dict[int, Dict[str, Any]] = {}
    for r in hist:
        try:
            t = int(r.get("ts"))
        except Exception:
            continue
        merged[t] = r if t not in merged else _better_record(merged[t], r)
    merged[ts] = rec if ts not in merged else _better_record(merged[ts], rec)
    out = [merged[t] for t in sorted(merged)]
    if isinstance(max_len, int) and max_len > 0 and len(out) > max_len:
        out = out[-max_len:]
    _write_history(path, out)

def last_known_amp(path: str) -> Optional[float]:
    for r in reversed(_load_history(path)):
        v = r.get("amp")
        if isinstance(v, (int, float)):
            return float(v)
    return None

# ─────── HTTP ───────
_SESSION = None
def _session():
    global _SESSION
    if _SESSION:
        return _SESSION
    if not requests:
        return None
    s = requests.Session()
    try:
        retries = Retry(
            total=2, connect=2, read=2, backoff_factor=0.6,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET"])
        )
        s.mount("https://", HTTPAdapter(max_retries=retries))
        s.mount("http://",  HTTPAdapter(max_retries=retries))
    except Exception:
        pass
    s.headers.update({"User-Agent": USER_AGENT})
    _SESSION = s
    return s

def _get(url, **params):
    s = _session()
    try:
        return s.get(url, params=params, timeout=15, allow_redirects=True)
    except Exception:
        return None

# ─────── Circuit breaker ───────
def _breaker_state():
    try:
        with open(BREAKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fail": 0, "until": 0}

def _breaker_save(st):
    try:
        with open(BREAKER_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception:
        pass

def breaker_allow() -> bool:
    return time.time() >= _breaker_state().get("until", 0)

def breaker_ok():
    _breaker_save({"fail": 0, "until": 0})

def breaker_bad():
    st = _breaker_state()
    st["fail"] = st.get("fail", 0) + 1
    if st["fail"] >= BREAKER_THRESHOLD:
        st["until"] = int(time.time()) + BREAKER_COOLDOWN
    _breaker_save(st)

# ─────── HTML/JSON parse helpers ───────

def extract_iframe_src(html: str | None) -> Optional[str]:
    if not html:
        return None
    m = IFRAME_SRC_RE.search(html)
    return m.group(1) if m else None

def extract_json_path_from_iframe(html: str | None) -> Optional[str]:
    if not html:
        return None
    m = DATA_JSON_FROM_RE.search(html)
    return m.group(1) if m else None

def extract_json_from_iframe_inline(html: str | None) -> Optional[Any]:
    """Редкий фоллбэк: если JSON оказался «внутри» как var … = {...}."""
    if not html:
        return None
    for m in JSON_IN_IFRAME_RE.finditer(html):
        block = m.group(1) or m.group(2)
        if not block:
            continue
        try:
            return json.loads(block)
        except Exception:
            continue
    return None

def _flatten_numbers_with_paths(obj: Any, path: Tuple[str, ...] = ()) -> List[Tuple[Tuple[str, ...], float]]:
    """Разворачивает все числа в JSON вместе с путём до них — для эвристики по станциям."""
    out: List[Tuple[Tuple[str, ...], float]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_flatten_numbers_with_paths(v, path + (str(k),)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten_numbers_with_paths(v, path + (str(i),)))
    elif isinstance(obj, (int, float)) and math.isfinite(obj):
        out.append((path, float(obj)))
    return out

def _numbers_near_station(json_obj: Any, station: str) -> List[float]:
    """
    Возвращает все числа, путь до которых содержит station (без учёта регистра).
    Подходит для структур вида {"GCI001": 12.3} или {"stations":{"GCI001":{"power":[... , 7.8]}}} и т.п.
    """
    station_lc = station.lower()
    vals: List[float] = []
    for path, num in _flatten_numbers_with_paths(json_obj):
        if any(station_lc in p.lower() for p in path):
            vals.append(num)
    return vals

def _aggregate_stations_power(json_obj: Any, stations: List[str]) -> Optional[float]:
    """Среднее по доступным станциям из списка. Берём последнее число по каждой станции."""
    per_station: List[float] = []
    for st in stations:
        cand = _numbers_near_station(json_obj, st)
        if cand:
            per_station.append(cand[-1])  # последнее как «актуальное»
    if per_station:
        return sum(per_station) / len(per_station)
    # как фоллбэк: поле 'power' без указания станции
    # попробуем вытащить последнее число по ключу 'power'
    flat = _flatten_numbers_with_paths(json_obj)
    power_like = [num for path, num in flat if any("power" == p.lower() for p in path)]
    if power_like:
        return power_like[-1]
    return None

# ─────── Источники ───────

def get_from_custom() -> Tuple[Optional[float], Optional[float], str]:
    if not CUSTOM_URL or not requests:
        return None, None, "none"
    try:
        r = _get(CUSTOM_URL)
        if not r or r.status_code != 200:
            return None, None, "custom_fail"
        data = r.json()
    except Exception:
        return None, None, "custom_fail"

    # Пытаемся найти freq/amp во всём JSON (наивно)
    def deep_find_number(obj, *keys):
        if obj is None:
            return None
        if isinstance(obj, list):
            for x in reversed(obj):
                v = deep_find_number(x, *keys)
                if isinstance(v, (int, float)):
                    return float(v)
            return None
        if isinstance(obj, dict):
            for k in keys:
                for kk, vv in obj.items():
                    if isinstance(kk, str) and kk.lower() == k.lower():
                        v = deep_find_number(vv, *keys)
                        if isinstance(v, (int, float)):
                            return float(v)
            for vv in obj.values():
                v = deep_find_number(vv, *keys)
                if isinstance(v, (int, float)):
                    return float(v)
            return None
        if isinstance(obj, (int, float)):
            return float(obj)
        if isinstance(obj, str):
            try:
                return float(obj.replace(",", "."))
            except Exception:
                return None
        return None

    freq = deep_find_number(data, "freq", "frequency")
    amp  = deep_find_number(data, "amp", "amplitude", "power")
    return freq, amp, "custom"

def get_gci_power() -> Tuple[Optional[float], str]:
    """
    Основной путь: страница → iframe → data-load-json-from → power_levels.php JSON.
    Возвращает (power, src), где power — то, что далее маппим в amp.
    """
    if not GCI_ENABLE or not requests:
        return None, "gci_disabled"
    if not breaker_allow():
        return None, "gci_circuit_open"

    page_html = None
    # 1) страница (обёртка)
    if GCI_PAGE_URL:
        r = _get(GCI_PAGE_URL)
        if r and r.status_code == 200:
            page_html = r.text
            _dump("gci_page.html", page_html)

    iframe_url = extract_iframe_src(page_html) or GCI_IFRAME_URL

    # 2) iframe HTML
    iframe_html = None
    if iframe_url:
        rr = _get(iframe_url)
        if rr and rr.status_code == 200:
            iframe_html = rr.text
            _dump("gci_iframe.html", iframe_html)

    # 3) извлечь относительный путь к JSON (power_levels.php)
    json_rel = extract_json_path_from_iframe(iframe_html)
    if not json_rel and GCI_SAVED_HTML:
        # попробуем из сохранённого файла
        try:
            saved_html = open(GCI_SAVED_HTML, encoding="utf-8").read()
            _dump("gci_saved.html", saved_html)
            json_rel = extract_json_path_from_iframe(saved_html)
        except Exception:
            pass

    # 4) запросить JSON напрямую
    if json_rel:
        json_url = urljoin(iframe_url, json_rel)
        rj = _get(json_url)
        if rj and rj.status_code == 200:
            try:
                data = rj.json()
            except Exception:
                data = None
            if data is not None:
                try:
                    _dump("gci_json.json", json.dumps(data, ensure_ascii=False))
                except Exception:
                    pass
                power = _aggregate_stations_power(data, GCI_STATIONS)  # среднее по станциям
                if isinstance(power, (int, float)):
                    breaker_ok()
                    return float(power), "gci_json"

    # 5) глубокий фоллбэк: попытка достать inline JSON из iframe
    data_inline = extract_json_from_iframe_inline(iframe_html)
    if data_inline is not None:
        try:
            _dump("gci_iframe_inline.json", json.dumps(data_inline, ensure_ascii=False))
        except Exception:
            pass
        power = _aggregate_stations_power(data_inline, GCI_STATIONS)
        if isinstance(power, (int, float)):
            breaker_ok()
            return float(power), "gci_iframe"

    # 6) отдельный запрос только iframe (если шаги выше не сработали)
    rr2 = _get(GCI_IFRAME_URL)
    if rr2 and rr2.status_code == 200:
        _dump("gci_iframe_only.html", rr2.text)
        data_inline2 = extract_json_from_iframe_inline(rr2.text)
        if data_inline2 is not None:
            power = _aggregate_stations_power(data_inline2, GCI_STATIONS)
            if isinstance(power, (int, float)):
                breaker_ok()
                return float(power), "gci_iframe"

    breaker_bad()
    return None, "gci_fail"

def get_tsu_liveness() -> Tuple[bool, str]:
    """
    TSU — страница живости. Чисел нет, но можно фиксировать «источник жив».
    """
    if not TSU_ENABLE or not requests:
        return False, "tsu_disabled"
    r = _get(TSU_URL)
    if r and r.status_code == 200:
        _dump("tsu_live.html", r.text)
        return True, "tsu_live"
    # локальный слепок — для тестов
    if TSU_SNAPSHOT:
        try:
            html = open(TSU_SNAPSHOT, encoding="utf-8").read()
            _dump("tsu_snapshot.html", html)
            return True, "tsu_live"
        except Exception:
            pass
    return False, "tsu_fail"

# ─────── Бизнес-логика сбора ───────

def _clamp_or_none(val, lo, hi):
    try:
        v = float(val)
        if math.isfinite(v) and lo <= v <= hi:
            return v
        return None
    except Exception:
        return None

def collect_once() -> Dict[str, Any]:
    ts = _now_hour_ts_utc()

    freq_val: Optional[float] = None
    amp_val: Optional[float] = None
    h7_amp: Optional[float] = None
    h7_spike: Optional[bool] = None
    src = "none"

    # 1) CUSTOM
    if CUSTOM_URL:
        f, a, src_c = get_from_custom()
        if f is not None:
            freq_val = f
        if a is not None:
            amp_val = a * AMP_SCALE
        src = src_c

    # 2) GCI
    if amp_val is None and GCI_ENABLE:
        power, src_g = get_gci_power()
        if isinstance(power, (int, float)) and MAP_GCI_TO_AMP:
            amp_val = float(power) * AMP_SCALE
            src = src_g
        elif src == "none":
            src = src_g  # хотя бы отметим источник

    # 3) TSU живость (как индикатор источника; чисел не даст)
    if amp_val is None and TSU_ENABLE:
        ok, src_t = get_tsu_liveness()
        if ok and src == "none":
            src = src_t

    # 4) Частота по умолчанию: 7.83 (и нормализатор)
    if freq_val is None:
        freq_val = 7.83
    freq_val = _clamp_or_none(freq_val, FREQ_MIN, FREQ_MAX) or 7.83

    # 5) Нормализация amp
    if amp_val is not None:
        amp_val = _clamp_or_none(amp_val, AMP_MIN, AMP_MAX)

    # 6) Кэш-фоллбэк по amp
    if amp_val is None and ALLOW_CACHE:
        amp_prev = last_known_amp(DEF_FILE)
        if amp_prev is not None:
            amp_val = amp_prev
            src = "cache"

    return {
        "ts": ts,
        "freq": freq_val,
        "amp": amp_val,
        "h7_amp": h7_amp,
        "h7_spike": h7_spike,
        "ver": 2,
        "src": src
    }

# ─────── Интерпретации ───────

def classify_freq_status(freq: Optional[float]) -> Tuple[str, str]:
    if not isinstance(freq, (int, float)):
        return "🟡 колебания", "yellow"
    if FREQ_RED_MIN <= freq <= FREQ_RED_MAX:
        if FREQ_GREEN_MIN <= freq <= FREQ_GREEN_MAX:
            return "🟢 в норме", "green"
        return "🟡 колебания", "yellow"
    return "🔴 сильное отклонение", "red"

def trend_human(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def format_h7(h7: Optional[float], h7s: Optional[bool]) -> str:
    if isinstance(h7, (int, float)):
        return f"· H7: {h7:.1f} (⚡ всплеск)" if h7s else f"· H7: {h7:.1f} — спокойно"
    return "· H7: — нет данных"

def gentle_interpretation(code: str) -> str:
    return {
        "green": "Волны Шумана близки к норме — организм реагирует как на обычный день.",
        "yellow": "Заметны колебания — возможна лёгкая чувствительность к погоде и настроению.",
        "red": "Сильные отклонения — прислушивайтесь к самочувствию и снижайте перегрузки."
    }.get(code, "")

def _trend_arrow(vals: List[float], delta: float = TREND_DELTA) -> str:
    if len(vals) < 2:
        return "→"
    last = vals[-1]
    avg_prev = sum(vals[:-1]) / (len(vals) - 1)
    if last - avg_prev >= delta:
        return "↑"
    if last - avg_prev <= -delta:
        return "↓"
    return "→"

# ─────── Публичное API ───────

def get_schumann() -> Dict[str, Any]:
    hist = _load_history(DEF_FILE)
    if not hist:
        return {
            "freq": None, "amp": None, "trend": "→", "trend_text": "стабильно",
            "status": "🟡 колебания", "status_code": "yellow",
            "h7_text": format_h7(None, None), "h7_amp": None, "h7_spike": None,
            "interpretation": gentle_interpretation("yellow"), "cached": True
        }

    # тренд по частоте (как раньше; частота может быть константой 7.83 — тогда «стабильно»)
    freq_series = [r.get("freq") for r in hist if isinstance(r.get("freq"), (int, float))]
    freq_series = freq_series[-max(TREND_WINDOW, 2):] if freq_series else []
    trend = _trend_arrow(freq_series) if freq_series else "→"

    last = hist[-1]
    freq, amp = last.get("freq"), last.get("amp")
    status, status_code = classify_freq_status(freq)

    return {
        "freq": freq, "amp": amp,
        "trend": trend, "trend_text": trend_human(trend),
        "status": status, "status_code": status_code,
        "h7_text": format_h7(last.get("h7_amp"), last.get("h7_spike")),
        "interpretation": gentle_interpretation(status_code),
        "cached": (last.get("src") == "cache"),
        "h7_amp": last.get("h7_amp"), "h7_spike": last.get("h7_spike")
    }

# ─────── История ───────

def fix_history(path: str) -> Tuple[int, int]:
    hist = _load_history(path)
    old = len(hist)
    by_ts: Dict[int, Dict[str, Any]] = {}
    for r in hist:
        try:
            ts = int(float(r.get("ts")))
        except Exception:
            continue
        rr = dict(r)
        f = _clamp_or_none(rr.get("freq"), FREQ_MIN, FREQ_MAX) or 7.83
        a = _clamp_or_none(abs(rr.get("amp")) if isinstance(rr.get("amp"), (int, float)) else None, AMP_MIN, AMP_MAX)
        rr.update(ts=ts, freq=f, amp=a, ver=2, src=rr.get("src") or "cache")
        rr.setdefault("h7_amp", None)
        rr.setdefault("h7_spike", None)
        by_ts[ts] = rr if ts not in by_ts else _better_record(by_ts[ts], rr)
    cleaned = [by_ts[k] for k in sorted(by_ts)]
    _write_history(path, cleaned)
    return old, len(cleaned)

# ─────── CLI ───────

def _cmd_collect():
    rec = collect_once()
    upsert_record(DEF_FILE, rec, DEF_MAX_LEN)
    print(f"collect: ts={rec['ts']} src={rec['src']} freq={rec['freq']} amp={rec['amp']}")
    if rec.get("src") == "cache":
        print("WARN: cache fallback — live unavailable")
    # echo last record JSON
    try:
        print("Last record JSON:", json.dumps(rec, ensure_ascii=False))
    except Exception:
        pass

def _cmd_fix_history():
    old, new = fix_history(DEF_FILE)
    print(f"fix-history: {old} -> {new}; file={DEF_FILE}")

def _cmd_print():
    state = get_schumann()
    print(json.dumps(state, ensure_ascii=False, indent=2))

def main():
    args = sys.argv[1:]
    if "--collect" in args:
        _cmd_collect(); return
    if "--fix-history" in args:
        _cmd_fix_history(); return
    if "--print" in args:
        _cmd_print(); return
    # По умолчанию — просто одна выборка (как в старых версиях)
    _cmd_collect()

if __name__ == "__main__":
    main()
