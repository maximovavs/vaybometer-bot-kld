#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safecast.py — сбор последних измерений радиации из Safecast API.

Поведение:
- Берёт точку (SC_LAT/SC_LON) и радиус (SC_DISTANCE_KM), окно (SC_SINCE_HOURS).
- Тянет измерения, фильтрует по µSv/h | uSv/h | nSv/h, конвертирует к µSv/h.
- Берёт самое свежее, сохраняет в историю JSON (SC_FILE).
- Запись дополняется полем "region" (если SC_REGION задан).

CLI:
  python safecast.py --collect   # обновить историю
  python safecast.py --once      # распечатать последний валидный замер

Окружение:
  SC_LAT, SC_LON                  — широта/долгота (обязательно)
  SC_DISTANCE_KM                  — радиус км (дефолт 50)
  SC_SINCE_HOURS                  — окно часов назад (дефолт 24)
  SC_BASE_URL                     — https://api.safecast.org
  SC_PER_PAGE                     — 1000
  SC_MAX_PAGES                    — 10
  SC_USER_AGENT                   — UA для запросов
  SC_FILE                         — имя файла истории
  SC_MAX_LEN                      — макс. длина истории (дефолт 5000)
  SC_REGION                       — человекочитаемая метка региона (добавляется в запись)

Доп. сетевые настройки (устойчивость):
  SC_RETRIES                      — число повторов при сетевых сбоях (дефолт 3)
  SC_TIMEOUT                      — таймаут запроса в сек (дефолт 30)
  SC_BACKOFF                      — множитель экспоненциальной паузы (дефолт 1.7)
"""

from __future__ import annotations
import os, sys, json, time, datetime as dt
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import requests
from requests import exceptions as req_exc

ISO8601 = "%Y-%m-%dT%H:%M:%SZ"

def env(name: str, default: Optional[str]=None) -> Optional[str]:
    v = os.environ.get(name)
    return v if (v is not None and v != "") else default

# ─────────────────────── сетевые параметры (ENV) ───────────────────────
SC_RETRIES = int(env("SC_RETRIES", "3") or "3")
SC_TIMEOUT = float(env("SC_TIMEOUT", "30") or "30")
SC_BACKOFF = float(env("SC_BACKOFF", "1.7") or "1.7")

def parse_float(x: Any) -> Optional[float]:
    try:
        if x is None: return None
        if isinstance(x, (int, float)): return float(x)
        return float(str(x).replace(",", "."))
    except Exception:
        return None

def now_utc() -> dt.datetime:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

def iso_utc(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime(ISO8601)

def normalize_unit_to_uSv_h(value: Any, unit: str) -> Optional[float]:
    """µSv/h, uSv/h, μSv/h, nSv/h -> µSv/h. Прочее (cpm) — None."""
    v = parse_float(value)
    if v is None or unit is None:
        return None
    u = str(unit).strip()
    if u in ("µSv/h", "uSv/h", "μSv/h"):
        return v
    if u == "nSv/h":
        return v / 1000.0
    return None  # cpm и прочее — пропускаем

def build_query(
    base_url: str,
    lat: float,
    lon: float,
    distance_km: float,
    captured_after_iso: str,
    page: int,
    per_page: int
) -> str:
    params = {
        "latitude":  f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "distance":  f"{distance_km:.3f}",   # км
        "captured_after": captured_after_iso,
        "page": str(page),
        "per_page": str(per_page),
        "format": "json",
        "order": "desc",
        "sort": "captured_at",
    }
    return f"{base_url.rstrip('/')}/measurements.json?{urllib.parse.urlencode(params)}"

def _http_get_with_retry(url: str, headers: Dict[str, str], timeout: float) -> requests.Response:
    """
    GET с ретраями на сетевые таймауты/коннект и 5xx/429.
    4xx (кроме 429) — не ретраим.
    """
    last_err: Optional[BaseException] = None
    for attempt in range(SC_RETRIES):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            # статус-коды для ретрая
            if r.status_code in (429,) or 500 <= r.status_code < 600:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            return r
        except (req_exc.Timeout, req_exc.ConnectTimeout, req_exc.ReadTimeout, req_exc.ConnectionError) as e:
            last_err = e
        except requests.HTTPError as e:
            last_err = e
            if e.response is not None and e.response.status_code not in (429,) and not (500 <= e.response.status_code < 600):
                # не ретраим «нормальные» 4xx — отдадим наверх
                raise
        # пауза перед следующим заходом, кроме последней попытки
        if attempt < SC_RETRIES - 1:
            time.sleep(SC_BACKOFF ** attempt)
    assert last_err is not None
    raise last_err  # type: ignore[misc]

def fetch_page(url: str, user_agent: Optional[str]=None, timeout: float = SC_TIMEOUT) -> List[Dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    r = _http_get_with_retry(url, headers=headers, timeout=timeout)
    # если сервер вернул 4xx/5xx не отретраенные, тут уже было raise_for_status в _http_get_with_retry
    data = r.json()
    if isinstance(data, dict) and "measurements" in data:
        data = data["measurements"]
    return data if isinstance(data, list) else []

def fetch_measurements(
    lat: float,
    lon: float,
    distance_km: float,
    since_hours: float,
    base_url: str,
    per_page: int,
    max_pages: int,
    user_agent: Optional[str]=None
) -> List[Dict[str, Any]]:
    captured_after = now_utc() - dt.timedelta(hours=since_hours)
    captured_after_iso = iso_utc(captured_after)
    out: List[Dict[str, Any]] = []
    for page in range(1, max_pages+1):
        url = build_query(base_url, lat, lon, distance_km, captured_after_iso, page, per_page)
        try:
            chunk = fetch_page(url, user_agent=user_agent, timeout=SC_TIMEOUT)
        except requests.HTTPError as e:
            # предсказуемые «нет данных/некорректный запрос» — прекращаем пагинацию
            if e.response is not None and e.response.status_code in (404, 422):
                break
            raise
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < per_page:
            break
    return out

def to_record(meas: Dict[str, Any], region: Optional[str]) -> Optional[Dict[str, Any]]:
    unit = str(meas.get("unit") or "").strip()
    value = meas.get("value")
    uSv_h = normalize_unit_to_uSv_h(value, unit)
    if uSv_h is None:
        return None

    captured_at = meas.get("captured_at")
    ts = None
    if isinstance(captured_at, str):
        try:
            t = dt.datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            ts = int(t.timestamp())
        except Exception:
            ts = None
    if ts is None:
        ts = int(time.time())

    lat = parse_float(meas.get("latitude") or (meas.get("location") or {}).get("latitude"))
    lon = parse_float(meas.get("longitude") or (meas.get("location") or {}).get("longitude"))

    rec = {
        "ts": ts,
        "uSv_h": round(uSv_h, 6),
        "lat": lat,
        "lon": lon,
        "id": meas.get("id"),
        "unit_raw": unit or None,
        "src": "safecast",
        "ver": 1,
    }
    if region:
        rec["region"] = region
    return rec

def collapse_latest(measurements: List[Dict[str, Any]], region: Optional[str]) -> Optional[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    for m in measurements:
        r = to_record(m, region)
        if r is not None:
            recs.append(r)
    if not recs:
        return None
    recs.sort(key=lambda x: x["ts"], reverse=True)
    return recs[0]

def load_history(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_history(path: str, items: List[Dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, separators=(",", ": "))
    os.replace(tmp, path)

def append_history(path: str, rec: Dict[str, Any], max_len: int) -> Tuple[bool, List[Dict[str, Any]]]:
    items = load_history(path)
    for it in items[-10:][::-1]:
        if it.get("ts") == rec.get("ts") and it.get("uSv_h") == rec.get("uSv_h"):
            return False, items
    items.append(rec)
    if len(items) > max_len:
        items = items[-max_len:]
    save_history(path, items)
    return True, items

def collect() -> int:
    base_url = env("SC_BASE_URL", "https://api.safecast.org")
    sc_file  = env("SC_FILE", "safecast_radiation.json")
    max_len  = int(env("SC_MAX_LEN", "5000") or "5000")
    per_page = int(env("SC_PER_PAGE", "1000") or "1000")
    max_pages= int(env("SC_MAX_PAGES", "10") or "10")
    ua       = env("SC_USER_AGENT", "vaybometer/1.0 (+github actions)")
    region   = env("SC_REGION", None)

    lat = parse_float(env("SC_LAT"))
    lon = parse_float(env("SC_LON"))
    dist_km = parse_float(env("SC_DISTANCE_KM", "50")) or 50.0
    since_hours = parse_float(env("SC_SINCE_HOURS", "24")) or 24.0

    if lat is None or lon is None:
        print("error: SC_LAT/SC_LON are required", file=sys.stderr)
        return 2

    try:
        raw = fetch_measurements(
            lat=lat, lon=lon, distance_km=dist_km, since_hours=since_hours,
            base_url=base_url, per_page=per_page, max_pages=max_pages, user_agent=ua
        )
    except Exception as e:
        print(f"fetch error: {e}", file=sys.stderr)
        return 3

    latest = collapse_latest(raw, region)
    if latest is None:
        print("collect: no valid µSv/h data in time window")
        return 0

    changed, items = append_history(sc_file, latest, max_len)
    ts = latest["ts"]; v = latest["uSv_h"]
    rg = latest.get("region")
    suffix = f" region={rg}" if rg else ""
    print(f"collect: ok ts={ts} uSv/h={v} src=safecast{suffix} -> {sc_file}")
    if changed:
        try:
            print("Last record JSON:", json.dumps(items[-1], ensure_ascii=False))
        except Exception:
            pass
    return 0

def print_once() -> int:
    base_url = env("SC_BASE_URL", "https://api.safecast.org")
    ua       = env("SC_USER_AGENT", "vaybometer/1.0 (+github actions)")
    region   = env("SC_REGION", None)
    lat = parse_float(env("SC_LAT"))
    lon = parse_float(env("SC_LON"))
    dist_km = parse_float(env("SC_DISTANCE_KM", "50")) or 50.0
    since_hours = parse_float(env("SC_SINCE_HOURS", "24")) or 24.0
    per_page = int(env("SC_PER_PAGE", "1000") or "1000")
    max_pages= int(env("SC_MAX_PAGES", "10") or "10")

    if lat is None or lon is None:
        print("error: SC_LAT/SC_LON are required", file=sys.stderr)
        return 2

    raw = fetch_measurements(
        lat=lat, lon=lon, distance_km=dist_km, since_hours=since_hours,
        base_url=base_url, per_page=per_page, max_pages=max_pages, user_agent=ua
    )
    latest = collapse_latest(raw, region)
    print(json.dumps(latest, ensure_ascii=False, indent=2))
    return 0

def main(argv: List[str]) -> int:
    if len(argv) > 1 and argv[1] == "--collect":
        return collect()
    if len(argv) > 1 and argv[1] == "--once":
        return print_once()
    print("Usage: python safecast.py --collect|--once", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))
