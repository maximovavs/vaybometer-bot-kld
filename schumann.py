#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор «Шумана» для VayboMeter.

Возможности:
• Источник HeartMath GCI (страница + iframe + JSON внутри):
  - перебор станций по списку SCHU_GCI_STATIONS ("GCI001,GCI003,...")
  - либо одна станция SCHU_GCI_STATION (по умолчанию GCI003 — Lithuania)
  - можно указать сохраненную HTML-страницу: SCHU_HEARTMATH_HTML=path/to/file.html
• Кастомный источник частоты/амплитуды (SCHU_CUSTOM_URL) — если есть свой JSON endpoint.
• H7-детектор (если есть спектр): H7_URL + пороги.
• Кеш-безопасный режим: если live-источники недоступны, не падаем — пишем запись со src="cache".
• Запись в schumann_hourly.json как массив записей (ver=2).

ENV (см. collect_schumann.yml):
  SCHU_FILE                 — путь к JSON файлу для записи (по умолчанию schumann_hourly.json)
  SCHU_MAX_LEN              — максимум записей (число)
  SCHU_ALLOW_CACHE_ON_FAIL  — 1/0 — писать запись из кэша при провале источников
  SCHU_CUSTOM_URL           — кастомный JSON endpoint с полями freq/amp (может быть вложено)
  SCHU_AMP_SCALE            — множитель для амплитуды (float)
  SCHU_TREND_WINDOW         — часовое окно для расчета тренда (не используется в этом файле, но оставлено для совместимости)
  SCHU_TREND_DELTA          — порог (не используется здесь; тренд рисуется в post_common)

HeartMath / GCI:
  SCHU_GCI_ENABLE           — 1/0 — включить GCI-источник (по умолчанию 1)
  SCHU_GCI_URL              — страница живых данных (по умолчанию официальная страница)
  SCHU_GCI_IFRAME           — прямой URL к странице с power_levels.html (если известен)
  SCHU_GCI_STATIONS         — список станций через запятую: "GCI001,GCI003,GCI006,GCI004,GCI005"
  SCHU_GCI_STATION          — одиночная станция (если SCHU_GCI_STATIONS не задан)
  SCHU_HEARTMATH_HTML       — путь к сохранённой HTML‑странице (fallback/офлайн)
  SCHU_MAP_GCI_POWER_TO_AMP — 1/0 — маппить «power» в «amp»

H7 (опционально):
  H7_URL, H7_TARGET_HZ, H7_WINDOW_H, H7_Z, H7_MIN_ABS
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

# Перебор станций: сначала SCHU_GCI_STATIONS (список), если пусто — SCHU_GCI_STATION (одна).
GCI_STATIONS_ENV = os.getenv("SCHU_GCI_STATIONS", "").strip()
if GCI_STATIONS_ENV:
    GCI_STATION_KEYS = [s.strip().upper() for s in GCI_STATIONS_ENV.split(",") if s.strip()]
else:
    GCI_STATION_KEYS = [os.getenv("SCHU_GCI_STATION", "GCI003").strip().upper()]

HEARTMATH_HTML = os.getenv("SCHU_HEARTMATH_HTML", "").strip()  # путь к сохранённой странице

# H7
H7_URL = os.getenv("H7_URL", "").strip()
H7_TARGET = float(os.getenv("H7_TARGET_HZ", "54.81"))
H7_WINDOW_H = int(os.getenv("H7_WINDOW_H", "48"))
H7_Z = float(os.getenv("H7_Z", "2.5"))
H7_MIN_ABS = float(os.getenv("H7_MIN_ABS", "0.2"))

MAP_POWER_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "1") == "1"

# ───────────────────────── Регексы (iframe и JSON) ─────────────────────────
# Ищем <iframe ... src="...power_levels.html"...>
IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*power_levels[^"\']*)["\']',
    re.IGNORECASE
)

# Ищем JSON внутри iframe: либо <script>var data=...</script>, либо чистый JSON
# Вариант 1: var data = [...]; или const data=...
DATA_ARRAY_RE = re.compile(
    r'(?:var|let|const)\s+data\s*=\s*(\[[\s\S]*?\])\s*;',
    re.IGNORECASE
)
# Вариант 2: "data":[ ... ] внутри объекта
DATA_FIELD_RE = re.compile(
    r'"data"\s*:\s*(\[[\s\S]*?\])',
    re.IGNORECASE
)
# Вариант 3: сам по себе JSON‑массив в корне iframe
ROOT_ARRAY_RE = re.compile(
    r'^\s*(\[[\s\S]*\])\s*$'
)

# Внутри "data" ищем объекты вида {"name":"GCI003", "y": <power>}
SERIES_OBJ_RE = re.compile(
    r'\{[^{}]*"name"\s*:\s*"(?P<name>GCI00[1-6])"[^{}]*"y"\s*:\s*(?P<val>-?\d+(?:\.\d+)?)',
    re.IGNORECASE
)

# ───────────────────────── Утилиты ─────────────────────────

def _now_hour_ts() -> int:
    """Текущий таймстамп, округлённый до часа вниз (как сутки/UTC-час)."""
    return int(time.time() // 3600 * 3600)

def _http_get(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "VayboMeter/1.0"})
        r.raise_for_status()
        return r.text
    except Exception:
        return None

def _http_get_json(url: str, timeout: int = 20) -> Optional[Any]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "VayboMeter/1.0"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None

def _extract_any_number(d: Any, keys=("amp","amplitude","power","value","val")) -> Optional[float]:
    """Пытается найти число (амплитуду/мощность) в словаре/вложенности."""
    if isinstance(d, dict):
        for k in keys:
            if k in d:
                val = _safe_float(d[k])
                if val is not None:
                    return val
        # рекурсивный поиск
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

def _append_record(path: Union[str, Path], rec: Dict[str, Any], max_len: int = MAX_LEN) -> None:
    arr = _read_json_array(path)
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

# ───────────────────────── Парсинг HeartMath (iframe/JSON) ─────────────────────────

def extract_iframe_src(html: str) -> Optional[str]:
    """Из основной страницы достаём src iframe с power_levels."""
    m = IFRAME_SRC_RE.search(html or "")
    if not m:
        return None
    src = m.group(1)
    # дополним до абсолютного URL, если нужно
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        # базовый домен берём из GCI_PAGE_URL
        try:
            from urllib.parse import urlparse, urljoin
            base = urlparse(GCI_PAGE_URL)
            return urljoin(f"{base.scheme}://{base.netloc}", src)
        except Exception:
            return None
    return src

def _extract_data_array_from_text(txt: str) -> Optional[str]:
    """Достаёт строку JSON-массива с данными из текста iframe."""
    # Вариант 1: var/let/const data = [ ... ];
    m = DATA_ARRAY_RE.search(txt or "")
    if m:
        return m.group(1)
    # Вариант 2: "data":[ ... ] внутри объекта
    m = DATA_FIELD_RE.search(txt or "")
    if m:
        return m.group(1)
    # Вариант 3: корневой массив
    m = ROOT_ARRAY_RE.search(txt or "")
    if m:
        return m.group(1)
    return None

def _parse_series_array(json_text: str) -> List[Dict[str, Any]]:
    """Парсит JSON-массив объектов. Возвращает список словарей."""
    try:
        data = json.loads(json_text)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        # Попробуем выдернуть через регекс пары "name" / "y"
        out: List[Dict[str, Any]] = []
        for m in SERIES_OBJ_RE.finditer(json_text or ""):
            name = m.group("name")
            val = _safe_float(m.group("val"))
            out.append({"name": name, "y": val})
        return out

def _pick_series_value(series: List[Dict[str, Any]], station: str) -> Optional[float]:
    """Ищем по 'name' == station, берём 'y'."""
    for s in series:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if isinstance(name, str) and name.upper() == station.upper():
            return _safe_float(s.get("y"))
    return None

def get_gci_power(station_key: str) -> Optional[Tuple[float, str]]:
    """
    Возвращает (power, 'gci') для одной станции.
    Источник: сначала SCHU_HEARTMATH_HTML (если задан), иначе live-страница -> iframe -> JSON.
    """
    # 1) Сохранённый HTML?
    html = None
    if HEARTMATH_HTML:
        try:
            html = Path(HEARTMATH_HTML).read_text(encoding="utf-8")
        except Exception:
            html = None

    # 2) Live-страница
    if html is None:
        html = _http_get(GCI_PAGE_URL)

    if not html:
        return None

    iframe_url = extract_iframe_src(html)
    if not iframe_url:
        # если iframe явно задан через ENV — используем его
        iframe_url = GCI_IFRAME_URL or None
        if not iframe_url:
            return None

    iframe_text = _http_get(iframe_url)
    if not iframe_text:
        # иногда iframe содержит прямой JSON — пробуем как JSON
        data = _http_get_json(iframe_url)
        if isinstance(data, list):
            series = data
            val = _pick_series_value(series, station_key)
            if val is not None:
                return float(val), "gci"
        return None

    # В iframe — ищем JSON с данными
    arr_txt = _extract_data_array_from_text(iframe_text)
    if not arr_txt:
        return None

    series = _parse_series_array(arr_txt)
    val = _pick_series_value(series, station_key)
    if val is None:
        return None

    return float(val), "gci"

# ───────────────────────── Кастомный URL (freq/amp) ─────────────────────────

def get_custom() -> Optional[Tuple[Optional[float], Optional[float], str]]:
    """
    Любой JSON, из которого можно вытащить freq и amp (или power).
    Возвращает (freq, amp, 'custom').
    """
    if not CUSTOM_URL:
        return None
    data = _http_get_json(CUSTOM_URL)
    if data is None:
        return None

    # freq: ищем по ключам
    freq = None
    if isinstance(data, dict):
        # сначала точные ключи
        for k in ("freq", "frequency", "f"):
            if k in data:
                freq = _safe_float(data[k])
                break
        if freq is None:
            # рекурсивный поиск
            freq = _extract_any_number(data, keys=("freq", "frequency", "f"))
    elif isinstance(data, list):
        # берем первое, где найдётся
        for item in data:
            if isinstance(item, dict):
                freq = _extract_any_number(item, keys=("freq", "frequency", "f"))
                if freq is not None:
                    break

    # amp/power
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

# ───────────────────────── H7 (опционально) ─────────────────────────

def get_h7_features() -> Tuple[Optional[float], Optional[bool]]:
    """
    Если H7_URL задан, пытается достать спектр и оценить амплитуду в окрестности H7_TARGET.
    Возвращает (h7_amp, h7_spike?). При ошибке — (None, None).
    Формат спектра не стандартизован — пытаемся искать array из пар [freq, amp] или dict.
    """
    if not H7_URL:
        return None, None
    data = _http_get_json(H7_URL)
    if data is None:
        return None, None

    # Соберём точки (f, a)
    points: List[Tuple[float, float]] = []

    def push_point(f: Any, a: Any):
        f1 = _safe_float(f)
        a1 = _safe_float(a)
        if f1 is not None and a1 is not None:
            points.append((f1, a1))

    def harvest(obj: Any):
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, list) and len(it) >= 2:
                    push_point(it[0], it[1])
                elif isinstance(it, dict):
                    # ищем пары внутри словаря
                    f = it.get("freq") or it.get("f") or it.get("hz")
                    a = it.get("amp") or it.get("amplitude") or it.get("pwr") or it.get("power") or it.get("a")
                    if f is not None and a is not None:
                        push_point(f, a)
                    else:
                        harvest(it.values())
        elif isinstance(obj, dict):
            for v in obj.values():
                harvest(v)

    harvest(data)

    if not points:
        return None, None

    # Ближайшая точка к H7_TARGET
    best = None
    for f, a in points:
        if best is None or abs(f - H7_TARGET) < abs(best[0] - H7_TARGET):
            best = (f, a)

    if best is None:
        return None, None

    h7_amp = best[1]
    # Наивная "вспышка": пороги по абсолютной амплитуде
    spike = None
    if h7_amp is not None:
        spike = bool(h7_amp >= max(H7_MIN_ABS, H7_Z))
    return float(h7_amp), spike

# ───────────────────────── Основной сбор ─────────────────────────

def collect() -> int:
    """
    Строит запись и дописывает в JSON.
    Приоритет источников:
      1) CUSTOM_URL (если задан)
      2) GCI (перебор станций из SCHU_GCI_STATIONS или одиночной)
      3) Кэш (если ALLOW_CACHE)
    """
    ts = _now_hour_ts()
    freq = 7.83  # базовая/константа (резонанс)
    amp: Optional[float] = None
    src = "cache"
    station_used: Optional[str] = None

    # 1) Кастомный endpoint
    if CUSTOM_URL:
        r = get_custom()
        if r:
            f, a, s = r
            if isinstance(f, (int, float)):
                freq = float(f)
            if isinstance(a, (int, float)):
                amp = float(a)
            src = s

    # 2) HeartMath GCI
    if src == "cache" and GCI_ENABLE:
        for station in GCI_STATION_KEYS:
            gci = get_gci_power(station)
            if gci:
                power, _ = gci
                station_used = station
                # хотим маппить power -> amp (если включено)
                if MAP_POWER_TO_AMP:
                    amp = power * AMP_SCALE if power is not None else None
                src = "gci"
                break

    # 3) Фоллбэк: подтянуть последнюю amp из файла, если нужно
    if amp is None and ALLOW_CACHE:
        last_amp = _last_amp_from_file(DEF_FILE)
        if last_amp is not None:
            amp = last_amp

    # 4) H7 (опционально)
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

    _append_record(DEF_FILE, rec, MAX_LEN)

    print(f"collect: ok ts={ts} src={src} freq={freq} amp={amp} h7={h7_amp} spike={h7_spike} -> {Path(DEF_FILE).resolve()}")
    # для CI логов:
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