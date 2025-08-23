#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных для «Шумана» (v2)

Возможности:
• Сбор ежечасной точки с безопасным кеш-фоллбэком (не плодит null).
• Источники:
  - CUSTOM JSON (SCHU_CUSTOM_URL) — любой JSON, где удаётся найти freq/amp.
  - HeartMath GCI (страница + iframe + JSON), перебор станций GCI001..006:
      * онлайн-страница (SCHU_GCI_URL)
      * прямой iframe (SCHU_GCI_IFRAME)
      * сохранённый HTML (SCHU_HEARTMATH_HTML)
    Можно маппить GCI power → amp (SCHU_MAP_GCI_POWER_TO_AMP=1)
• Запись в файл истории (SCHU_FILE, по умолчанию schumann_hourly.json) с апсертом.
• Forward‑fill амплитуды при src=='cache', чтобы не было amp:null.
• H7: поля h7_amp/h7_spike оставлены под будущее (если появится спектр).
• Функция get_schumann() для поста: вернёт freq/amp/trend/cached.
• CLI:
    --collect    собрать очередную точку (для GitHub Actions)
    --last       вывести последнюю точку
    --dedupe     очистить дубли/перезаписать историю

Переменные окружения (основные):
  SCHU_FILE                 = "schumann_hourly.json"
  SCHU_MAX_LEN              = "5000"
  SCHU_ALLOW_CACHE_ON_FAIL  = "1"
  SCHU_AMP_SCALE            = "1"
  SCHU_TREND_WINDOW         = "24"
  SCHU_TREND_DELTA          = "0.1"

HeartMath:
  SCHU_GCI_ENABLE           = "1"
  SCHU_GCI_STATIONS         = "GCI001,GCI003,GCI006,GCI004,GCI005"
  SCHU_GCI_URL              = "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/"
  SCHU_GCI_IFRAME           = "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html"
  SCHU_HEARTMATH_HTML       = "data/gcms_magnetometer_heartmath.html"  # локально сохранённая страница
  SCHU_MAP_GCI_POWER_TO_AMP = "1"   # просто берём power как amp (или через scale)

Custom JSON:
  SCHU_CUSTOM_URL           = ""    # если указан — пытаемся достать freq/amp из этого JSON

H7 (зарезервировано):
  H7_URL, H7_TARGET_HZ, H7_WINDOW_H, H7_Z, H7_MIN_ABS
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except Exception as e:
    requests = None  # В GH Actions ставится в шаге "Install deps"

# ────────────────────────── Константы/дефолты ──────────────────────────

DEF_FILE = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

# HeartMath / GCI
GCI_ENABLE = os.getenv("SCHU_GCI_ENABLE", "0") == "1"
GCI_STATIONS = [s.strip() for s in os.getenv("SCHU_GCI_STATIONS", "GCI003").split(",") if s.strip()]
GCI_PAGE_URL = os.getenv("SCHU_GCI_URL", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/")
GCI_IFRAME_URL = os.getenv("SCHU_GCI_IFRAME", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html")
GCI_SAVED_HTML = os.getenv("SCHU_HEARTMATH_HTML", "")
MAP_GCI_POWER_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "0") == "1"

# Optional custom JSON endpoint
CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

# H7 placeholders (на будущее)
H7_URL = os.getenv("H7_URL", "").strip()
H7_TARGET_HZ = float(os.getenv("H7_TARGET_HZ", "54.81"))
H7_WINDOW_H = int(os.getenv("H7_WINDOW_H", "48"))
H7_Z = float(os.getenv("H7_Z", "2.5"))
H7_MIN_ABS = float(os.getenv("H7_MIN_ABS", "0.2"))

DEBUG = os.getenv("SCHU_DEBUG", "0") == "1"

# ────────────────────────── Регэкспы для HeartMath ─────────────────────

# iframe src из основной страницы
IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']',
    re.IGNORECASE
)

# JSON в iframe: ищем любой крупный JSON
# 1) window.postMessage(..., JSON)
# 2) var something = {...} или = [...]
JSON_IN_IFRAME_RE = re.compile(
    r'(?:postMessage\s*\(\s*(\{.*?\})\s*,|\bvar\s+\w+\s*=\s*(\{.*?\}|\[.*?\]))',
    re.IGNORECASE | re.DOTALL
)

# Иногда нужная станция подписана как "GCI003" или её человекочитаемое имя рядом
NAME_NEAR_DATA_RE = re.compile(
    r'(GCI00[1-6])|(?i)(Lithuania|Alberta|California|Saudi Arabia|New Zealand|South Africa)'
)

# ────────────────────────── Утилиты I/O, JSON, HTTP ────────────────────

def _get(url: str, **params) -> Optional[requests.Response]:
    if requests is None:
        return None
    try:
        return requests.get(url, params=params, timeout=15)
    except Exception:
        return None

def _read_file_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None

def _now_hour_ts_utc() -> int:
    # метка времени на начало текущего часа (UTC)
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
    """
    Вставляет/обновляет запись по ключу ts без дублей.
    Если уже есть запись с таким ts — заменяет её (не добавляет).
    """
    hist = _load_history(path)
    ts = rec.get("ts")
    hist = [r for r in hist if r.get("ts") != ts]
    hist.append(rec)
    hist.sort(key=lambda r: r.get("ts", 0))
    if isinstance(max_len, int) and max_len > 0 and len(hist) > max_len:
        hist = hist[-max_len:]
    _write_history(path, hist)

def last_known_amp(path: str) -> Optional[float]:
    hist = _load_history(path)
    for r in reversed(hist):
        v = r.get("amp")
        if isinstance(v, (int, float)):
            return float(v)
    return None

def extract_iframe_src(html: str) -> Optional[str]:
    m = IFRAME_SRC_RE.search(html or "")
    if m:
        return m.group(1)
    return None

def extract_json_from_iframe(html: str) -> Optional[Any]:
    """
    Возвращает распарсенный JSON‑объект (dict/list), если найдёт крупный JSON блок.
    """
    if not html:
        return None
    for m in JSON_IN_IFRAME_RE.finditer(html):
        block = m.group(1) or m.group(2)
        if not block:
            continue
        # баланс фигурных скобок — упрощёнко: пытаемся резать до согласованности
        text = block.strip()
        # Быстрый parse с обрезкой хвостов
        for length in range(len(text), max(len(text) - 2000, 0), -1):
            try:
                return json.loads(text[:length])
            except Exception:
                continue
    return None

def deep_find_number(obj: Any, *keys: str) -> Optional[float]:
    """
    Пытается найти число по цепочке ключей/имен станций (case-insensitive).
    Работает «широко»: если попали в список — берём последний числовой элемент.
    Если попали в dict — пытаемся по ключам и станциям.
    """
    if obj is None:
        return None

    # Если список — берём последний валидный float
    if isinstance(obj, list):
        for x in reversed(obj):
            v = deep_find_number(x, *keys)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    # Если словарь — пробуем ключи и имена станций
    if isinstance(obj, dict):
        # прямые ключи
        for k in keys:
            # ключи могут быть в разных регистрах
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == k.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)
        # перебор станций GCI00x — вдруг значения лежат под кодами станций
        for station in GCI_STATIONS:
            for kk, vv in obj.items():
                if isinstance(kk, str) and kk.lower() == station.lower():
                    v = deep_find_number(vv, *keys)
                    if isinstance(v, (int, float)):
                        return float(v)

        # если остальное: пройтись по значениям
        for vv in obj.values():
            v = deep_find_number(vv, *keys)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    # Прямое число
    if isinstance(obj, (int, float)):
        return float(obj)

    # Строка с числом
    if isinstance(obj, str):
        try:
            return float(obj.replace(",", ".").strip())
        except Exception:
            return None

    return None

# ────────────────────────── Источники данных ───────────────────────────

def get_from_custom() -> Tuple[Optional[float], Optional[float], str]:
    """
    Пробуем взять freq/amp из SCHU_CUSTOM_URL.
    Возвращает (freq, amp, src)
    """
    if not CUSTOM_URL or requests is None:
        return None, None, "none"
    try:
        r = _get(CUSTOM_URL)
        if not r or r.status_code != 200:
            return None, None, "custom_fail"
        data = r.json()
    except Exception:
        return None, None, "custom_fail"

    # пытаемся вытащить freq / amp из любых уровней
    freq = deep_find_number(data, "freq", "frequency", "f0")
    amp  = deep_find_number(data, "amp", "amplitude", "power", "pwr")
    return freq, amp, "custom"

def get_gci_power() -> Tuple[Optional[float], str]:
    """
    Пытаемся достать power (hourly) для одной из станций из HeartMath:
      1) если указан сохранённый HTML — читаем его (SCHU_HEARTMATH_HTML)
      2) если есть общий GCI_PAGE_URL — достаём iframe src
      3) если задан прямой iframe — открываем его
    Затем ищем JSON внутри iframe и вытаскиваем power.

    Возвращает (power, src_label)
    """
    if not GCI_ENABLE or requests is None:
        return None, "gci_disabled"

    # 1) сохранённая страница
    if GCI_SAVED_HTML:
        html = _read_file_text(GCI_SAVED_HTML)
        if html:
            iframe_url = extract_iframe_src(html) or GCI_IFRAME_URL
            if DEBUG:
                print("DEBUG: saved HTML -> iframe:", iframe_url)
            iframe_html = None
            # даже из сохранённой страницы iframe мог быть сохранён рядом — попробуем прочесть
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
                return float(power), "gci_saved"
        # если не получилось — попробуем онлайн ниже

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
                        return float(power), "gci_live"
    # 3) прямой iframe (напрямую)
    if GCI_IFRAME_URL:
        rr = _get(GCI_IFRAME_URL)
        if rr and rr.status_code == 200 and rr.text:
            data = extract_json_from_iframe(rr.text)
            power = deep_find_number(data, "power", "value", "amp", "amplitude")
            if isinstance(power, (int, float)):
                return float(power), "gci_iframe"

    return None, "gci_fail"

# ────────────────────────── Бизнес‑логика сбора ─────────────────────────

def collect_once() -> Dict[str, Any]:
    """
    Собирает одну часовую точку.
    Алгоритм:
      1) CUSTOM_URL (freq/amp из твоего JSON) — если есть.
      2) HeartMath GCI power (map->amp при включенном флаге).
      3) Если не получилось и ALLOW_CACHE — берём freq=7.83, amp = last_known_amp().
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
            print("DEBUG: custom", f, a, src)

    # 2) HeartMath GCI (если amp ещё нет)
    if amp_val is None and GCI_ENABLE:
        gci_power, gsrc = get_gci_power()
        if DEBUG:
            print("DEBUG: gci_power:", gci_power, gsrc)
        if isinstance(gci_power, (int, float)) and MAP_GCI_POWER_TO_AMP:
            amp_val = float(gci_power) * AMP_SCALE
            src_label = gsrc

    # freq по умолчанию, если ничего не дали источники
    if freq_val is None:
        freq_val = 7.83

    # 3) Фоллбэк из кеша (forward‑fill amp)
    if amp_val is None and ALLOW_CACHE:
        src_label = "cache"
        amp_prev = last_known_amp(out_path)
        if amp_prev is not None:
            amp_val = amp_prev

    rec = {
        "ts": ts,
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
    """
    Сравниваем последний со средним предыдущих: ↑ / ↓ / →.
    """
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

    # берём последние N для тренда по freq
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
        # Дополнительно отдаём h7, если появится
        "h7_amp": last.get("h7_amp"),
        "h7_spike": last.get("h7_spike"),
    }

# ────────────────────────── CLI ─────────────────────────────────────────

def cmd_collect() -> int:
    rec = collect_once()
    upsert_record(DEF_FILE, rec, max_len=DEF_MAX_LEN)
    print(
        f"collect: ok ts={rec['ts']} src={rec['src']} "
        f"freq={rec['freq']} amp={rec['amp']} h7={rec['h7_amp']} spike={rec['h7_spike']} "
        f"-> {os.path.abspath(DEF_FILE)}"
    )
    # показать последнюю запись компактно
    last = _load_history(DEF_FILE)[-1]
    print("Last record JSON:", json.dumps(last, ensure_ascii=False))
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
            seen[ts] = r  # последний побеждает
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
    if cmd == "--collect":
        return cmd_collect()
    if cmd == "--last":
        return cmd_last()
    if cmd == "--dedupe":
        return cmd_dedupe()
    print("Unknown command:", cmd)
    return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))