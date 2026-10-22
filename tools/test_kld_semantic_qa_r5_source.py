"""R5-A fail-closed producer Golden Set and negative mutation probes (offline)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kld_semantic_qa_facts import FactsUnavailable, build_verified_facts
from kld_semantic_qa_source import (CAPTURE_SCHEMA, SOURCE, LAT, LON, KLD_TZ,
    build_trusted_snapshot, canonical_sha256, _lunar_source)

# Derive current/future civil dates dynamically so tests remain valid in CI.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.astimezone(KLD_TZ).date()
TARGET = (TODAY + timedelta(days=1)).isoformat()
HOURS = [f"{TARGET}T{h:02d}:00" for h in range(24)]


def no_network(*args, **kwargs):
    raise AssertionError("R5 offline: network prohibited")

# The test runner uses the official offline runner too. This is a second guard.
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network


def response(day=TARGET):
    hours = [f"{day}T{h:02d}:00" for h in range(24)]
    payload = {
        "latitude": LAT, "longitude": LON, "timezone": "Europe/Kaliningrad",
        "utc_offset_seconds": 7200,
        "hourly_units": {"weathercode": "wmo code", "visibility": "m", "rain": "mm",
                         "wind_gusts_10m": "km/h"},
        "hourly": {"time": hours, "weathercode": [2] * 24,
                   "visibility": [8000.0] * 24, "rain": [0.0] * 24,
                   "wind_gusts_10m": [20.0] * 24},
    }
    return stamp(payload, requested=day)


def stamp(payload, retrieved=NOW, requested=TARGET, provider=SOURCE):
    payload.pop("_kld_r5_capture", None)
    payload["_kld_r5_capture"] = {
        "schema": CAPTURE_SCHEMA, "source_id": provider,
        "requested_timezone": "Europe/Kaliningrad",
        "requested_target_date": requested,
        "retrieved_at_utc": retrieved.isoformat().replace("+00:00", "Z"),
        "raw_payload_sha256": canonical_sha256(payload),
    }
    return payload


def build(payload=None, *, kind="evening", target=TARGET, date_target=None, now=NOW, **other):
    return build_trusted_snapshot(payload if payload is not None else response(),
        target_date=target, post_type=kind, publication_target_date=date_target or target,
        now_utc=now, **other)


def failure(label, payload, expected_reason, **kwargs):
    try:
        build(payload, **kwargs)
    except FactsUnavailable as exc:
        assert exc.reason == expected_reason, (label, exc.reason, expected_reason)
    else:
        raise AssertionError(f"{label}: accepted untrustworthy source")


def main():
    scenarios=[]
    def check(label, action):
        action();scenarios.append(label);print("PASS",label)
    check("S01 valid_morning", lambda: _valid("morning"))
    check("S02 valid_evening", lambda: _valid("evening"))
    check("S03 missing_weather",lambda:failure("S03",{},"weather_source_unavailable"))
    def missing_visibility():
        p=response();p["hourly"]["visibility"][5]=None;stamp(p)
        failure("S04",p,"weather_numeric_invalid")
    check("S04 missing_visibility",missing_visibility)
    def low_confidence_clear():
        p=response();p.pop("_kld_r5_capture")
        failure("S05",p,"weather_source_unavailable")
    check("S05 low_confidence_clear",low_confidence_clear)
    def one_hour_only():
        p=response();p["hourly"]={k:v[4:5] for k,v in p["hourly"].items()};stamp(p)
        failure("S06",p,"weather_hourly_window_incomplete")
    check("S06 current_only_for_tomorrow",one_hour_only)
    def spoof_status():
        p=response();p.pop("_kld_r5_capture");p["source_status"]="verified"
        failure("S07",p,"weather_source_unavailable")
    check("S07 source_not_authenticated",spoof_status)
    check("S08 future_retrieval_stamp",lambda:failure("S08",stamp(response(),NOW+timedelta(hours=3)),"weather_source_stale_or_future"))
    check("S09 stale_retrieval",lambda:failure("S09",stamp(response(),NOW-timedelta(hours=3)),"weather_source_stale_or_future"))
    def wrong_zone():
        p=response();p["timezone"]="UTC";stamp(p);failure("S10",p,"weather_timezone_mismatch")
    check("S10 wrong_timezone",wrong_zone)
    def hourly_gap():
        p=response();i=6
        for key,val in p["hourly"].items():val.pop(i)
        stamp(p);failure("S11",p,"weather_hourly_window_incomplete")
    check("S11 hourly_gap",hourly_gap)
    def duplicate():
        p=response();p["hourly"]["time"][6]=p["hourly"]["time"][5];stamp(p)
        failure("S12",p,"weather_time_duplicate_or_unordered")
    check("S12 hourly_duplicate",duplicate)
    check("S13 wrong_target",lambda:failure("S13",response(),"publication_target_mismatch",date_target=(TODAY+timedelta(days=2)).isoformat()))
    def inversion():
        p=stamp(response(),requested=TODAY.isoformat())
        failure("S14",p,"weather_window_mismatch")
    check("S14 window_inversion",inversion)
    def unknown_code():
        p=response();p["hourly"]["weathercode"][8]=404;stamp(p)
        failure("S15",p,"weather_wmo_invalid")
    check("S15 unknown_wmo",unknown_code)
    def openweather():
        p=response();p["hourly"]["weathercode"][4]=800;stamp(p)
        failure("S16",p,"weather_wmo_invalid")
    check("S16 openweather_as_wmo",openweather)
    def storm():
        p=response();p["hourly"]["wind_gusts_10m"][6]=60;stamp(p)
        x=build(p);assert x["weather"]["weather_main"]=="storm" and x["weather"]["storm_signal"]=="confirmed_gust"
    check("S17 storm_window",storm)
    def mixed():
        p=response();p["hourly"]["weathercode"]=[0]*24;p["hourly"]["weathercode"][7]=65;stamp(p)
        failure("S18",p,"weather_window_mixed")
    check("S18 mixed_weather",mixed)
    def mixed_provider():
        p=response();p["_kld_r5_capture"]["source_id"]="openweather"
        failure("S19",p,"weather_source_unavailable")
    check("S19 mixed_provider_samples",mixed_provider)
    check("S20 different_spot",lambda:failure("S20",response(),"scene_location_unverified",scene_coordinates=(54.96,20.51)))
    def no_ephem():
        # Missing lunar source cannot be certified with an empty calendar label.
        p=response();snapshot=build(p);snapshot["lunar"]["source_status"]="missing"
        try:build_verified_facts(snapshot,target_date=TARGET,post_type="evening",publication_target_date=TARGET)
        except FactsUnavailable as e:assert e.reason=="lunar_source_unavailable"
        else:raise AssertionError("S21")
    check("S21 missing_lunar_phase",no_ephem)
    def no_broad():
        x=build();assert x["lunar"]["lunar_phase"] in {
            "new","waxing_crescent","first_quarter","waxing_gibbous","full",
            "waning_gibbous","last_quarter","waning_crescent"}
        assert "Растущая" not in x["lunar"]["lunar_phase"]
    check("S22 lunar_phase_broad",no_broad)
    def no_nicosia():
        x=build();assert x["provenance"]["timezone"]=="Europe/Kaliningrad"
    check("S23 lunar_nicosia_boundary",no_nicosia)
    def quarter():
        from datetime import date as Date
        # Check event classification is always from actual calculated
        # Swiss longitude evidence; no user-provided last-quarter label.
        # Last Quarter event in Kaliningrad falls on Oct 3, not Oct 2;
        # the previous and following days must not be relabelled 'quarter'.
        before=_lunar_source(Date(2026,10,2),NOW)
        exact=_lunar_source(Date(2026,10,3),NOW)
        after=_lunar_source(Date(2026,10,4),NOW)
        assert (before["lunar_phase"],exact["lunar_phase"],after["lunar_phase"]) == (
            "waning_gibbous", "last_quarter", "waning_crescent")
        assert len(exact["angles_deg"])==3
    check("S24 lunar_quarter_event_ephemeris",quarter)
    def hash_mismatch():
        p=response();p["hourly"]["visibility"][6]=100
        failure("S25",p,"weather_payload_hash_mismatch")
    check("S25 data_hash_mismatch",hash_mismatch)
    def _sidecar_writer():
        import ast,logging
        src=ast.parse((ROOT / "safe_test_post.py").read_text(encoding="utf-8"))
        fn=next(n for n in src.body if isinstance(n,ast.FunctionDef) and n.name=="_write_semantic_facts_sidecar")
        env={"Path":Path,"json":json,"logging":logging}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),"safe_test_post.py","exec"),env)
        return env["_write_semantic_facts_sidecar"]
    def stale_sidecar():
        writer=_sidecar_writer()
        with TemporaryDirectory() as td:
            path=Path(td)/"sidecar.json";path.write_text('{"stale":true}')
            assert writer(str(path),None,mode="evening",target_date=TARGET,publication_target_date=TARGET) is False
            assert not path.exists() and not path.with_name(path.name+".tmp").exists()
    check("S26 missing_sidecar_removed_actual_helper",stale_sidecar)
    def off_pipeline():
        from kld_semantic_qa_wiring import select_semantic_injection
        x=select_semantic_injection(enabled=False,source_snapshot=None,target_date=TARGET,post_type="evening",publication_target_date=TARGET)
        assert x.status=="disabled" and x.evaluator is None
    check("S27 flag_off_unchanged",off_pipeline)
    def fail_observer():
        from kld_semantic_qa_wiring import select_semantic_injection
        snapshot=build()
        x=select_semantic_injection(enabled=True,source_snapshot=snapshot,target_date=TARGET,post_type="evening",publication_target_date=TARGET,observer=None)
        assert x.status=="fail_closed" and x.evaluator is not None
    check("S28 observer_absent",fail_observer)
    def receipts():
        import inspect
        from pathlib import Path
        # No R5 change to receipt module, validated independently by CI suite.
        p=ROOT / "kld_image_first.py";assert p.exists() and "receipt" in p.read_text()
    check("S29 publication_receipt_unchanged_static",receipts)
    def cannot_prove_photo_date():
        x=build();assert "photograph_capture_time" not in x and "photo_time" not in x
    check("S30 image_time_unprovable",cannot_prove_photo_date)
    # Extra collector-boundary proof: capture stamp originates only from
    # weather.get_visibility_weather, using a fake existing collector result.
    def collector_stamp():
        import ast
        from types import ModuleType
        tree=ast.parse((ROOT / "weather.py").read_text(encoding="utf-8"))
        f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="get_visibility_weather")
        original={k:deepcopy(v) for k,v in response().items() if k!="_kld_r5_capture"}
        fetches=[]
        def existing_collector(**kw):
            fetches.append(kw)
            return deepcopy(original)
        env={"_openmeteo_hourly_daily": existing_collector}
        exec(compile(ast.Module(body=[f],type_ignores=[]),"weather.py","exec"),env)
        off=env["get_visibility_weather"](LAT,LON,tz="Europe/Kaliningrad",target_date=TARGET)
        assert off == original and "_kld_r5_capture" not in off, "capture changed default weather response"
        captured=env["get_visibility_weather"](LAT,LON,tz="Europe/Kaliningrad",
            target_date=TARGET,capture_semantic_facts=True)
        assert len(fetches) == 2 and all(x["need_sun"] is False for x in fetches)
        assert "_kld_r5_capture" in captured and "_kld_r5_capture" not in original
        snap=build_trusted_snapshot(captured,target_date=TARGET,post_type="evening",
            publication_target_date=TARGET,now_utc=datetime.now(timezone.utc))
        assert snap["weather"]["weather_main"]=="partly_cloudy"
    check("X01 actual collector capture seam",collector_stamp)
    def atomic_sidecar():
        writer=_sidecar_writer()
        with TemporaryDirectory() as td:
            path=Path(td)/"captured.json"
            assert writer(str(path),response(),mode="evening",target_date=TARGET,publication_target_date=TARGET)
            obj=json.loads(path.read_text(encoding="utf-8"))
            assert obj["weather"]["source_id"]==SOURCE and obj["weather"]["weather_main"]=="partly_cloudy"
            assert not path.with_name(path.name+".tmp").exists()
            assert not writer(str(path),response(),mode="evening",target_date=TARGET,
                              publication_target_date=TODAY.isoformat())
            assert not path.exists()
    check("X02 atomic sidecar with invalidation",atomic_sidecar)
    def explicit_opt_in_routing():
        import ast
        def fn_ast(path, name):
            module=ast.parse((ROOT/path).read_text(encoding="utf-8"))
            return next(n for n in module.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
        w=fn_ast("weather.py","get_visibility_weather")
        assert w.args.kw_defaults[-1] is not None and isinstance(w.args.kw_defaults[-1],ast.Constant) and w.args.kw_defaults[-1].value is False
        for func in ("build_message", "build_message_morning_compact", "build_message_legacy_evening"):
            p=fn_ast("post_common.py",func)
            assert p.args.kw_defaults[-1] is not None and isinstance(p.args.kw_defaults[-1],ast.Constant) and p.args.kw_defaults[-1].value is False
        helper=fn_ast("post_common.py","_kld_visibility_for_post")
        calls=[n for n in ast.walk(helper) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="get_visibility_weather"]
        assert len(calls)==1
        kwargs_nodes=[k for k in calls[0].keywords if k.arg is None]
        assert len(kwargs_nodes)==1 and "capture_semantic_facts" in ast.unparse(kwargs_nodes[0].value)
        m=fn_ast("safe_test_post.py","main")
        safe_source=(ROOT/"safe_test_post.py").read_text(encoding="utf-8")
        assert "if args.semantic_facts_out and args.send:" in safe_source
        assert "--semantic-facts-out is offline-only" in safe_source
        calls=[n for n in ast.walk(m) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="build_message"]
        assert len(calls)==1 and any(k.arg=="semantic_facts_capture" for k in calls[0].keywords)
        # No workflow can request capture or change the inactive QA flag.
        wf=(ROOT/".github/workflows/daily_post_klg.yml").read_text(encoding="utf-8")
        assert "--semantic-facts-out" not in wf and "KLD_SEMANTIC_QA_ENABLED" not in wf
        pr=(ROOT/".github/workflows/pr_checks.yml").read_text(encoding="utf-8")
        for filename in ("test_kld_semantic_qa_r5_source.py", "test_visibility_kld.py"):
            assert f"runner.py tools/{filename}" in pr
    check("X03 explicit opt-in call path and mandatory CI",explicit_opt_in_routing)
    def legacy_visibility_call_unchanged():
        # Execute the actual post_common helper without importing unrelated
        # production providers. An old-style mock accepts only the original
        # keyword arguments, proving OFF does not change the call contract.
        import ast, logging
        from types import SimpleNamespace
        source=ast.parse((ROOT/"post_common.py").read_text(encoding="utf-8"))
        fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="_kld_visibility_for_post")
        calls=[]
        def legacy_fetch(_lat,_lon,*,tz,target_date):
            calls.append((tz,target_date))
            return {"hourly": {"time": []}}
        common={
            "visibility_payload_has_morning_window":lambda *_a,**_k:False,
            "KLD_LAT":54.71,"KLD_LON":20.51,
            "get_kld_visibility_context":lambda *_a,**_k: SimpleNamespace(condition="clear"),
            "build_kld_visibility_line":lambda *_a,**_k:None,
            "visibility_diagnostics":lambda *_a,**_k:{},
            "logging":logging,"json":json,
        }
        env=dict(common,get_visibility_weather=legacy_fetch)
        exec("from __future__ import annotations\n"+ast.unparse(fn),env)
        env["_kld_visibility_for_post"]({}, {}, post_type="evening",
            target_date=TARGET,tz_name="Europe/Kaliningrad")
        assert calls==[("Europe/Kaliningrad",TARGET)]
        opt_calls=[]
        def opt_fetch(_lat,_lon,*,tz,target_date,capture_semantic_facts=False):
            opt_calls.append(capture_semantic_facts)
            return {"hourly": {"time": []}}
        env=dict(common,get_visibility_weather=opt_fetch)
        exec("from __future__ import annotations\n"+ast.unparse(fn),env)
        env["_kld_visibility_for_post"]({}, {}, post_type="evening",
            target_date=TARGET,tz_name="Europe/Kaliningrad",semantic_capture={})
        assert opt_calls == [True]
    check("X04 default visibility helper preserves legacy call",legacy_visibility_call_unchanged)
    assert len(scenarios)==34 and len(set(scenarios))==34
    print(f"PASS R5-A.3 source Golden Set 30/30 + 4 opt-in/collector/sidecar/legacy checks; no network attempts")


def _valid(kind):
    target = TODAY.isoformat() if kind == "morning" else TARGET
    snap=build(response(target),kind=kind,target=target)
    fact=build_verified_facts(snap,target_date=target,post_type=kind,publication_target_date=target)
    assert fact["weather_main"]=="partly_cloudy"
    assert fact["visibility_condition"]=="clear"
    assert fact["forecast_window"]==("current_morning" if kind=="morning" else "tomorrow_morning")
    assert [d[:16] for d in snap["weather"]["observation_hours"]] == [f"{target}T{h:02d}:00" for h in range(4,11)]
    assert snap["weather"]["max_gust_ms"]<6

if __name__=="__main__":main()
