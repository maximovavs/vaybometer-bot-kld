#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical WeeklySnapshot schema, delta rules, and Actions artifact restore helpers."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any
import urllib.parse
import zipfile

SCHEMA_VERSION = 1

def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_date(value: object) -> date | None:
    try: return date.fromisoformat(str(value or "")[:10])
    except ValueError: return None

def _is_number(value: object) -> bool:
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value))

def _mean(values: list[float]) -> float | None:
    return sum(values)/len(values) if values else None

def build_snapshot_candidate(*, region: str, week_start: date, weather_days: list[dict[str, Any]],
    weather_coverage_days: int, weather_coverage_complete: bool, sea_temps: list[float],
    expected_sea_samples: int, generated_at_utc: str | None = None) -> dict[str, Any] | None:
    expected_dates=[(week_start+timedelta(days=i)).isoformat() for i in range(7)]
    returned=[str(item.get("date") or "")[:10] for item in weather_days if isinstance(item,dict)]
    if not weather_coverage_complete or weather_coverage_days!=7 or returned!=expected_dates: return None
    highs=[float(x["tmax"]) for x in weather_days if _is_number(x.get("tmax"))]
    lows=[float(x["tmin"]) for x in weather_days if _is_number(x.get("tmin"))]
    winds=[float(x["wind"]) for x in weather_days if _is_number(x.get("wind"))]
    gusts=[float(x["gust"]) for x in weather_days if _is_number(x.get("gust"))]
    rain=[x.get("rainy") for x in weather_days]
    precip=[float(x["precip_sum"]) for x in weather_days if _is_number(x.get("precip_sum"))]
    mean_low=_mean(lows) if len(lows)==7 else None; mean_high=_mean(highs) if len(highs)==7 else None
    sea=[float(v) for v in sea_temps if _is_number(v)]; sea_complete=expected_sea_samples>0 and len(sea)==expected_sea_samples
    return {
        "schema_version":SCHEMA_VERSION,"region":region,"week_start":week_start.isoformat(),"week_end":(week_start+timedelta(days=6)).isoformat(),
        "generated_at_utc":generated_at_utc or _iso_utc_now(),"published_at_utc":None,"production_text_message_id":None,
        "production_authority":False,"weather_coverage_days":7,"weather_coverage_complete":True,
        "weekly_min_c":min(lows) if len(lows)==7 else None,"weekly_max_c":max(highs) if len(highs)==7 else None,
        "mean_daily_low_c":mean_low,"mean_daily_high_c":mean_high,
        "thermal_midpoint_c":(mean_low+mean_high)/2 if mean_low is not None and mean_high is not None else None,
        "rainy_day_count":sum(1 for flag in rain if flag is True) if all(isinstance(flag,bool) for flag in rain) else None,
        "precipitation_sum_mm":sum(precip) if len(precip)==7 else None,
        "windy_day_count":sum(1 for v in gusts if v>=10.0) if len(gusts)==7 else None,
        "max_wind_ms":max(winds) if len(winds)==7 else None,"max_gust_ms":max(gusts) if len(gusts)==7 else None,
        "sea_expected_sample_count":int(expected_sea_samples),"sea_sample_count":len(sea),
        "sea_min_c":min(sea) if sea_complete else None,"sea_max_c":max(sea) if sea_complete else None,
        "sea_mean_c":_mean(sea) if sea_complete else None,"regime_key":None,
    }

def finalize_snapshot(candidate: dict[str, Any], message_id: int, *, published_at_utc: str | None = None) -> dict[str, Any]:
    if not isinstance(message_id,int) or isinstance(message_id,bool) or message_id<=0: raise ValueError("positive production text message_id required")
    out=dict(candidate); out["production_text_message_id"]=message_id; out["published_at_utc"]=published_at_utc or _iso_utc_now(); out["production_authority"]=True; return out

def validate_snapshot(payload: Any, *, region: str, expected_week_start: str | None = None, require_authority: bool = True) -> bool:
    if not isinstance(payload,dict) or payload.get("schema_version")!=SCHEMA_VERSION or payload.get("region")!=region: return False
    start=_parse_date(payload.get("week_start")); end=_parse_date(payload.get("week_end"))
    if start is None or end!=start+timedelta(days=6): return False
    if expected_week_start and start.isoformat()!=expected_week_start: return False
    if payload.get("weather_coverage_complete") is not True or payload.get("weather_coverage_days")!=7: return False
    if not str(payload.get("generated_at_utc") or "").strip(): return False
    if require_authority:
        mid=payload.get("production_text_message_id")
        if payload.get("production_authority") is not True or not isinstance(mid,int) or isinstance(mid,bool) or mid<=0: return False
        if not str(payload.get("published_at_utc") or "").strip(): return False
    for key in ("weekly_min_c","weekly_max_c","mean_daily_low_c","mean_daily_high_c","thermal_midpoint_c",
                "precipitation_sum_mm","max_wind_ms","max_gust_ms","sea_min_c","sea_max_c","sea_mean_c"):
        value=payload.get(key)
        if value is not None and not _is_number(value): return False
    for key in ("rainy_day_count","windy_day_count"):
        value=payload.get(key)
        if value is not None and (not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=7): return False
    return True

def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8"); tmp.replace(path)

def write_snapshot_atomic(path: Path, payload: dict[str, Any]) -> None:
    if not validate_snapshot(payload,region=str(payload.get("region") or ""),require_authority=True): raise ValueError("refusing invalid/non-authoritative WeeklySnapshot")
    _write_json_atomic(path,payload)

def load_snapshot_file(path: Path, *, region: str, expected_week_start: str) -> dict[str, Any] | None:
    try: payload=json.loads(path.read_text("utf-8"))
    except Exception: return None
    return payload if validate_snapshot(payload,region=region,expected_week_start=expected_week_start,require_authority=True) else None

def derive_delta(current: dict[str, Any] | None, previous: dict[str, Any] | None, *, region: str) -> dict[str,dict[str,Any]]:
    if not isinstance(current,dict) or not validate_snapshot(current,region=region,expected_week_start=str(current.get("week_start") or ""),require_authority=False): return {}
    start=_parse_date(current.get("week_start"))
    if start is None or not validate_snapshot(previous,region=region,expected_week_start=(start-timedelta(days=7)).isoformat(),require_authority=True): return {}
    out={}
    ct,pt=current.get("thermal_midpoint_c"),previous.get("thermal_midpoint_c")
    if _is_number(ct) and _is_number(pt):
        d=float(ct)-float(pt)
        if d>=2: out["temperature"]={"direction":"warmer","delta":d,"score":abs(d)/2}
        elif d<=-2: out["temperature"]={"direction":"colder","delta":d,"score":abs(d)/2}
    cr,pr=current.get("rainy_day_count"),previous.get("rainy_day_count")
    if isinstance(cr,int) and isinstance(pr,int):
        d=cr-pr
        if d>=2: out["rain"]={"direction":"wetter","delta":d,"score":abs(d)/2}
        elif d<=-2: out["rain"]={"direction":"drier","delta":d,"score":abs(d)/2}
    cw,pw=current.get("windy_day_count"),previous.get("windy_day_count"); cg,pg=current.get("max_gust_ms"),previous.get("max_gust_ms")
    count_dir=None; count_delta=None
    if isinstance(cw,int) and isinstance(pw,int):
        count_delta=cw-pw
        if count_delta>=2: count_dir="windier"
        elif count_delta<=-2: count_dir="calmer"
    gust_dir=None; gust_delta=None
    if _is_number(cg) and _is_number(pg):
        gust_delta=float(cg)-float(pg)
        if gust_delta>=3: gust_dir="windier"
        elif gust_delta<=-3: gust_dir="calmer"
    if count_dir and gust_dir and count_dir!=gust_dir: out["wind"]={"direction":"mixed","delta":count_delta,"gust_delta":gust_delta,"score":1.0}
    elif count_dir: out["wind"]={"direction":count_dir,"delta":count_delta,"gust_delta":gust_delta,"score":abs(float(count_delta))/2}
    elif gust_dir: out["wind"]={"direction":gust_dir,"delta":count_delta,"gust_delta":gust_delta,"score":abs(float(gust_delta))/3}
    sea_complete=(current.get("sea_expected_sample_count",0)>0 and current.get("sea_sample_count")==current.get("sea_expected_sample_count") and previous.get("sea_sample_count")==previous.get("sea_expected_sample_count"))
    cs,ps=current.get("sea_mean_c"),previous.get("sea_mean_c")
    if sea_complete and _is_number(cs) and _is_number(ps):
        d=float(cs)-float(ps)
        if d>=1: out["sea"]={"direction":"warmer","delta":d,"score":abs(d)}
        elif d<=-1: out["sea"]={"direction":"colder","delta":d,"score":abs(d)}
    return out

def derive_delta_lines(current: dict[str, Any] | None, previous: dict[str, Any] | None, *, region: str) -> list[str]:
    d=derive_delta(current,previous,region=region); items=[]
    td=d.get("temperature",{}).get("direction")
    if td=="warmer": items.append((d["temperature"]["score"],"Температура: новый недельный прогноз заметно теплее."))
    elif td=="colder": items.append((d["temperature"]["score"],"Температура: новый недельный прогноз заметно холоднее."))
    rd=d.get("rain",{}).get("direction")
    if rd=="wetter": items.append((d["rain"]["score"],"Осадки: в новом прогнозе заметно больше дождливых дней."))
    elif rd=="drier": items.append((d["rain"]["score"],"Осадки: новый прогноз заметно суше."))
    wd=d.get("wind",{}).get("direction")
    if wd=="windier": items.append((d["wind"]["score"],"Ветер: новая неделя по прогнозу выглядит ветренее."))
    elif wd=="calmer": items.append((d["wind"]["score"],"Ветер: новая неделя по прогнозу выглядит спокойнее."))
    elif wd=="mixed": items.append((d["wind"]["score"],"Ветер: картина смешанная — число ветреных дней и пиковые порывы дают разные сигналы."))
    sd=d.get("sea",{}).get("direction"); water="Море" if region=="cyprus" else "Балтика"
    if sd=="warmer": items.append((d["sea"]["score"],f"{water}: текущий снимок воды теплее, чем в прошлом недельном выпуске."))
    elif sd=="colder": items.append((d["sea"]["score"],f"{water}: текущий снимок воды холоднее, чем в прошлом недельном выпуске."))
    items.sort(key=lambda x:x[0],reverse=True); return [value for _score,value in items[:3]]

def select_earliest_valid_snapshot(records: list[dict[str, Any]], *, region: str, expected_week_start: str) -> dict[str,Any] | None:
    for record in sorted(records,key=lambda item:str(item.get("created_at") or "")):
        payload=record.get("payload")
        if validate_snapshot(payload,region=region,expected_week_start=expected_week_start,require_authority=True): return payload
    return None

def artifact_name(prefix: str, week_start: str) -> str: return f"{prefix}-{week_start}"

def _gh_artifacts(name: str) -> tuple[list[dict[str,Any]],bool]:
    repo=str(os.getenv("GITHUB_REPOSITORY") or "").strip(); token=os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not repo or not token: return [],False
    endpoint=f"repos/{repo}/actions/artifacts?{urllib.parse.urlencode({'name':name,'per_page':100})}"
    try: data=json.loads(subprocess.check_output(["gh","api",endpoint],text=True))
    except Exception as exc:
        print(f"WeeklySnapshot artifact lookup failed closed: {type(exc).__name__}: {exc}",file=os.sys.stderr); return [],False
    artifacts=[x for x in data.get("artifacts",[]) if isinstance(x,dict) and not x.get("expired") and x.get("name")==name]
    artifacts.sort(key=lambda x:str(x.get("created_at") or "")); return artifacts,True

def _download_snapshot_payload(artifact: dict[str,Any], *, region: str, expected_week_start: str) -> dict[str,Any] | None:
    repo=str(os.getenv("GITHUB_REPOSITORY") or "").strip(); aid=artifact.get("id")
    if not repo or not isinstance(aid,int): return None
    with tempfile.TemporaryDirectory(prefix="weekly_snapshot_") as td:
        root=Path(td); zp=root/"artifact.zip"; ex=root/"payload"
        try:
            with zp.open("wb") as fh: subprocess.check_call(["gh","api",f"repos/{repo}/actions/artifacts/{aid}/zip"],stdout=fh)
            ex.mkdir()
            with zipfile.ZipFile(zp) as archive: archive.extractall(ex)
        except Exception: return None
        for path in sorted(ex.rglob("*.json")):
            try: payload=json.loads(path.read_text("utf-8"))
            except Exception: continue
            if validate_snapshot(payload,region=region,expected_week_start=expected_week_start,require_authority=True): return payload
    return None

def restore_previous(*, region: str, current_week_start: str, artifact_prefix: str, output: Path) -> bool:
    current=_parse_date(current_week_start)
    if current is None: return False
    expected=(current-timedelta(days=7)).isoformat(); artifacts,ok=_gh_artifacts(artifact_name(artifact_prefix,expected))
    if not ok: return False
    for artifact in artifacts:
        payload=_download_snapshot_payload(artifact,region=region,expected_week_start=expected)
        if payload is not None: _write_json_atomic(output,payload); return True
    return False

def canonical_exists(*, region: str, week_start: str, artifact_prefix: str) -> tuple[bool,bool]:
    artifacts,ok=_gh_artifacts(artifact_name(artifact_prefix,week_start))
    if not ok: return True,False
    for artifact in artifacts:
        if _download_snapshot_payload(artifact,region=region,expected_week_start=week_start) is not None: return True,True
    return False,True

def main() -> int:
    parser=argparse.ArgumentParser(description="WeeklySnapshot artifact helper"); sub=parser.add_subparsers(dest="command",required=True)
    r=sub.add_parser("restore"); r.add_argument("--region",required=True); r.add_argument("--current-week-start",required=True); r.add_argument("--artifact-prefix",required=True); r.add_argument("--output",required=True)
    e=sub.add_parser("canonical-exists"); e.add_argument("--region",required=True); e.add_argument("--week-start",required=True); e.add_argument("--artifact-prefix",required=True)
    args=parser.parse_args()
    if args.command=="restore":
        ok=restore_previous(region=args.region,current_week_start=args.current_week_start,artifact_prefix=args.artifact_prefix,output=Path(args.output))
        print("WeeklySnapshot previous restore: restored" if ok else "WeeklySnapshot previous restore: none"); return 0
    found,lookup_ok=canonical_exists(region=args.region,week_start=args.week_start,artifact_prefix=args.artifact_prefix)
    if not lookup_ok:
        print("WeeklySnapshot canonical lookup unavailable; refusing duplicate upload.",file=os.sys.stderr); return 0
    return 0 if found else 1

if __name__=="__main__":
    raise SystemExit(main())
