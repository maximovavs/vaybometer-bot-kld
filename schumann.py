#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных для «Шумана» (v2.2, debug-friendly)

Основное:
• Сбор точки раз в запуск (часовой грид по UTC), апсерт по ts.
• Источники:
  1) CUSTOM JSON (SCHU_CUSTOM_URL) — freq/amp из любого совместимого JSON.
  2) HeartMath GCI (страница -> iframe -> JSON), перебор станций:
     GCI001,GCI003,GCI006,GCI004,GCI005 (настраивается).
     Можно маппить GCI "power" → amp (SCHU_MAP_GCI_POWER_TO_AMP=1).
• Безопасный fallback:
  - freq → 7.83, amp → последний известный (если разрешено ALLOW_CACHE).
• Санитация:
  - amp всегда неотрицательный (abs), во избежание «-1.14».
• Подробная отладка:
  - SCHU_DEBUG=1 — печатает путь извлечения данных, урлы, состояние парсинга.
  - Явно пишет, если ушли в cache и почему.

CLI:
  --collect   собрать точку и записать в историю (апсерт, дедуп)
  --last      показать последнюю запись
  --dedupe    удалить дубли по ts и перезаписать файл

Переменные окружения (важное):
  SCHU_FILE="schumann_hourly.json"
  SCHU_MAX_LEN="5000"
  SCHU_ALLOW_CACHE_ON_FAIL="1"
  SCHU_AMP_SCALE="1"
  SCHU_TREND_WINDOW="24"
  SCHU_TREND_DELTA="0.1"
  SCHU_DEBUG="0|1"

HeartMath / GCI:
  SCHU_GCI_ENABLE="1"
  SCHU_GCI_STATIONS="GCI001,GCI003,GCI006,GCI004,GCI005"
  SCHU_GCI_URL="https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/"
  SCHU_GCI_IFRAME="https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html"
  SCHU_HEARTMATH_HTML="data/gcms_magnetometer_heartmath.html"
  SCHU_MAP_GCI_POWER_TO_AMP="1"

Custom JSON:
  SCHU_CUSTOM_URL=""   # если указан — сначала пробуем его

H7 — зарезервировано.
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except Exception:
    requests = None

# ───────────────────────── Конфиг ─────────────────────────

DEF_FILE = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

GCI_ENABLE = os.getenv("SCHU_GCI_ENABLE", "0") == "1"
GCI_STATIONS = [s.strip() for s in os.getenv("SCHU_GCI_STATIONS", "GCI003").split(",") if s.strip()]
GCI_PAGE_URL = os.getenv("SCHU_GCI_URL", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/")
GCI_IFRAME_URL = os.getenv("SCHU_GCI_IFRAME", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html")
GCI_SAVED_HTML = os.getenv("SCHU_HEARTMATH_HTML", "")
MAP_GCI_POWER_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "0") == "1"

CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

DEBUG = os.getenv("SCHU_DEBUG", "0") == "1"

# ───────────────────────── Реги/утилиты ─────────────────────────

IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']', re.I)

# Ловим крупные JSON-блоки в iframe:
#  1) window.postMessage(<JSON>, ...)
#  2) var something = <JSON>;
JSON_BLOCK_RE = re.compile(
    r'(?:postMessage\s*\(\s*(\{.*?\}|\[.*?\])\s*,)|(?:var\s+\w+\s*=\s*(\{.*?\}|\[.*?\]);)',
    re.I | re.S
)

def log(*a):
    if DEBUG:
        print("DEBUG:", *a)

def _get(url: str, **params) -> Optional[requests.Response]:
    if requests is None:
        return None
    try:
        r = requests.get(url, params=params, timeout=20)
        return r
    except Exception as e:
        log("HTTP error:", e)
        return None

def _read_file_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        log("read file error", path, e)
        return None

def _now_hour_ts_utc() -> int:
    t = time.gmtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, 0, 0, 0, 0, 0)))

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

def upsert_record(path: str, rec: Dict[str, Any], max_len: int | None = None) -> None:
    hist = _load_history(path)
    ts = rec.get("ts")
    hist = [r for r in hist if r.get("ts") != ts]
    hist.append(rec)
    hist.sort(key=lambda r: r.get("ts", 0))
    if isinstance(max_len, int) and max_len > 0 and len(hist) > max_len:
        hist = hist[-max_len:]
    _write_history(path, hist)

def last_known_amp(path: str) -> Optional[float]:
    for r in reversed(_load_history(path)):
        v = r.get("amp")
        if isinstance(v, (int, float)):
            return float(v)
    return None

# ───────────── JSON извлечение из iframe ─────────────

def extract_iframe_src(html: str) -> Optional[str]:
    m = IFRAME_SRC_RE.search(html or "")
    if m:
        return m.group(1)
    return None

def _balanced_json_slice(text: str, start_idx: int) -> Optional[str]:
    """
    Возвращает «сбалансированный» фрагмент JSON, начиная с { или [.
    Учитывает кавычки и экранирование.
    """
    if start_idx < 0 or start_idx >= len(text):
        return None
    open_char = text[start_idx]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    i = start_idx
    in_str = False
    esc = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return text[start_idx:i+1]
        i += 1
    return None

def extract_json_from_iframe(html: str) -> Optional[Any]:
    if not html:
        return None

    # Сначала — быстрые попадания по JSON_BLOCK_RE
    for m in JSON_BLOCK_RE.finditer(html):
        blob = m.group(1) or m.group(2)
        if not blob:
            continue
        # Попробуем распарсить напрямую
        try:
            return json.loads(blob)
        except Exception:
            # Если не вышло — пробуем через «сбалансированный» срез от первой { или [
            first = blob.find("{")
            if first < 0:
                first = blob.find("[")
            if first >= 0:
                sl = _balanced_json_slice(blob, first)
                if sl:
                    try:
                        return json.loads(sl)
                    except Exception:
                        pass

    # Бэкап: найдём первый крупный блок, начинающийся с { или [
    for marker in ("{", "["):
        pos = html.find(marker)
        while pos != -1:
            sl = _balanced_json_slice(html, pos)
            if sl and len(sl) > 1000:  # игнорируем мелочь
                try:
                    return json.loads(sl)
                except Exception:
                    pass
            pos = html.find(marker, pos + 1)
    return None

# ───────────── Поиск чисел в сложных структурах ─────────────

def deep_find_number(obj: Any, *keys: str) -> Optional[float]:
    """
    Пытаемся найти число по цепочке ключей/имен станций (case-insensitive).
    В списке берём последний валидный; в dict — по ключам и по названиям станций.
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
        # Прямой доступ по ключам
        for k in keys:
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == k.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)
        # Попытка по кодам станций
        for station in GCI_STATIONS:
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == station.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)
        # Глубокий проход
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

# ───────────── Источники ─────────────

def get_from_custom() -> Tuple[Optional[float], Optional[float], str, str]:
    """
    Возвращает (freq, amp, src, note)
    """
    if not CUSTOM_URL or requests is None:
        return None, None, "none", "custom: disabled"
    try:
        r = _get(CUSTOM_URL)
        if not r or r.status_code != 200:
            return None, None, "custom_fail", f"http {getattr(r,'status_code',None)}"
        data = r.json()
    except Exception as e:
        return None, None, "custom_fail", f"json error {e}"

    freq = deep_find_number(data, "freq", "frequency", "f0")
    amp  = deep_find_number(data, "amp", "amplitude", "power", "pwr")
    return freq, amp, "custom", "parsed"

def get_gci_power() -> Tuple[Optional[float], str, str]:
    """
    Возвращает (power, src, note)
    src: gci_saved|gci_live|gci_iframe|gci_fail|gci_disabled
    """
    if not GCI_ENABLE or requests is None:
        return None, "gci_disabled", "disabled or no requests"

    # 1) Сохранённая HTML-страница
    if GCI_SAVED_HTML:
        html = _read_file_text(GCI_SAVED_HTML)
        if html:
            iframe_url = extract_iframe_src(html) or GCI_IFRAME_URL
            log("saved HTML -> iframe:", iframe_url)
            iframe_html = None
            if iframe_url and iframe_url.startswith("http"):
                r = _get(iframe_url)
                iframe_html = r.text if r and r.status_code == 200 else None
            if not iframe_html:
                iframe_html = html
            data = extract_json_from_iframe(iframe_html or "")
            power = deep_find_number(data, "power", "value", "amp", "amplitude")
            if isinstance(power, (int, float)):
                return float(power), "gci_saved", "iframe json ok"

    # 2) Живая страница -> iframe
    if GCI_PAGE_URL:
        r = _get(GCI_PAGE_URL)
        if r and r.status_code == 200 and r.text:
            iframe_url = extract_iframe_src(r.text) or GCI_IFRAME_URL
            log("live PAGE -> iframe:", iframe_url)
            if iframe_url:
                rr = _get(iframe_url)
                if rr and rr.status_code == 200 and rr.text:
                    data = extract_json_from_iframe(rr.text)
                    power = deep_find_number(data, "power", "value", "amp", "amplitude")
                    if isinstance(power, (int, float)):
                        return float(power), "gci_live", "iframe json ok"

    # 3) Прямой iframe
    if GCI_IFRAME_URL:
        rr = _get(GCI_IFRAME_URL)
        if rr and rr.status_code == 200 and rr.text:
            data = extract_json_from_iframe(rr.text)
            power = deep_find_number(data, "power", "value", "amp", "amplitude")
            if isinstance(power, (int, float)):
                return float(power), "gci_iframe", "iframe json ok"

    return None, "gci_fail", "no json/power found"

# ───────────── Сбор точки ─────────────

def _sanitize_amp(v: Optional[float]) -> Optional[float]:
    if isinstance(v, (int, float)):
        vv = float(v) * AMP_SCALE
        # амплитуда по смыслу не отрицательная
        return abs(vv)
    return None

def collect_once() -> Dict[str, Any]:
    ts = _now_hour_ts_utc()
    out_path = DEF_FILE
    freq_val: Optional[float] = None
    amp_val: Optional[float] = None
    src_label = "none"
    note = ""

    log("collect ts:", ts)

    # 1) Custom JSON
    if CUSTOM_URL:
        f, a, s, n = get_from_custom()
        log("custom:", f, a, s, n)
        if f is not None:
            freq_val = float(f)
        if a is not None:
            amp_val = _sanitize_amp(a)
        src_label = s
        note = n

    # 2) HeartMath GCI → power → amp
    if amp_val is None and GCI_ENABLE:
        power, s, n = get_gci_power()
        log("gci:", power, s, n)
        if isinstance(power, (int, float)) and MAP_GCI_POWER_TO_AMP:
            amp_val = _sanitize_amp(power)
            src_label = s
            note = n + " (mapped power->amp)"

    # freq по умолчанию
    if freq_val is None:
        freq_val = 7.83

    # 3) fallback cache
    used_cache = False
    if amp_val is None and ALLOW_CACHE:
        amp_prev = last_known_amp(out_path)
        if amp_prev is not None:
            amp_val = _sanitize_amp(amp_prev)
            src_label = "cache"
            used_cache = True
            note = "used last_known_amp"
        else:
            src_label = "cache"
            used_cache = True
            note = "no previous amp; amp stays null"

    if used_cache:
        log("FALLBACK to cache:", note)

    rec = {
        "ts": ts,
        "freq": float(freq_val),
        "amp": (float(amp_val) if isinstance(amp_val, (int, float)) else None),
        "h7_amp": None,
        "h7_spike": None,
        "ver": 2,
        "src": src_label,
        "comment": note if DEBUG else None,
    }
    return rec

# ───────────── Выдача для поста ─────────────

def _trend_arrow(values: List[float], delta: float = TREND_DELTA) -> str:
    if len(values) < 2:
        return "→"
    last = values[-1]
    prev = values[:-1]
    if not prev:
        return "→"
    avg = sum(prev) / len(prev)
    d = last - avg
    if d >= delta: return "↑"
    if d <= -delta: return "↓"
    return "→"

def get_schumann() -> Dict[str, Any]:
    hist = _load_history(DEF_FILE)
    if not hist:
        return {"freq": None, "amp": None, "trend": "→", "cached": True}

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

# ───────────── CLI ─────────────

def cmd_collect() -> int:
    rec = collect_once()
    upsert_record(DEF_FILE, rec, max_len=DEF_MAX_LEN)
    print(
        f"collect: ts={rec['ts']} src={rec['src']} "
        f"freq={rec['freq']:.2f} amp={rec['amp']} -> {os.path.abspath(DEF_FILE)}"
    )
    if DEBUG and rec.get("comment"):
        print("collect-note:", rec["comment"])
    # показать последнюю запись
    last = _load_history(DEF_FILE)[-1]
    print("Last record JSON:", json.dumps(last, ensure_ascii=False))
    # предупреждение, если снова кэш
    if rec["src"] == "cache":
        print("WARN: fell back to cache (no live data). Check SCHU_CUSTOM_URL / HeartMath access.")
    return 0

def cmd_last() -> int:
    hist = _load_history(DEF_FILE)
    if not hist:
        print("no data")
        return 2
    last = hist[-1]
    print(json.dumps(last, ensure_ascii=False, indent=2))
    return 0

def cmd_dedupe() -> int:
    hist = _load_history(DEF_FILE)
    seen: Dict[int, Dict[str, Any]] = {}
    for r in hist:
        ts = r.get("ts")
        if isinstance(ts, int):
            seen[ts] = r
    cleaned = list(seen.values())
    cleaned.sort(key=lambda r: r.get("ts", 0))
    _write_history(DEF_FILE, cleaned)
    print(f"dedupe: {len(hist)} -> {len(cleaned)}")
    return 0

def main(argv: List[str]) -> int:
    if len(argv) <= 1:
        print("Usage: schumann.py --collect | --last | --dedupe")
        return 1
    cmd = argv[1]
    if cmd == "--collect": return cmd_collect()
    if cmd == "--last":    return cmd_last()
    if cmd == "--dedupe":  return cmd_dedupe()
    print("Unknown command:", cmd)
    return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))