#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных для «Шумана» (v2.3)

Возможности:
• Сбор ежечасной точки с безопасным кэш-фоллбэком (минимум null).
• Источники:
  - CUSTOM JSON (SCHU_CUSTOM_URL) — любой JSON, где можно найти freq/amp.
  - TSU / SOSRFF (SCHU_TSU_URL) — тянем f0 (частоту фундаментала).
  - HeartMath GCI (страница/iframe/локальный HTML) — «power» → amp.
• Запись в историю (SCHU_FILE, по умолчанию schumann_hourly.json).
• Forward-fill амплитуды при src=='cache'.
• H7 (зарезервировано): h7_amp/h7_spike.
• get_schumann(): freq/amp/trend/status/h7/interpretation/cached.

CLI:
  --collect        Собрать одну точку и апсертом записать в историю
  --fix-history    Нормализовать/дедуплицировать историю
  --show           Показать «человеческую» сводку последней точки
  --json           Вывести последний объект в формате JSON
"""

from __future__ import annotations

import os
import re
import json
import time
import calendar
from typing import Any, Dict, List, Optional, Tuple

# ───────────── опциональные зависимости ─────────────
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    requests = None  # воркфлоу проглотит — будет фоллбэк в cache

try:
    from bs4 import BeautifulSoup  # для TSU
except Exception:
    BeautifulSoup = None

# ───────────── конфиг через ENV (с дефолтами) ─────────────

DEF_FILE    = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE    = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA  = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

# диапазоны допустимых значений (защита от мусора)
FREQ_MIN = float(os.getenv("SCHU_FREQ_MIN", "0"))
FREQ_MAX = float(os.getenv("SCHU_FREQ_MAX", "100"))
AMP_MIN  = float(os.getenv("SCHU_AMP_MIN",  "0"))
AMP_MAX  = float(os.getenv("SCHU_AMP_MAX",  "1000000"))

# пороги статуса частоты
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
MAP_GCI_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "1") == "1"  # по умолчанию включено

# TSU / SOSRFF
TSU_ENABLE   = os.getenv("SCHU_TSU_ENABLE", "0") == "1"
TSU_URL      = os.getenv("SCHU_TSU_URL", "").strip()
TSU_SNAPSHOT = os.getenv("SCHU_TSU_SNAPSHOT", "").strip()  # опционально сохраняем HTML

# кастомный JSON
CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

# H7 placeholders
H7_URL       = os.getenv("H7_URL", "").strip()
H7_TARGET_HZ = float(os.getenv("H7_TARGET_HZ", "54.81"))

DEBUG      = os.getenv("SCHU_DEBUG", "0") == "1"
USER_AGENT = os.getenv("SCHU_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64)")

# Circuit breaker (чтобы не долбить HeartMath когда он «лежит»)
BREAKER_FILE      = ".schu_breaker.json"
BREAKER_THRESHOLD = int(os.getenv("SCHU_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN  = int(os.getenv("SCHU_BREAKER_COOLDOWN",  "1800"))  # сек

# ───────────── регэкспы ─────────────

IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']', re.I
)

# ВАЖНО: здесь раньше был битый символ. Сейчас — корректный шаблон.
JSON_IN_IFRAME_RE = re.compile(
    r'(?:postMessage\s*\(\s*(\{.*?\})\s*,|\bvar\s+\w+\s*=\s*(\{.*?\}|\[.*?\]))',
    re.I | re.S
)

# ───────────── утилиты времени/файла ─────────────

def _now_hour_ts_utc() -> int:
    """UTC-таймстамп, округлённый до часа."""
    t = time.gmtime()
    return int(calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, 0, 0)))

def _safe_read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

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

# ───────────── выбор «лучшей» записи по источнику ─────────────

def _src_rank(src: str) -> int:
    # live > прочие «полуживые» > cache
    return {
        "live": 3,
        "tsu_live": 3,
        "custom": 2,
        "gci_live": 2,
        "gci_saved": 2,
        "gci_iframe": 2,
        "cache": 1
    }.get(str(src), 0)

def _better_record(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    ra, rb = _src_rank(a.get("src")), _src_rank(b.get("src"))
    if ra != rb:
        return a if ra > rb else b
    # при равной «силе» источника предпочитаем ту, где есть amp
    a_has = isinstance(a.get("amp"), (int, float))
    b_has = isinstance(b.get("amp"), (int, float))
    if a_has and not b_has:
        return a
    if b_has and not a_has:
        return b
    return b  # новая запись выигрывает

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

# ───────────── HTTP с ретраями ─────────────

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

# ───────────── circuit breaker ─────────────

def _breaker_state() -> Dict[str, Any]:
    try:
        return json.load(open(BREAKER_FILE, "r", encoding="utf-8"))
    except Exception:
        return {"fail": 0, "until": 0}

def _breaker_save(st: Dict[str, Any]) -> None:
    json.dump(st, open(BREAKER_FILE, "w", encoding="utf-8"), ensure_ascii=False)

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

# ───────────── парсинг HeartMath (iframe) ─────────────

def extract_iframe_src(html: str | None) -> Optional[str]:
    if not html:
        return None
    m = IFRAME_SRC_RE.search(html)
    return m.group(1) if m else None

def extract_json_from_iframe(html: str | None) -> Optional[Any]:
    if not html:
        return None
    for m in JSON_IN_IFRAME_RE.finditer(html):
        block = m.group(1) or m.group(2)
        if not block:
            continue
        # иногда «липкий» хвост ломает json — попробуем усечением
        for l in range(len(block), max(len(block) - 2000, 0), -1):
            try:
                return json.loads(block[:l])
            except Exception:
                continue
    return None

def deep_find_number(obj: Any, *keys: str) -> Optional[float]:
    """Глубокий поиск числа по наборам ключей + поддержка массивов/строк."""
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
        # ключи-станции HeartMath
        for st in GCI_STATIONS:
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == st.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)
        # брутфорс по всем значениям
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

# ───────────── источники ─────────────

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
    return (
        deep_find_number(data, "freq"),
        deep_find_number(data, "amp", "amplitude", "power"),
        "custom"
    )

def get_gci_power() -> Tuple[Optional[float], str]:
    """Серии попыток: сохранённый HTML → страница → iframe напрямую."""
    if not GCI_ENABLE or not requests:
        return None, "gci_disabled"
    if not breaker_allow():
        return None, "gci_circuit_open"

    # 1) локальный HTML
    if GCI_SAVED_HTML:
        html = _safe_read_text(GCI_SAVED_HTML)
        if html:
            iframe = extract_iframe_src(html) or GCI_IFRAME_URL
            rr = _get(iframe) if iframe else None
            iframe_html = rr.text if (rr and rr.status_code == 200) else html
            data = extract_json_from_iframe(iframe_html)
            p = deep_find_number(data, "power", "value", "amp")
            if isinstance(p, (int, float)):
                breaker_ok()
                return float(p), "gci_saved"

    # 2) основная страница
    if GCI_PAGE_URL:
        r = _get(GCI_PAGE_URL)
        if r and r.status_code == 200:
            iframe = extract_iframe_src(r.text) or GCI_IFRAME_URL
            rr = _get(iframe)
            if rr and rr.status_code == 200:
                data = extract_json_from_iframe(rr.text)
                p = deep_find_number(data, "power", "value", "amp")
                if isinstance(p, (int, float)):
                    breaker_ok()
                    return float(p), "gci_live"

    # 3) прямой iframe
    rr = _get(GCI_IFRAME_URL)
    if rr and rr.status_code == 200:
        data = extract_json_from_iframe(rr.text)
        p = deep_find_number(data, "power", "value", "amp")
        if isinstance(p, (int, float)):
            breaker_ok()
            return float(p), "gci_iframe"

    breaker_bad()
    return None, "gci_fail"

def get_tsu() -> Tuple[Optional[float], Optional[float], str]:
    """
    TSU / SOSRFF: пробуем достать f0 (частоту фундаментала).
    Амплитуды на сайте обычно нет -> возвращаем только freq.
    """
    if not (TSU_ENABLE and TSU_URL and requests):
        return None, None, "tsu_disabled"

    r = _get(TSU_URL)
    if not r or r.status_code != 200:
        breaker_bad()
        return None, None, "tsu_fail"

    html = r.text or ""
    # по желанию — сохраним снапшот для дебага
    try:
        if TSU_SNAPSHOT:
            os.makedirs(os.path.dirname(TSU_SNAPSHOT), exist_ok=True)
            with open(TSU_SNAPSHOT, "w", encoding="utf-8") as f:
                f.write(html)
    except Exception:
        pass

    text = html
    if BeautifulSoup is not None:
        try:
            text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        except Exception:
            pass

    # Варианты на странице: "f0 = 7.83", "f0: 7,8", "f0 7.8"
    freq = None
    m = re.search(r'\bf0\b[^0-9\-+]*([0-9]+(?:[.,][0-9]+)?)', text, re.I)
    if m:
        try:
            freq = float(m.group(1).replace(",", "."))
        except Exception:
            freq = None

    # Подстраховка — прямой поиск ~7.8
    if freq is None:
        m = re.search(r'\b7[.,]8\d?\b', text)
        if m:
            try:
                freq = float(m.group(0).replace(",", "."))
            except Exception:
                freq = None

    if freq is not None:
        breaker_ok()
        return freq, None, "tsu_live"

    breaker_bad()
    return None, None, "tsu_fail"

# ───────────── бизнес-логика сбора ─────────────

def _clamp_or_none(val, lo, hi):
    try:
        v = float(val)
        return v if lo <= v <= hi else None
    except Exception:
        return None

def collect_once() -> Dict[str, Any]:
    ts = _now_hour_ts_utc()
    freq_val: Optional[float] = None
    amp_val: Optional[float] = None
    h7_amp: Optional[float] = None
    h7_spike: Optional[bool] = None
    src = "none"

    # 1) кастомный JSON
    if CUSTOM_URL:
        f, a, src_c = get_from_custom()
        if f is not None:
            freq_val = f
            src = src_c
        if a is not None:
            amp_val = a * AMP_SCALE
            src = src_c

    # 2) TSU — частота
    if freq_val is None:
        f_tsu, a_tsu, src_t = get_tsu()
        if f_tsu is not None:
            freq_val = f_tsu
            src = src_t
        if a_tsu is not None:
            amp_val = a_tsu * AMP_SCALE
            src = src_t

    # 3) HeartMath — амплитуда
    if amp_val is None and GCI_ENABLE:
        gci, srcg = get_gci_power()
        if isinstance(gci, (int, float)) and MAP_GCI_TO_AMP:
            amp_val = gci * AMP_SCALE
            src = srcg

    # 4) нормализация + фоллбэк
    if freq_val is None:
        freq_val = 7.83  # безопасный дефолт
    freq_val = _clamp_or_none(freq_val, FREQ_MIN, FREQ_MAX) or 7.83

    if amp_val is not None:
        amp_val = _clamp_or_none(amp_val, AMP_MIN, AMP_MAX)

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

# ───────────── статус/интерпретации ─────────────

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

def format_h7(h7_amp: Optional[float], h7_spike: Optional[bool]) -> str:
    if isinstance(h7_amp, (int, float)):
        return f"· H7: {h7_amp:.1f} (⚡ всплеск)" if h7_spike else f"· H7: {h7_amp:.1f} — спокойно"
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

# ───────────── публичное API для поста ─────────────

def get_schumann() -> Dict[str, Any]:
    """
    Читает историю и отдаёт унифицированный словарь:
      freq/amp/trend/trend_text/status/status_code/h7_text/interpretation/cached/h7_amp/h7_spike
    """
    hist = _load_history(DEF_FILE)
    if not hist:
        return {
            "freq": None, "amp": None, "trend": "→", "trend_text": "стабильно",
            "status": "🟡 колебания", "status_code": "yellow",
            "h7_text": format_h7(None, None),
            "interpretation": gentle_interpretation("yellow"),
            "cached": True, "h7_amp": None, "h7_spike": None
        }

    # тренд по частоте
    freq_series = [float(r.get("freq")) for r in hist if isinstance(r.get("freq"), (int, float))]
    if not freq_series:
        trend = "→"
    else:
        freq_series = freq_series[-max(TREND_WINDOW, 2):]
        trend = _trend_arrow(freq_series)

    last = hist[-1]
    freq = last.get("freq")
    amp = last.get("amp")
    status, status_code = classify_freq_status(freq)

    return {
        "freq": freq,
        "amp": amp,
        "trend": trend,
        "trend_text": trend_human(trend),
        "status": status,
        "status_code": status_code,
        "h7_text": format_h7(last.get("h7_amp"), last.get("h7_spike")),
        "interpretation": gentle_interpretation(status_code),
        "cached": last.get("src") == "cache",
        "h7_amp": last.get("h7_amp"),
        "h7_spike": last.get("h7_spike"),
    }

# ───────────── нормализация истории ─────────────

def fix_history(path: str) -> Tuple[int, int]:
    hist = _load_history(path)
    old_n = len(hist)
    by_ts: Dict[int, Dict[str, Any]] = {}
    for r in hist:
        try:
            ts = int(float(r.get("ts")))
        except Exception:
            continue
        rr = dict(r)
        f = _clamp_or_none(rr.get("freq"), FREQ_MIN, FREQ_MAX) or 7.83
        a_val = rr.get("amp")
        aa = abs(a_val) if isinstance(a_val, (int, float)) else None
        a = _clamp_or_none(aa, AMP_MIN, AMP_MAX)
        rr.update(ts=ts, freq=f, amp=a, ver=2, src=rr.get("src") or "cache")
        rr.setdefault("h7_amp", None)
        rr.setdefault("h7_spike", None)
        by_ts[ts] = rr if ts not in by_ts else _better_record(by_ts[ts], rr)
    cleaned = [by_ts[k] for k in sorted(by_ts)]
    _write_history(path, cleaned)
    return old_n, len(cleaned)

# ───────────── CLI ─────────────

def _print_human_summary(obj: Dict[str, Any]) -> None:
    f = obj.get("freq")
    a = obj.get("amp")
    print(
        f"freq={f if isinstance(f,(int,float)) else 'n/d'}; "
        f"amp={a if isinstance(a,(int,float)) else 'n/d'}; "
        f"trend={obj.get('trend')} ({obj.get('trend_text')}); "
        f"status={obj.get('status')} cached={obj.get('cached')}"
    )

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Schumann collector / reader")
    parser.add_argument("--collect", action="store_true", help="Collect one hourly point and upsert to history")
    parser.add_argument("--fix-history", action="store_true", help="Normalize + dedupe history file")
    parser.add_argument("--show", action="store_true", help="Print last human-readable summary")
    parser.add_argument("--json", action="store_true", help="Print last object as JSON")
    args = parser.parse_args()

    if args.collect:
        rec = collect_once()
        upsert_record(DEF_FILE, rec, DEF_MAX_LEN)
        print(f"collect: ts={rec['ts']} src={rec['src']} freq={rec['freq']} amp={rec['amp']}")
        if rec.get("src") == "cache":
            print("WARN: cache fallback — live unavailable")

    if args.fix_history:
        old_n, new_n = fix_history(DEF_FILE)
        print(f"fix-history: {old_n} -> {new_n}")

    if args.show or args.json:
        obj = get_schumann()
        if args.json:
            print(json.dumps(obj, ensure_ascii=False))
        if args.show:
            _print_human_summary(obj)

if __name__ == "__main__":
    main()
