#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных для «Шумана» (v2.1)

Возможности:
• Сбор ежечасной точки с безопасным кеш-фоллбэком (минимум null).
• Источники:
  - CUSTOM JSON (SCHU_CUSTOM_URL) — любой JSON, где удаётся найти freq/amp.
  - HeartMath GCI (страница + iframe + JSON), перебор станций GCI001..006:
      * онлайн-страница (SCHU_GCI_URL)
      * прямой iframe (SCHU_GCI_IFRAME)
      * сохранённый HTML (SCHU_HEARTMATH_HTML)
    Можно маппить GCI power → amp (SCHU_MAP_GCI_POWER_TO_AMP=1)
• Запись в файл истории (SCHU_FILE, по умолчанию schumann_hourly.json) с апсертом.
• Forward-fill амплитуды при src=='cache', чтобы не было amp:null.
• H7: поля h7_amp/h7_spike оставлены под будущее (если появится спектр).
• Функция get_schumann() для поста: вернёт freq/amp/trend/cached.
• CLI:
    --collect      собрать очередную точку (для GitHub Actions)
    --last         вывести последнюю точку
    --dedupe       очистить дубли (перезаписать историю, с приоритетом источников)
    --fix-history  привести историю к v2: нормализация + дедуп
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
import calendar
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    requests = None  # В GH Actions ставится в шаге "Install deps"

# ────────────────────────── Константы/дефолты ──────────────────────────

DEF_FILE      = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN   = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE   = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE     = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW  = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA   = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

# Диапазоны значений (санити-чеки)
FREQ_MIN = float(os.getenv("SCHU_FREQ_MIN", "0"))
FREQ_MAX = float(os.getenv("SCHU_FREQ_MAX", "100"))
AMP_MIN  = float(os.getenv("SCHU_AMP_MIN",  "0"))
AMP_MAX  = float(os.getenv("SCHU_AMP_MAX",  "1000000"))

# HeartMath / GCI
GCI_ENABLE       = os.getenv("SCHU_GCI_ENABLE", "0") == "1"
GCI_STATIONS     = [s.strip() for s in os.getenv("SCHU_GCI_STATIONS", "GCI003").split(",") if s.strip()]
GCI_PAGE_URL     = os.getenv("SCHU_GCI_URL", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/")
GCI_IFRAME_URL   = os.getenv("SCHU_GCI_IFRAME", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html")
GCI_SAVED_HTML   = os.getenv("SCHU_HEARTMATH_HTML", "")
MAP_GCI_TO_AMP   = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "0") == "1"

# Optional custom JSON endpoint
CUSTOM_URL       = os.getenv("SCHU_CUSTOM_URL", "").strip()

# H7 placeholders (на будущее)
H7_URL           = os.getenv("H7_URL", "").strip()
H7_TARGET_HZ     = float(os.getenv("H7_TARGET_HZ", "54.81"))
H7_WINDOW_H      = int(os.getenv("H7_WINDOW_H", "48"))
H7_Z             = float(os.getenv("H7_Z", "2.5"))
H7_MIN_ABS       = float(os.getenv("H7_MIN_ABS", "0.2"))

DEBUG            = os.getenv("SCHU_DEBUG", "0") == "1"
USER_AGENT       = os.getenv(
    "SCHU_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Circuit Breaker (персистентный между раннами)
BREAKER_FILE      = ".schu_breaker.json"
BREAKER_THRESHOLD = int(os.getenv("SCHU_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN  = int(os.getenv("SCHU_BREAKER_COOLDOWN",  "1800"))  # сек, 30 мин

# ────────────────────────── Регэкспы для HeartMath ─────────────────────

IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']',
    re.IGNORECASE
)

JSON_IN_IFRAME_RE = re.compile(
    r'(?:postMessage\s*\(\s*(\{.*?\})\s*,|\bvar\s+\w+\s*=\s*(\{.*?\}|\[.*?\]))',
    re.IGNORECASE | re.DOTALL
)

# ────────────────────────── Утилиты I/O, JSON ──────────────────────────

def _now_hour_ts_utc() -> int:
    """метка времени на начало текущего часа (UTC)"""
    t = time.gmtime()
    # calendar.timegm корректно интерпретирует как UTC (в отличие от mktime)
    return int(calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, 0, 0)))

def _load_history(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def _write_history(path: str, items: List[Dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, path)

# ────────────────────────── Приоритет источников и апсерт ──────────────

def _src_rank(src: str) -> int:
    # больше — «важнее»
    return {
        "live": 3,
        "custom": 2, "gci_live": 2, "gci_saved": 2, "gci_iframe": 2, "custom_fail": 0,
        "cache": 1,
        "none": 0, "gci_disabled": 0, "gci_circuit_open": 0, "gci_fail": 0
    }.get(str(src), 0)

def _better_record(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Выбираем «лучший» из двух записей одного ts:
      • по рангу источника,
      • при равном ранге — запись с валидным amp,
      • иначе — более свежая (b).
    """
    ra, rb = _src_rank(a.get("src")), _src_rank(b.get("src"))
    if ra != rb:
        return a if ra > rb else b
    a_amp = a.get("amp"); b_amp = b.get("amp")
    if isinstance(a_amp, (int, float)) and not isinstance(b_amp, (int, float)):
        return a
    if isinstance(b_amp, (int, float)) and not isinstance(a_amp, (int, float)):
        return b
    return b

def upsert_record(path: str, rec: Dict[str, Any], max_len: int | None = None) -> None:
    """Вставляет/обновляет запись по ключу ts без дублей и без перезаписи live кэшем."""
    hist = _load_history(path)
    try:
        ts = int(rec.get("ts"))
    except Exception:
        return
    merged: Dict[int, Dict[str, Any]] = {}
    for r in hist:
        try:
            t = int(r.get("ts"))
        except Exception:
            continue
        merged[t] = r if t not in merged else _better_record(merged[t], r)
    merged[ts] = rec if ts not in merged else _better_record(merged[ts], rec)
    out = [merged[t] for t in sorted(merged.keys())]
    if isinstance(max_len, int) and max_len > 0 and len(out) > max_len:
        out = out[-max_len:]
    _write_history(path, out)

def last_known_amp(path: str) -> Optional[float]:
    hist = _load_history(path)
    for r in reversed(hist):
        v = r.get("amp")
        if isinstance(v, (int, float)):
            return float(v)
    return None

# ────────────────────────── HTTP: Session с ретраями ───────────────────

_SESSION: Optional[requests.Session] = None

def _session() -> Optional[requests.Session]:
    if requests is None:
        return None
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    s = requests.Session()
    # Ретраи только на сетевые/5xx, не на 404 и т.п.
    try:
        retries = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.6,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET"])
        )
        s.mount("https://", HTTPAdapter(max_retries=retries))
        s.mount("http://",  HTTPAdapter(max_retries=retries))
    except Exception:
        pass
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    _SESSION = s
    return s

def _get(url: str, **params) -> Optional[requests.Response]:
    s = _session()
    if s is None:
        return None
    try:
        return s.get(url, params=params, timeout=15, allow_redirects=True)
    except Exception:
        return None

# ────────────────────────── Circuit Breaker ─────────────────────────────

def _breaker_state():
    try:
        return json.load(open(BREAKER_FILE, "r", encoding="utf-8"))
    except Exception:
        return {"fail": 0, "until": 0}

def _breaker_save(state):
    tmp = BREAKER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, BREAKER_FILE)

def breaker_allow() -> bool:
    st = _breaker_state()
    return time.time() >= st.get("until", 0)

def breaker_ok():
    st = _breaker_state()
    if st.get("fail", 0) != 0 or st.get("until", 0) != 0:
        st["fail"] = 0
        st["until"] = 0
        _breaker_save(st)

def breaker_bad():
    st = _breaker_state()
    st["fail"] = int(st.get("fail", 0)) + 1
    if st["fail"] >= BREAKER_THRESHOLD:
        st["until"] = int(time.time()) + BREAKER_COOLDOWN
    _breaker_save(st)

# ────────────────────────── HTML/JSON helpers ──────────────────────────

def _read_file_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None

def extract_iframe_src(html: str) -> Optional[str]:
    m = IFRAME_SRC_RE.search(html or "")
    return m.group(1) if m else None

def extract_json_from_iframe(html: str) -> Optional[Any]:
    """Возвращает распарсенный JSON-объект (dict/list), если найдёт крупный JSON блок."""
    if not html:
        return None
    for m in JSON_IN_IFRAME_RE.finditer(html):
        block = m.group(1) or m.group(2)
        if not block:
            continue
        text = block.strip()
        # Пытаемся аккуратно «обрезать» хвосты до валидного JSON.
        for length in range(len(text), max(len(text) - 2000, 0), -1):
            try:
                return json.loads(text[:length])
            except Exception:
                continue
    return None

def deep_find_number(obj: Any, *keys: str) -> Optional[float]:
    """
    Пытается найти число по цепочке ключей/имен станций (case-insensitive).
    Работает «широко»: если список — берём последний числовой элемент.
    Если dict — пробуем ключи и коды станций GCI00x.
    """
    if obj is None:
        return None

    if isinstance(obj, list):
        for x in reversed(obj):
            v = deep_find_number(x, *keys)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    if isinstance(obj, dict):
        # прямые ключи
        for k in keys:
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == k.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)
        # станции
        for station in GCI_STATIONS:
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == station.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)
        # обход значений
        for vv in obj.values():
            v = deep_find_number(vv, *keys)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    if isinstance(obj, (int, float)):
        return float(obj)

    if isinstance(obj, str):
        try:
            return float(obj.replace(",", ".").strip())
        except Exception:
            return None

    return None

# ────────────────────────── Источники данных ───────────────────────────

def get_from_custom() -> Tuple[Optional[float], Optional[float], str]:
    """Пробуем взять freq/amp из SCHU_CUSTOM_URL. Возвращает (freq, amp, src)."""
    if not CUSTOM_URL or requests is None:
        return None, None, "none"
    try:
        r = _get(CUSTOM_URL)
        if not r or r.status_code != 200:
            return None, None, "custom_fail"
        data = r.json()
    except Exception:
        return None, None, "custom_fail"

    freq = deep_find_number(data, "freq", "frequency", "f0")
    amp  = deep_find_number(data, "amp", "amplitude", "power", "pwr")
    return freq, amp, "custom"

def get_gci_power() -> Tuple[Optional[float], str]:
    """
    Пытаемся достать power (hourly) для одной из станций из HeartMath.
    Приоритет:
      1) сохранённая страница (SCHU_HEARTMATH_HTML) -> iframe
      2) онлайн-страница (GCI_PAGE_URL) -> iframe
      3) прямой iframe (GCI_IFRAME_URL)
    Возвращает (power, src_label)
    """
    if not GCI_ENABLE or requests is None:
        return None, "gci_disabled"

    if not breaker_allow():
        return None, "gci_circuit_open"

    # 1) сохранённая страница
    if GCI_SAVED_HTML:
        html = _read_file_text(GCI_SAVED_HTML)
        if html:
            iframe_url = extract_iframe_src(html) or GCI_IFRAME_URL
            if DEBUG:
                print("DEBUG: saved HTML -> iframe:", iframe_url)
            iframe_html = None
            if iframe_url and iframe_url.startswith("http"):
                r = _get(iframe_url)
                iframe_html = r.text if r and r.status_code == 200 else None
            if not iframe_html:
                iframe_html = html  # fallback: искать JSON прямо в сохранённом файле
            data = extract_json_from_iframe(iframe_html or "")
            if DEBUG:
                print("DEBUG: iframe JSON present:", isinstance(data, (dict, list)))
            power = deep_find_number(data, "power", "value", "amp", "amplitude")
            if isinstance(power, (int, float)):
                breaker_ok()
                return float(power), "gci_saved"

    # 2) онлайн страница -> iframe
    if GCI_PAGE_URL:
        r = _get(GCI_PAGE_URL)
        if r and r.status_code == 200 and r.text:
            iframe_url = extract_iframe_src(r.text) or GCI_IFRAME_URL
            if DEBUG:
                print("DEBUG: live PAGE -> iframe:", iframe_url)
            if iframe_url:
                rr = _get(iframe_url)
                if rr and rr.status_code == 200 and rr.text:
                    data = extract_json_from_iframe(rr.text)
                    power = deep_find_number(data, "power", "value", "amp", "amplitude")
                    if isinstance(power, (int, float)):
                        breaker_ok()
                        return float(power), "gci_live"

    # 3) прямой iframe
    if GCI_IFRAME_URL:
        rr = _get(GCI_IFRAME_URL)
        if rr and rr.status_code == 200 and rr.text:
            data = extract_json_from_iframe(rr.text)
            power = deep_find_number(data, "power", "value", "amp", "amplitude")
            if isinstance(power, (int, float)):
                breaker_ok()
                return float(power), "gci_iframe"

    breaker_bad()
    return None, "gci_fail"

# ────────────────────────── Бизнес-логика сбора ─────────────────────────

def _clamp_or_none(val, lo, hi):
    try:
        v = float(val)
        if lo <= v <= hi:
            return v
    except Exception:
        pass
    return None

def collect_once() -> Dict[str, Any]:
    """
    Собирает одну часовую точку.
      1) CUSTOM_URL — freq/amp из твоего JSON (если есть).
      2) HeartMath GCI power (map->amp при включённом флаге).
      3) Если не получилось и ALLOW_CACHE — берём freq=7.83, amp=last_known_amp().
    """
    ts = _now_hour_ts_utc()
    out_path = DEF_FILE
    freq_val: Optional[float] = None
    amp_val: Optional[float] = None
    h7_amp: Optional[float] = None
    h7_spike: Optional[bool] = None
    src_label = "none"

    # 1) Custom JSON
    if CUSTOM_URL:
        f, a, src = get_from_custom()
        if f is not None:
            freq_val = float(f)
        if a is not None:
            amp_val = float(a) * AMP_SCALE
        src_label = src
        if DEBUG:
            print("DEBUG: custom:", f, a, src)

    # 2) HeartMath GCI (если amp ещё нет)
    if amp_val is None and GCI_ENABLE:
        gci_power, gsrc = get_gci_power()
        if DEBUG:
            print("DEBUG: gci_power:", gci_power, gsrc)
        if isinstance(gci_power, (int, float)) and MAP_GCI_TO_AMP:
            amp_val = float(gci_power) * AMP_SCALE
            src_label = gsrc

    # freq по умолчанию
    if freq_val is None:
        freq_val = 7.83

    # Санити-чеки
    freq_val = _clamp_or_none(freq_val, FREQ_MIN, FREQ_MAX) or 7.83
    if amp_val is not None:
        amp_val = _clamp_or_none(amp_val, AMP_MIN, AMP_MAX)

    # 3) Фоллбэк из кеша (forward-fill amp)
    if amp_val is None and ALLOW_CACHE:
        src_label = "cache"
        amp_prev = last_known_amp(out_path)
        if amp_prev is not None:
            amp_val = amp_prev

    rec = {
        "ts": int(ts),
        "freq": float(freq_val) if isinstance(freq_val, (int, float)) else 7.83,
        "amp": float(amp_val) if isinstance(amp_val, (int, float)) else None,
        "h7_amp": h7_amp,
        "h7_spike": h7_spike,
        "ver": 2,
        "src": src_label,
    }
    return rec

# ────────────────────────── Публичная выдача (для поста) ───────────────

def _trend_arrow(values: List[float], delta: float = TREND_DELTA) -> str:
    """Сравниваем последний со средним предыдущих: ↑ / ↓ / →."""
    if len(values) < 2:
        return "→"
    last = values[-1]
    prev = values[:-1]
    if not prev:
        return "→"
    avg = sum(prev) / len(prev)
    d = last - avg
    if d >= delta:
        return "↑"
    if d <= -delta:
        return "↓"
    return "→"

def get_schumann() -> Dict[str, Any]:
    """
    Возвращает текущие данные (последняя точка) + тренд по частоте.
    Формат:
      {"freq": float|None, "amp": float|None, "trend": "↑/↓/→", "cached": bool}
    """
    hist = _load_history(DEF_FILE)
    if not hist:
        return {"freq": None, "amp": None, "trend": "→", "cached": True}

    # Берём последние N для тренда по freq
    freq_series = [r.get("freq") for r in hist if isinstance(r.get("freq"), (int, float))]
    freq_series = freq_series[-max(TREND_WINDOW, 2):] if freq_series else []
    trend = _trend_arrow([float(x) for x in freq_series]) if freq_series else "→"

    last = hist[-1]
    cached = (last.get("src") == "cache")
    return {
        "freq": last.get("freq"),
        "amp": last.get("amp"),
        "trend": trend,
        "cached": bool(cached),
        "h7_amp": last.get("h7_amp"),
        "h7_spike": last.get("h7_spike"),
    }

# ────────────────────────── Ремонт истории ─────────────────────────────

def fix_history(path: str) -> Tuple[int, int]:
    """
    Приводит историю к формату v2:
      • дедуп по ts (с приоритетом источников);
      • отрицательные/грязные amp → None (либо abs в допустимом диапазоне);
      • freq → float в диапазоне, дефолт 7.83;
      • src/ver/h7_* присутствуют.
    Возвращает (старый размер, новый размер).
    """
    hist = _load_history(path)
    old_len = len(hist)
    if not hist:
        _write_history(path, [])
        return (0, 0)

    by_ts: Dict[int, Dict[str, Any]] = {}
    for r in hist:
        ts = r.get("ts")
        try:
            ts = int(float(ts))
        except Exception:
            continue
        rr: Dict[str, Any] = dict(r)

        # freq
        try:
            f = float(rr.get("freq"))
        except Exception:
            f = 7.83
        rr["freq"] = f if FREQ_MIN <= f <= FREQ_MAX else 7.83

        # amp
        a = rr.get("amp")
        aa = None
        if isinstance(a, (int, float)):
            a = abs(float(a))
            if AMP_MIN <= a <= AMP_MAX:
                aa = a
        rr["amp"] = aa

        # h7
        if "h7_amp" not in rr:
            rr["h7_amp"] = None
        if "h7_spike" not in rr:
            rr["h7_spike"] = None

        # служебные
        rr["ver"] = 2
        rr["src"] = rr.get("src") or "cache"
        rr.pop("comment", None)
        rr["ts"] = ts

        by_ts[ts] = rr if ts not in by_ts else _better_record(by_ts[ts], rr)

    cleaned = [by_ts[k] for k in sorted(by_ts.keys())]
    _write_history(path, cleaned)
    return (old_len, len(cleaned))

# ────────────────────────── CLI ─────────────────────────────────────────

def cmd_collect() -> int:
    rec = collect_once()
    upser