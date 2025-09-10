#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных «Шумана» (v2.4)

Возможности:
• Сбор ежечасной точки с безопасным кэш-фоллбэком (минимум null).
• Источники:
  - CUSTOM JSON (SCHU_CUSTOM_URL) — любой JSON, где удаётся найти freq/amp.
  - HeartMath GCI (страница + iframe + JSON/скрипты), станции GCI001..GCI006.
  - TSU / SOSRFF (https://sosrff.tsu.ru/?page_id=502) — эвристика по HTML.
• Запись/обновление истории (SCHU_FILE, по умолчанию schumann_hourly.json).
• «Умная» дедупликация по ts и выбор «лучшей» записи по приоритету src.
• H7-поля (h7_amp/h7_spike) зарезервированы — сейчас заполняются None.
• get_schumann() возвращает freq/amp/trend/status/h7/interpretation.
• CLI:
    --collect         : собрать точку и записать в историю
    --fix-history     : нормализовать и дедуплицировать историю
    --print           : вывести текущую сводку (get_schumann) как JSON
    --last            : вывести последнюю запись истории
"""

from __future__ import annotations
import os
import re
import json
import time
import math
import calendar
from typing import Any, Dict, List, Optional, Tuple

# ────────────────────────────── deps (optional) ─────────────────────────────
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:  # requests может быть отсутствовать (локальные тесты)
    requests = None  # type: ignore

try:
    from bs4 import BeautifulSoup  # опционально для парсинга iframe
except Exception:
    BeautifulSoup = None  # type: ignore

# ────────────────────────────── env / constants ─────────────────────────────

DEF_FILE    = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE    = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA  = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

# Диапазоны значений (защита от мусора)
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
GCI_PAGE_URL   = os.getenv("SCHU_GCI_URL", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/").strip()
GCI_IFRAME_URL = os.getenv("SCHU_GCI_IFRAME", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html").strip()
GCI_SAVED_HTML = os.getenv("SCHU_HEARTMATH_HTML", "").strip()
MAP_GCI_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "1") == "1"

CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

# TSU / SOSRFF
TSU_ENABLE   = os.getenv("SCHU_TSU_ENABLE", "0") == "1"
TSU_URL      = os.getenv("SCHU_TSU_URL", "https://sosrff.tsu.ru/?page_id=502").strip()
TSU_SNAPSHOT = os.getenv("SCHU_TSU_SNAPSHOT", "").strip()

# H7 placeholders (зарезервировано)
H7_URL       = os.getenv("H7_URL", "").strip()
H7_TARGET_HZ = float(os.getenv("H7_TARGET_HZ", "54.81"))
H7_WINDOW_H  = int(os.getenv("H7_WINDOW_H", "48"))
H7_Z         = float(os.getenv("H7_Z", "2.5"))
H7_MIN_ABS   = float(os.getenv("H7_MIN_ABS", "0.2"))

DEBUG      = os.getenv("SCHU_DEBUG", "0") == "1"
USER_AGENT = os.getenv("SCHU_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64)")

# Circuit breaker для сетевых ошибок
BREAKER_FILE      = ".schu_breaker.json"
BREAKER_THRESHOLD = int(os.getenv("SCHU_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN  = int(os.getenv("SCHU_BREAKER_COOLDOWN",  "1800"))

CACHE_DIR = ".cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# ────────────────────────────── utils: time / io ────────────────────────────

def _now_hour_ts_utc() -> int:
    """Начало текущего часа (UTC) как unix timestamp."""
    t = time.gmtime()
    return int(calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, 0, 0)))

def _load_history(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else []
    except Exception:
        return []

def _write_history(path: str, items: List[Dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, path)

def _dump(name: str, blob: str | bytes | None):
    """Сохраняет дамп во .cache/NAME при DEBUG=1."""
    if not DEBUG or not blob:
        return
    p = os.path.join(CACHE_DIR, name)
    try:
        with open(p, "wb") as f:
            if isinstance(blob, (bytes, bytearray)):
                f.write(blob)
            else:
                f.write(blob.encode("utf-8", "ignore"))
    except Exception:
        pass

# ────────────────────────────── merge / ranking ─────────────────────────────

def _src_rank(src: str) -> int:
    # live > gci/tsu/custom > cache > none
    return {
        "live": 5,
        "gci_live": 4, "gci_iframe": 4, "gci_saved": 4,
        "tsu_live": 4, "tsu_snapshot": 3,
        "custom": 3,
        "cache": 2,
        "none": 1,
    }.get(str(src), 0)

def _better_record(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    ra, rb = _src_rank(a.get("src", "")), _src_rank(b.get("src", ""))
    if ra != rb:
        return a if ra > rb else b
    # при равном приоритете — предпочитаем запись с валидной amp
    a_has = isinstance(a.get("amp"), (int, float))
    b_has = isinstance(b.get("amp"), (int, float))
    if a_has != b_has:
        return a if a_has else b
    return b  # по умолчанию — более поздняя (b) выигрывает

def upsert_record(path: str, rec: Dict[str, Any], max_len: Optional[int] = None) -> None:
    """Вставляет/обновляет запись по ts, проводит дедупликацию и подрезает историю."""
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

# ────────────────────────────── HTTP / network ─────────────────────────────

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
        s.mount("http://", HTTPAdapter(max_retries=retries))
    except Exception:
        pass
    s.headers.update({"User-Agent": USER_AGENT})
    _SESSION = s
    return s

def _get(url: str, **params):
    s = _session()
    if not s:
        return None
    try:
        return s.get(url, params=params, timeout=15, allow_redirects=True)
    except Exception:
        return None

# ────────────────────────────── Circuit breaker ─────────────────────────────

def _breaker_state() -> Dict[str, Any]:
    try:
        with open(BREAKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fail": 0, "until": 0}

def _breaker_save(state: Dict[str, Any]) -> None:
    try:
        with open(BREAKER_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass

def breaker_allow() -> bool:
    return time.time() >= _breaker_state().get("until", 0)

def breaker_ok() -> None:
    _breaker_save({"fail": 0, "until": 0})

def breaker_bad() -> None:
    st = _breaker_state()
    st["fail"] = st.get("fail", 0) + 1
    if st["fail"] >= BREAKER_THRESHOLD:
        st["until"] = int(time.time()) + BREAKER_COOLDOWN
    _breaker_save(st)

# ────────────────────────────── parsing helpers ─────────────────────────────

IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']',
    re.I
)

def extract_iframe_src(html: str) -> Optional[str]:
    m = IFRAME_SRC_RE.search(html or "")
    return m.group(1) if m else None

def _numbers_from_js_array(s: str) -> List[float]:
    """Выделяем числа из текстового представления массива: [1, 2, null, 3.4, ...]."""
    nums: List[float] = []
    try:
        # извлекаем содержимое первых нескольких [] блоков
        for block in re.findall(r'\[([^\]]+)\]', s, flags=re.S)[:8]:
            for tok in re.split(r'[\s,]+', block):
                t = tok.strip()
                if not t or t.lower() in ("null", "nan"):
                    continue
                try:
                    nums.append(float(t))
                except Exception:
                    pass
    except Exception:
        pass
    return nums

def _gci_extract_from_html(html: str | bytes) -> Optional[float]:
    """
    Достаём последнюю power/amp по станциям GCI00[1-6] из <script>:
    - ищем блоки, где рядом встречаются имена станций и массивы с «power|values|data|amp».
    - берём последнюю валидную точку, из всех — максимум (от разных станций).
    """
    try:
        text = html.decode("utf-8", "ignore") if isinstance(html, (bytes, bytearray)) else html
    except Exception:
        text = str(html)

    # собираем потенциальные скрипты
    scripts: List[str] = []
    if BeautifulSoup:
        try:
            soup = BeautifulSoup(text, "lxml")
            scripts = [s.get_text("\n", strip=False) for s in soup.find_all("script")]
        except Exception:
            scripts = []
    if not scripts:
        scripts = [text]

    best: Optional[float] = None
    station_re = re.compile(r'GCI00[1-6]', re.I)

    for sc in scripts:
        if not station_re.search(sc):
            continue
        for key in ("power", "values", "data", "amp"):
            for m in re.finditer(rf'{key}\s*[:=]\s*(\[[^\]]+\])', sc, flags=re.I | re.S):
                arr_txt = m.group(1)
                nums = _numbers_from_js_array(arr_txt)
                if not nums:
                    continue
                last = None
                for v in reversed(nums):
                    if isinstance(v, (int, float)):
                        last = float(v)
                        break
                if last is not None:
                    best = last if best is None else max(best, last)

    # Последний шанс: любые массивы чисел в окрестности упоминаний GCI
    if best is None:
        for sc in scripts:
            for m in re.finditer(r'(GCI00[1-6].{0,1200}\[[^\]]+\])', sc, flags=re.I | re.S):
                chunk = m.group(1)
                nums = _numbers_from_js_array(chunk)
                if not nums:
                    continue
                last = None
                for v in reversed(nums):
                    if isinstance(v, (int, float)):
                        last = float(v)
                        break
                if last is not None:
                    best = last if best is None else max(best, last)

    return best

# ────────────────────────────── sources ─────────────────────────────────────

def get_from_custom() -> Tuple[Optional[float], Optional[float], str]:
    """CUSTOM JSON endpoint — находим freq/amp в любой вложенности."""
    if not CUSTOM_URL or not requests:
        return None, None, "none"
    try:
        r = _get(CUSTOM_URL)
        if not r or r.status_code != 200:
            return None, None, "custom_fail"
        data = r.json()
    except Exception:
        return None, None, "custom_fail"

    def deep_find_number(obj: Any, *keys: str) -> Optional[float]:
        if obj is None:
            return None
        if isinstance(obj, list):
            for x in reversed(obj):
                v = deep_find_number(x, *keys)
                if isinstance(v, (int, float)):
                    return float(v)
            return None
        if isinstance(obj, dict):
            # точное совпадение ключей
            for k in keys:
                for kk, vv in obj.items():
                    if isinstance(kk, str) and kk.lower() == k.lower():
                        v = deep_find_number(vv, *keys)
                        if isinstance(v, (int, float)):
                            return float(v)
            # эвристика по станциям
            for st in GCI_STATIONS:
                for kk, vv in obj.items():
                    if isinstance(kk, str) and kk.lower() == st.lower():
                        v = deep_find_number(vv, *keys)
                        if isinstance(v, (int, float)):
                            return float(v)
            # глубже
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

    freq = deep_find_number(data, "freq", "frequency", "f")
    amp  = deep_find_number(data, "amp", "amplitude", "power", "value")
    return freq, amp, "custom"

def get_gci_power() -> Tuple[Optional[float], str]:
    if not GCI_ENABLE or not requests:
        return None, "gci_disabled"
    if not breaker_allow():
        return None, "gci_circuit_open"

    # 1) сохранённый HTML (если задан)
    if GCI_SAVED_HTML and os.path.exists(GCI_SAVED_HTML):
        try:
            html = open(GCI_SAVED_HTML, "rb").read()
            _dump("gci_saved.html", html)
            val = _gci_extract_from_html(html)
            if isinstance(val, (int, float)):
                breaker_ok()
                return float(val), "gci_saved"
        except Exception:
            pass

    # 2) живая страница → iframe
    r = _get(GCI_PAGE_URL) if GCI_PAGE_URL else None
    if r and r.status_code == 200 and r.text:
        _dump("gci_page.html", r.text)
        iframe = extract_iframe_src(r.text) or GCI_IFRAME_URL
        rr = _get(iframe) if iframe else None
        if rr and rr.status_code == 200 and rr.text:
            _dump("gci_iframe.html", rr.text)
            val = _gci_extract_from_html(rr.text)
            if isinstance(val, (int, float)):
                breaker_ok()
                return float(val), "gci_live"

    # 3) прямой iframe запасным путём
    rr = _get(GCI_IFRAME_URL) if GCI_IFRAME_URL else None
    if rr and rr.status_code == 200 and rr.text:
        _dump("gci_iframe_only.html", rr.text)
        val = _gci_extract_from_html(rr.text)
        if isinstance(val, (int, float)):
            breaker_ok()
            return float(val), "gci_iframe"

    breaker_bad()
    return None, "gci_fail"

def get_tsu_amp() -> Tuple[Optional[float], str]:
    """TSU/SOSRFF: грубая эвристика — число рядом с pT, иначе максимум чисел на странице."""
    if not TSU_ENABLE:
        return None, "tsu_disabled"

    def _parse(html: str | bytes) -> Optional[float]:
        try:
            text = html.decode("utf-8", "ignore") if isinstance(html, (bytes, bytearray)) else html
        except Exception:
            text = str(html)
        # 1) явное число рядом с pT/пТ
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:pT|пТ)', text, flags=re.I)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except Exception:
                pass
        # 2) fallback: максимум чисел на странице
        nums = []
        for x in re.findall(r'\b\d+(?:[.,]\d+)?\b', text):
            try:
                nums.append(float(x.replace(",", ".")))
            except Exception:
                pass
        return max(nums) if nums else None

    # live
    if requests and TSU_URL:
        r = _get(TSU_URL)
        if r and r.status_code == 200 and r.text:
            _dump("tsu_live.html", r.text)
            val = _parse(r.text)
            if isinstance(val, (int, float)):
                return float(val), "tsu_live"

    # snapshot
    if TSU_SNAPSHOT and os.path.exists(TSU_SNAPSHOT):
        try:
            html = open(TSU_SNAPSHOT, "rb").read()
            _dump("tsu_snapshot.html", html)
            val = _parse(html)
            if isinstance(val, (int, float)):
                return float(val), "tsu_snapshot"
        except Exception:
            pass

    return None, "tsu_fail"

# ────────────────────────────── business logic ──────────────────────────────

def _clamp_or_none(val: Any, lo: float, hi: float) -> Optional[float]:
    try:
        v = float(val)
        return v if lo <= v <= hi else None
    except Exception:
        return None

def collect_once() -> Dict[str, Any]:
    """
    Собираем freq/amp:
      1) CUSTOM JSON
      2) HeartMath / GCI (power → amp при MAP_GCI_TO_AMP=1)
      3) TSU / SOSRFF
      4) cache fallback по amp, если разрешён
    Частота — 7.83 по умолчанию (при отсутствии источника).
    """
    ts = _now_hour_ts_utc()
    freq_val: Optional[float] = None
    amp_val: Optional[float] = None
    h7_amp, h7_spike = None, None
    src = "none"

    # 0) кастомный JSON
    if CUSTOM_URL:
        f, a, src0 = get_from_custom()
        if isinstance(f, (int, float)):
            freq_val = f
        if isinstance(a, (int, float)):
            amp_val = a * AMP_SCALE
            src = src0

    # 1) HeartMath / GCI
    if amp_val is None:
        gci, srcg = get_gci_power()
        if isinstance(gci, (int, float)):
            # Если power считать амплитудой — умножаем на AMP_SCALE.
            amp_val = (gci * AMP_SCALE) if MAP_GCI_TO_AMP else gci
            src = srcg

    # 2) TSU / SOSRFF
    if amp_val is None:
        tv, srct = get_tsu_amp()
        if isinstance(tv, (int, float)):
            amp_val = tv * AMP_SCALE
            src = srct

    # частота — безопасный дефолт, если не пришла
    if freq_val is None:
        freq_val = 7.83

    # нормализация/кламп
    freq_val = _clamp_or_none(freq_val, FREQ_MIN, FREQ_MAX) or 7.83
    if amp_val is not None:
        amp_val = _clamp_or_none(amp_val, AMP_MIN, AMP_MAX)

    # cache fallback
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
        "src": src,
    }

# ────────────────────────────── interpretation ─────────────────────────────

def classify_freq_status(freq: Any) -> Tuple[str, str]:
    if not isinstance(freq, (int, float)):
        return "🟡 колебания", "yellow"
    f = float(freq)
    if FREQ_RED_MIN <= f <= FREQ_RED_MAX:
        if FREQ_GREEN_MIN <= f <= FREQ_GREEN_MAX:
            return "🟢 в норме", "green"
        return "🟡 колебания", "yellow"
    return "🔴 сильное отклонение", "red"

def trend_human(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def format_h7(h7: Any, h7s: Any) -> str:
    if isinstance(h7, (int, float)):
        return f"· H7: {h7:.1f} (⚡ всплеск)" if bool(h7s) else f"· H7: {h7:.1f} — спокойно"
    return "· H7: — нет данных"

def gentle_interpretation(code: str) -> str:
    return {
        "green": "Волны Шумана близки к норме — организм реагирует как на обычный день.",
        "yellow": "Заметны колебания — возможна лёгкая чувствительность к погоде и настроению.",
        "red": "Сильные отклонения — прислушивайтесь к самочувствию и снижайте перегрузки.",
    }.get(code, "")

def _trend_arrow(vals: List[float], delta: float = TREND_DELTA) -> str:
    if len(vals) < 2:
        return "→"
    last = vals[-1]
    avg_prev = sum(vals[:-1]) / (len(vals) - 1)
    d = last - avg_prev
    if d >= delta:
        return "↑"
    if d <= -delta:
        return "↓"
    return "→"

def get_schumann() -> Dict[str, Any]:
    """
    Возвращает сводку для UI:
      freq, amp, trend('↑/↓/→'), trend_text, status, status_code,
      h7_text, h7_amp, h7_spike, interpretation, cached.
    trend считается по частоте (freq) на окне TREND_WINDOW.
    """
    hist = _load_history(DEF_FILE)
    if not hist:
        return {
            "freq": None, "amp": None,
            "trend": "→", "trend_text": "стабильно",
            "status": "🟡 колебания", "status_code": "yellow",
            "h7_text": format_h7(None, None),
            "h7_amp": None, "h7_spike": None,
            "interpretation": gentle_interpretation("yellow"),
            "cached": True,
        }

    # окно по частоте
    freq_series = [float(r.get("freq")) for r in hist if isinstance(r.get("freq"), (int, float))]
    if freq_series:
        freq_series = freq_series[-max(TREND_WINDOW, 2):]
    trend = _trend_arrow(freq_series) if freq_series else "→"

    last = hist[-1]
    freq, amp = last.get("freq"), last.get("amp")
    status, code = classify_freq_status(freq)

    return {
        "freq": freq, "amp": amp,
        "trend": trend, "trend_text": trend_human(trend),
        "status": status, "status_code": code,
        "h7_text": format_h7(last.get("h7_amp"), last.get("h7_spike")),
        "h7_amp": last.get("h7_amp"), "h7_spike": last.get("h7_spike"),
        "interpretation": gentle_interpretation(code),
        "cached": (last.get("src") == "cache"),
    }

# ────────────────────────────── history tools ───────────────────────────────

def fix_history(path: str) -> Tuple[int, int]:
    """Нормализует историю: кламп значений, ver=2, src, h7_*; дедуп по ts."""
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
        a = rr.get("amp")
        if isinstance(a, (int, float)):
            a = abs(float(a))
            a = _clamp_or_none(a, AMP_MIN, AMP_MAX)
        rr.update(ts=ts, freq=f, amp=a, ver=2, src=rr.get("src") or "cache")
        rr.setdefault("h7_amp", None)
        rr.setdefault("h7_spike", None)
        by_ts[ts] = rr if ts not in by_ts else _better_record(by_ts[ts], rr)
    cleaned = [by_ts[k] for k in sorted(by_ts)]
    _write_history(path, cleaned)
    return old, len(cleaned)

# ────────────────────────────── CLI ─────────────────────────────────────────

def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Schumann collector / reader")
    p.add_argument("--collect", action="store_true", help="collect one hourly point into history")
    p.add_argument("--fix-history", action="store_true", help="normalize & dedupe history file")
    p.add_argument("--print", action="store_true", help="print get_schumann() JSON")
    p.add_argument("--last", action="store_true", help="print last history record JSON")
    args = p.parse_args()

    if args.collect:
        rec = collect_once()
        upsert_record(DEF_FILE, rec, DEF_MAX_LEN)
        print(f"collect: ts={rec['ts']} src={rec['src']} freq={rec['freq']} amp={rec['amp']}")
        if rec.get("src") == "cache":
            print("WARN: cache fallback — live unavailable")

    if args.fix_history:
        old, new = fix_history(DEF_FILE)
        print(f"fix-history: {old} -> {new}")

    if args.last:
        hist = _load_history(DEF_FILE)
        _print_json(hist[-1] if hist else {})

    if args.print:
        _print_json(get_schumann())

if __name__ == "__main__":
    main()
