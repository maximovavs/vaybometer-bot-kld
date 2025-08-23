#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — единый модуль и коллектор (live + GCI Power + cache + H7)

Функционал:
• get_schumann() — для поста: live → cache → no data
• CLI:  python schumann.py --collect  → пишет schumann_hourly.json (v2):
        {"ts","freq","amp","h7_amp","h7_spike","ver":2,"src":...}

Источники:
1) SCHU_CUSTOM_URL (если задан) — твой JSON с freq/amp (любой структуры).
2) GCI «live» зеркала (JSON-подобные; если отзываются).
3) GCI Magnetometer (HeartMath) — скрейпинг Power для станции GCMS_SITE
   (по умолчанию — GCI003 Lithuania). Это hourly-метрика «Power».
4) Cache fallback — если всё отвалилось, но разрешено ALLOW_CACHE_ON_FAIL.

H7 (7-я гармоника):
• Если задан H7_URL со спектром (см. форматы ниже) — вытягиваем амплитуду
  вблизи H7_TARGET_HZ и считаем флаг всплеска (robust z-score via MAD).

ENV:
  SCHU_FILE            — путь к schumann_hourly.json (default: schumann_hourly.json)
  SCHU_MAX_LEN         — максимум записей (default: 5000)
  SCHU_AMP_SCALE       — множитель амплитуды источника (если он в нТ; default: 1)
  SCHU_TREND_WINDOW    — окно для тренда (default: 24)
  SCHU_TREND_DELTA     — порог для стрелки (default: 0.1)
  SCHU_ALLOW_CACHE_ON_FAIL — 1/0, разрешать ли падать в кэш при fail live (default: 1)

  SCHU_CUSTOM_URL      — опц. твой endpoint с freq/amp (любой JSON; мы сами выковыряем)
  SCHU_GCI_URLS        — опц. CSV список зеркал GCI JSON; иначе дефолтный список

  GCMS_PAGE_URL        — страница GCI Magnetometer для скрейпа Power
                         (default: https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/)
  GCMS_SITE            — код станции (GCI001..GCI006). По умолчанию GCI003 (Lithuania).

  H7_URL               — опц. endpoint спектра (JSON форматы см. _fetch_h7_amp)
  H7_TARGET_HZ         — частота 7-й гармоники (default: 54.81)
  H7_WINDOW_H          — окно для z-score (default: 48)
  H7_Z                 — порог z-score (default: 2.5)
  H7_MIN_ABS           — минимальная абсолютная амплитуда для «всплеска» (default: 0.2)

Зависимости: requests
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

__all__ = ("get_schumann",)

# ───────────────────────────── ENV / Константы ───────────────────────────────

OUT_PATH = Path(os.environ.get("SCHU_FILE", "schumann_hourly.json")).resolve()
MAX_LEN = int(os.environ.get("SCHU_MAX_LEN", "5000"))
AMP_SCALE = float(os.environ.get("SCHU_AMP_SCALE", "1"))

TREND_WINDOW = int(os.environ.get("SCHU_TREND_WINDOW", "24"))
TREND_DELTA  = float(os.environ.get("SCHU_TREND_DELTA", "0.1"))
ALLOW_CACHE_ON_FAIL = os.environ.get("SCHU_ALLOW_CACHE_ON_FAIL", "1") not in ("0", "false", "False")

CUSTOM_URL = os.environ.get("SCHU_CUSTOM_URL", "").strip()
GCI_URLS = [u.strip() for u in (os.environ.get("SCHU_GCI_URLS") or "").split(",") if u.strip()] or [
    # если есть рабочие JSON-зеркала — добавляй сюда; будут опрошены по очереди
]

# GCI Magnetometer (HeartMath) — скрейпинг Power
GCMS_PAGE_URL = os.environ.get(
    "GCMS_PAGE_URL",
    "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/"
).strip()
GCMS_SITE = os.environ.get("GCMS_SITE", "GCI003").strip()  # по умолчанию Lithuania

# 7-я гармоника
H7_URL = os.environ.get("H7_URL", "").strip()
H7_TARGET_HZ = float(os.environ.get("H7_TARGET_HZ", "54.81"))
H7_WINDOW = int(os.environ.get("H7_WINDOW_H", "48"))
H7_Z = float(os.environ.get("H7_Z", "2.5"))
H7_MIN_ABS = float(os.environ.get("H7_MIN_ABS", "0.2"))

# ───────────────────────────── Утилиты ───────────────────────────────────────

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

def _read_store() -> List[Dict[str, Any]]:
    if not OUT_PATH.exists():
        return []
    try:
        js = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        return js if isinstance(js, list) else []
    except Exception:
        return []

def _trend_arrow(values: List[float]) -> str:
    if len(values) < 2:
        return "→"
    avg = sum(values[:-1]) / max(1, (len(values) - 1))
    d = values[-1] - avg
    if d >= TREND_DELTA:
        return "↑"
    if d <= -TREND_DELTA:
        return "↓"
    return "→"

def _series_from_rows(rows: List[Dict[str, Any]]) -> Tuple[List[float], List[float], List[Optional[float]]]:
    freqs, amps, h7s = [], [], []
    for it in rows:
        f = it.get("freq"); a = it.get("amp"); h7 = it.get("h7_amp")
        if isinstance(f, (int, float)):
            freqs.append(float(f))
            if isinstance(a, (int, float)):
                amps.append(float(a))
            h7s.append(float(h7) if isinstance(h7, (int, float)) else None)
    return freqs, amps, h7s

# ───────────────────────────── Live источники ────────────────────────────────

def _extract_freq_amp_any(obj: Any) -> Optional[Tuple[float, float]]:
    """
    Универсальный извлекатель freq (~6..10 Гц) и amp из любого JSON.
    Обходит словари/списки в глубину.
    """
    cand_freqs: List[float] = []
    cand_amps: List[float] = []

    def walk(x: Any):
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in ("freq", "frequency", "sr_freq", "sr1_freq"):
                    try:
                        fv = float(v)
                        if 6.0 <= fv <= 10.0 and math.isfinite(fv):
                            cand_freqs.append(fv)
                    except Exception:
                        pass
                if lk in ("amp", "amplitude", "sr_amp", "sr1_amp", "power"):
                    try:
                        av = float(v) * AMP_SCALE
                        if math.isfinite(av):
                            cand_amps.append(av)
                    except Exception:
                        pass
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    if not cand_freqs or not cand_amps:
        return None
    return float(cand_freqs[-1]), float(cand_amps[-1])

def _fetch_live_custom() -> Optional[Tuple[float, float]]:
    if not CUSTOM_URL:
        return None
    try:
        r = requests.get(CUSTOM_URL, timeout=10)
        r.raise_for_status()
        js = r.json()
        return _extract_freq_amp_any(js)
    except Exception:
        return None

def _fetch_live_gci_json() -> Optional[Tuple[float, float]]:
    """
    Если укажешь рабочие JSON-зеркала GCI в SCHU_GCI_URLS — опросим их.
    Формат не фиксирован — пытаемся извлечь freq/amp универсально.
    """
    for url in GCI_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            js = r.json()
            got = _extract_freq_amp_any(js)
            if got:
                return got
        except Exception:
            continue
    return None

# ───── GCI Magnetometer (Highcharts) — скрейпинг Power для станции ─────

_GCI_SERIES_RE = re.compile(r"series\s*:\s*\[(.*?)\]\s*,\s*credits", re.S | re.I)
_GCI_POINT_RE  = re.compile(
    r"\{[^}]*?name\s*:\s*['\"](?P<name>GCI00[1-6][^'\"]*)['\"][^}]*?data\s*:\s*(?P<data>\[[^\]]+\])",
    re.S | re.I
)
_NUM_RE        = re.compile(r"(-?\d+(?:\.\d+)?)")

def _fetch_gci_power(site: str = "GCI003") -> Optional[float]:
    """
    Скрейпит страницу GCMS Magnetometer и возвращает последний Power (float)
    для заданной станции (например, GCI003 — Lithuania).
    Ищем highcharts-конфиг: series[..].data[..] и берём последний y.
    """
    try:
        r = requests.get(GCMS_PAGE_URL, timeout=15)
        r.raise_for_status()
        html = unescape(r.text)
    except Exception:
        return None

    m = _GCI_SERIES_RE.search(html)
    if not m:
        return None
    blob = m.group(1)

    for sm in _GCI_POINT_RE.finditer(blob):
        name = sm.group("name")
        data = sm.group("data")
        if site not in name:
            continue
        nums = _NUM_RE.findall(data)
        if not nums:
            continue
        try:
            val = float(nums[-1])  # берём последнее число как последний y
            return val if math.isfinite(val) else None
        except Exception:
            continue
    return None

def _fetch_live() -> Optional[Dict[str, Any]]:
    """
    Пытаемся получить «живые» freq/amp:
      1) CUSTOM_URL → универсальный JSON-парсер
      2) GCI_JSON зеркала → универсальный парсер
      3) GCI Magnetometer Power (hourly): freq=7.83, amp=power
    """
    # 1) твой кастомный endpoint
    fa = _fetch_live_custom()
    if fa:
        freq, amp = fa
        return {
            "freq": round(float(freq), 2),
            "amp":  round(float(amp), 1),
            "trend": "→",
            "high": (freq > 8.0 or amp > 100.0),
            "cached": False,
            "src": "custom",
        }

    # 2) JSON зеркала GCI (если заданы)
    fa = _fetch_live_gci_json()
    if fa:
        freq, amp = fa
        return {
            "freq": round(float(freq), 2),
            "amp":  round(float(amp), 1),
            "trend": "→",
            "high": (freq > 8.0 or amp > 100.0),
            "cached": False,
            "src": "gci_json",
        }

    # 3) Скрейп Power с GCI Magnetometer
    pwr = _fetch_gci_power(GCMS_SITE)
    if isinstance(pwr, (int, float)):
        return {
            "freq": 7.83,                     # трактуем как базовую гармонику
            "amp":  round(float(pwr), 2),     # Power → в «amp» (метрика энергии)
            "trend": "→",
            "high": False,
            "cached": False,
            "src": "gci_power",
        }

    return None

# ───────────────────────────── H7 (7-я гармоника) ────────────────────────────

def _robust_h7_spike(history: List[float], last: float) -> Optional[bool]:
    """
    Робастная детекция всплеска: last > median + H7_Z * MAD и last > H7_MIN_ABS
    """
    vals = [v for v in history if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(vals) < 12 or not isinstance(last, (int, float)):
        return None
    med = statistics.median(vals)
    mad = statistics.median([abs(x - med) for x in vals]) or 0.01
    return bool(last > med + H7_Z * mad and last > H7_MIN_ABS)

def _fetch_h7_amp() -> Optional[float]:
    """
    Если задан H7_URL — ждем JSON спектра и берём амплитуду у ближайшего бина
    к H7_TARGET_HZ. Допустимые форматы:
      • {"freq":[...], "amp":[...]}  или {"frequency":[...], "amplitude":[...]}
      • [{"f":..,"a":..}, ...] или [{"freq":..,"amp":..}, ...]
    """
    if not H7_URL:
        return None
    try:
        r = requests.get(H7_URL, timeout=12)
        r.raise_for_status()
        obj = r.json()
    except Exception:
        return None

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

    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        try:
            idx = min(range(len(obj)), key=lambda i: abs(float(obj[i].get("f") or obj[i].get("freq")) - H7_TARGET_HZ))
            raw = obj[idx].get("a") or obj[idx].get("amp")
            val = float(raw)
            return val if math.isfinite(val) else None
        except Exception:
            return None

    return None

# ───────────────────────────── Публичный API ────────────────────────────────

def get_schumann() -> Dict[str, Any]:
    """
    Возвращает dict:
      {freq, amp, trend, high, cached, [h7_amp], [h7_spike], [src]}
    """
    live = _fetch_live()
    if live:
        # фильтрация мусора амплитуды (на всякий)
        if isinstance(live.get("amp"), (int, float)) and live["amp"] < 0:
            live["amp"] = None
        return live

    rows = _read_store()
    if not rows:
        return {"msg": "no data"}

    freqs, amps, h7s = _series_from_rows(rows)
    if not freqs:
        return {"msg": "no data"}

    f_w = freqs[-TREND_WINDOW:] if len(freqs) >= TREND_WINDOW else freqs
    a_w = amps[-TREND_WINDOW:]  if len(amps)  >= TREND_WINDOW else amps
    trend = _trend_arrow(f_w)

    out: Dict[str, Any] = {
        "freq": round(float(f_w[-1]), 2),
        "amp":  round(float(a_w[-1]), 1) if a_w else None,
        "trend": trend,
        "high": (f_w[-1] > 8.0 or (a_w and a_w[-1] > 100.0)),
        "cached": True,
        "src": "cache",
    }

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

# ───────────────────────────── CLI / Сбор ───────────────────────────────────

def _collect_once() -> int:
    """
    Один тик коллектора:
      live (custom → gci_json → gci_power) → если нет и разрешено, cache → запись v2.
    """
    now = int(time.time())
    live = _fetch_live()

    if not live:
        if not ALLOW_CACHE_ON_FAIL:
            print("collect: no live source available, and cache fallback disabled", file=sys.stderr)
            return 2
        rows = _read_store()
        if not rows:
            print("collect: no live source and no cache", file=sys.stderr)
            return 2
        last = rows[-1]
        freq = last.get("freq")
        amp  = last.get("amp")
        src  = "cache"
    else:
        freq = live.get("freq")
        amp  = live.get("amp")
        src  = live.get("src") or "live"

    h7_amp = _fetch_h7_amp()

    rows = _read_store()
    if rows and isinstance(rows[-1], dict) and rows[-1].get("ts") == now:
        rows.pop()

    rec = {
        "ts": now,
        "freq": float(freq) if isinstance(freq, (int, float)) else None,
        "amp":  float(amp)  if isinstance(amp,  (int, float)) else None,
        "h7_amp": float(h7_amp) if isinstance(h7_amp, (int, float)) else None,
        "ver": 2,
        "src": src,
    }

    # санитария: отрицательные амплитуды считаем нонсенсом → «н/д»
    if isinstance(rec["amp"], (int, float)) and rec["amp"] < 0:
        rec["amp"] = None

    # h7_spike
    if rec["h7_amp"] is not None:
        hist = [r.get("h7_amp") for r in rows if isinstance(r, dict) and isinstance(r.get("h7_amp"), (int, float))]
        hist = hist[-(H7_WINDOW-1):] + [rec["h7_amp"]]
        hist_clean = [float(v) for v in hist if isinstance(v, (int, float)) and math.isfinite(v)]
        if len(hist_clean) >= 12:
            last_val = hist_clean[-1]
            spike = _robust_h7_spike(hist_clean[:-1], last_val)
            rec["h7_spike"] = bool(spike) if spike is not None else None
        else:
            rec["h7_spike"] = None
    else:
        rec["h7_spike"] = None

    rows.append(rec)
    if len(rows) > MAX_LEN:
        rows = rows[-MAX_LEN:]

    _atomic_write_json(OUT_PATH, rows)
    print(f"collect: ok ts={now} src={src} freq={rec['freq']} amp={rec['amp']} h7={rec['h7_amp']} spike={rec['h7_spike']} -> {OUT_PATH}")
    return 0

def _cli() -> int:
    ap = argparse.ArgumentParser(description="Schumann SR: live + GCI Power + cache + H7")
    ap.add_argument("--collect", action="store_true", help="collect one tick into schumann_hourly.json (v2)")
    args = ap.parse_args()
    if args.collect:
        return _collect_once()
    # debug dump
    import pprint
    pprint.pp(get_schumann())
    return 0

if __name__ == "__main__":
    raise SystemExit(_cli())
