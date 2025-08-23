#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — единый модуль и коллектор для КЛД/Кипра.

Что умеет:
1) Публичное API для поста:
   get_schumann() -> {freq, amp, trend, high, cached, [h7_amp], [h7_spike]}
   - Берёт live с GCI (несколько зеркал).
   - Фоллбэк: локальный JSON-кэш schumann_hourly.json (поддерживает 2 формата).

2) CLI-коллектор:
   python schumann.py --collect
   - Достаёт freq/amp, опционально считает 7-ю гармонику из H7_URL,
     дописывает запись в schumann_hourly.json (формат v2):
     {"ts", "freq", "amp", "h7_amp", "h7_spike", "ver": 2}
   - Атомарная запись + обрезка файла до MAX_LEN.
   - Параметры через ENV: см. блок "Настройки ENV" ниже.

Зависимости: requests (стандартная для твоего проекта).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

__all__ = ("get_schumann",)

# ───────────────────── Настройки ENV (можно править в YAML) ──────────────────

# Куда пишем/читаем JSON с историей (по умолчанию — рядом со скриптом)
OUT_PATH = Path(os.environ.get("SCHU_FILE", "schumann_hourly.json")).resolve()

# Ограничение длины истории
MAX_LEN = int(os.environ.get("SCHU_MAX_LEN", "5000"))

# Масштаб амплитуды (если источник отдаёт нТ вместо пТ — поставить 1000)
AMP_SCALE = float(os.environ.get("SCHU_AMP_SCALE", "1"))

# Параметры H7
H7_URL = os.environ.get("H7_URL", "").strip()  # эндпоинт спектра (опц.)
H7_TARGET_HZ = float(os.environ.get("H7_TARGET_HZ", "54.81"))
H7_WINDOW = int(os.environ.get("H7_WINDOW_H", "48"))
H7_Z = float(os.environ.get("H7_Z", "2.5"))
H7_MIN_ABS = float(os.environ.get("H7_MIN_ABS", "0.2"))  # pT

# Параметры тренда по базовой частоте
TREND_WINDOW = int(os.environ.get("SCHU_TREND_WINDOW", "24"))
TREND_DELTA = float(os.environ.get("SCHU_TREND_DELTA", "0.1"))

# Источники live (GCI, можно расширять)
GCI_URLS = [
    # Примеры форматов; скрипт умно парсит оба
    "https://gci-api.ucsd.edu/data/latest",   # {"data":[{"freq":..,"amp":..,"ts":..}, ...]}
    "https://gci-api.com/sr/latest",          # {"sr1":{"freq":..,"amp":..}}
]

# ───────────────────── Вспомогательные ───────────────────────────────────────

def _atomic_write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="schu_", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass

def _trend_arrow(values: List[float]) -> str:
    if len(values) < 2:
        return "→"
    avg = sum(values[:-1]) / (len(values) - 1)
    d = values[-1] - avg
    if d >= TREND_DELTA:
        return "↑"
    if d <= -TREND_DELTA:
        return "↓"
    return "→"

def _parse_gci_payload(js: Dict[str, Any]) -> Tuple[float, float]:
    # Вариант 1: {"sr1":{"freq":7.83,"amp":112.4}}
    if isinstance(js.get("sr1"), dict):
        sr = js["sr1"]
        return float(sr["freq"]), float(sr["amp"]) * AMP_SCALE
    # Вариант 2: {"data":[ {...,"freq":..,"amp":..}, ... ]}
    data = js.get("data")
    if isinstance(data, list) and data:
        last = data[-1]
        return float(last["freq"]), float(last["amp"]) * AMP_SCALE
    raise ValueError("Unsupported GCI JSON structure")

def _fetch_live() -> Optional[Dict[str, Any]]:
    for url in GCI_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            freq, amp = _parse_gci_payload(r.json())
            return {
                "freq": round(float(freq), 2),
                "amp":  round(float(amp), 1),
                "trend": "→",  # без истории
                "high": (freq > 8.0 or amp > 100.0),
                "cached": False,
            }
        except Exception:
            continue
    return None

def _read_store() -> List[Dict[str, Any]]:
    if not OUT_PATH.exists():
        return []
    try:
        js = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        # Нормализуем к списку записей
        if isinstance(js, list):
            return js
        if isinstance(js, dict):
            # альт. формат — словарь по часам
            rows: List[Dict[str, Any]] = []
            try:
                for k, v in sorted(js.items(), key=lambda kv: kv[0]):
                    if isinstance(v, dict):
                        row = dict(v)
                        # ts возьмём как int из ключа, если это unix; иначе None
                        row.setdefault("ts", None)
                        rows.append(row)
                return rows
            except Exception:
                return []
        return []
    except Exception:
        return []

def _series_from_rows(rows: List[Dict[str, Any]]) -> Tuple[List[float], List[float], List[Optional[float]]]:
    freqs, amps, h7s = [], [], []
    for it in rows:
        f = it.get("freq"); a = it.get("amp"); h7 = it.get("h7_amp")
        if isinstance(f, (int, float)) and isinstance(a, (int, float)):
            freqs.append(float(f))
            amps.append(float(a))
            h7s.append(float(h7) if isinstance(h7, (int, float)) else None)
    return freqs, amps, h7s

def _robust_h7_spike(history: List[float], last: float) -> Optional[bool]:
    """
    Робастная детекция всплеска:
      last > median + H7_Z * MAD  И  last > H7_MIN_ABS
    """
    vals = [v for v in history if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(vals) < 12 or not isinstance(last, (int, float)):
        return None
    med = statistics.median(vals)
    mad = statistics.median([abs(x - med) for x in vals]) or 0.01
    return bool(last > med + H7_Z * mad and last > H7_MIN_ABS)

# ───────────────────── H7: получение амплитуды из спектра (опц.) ─────────────

def _fetch_h7_amp() -> Optional[float]:
    """
    Если задан H7_URL — скачиваем JSON со спектром и берём амплитуду ближайшего
    бина к H7_TARGET_HZ. Поддерживаются форматы:
      1) {"freq":[...], "amp":[...]}  или {"frequency":[...], "amplitude":[...]}
      2) [{"f":..,"a":..}, ...] или [{"freq":..,"amp":..}, ...]
    В противном случае возвращаем None.
    """
    if not H7_URL:
        return None
    try:
        r = requests.get(H7_URL, timeout=12)
        r.raise_for_status()
        obj = r.json()
    except Exception:
        return None

    # Вариант 1
    if isinstance(obj, dict):
        f = obj.get("freq") or obj.get("frequency")
        a = obj.get("amp")  or obj.get("amplitude")
        if isinstance(f, list) and isinstance(a, list) and len(f) == len(a) and f:
            try:
                idx = min(range(len(f)), key=lambda i: abs(float(f[i]) - H7_TARGET_HZ))
                val = float(a[idx])
                return val if math.isfinite(val) else None
            except Exception:
                return None

    # Вариант 2
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        try:
            idx = min(range(len(obj)), key=lambda i: abs(float(obj[i].get("f") or obj[i].get("freq")) - H7_TARGET_HZ))
            raw = obj[idx].get("a") or obj[idx].get("amp")
            val = float(raw)
            return val if math.isfinite(val) else None
        except Exception:
            return None

    return None

# ───────────────────── Публичное API для поста ───────────────────────────────

def get_schumann() -> Dict[str, Any]:
    """
    1) пробуем live с GCI;
    2) если не вышло — читаем локальный schumann_hourly.json (любой из двух форматов);
    3) если пусто — {"msg":"no data"}.
    """
    live = _fetch_live()
    if live:
        return live

    rows = _read_store()
    if not rows:
        return {"msg": "no data"}

    freqs, amps, h7s = _series_from_rows(rows)
    if not freqs:
        return {"msg": "no data"}

    # окно для тренда
    f_w = freqs[-TREND_WINDOW:] if len(freqs) >= TREND_WINDOW else freqs
    a_w = amps[-TREND_WINDOW:]  if len(amps)  >= TREND_WINDOW else amps
    trend = _trend_arrow(f_w)

    out: Dict[str, Any] = {
        "freq": round(float(f_w[-1]), 2),
        "amp":  round(float(a_w[-1]), 1) if a_w else None,
        "trend": trend,
        "high": (f_w[-1] > 8.0 or (a_w and a_w[-1] > 100.0)),
        "cached": True,
    }

    # 7-я гармоника из истории (если писали)
    if h7s:
        h7_clean = [v for v in h7s if isinstance(v, (int, float))]
        if h7_clean:
            last = h7_clean[-1]
            out["h7_amp"] = round(float(last), 3)
            hist = h7_clean[-H7_WINDOW:-1] if len(h7_clean) > 1 else []
            spike = _robust_h7_spike(hist, last)
            if spike is not None:
                out["h7_spike"] = spike

    return out

# ───────────────────── CLI-коллектор ─────────────────────────────────────────

def _collect_once() -> int:
    """
    Делает один тик сбора: freq/amp (GCI), h7_amp (опц. из H7_URL),
    дописывает schumann_hourly.json в формате v2.
    """
    now = int(time.time())

    live = _fetch_live()
    if not live:
        print("collect: no live source available", file=sys.stderr)
        return 2

    freq = live.get("freq")
    amp  = live.get("amp")

    h7_amp = _fetch_h7_amp()

    rows = _read_store()

    # если последняя запись имеет такой же ts — обновим её
    if rows and isinstance(rows[-1], dict) and rows[-1].get("ts") == now:
        rows.pop()

    rec = {
        "ts": now,
        "freq": float(freq) if isinstance(freq, (int, float)) else None,
        "amp":  float(amp)  if isinstance(amp,  (int, float)) else None,
        "h7_amp": float(h7_amp) if isinstance(h7_amp, (int, float)) else None,
        "ver": 2,
    }

    # посчитаем h7_spike по окну
    if rec["h7_amp"] is not None:
        hist = [r.get("h7_amp") for r in rows if isinstance(r, dict) and isinstance(r.get("h7_amp"), (int, float))]
        hist = hist[-(H7_WINDOW-1):] + [rec["h7_amp"]]
        hist_clean = [float(v) for v in hist if isinstance(v, (int, float)) and math.isfinite(v)]
        if len(hist_clean) >= 12:
            last = hist_clean[-1]
            spike = _robust_h7_spike(hist_clean[:-1], last)
            if spike is not None:
                rec["h7_spike"] = bool(spike)
        else:
            rec["h7_spike"] = None
    else:
        rec["h7_spike"] = None

    rows.append(rec)
    if len(rows) > MAX_LEN:
        rows = rows[-MAX_LEN:]

    _atomic_write_json(OUT_PATH, rows)
    print(f"collect: ok ts={now} freq={rec['freq']} amp={rec['amp']} h7={rec['h7_amp']} spike={rec['h7_spike']} -> {OUT_PATH}")
    return 0

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Schumann SR collector / reader")
    parser.add_argument("--collect", action="store_true", help="collect one tick into schumann_hourly.json (v2)")
    args = parser.parse_args()
    if args.collect:
        return _collect_once()
    # без флага — просто показать текущее API-ответ
    import pprint
    pprint.pp(get_schumann())
    return 0

# ───────────────────── main ──────────────────────────────────────────────────

if __name__ == "__main__":
    raise SystemExit(_cli())
