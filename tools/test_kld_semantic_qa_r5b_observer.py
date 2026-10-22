"""R5-B1 offline image-only adapter, killable evaluator, fallback invariant probes."""
from __future__ import annotations

import json
import multiprocessing
import os
import socket
import sys
import tempfile
import time
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ATTEMPTS = []

def _deny(*args, **kwargs):
    ATTEMPTS.append((args, kwargs))
    raise AssertionError("No network in observer tests")

socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.create_connection = _deny

from kld_semantic_qa_observer import OBS_SCHEMA, wrap_image_only_observer
from kld_semantic_qa_evaluator import wrap_observer, fail_closed_observer
from kld_semantic_qa_facts import build_verified_facts
from kld_visual_semantic_qa import SCHEMA, evaluate_semantic_qa

DATE="2026-07-20"
IMAGE=b"raw-kld-r5b-image-bytes"


def facts(weather="cloudy", visibility="clear", post="evening"):
    window="tomorrow_morning" if post=="evening" else "current_morning"
    base={"target_date":DATE,"source_status":"verified","source_id":"independent-fixture",
          "retrieved_at_utc":"2026-07-19T06:00:00Z"}
    snap={"weather":dict(base,window=window,weather_main=weather,
                         storm_signal="confirmed_weather" if weather=="storm" else "none"),
          "visibility":dict(base,window=window,visibility_condition=visibility),
          "lunar":dict(base,lunar_phase="waxing_crescent")}
    return build_verified_facts(snap,target_date=DATE,post_type=post,publication_target_date=DATE)


def source(visible="cloudy", **overrides):
    obs={"moon_visibility":"not_visible", "moon_shape":"not_applicable", "dominant_moon":"no",
         "visibility_appearance":"normal", "weather_appearance":visible,
         "cloud_appearance":"overcast" if visible!="clear" else "clear",
         "precipitation_appearance": {"snow":"snow", "rain":"rain", "drizzle":"drizzle"}.get(visible,"none"),
         "wind_appearance":"calm", "sea_appearance":"calm"}
    obs.update(overrides)
    return {"observation_schema":OBS_SCHEMA,"model_or_evaluator_version":"offline-image-observer-1",
            "decision":"ACCEPT","reason_codes":[],"observations":obs}


def evaluate(worker, src=None, *, image=IMAGE, deadline=1, post="evening"):
    return evaluate_semantic_qa(worker,image,src or facts(),expected_target_date=DATE,
                                expected_post_type=post,timeout_s=deadline)


def test_independent_image_only_contract():
    seen=[]
    def observer(image):
        seen.append(image)
        return source()
    v=evaluate(wrap_observer(wrap_image_only_observer(observer)))
    assert v.accepted and v.image_sha256==sha256(IMAGE).hexdigest(),v
    assert seen==[]  # child process isolation; no parent observer side effects
    assert evaluate(wrap_image_only_observer(lambda image: json.dumps(source()))).accepted


def test_snow_rain_cloud_moon_wind_sea():
    for weather,visible,exp in (("snow","snow","ACCEPT"),("snow","clear","REJECT"),
                                ("clear","snow","REJECT"),("rain","rain","ACCEPT"),
                                ("rain","clear","REJECT"),("clear","cloudy","REJECT")):
        v=evaluate(wrap_image_only_observer(lambda image,visible=visible:source(visible)),facts(weather))
        assert v.decision==exp,(weather,visible,v)
    v=evaluate(wrap_image_only_observer(lambda i:source("storm",wind_appearance="calm",sea_appearance="calm")),facts("storm"))
    assert (v.decision,v.reason)==("REJECT","storm_conditions_not_shown"),v
    v=evaluate(wrap_image_only_observer(lambda i:source("storm",wind_appearance="strong",sea_appearance="rough")),facts("storm"))
    assert v.accepted,v
    v=evaluate(wrap_image_only_observer(lambda i:source("cloudy",moon_visibility="visible",moon_shape="crescent")),facts(post="morning"),post="morning")
    assert (v.decision,v.reason)==("REJECT","morning_moon_visible"),v


def test_reduced_visibility():
    v=evaluate(wrap_image_only_observer(lambda i:source()),facts(visibility="dense_fog"))
    assert (v.decision,v.reason)==("REJECT","reduced_visibility_not_shown"),v
    v=evaluate(wrap_image_only_observer(lambda i:source("fog",visibility_appearance="dense_fog")),facts(weather="fog",visibility="dense_fog"))
    assert v.accepted,v


def test_no_image_time_or_source_claims():
    cases=[]
    for fld in ("target_date","image_date","forecast_window","source_provenance",
                "source_id","latitude","weather_main","pictured_weather_window"):
        raw=source();raw[fld]="2026-07-20";cases.append(raw)
    raw=source();raw["observations"]["image_date"]="2026-07-20";cases.append(raw)
    for raw in cases:
        v=evaluate(wrap_image_only_observer(lambda i,raw=raw:raw))
        assert (v.decision,v.reason)==("UNKNOWN","qa_unavailable"),v
    bad=facts();bad.pop("source_provenance")
    v=evaluate(wrap_image_only_observer(lambda i:source()),bad)
    assert (v.decision,v.reason)==("UNKNOWN","image_window_unknown"),v
    v=evaluate(wrap_image_only_observer(lambda i:source()),dict(facts(),forecast_window="current_morning"))
    assert (v.decision,v.reason)==("UNKNOWN","image_window_unknown"),v


def test_malformed_and_contradictory_observer():
    bad_cases=["{not json", "ACCEPT", [], None, {},
               dict(source(),observation_schema="v0"),
               dict(source(),decision="accept"),
               dict(source(),reason_codes=["moon_phase_conflict"]),
               dict(source(),model_or_evaluator_version="")]
    for altered in [dict(moon_shape="banana"),dict(wind_appearance="unclear"),
                    dict(precipitation_appearance="rain"),dict(cloud_appearance="clear",weather_appearance="clear",precipitation_appearance="snow")]:
        raw=source();raw["observations"].update(altered);bad_cases.append(raw)
    for raw in bad_cases:
        v=evaluate(wrap_image_only_observer(lambda i,raw=raw:raw))
        assert v.decision=="UNKNOWN",(raw,v)


def test_wrong_hash_legacy_still_rejected():
    def fake(image,f):
        return {"qa_schema_version":SCHEMA,"model_or_evaluator_version":"fixture",
                "raw_image_sha256":"0"*64,"decision":"ACCEPT","reason_codes":[],
                "observations":{"moon_visibility":"not_visible","moon_shape":"not_applicable",
                                "dominant_moon":"no","visibility_appearance":"normal",
                                "weather_appearance":"clear"}}
    v=evaluate(wrap_observer(fake))
    assert (v.decision,v.reason)==("UNKNOWN","image_hash_mismatch"),v
    assert (evaluate(fail_closed_observer).decision)=="UNKNOWN"


def test_hard_timeout_no_late_accept_or_leak():
    if os.name!="posix" or "fork" not in multiprocessing.get_all_start_methods():
        assert evaluate(wrap_image_only_observer(lambda i:source())).decision=="UNKNOWN"
        return
    with tempfile.TemporaryDirectory() as tmp:
        started=Path(tmp)/"started.txt"; late=Path(tmp)/"late.txt"
        def hang(image):
            started.write_text(str(os.getpid()))
            time.sleep(1.1)
            late.write_text("LATE_ACCEPT")
            return source()
        start=time.monotonic()
        v=evaluate(wrap_observer(wrap_image_only_observer(hang)),deadline=.12)
        elapsed=time.monotonic()-start
        assert (v.decision,v.reason)==("UNKNOWN","qa_timeout"),v
        assert elapsed<.9,elapsed
        assert started.is_file()
        pid=int(started.read_text())
        time.sleep(.16)
        assert not late.exists(),"late observer survived its hard deadline"
        try:
            os.kill(pid,0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("timed-out evaluator process survived")
        assert not multiprocessing.active_children()


def test_crash_and_bad_deadline():
    def crash(image):
        raise RuntimeError("fixture crash")
    assert evaluate(wrap_image_only_observer(crash)).decision=="UNKNOWN"
    for deadline in (0,-1,float("nan"),float("inf"),50):
        assert evaluate(wrap_image_only_observer(lambda i:source()),deadline=deadline).decision=="UNKNOWN"


def test_legacy_evaluator_also_has_hard_timeout():
    """R4 callbacks no longer escape via ThreadPoolExecutor."""
    with tempfile.TemporaryDirectory() as tmp:
        started=Path(tmp)/"started"; late=Path(tmp)/"late"
        def legacy(image, src):
            started.write_text(str(os.getpid()))
            time.sleep(1.2)
            late.write_text("INVALID_LATE_ACCEPT")
            return {"qa_schema_version":SCHEMA,
                    "model_or_evaluator_version":"late-legacy",
                    "raw_image_sha256":sha256(image).hexdigest(),
                    "decision":"ACCEPT","reason_codes":[],
                    "observations":{"moon_visibility":"not_visible", "moon_shape":"not_applicable",
                                    "dominant_moon":"no", "visibility_appearance":"normal",
                                    "weather_appearance":"cloudy"}}
        start=time.monotonic()
        verdict=evaluate(legacy,deadline=.10)
        assert (verdict.decision,verdict.reason)==("UNKNOWN","qa_timeout"),verdict
        assert time.monotonic()-start < .9
        assert started.is_file(),"legacy callable was not isolated"
        time.sleep(.12)
        assert not late.exists(),"legacy call outlived deadline"
        try: os.kill(int(started.read_text()),0)
        except ProcessLookupError: pass
        else: raise AssertionError("legacy child survived timeout")
        assert not multiprocessing.active_children()


def test_legacy_wrapped_timeout_and_crash():
    def never(image,facts):
        while True: time.sleep(.1)
    verdict=evaluate(wrap_observer(never),deadline=.10)
    assert (verdict.decision,verdict.reason)==("UNKNOWN","qa_timeout"),verdict
    assert not multiprocessing.active_children()
    def crash(image,facts): raise RuntimeError("failure")
    verdict=evaluate(wrap_observer(crash),deadline=.8)
    assert (verdict.decision,verdict.reason)==("UNKNOWN","qa_unavailable"),verdict


def test_worker_process_group_descendants_are_reaped():
    # Even a worker that returns early must not leave a forked child running.
    if os.name != "posix" or not hasattr(os, "fork"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        late=Path(tmp)/"late-descendant"
        def observer(image):
            pid=os.fork()
            if pid == 0:
                time.sleep(.55)
                late.write_text("ESCAPE")
                os._exit(0)
            return source()
        verdict=evaluate(wrap_image_only_observer(observer),deadline=.8)
        assert verdict.accepted,verdict
        time.sleep(.65)
        assert not late.exists(),"worker descendant survived its process group"


def test_bounded_process_unavailable_fails_closed():
    # Deliberately simulate platform/pool unavailable without falling back
    # to threads; source inputs stay unchanged.
    from unittest.mock import patch
    with patch("kld_visual_semantic_qa.threading.active_count",return_value=2):
        v=evaluate(wrap_observer(lambda image,facts: source()),deadline=.2)
    assert (v.decision,v.reason)==("UNKNOWN","qa_unavailable"),v


def test_bad_large_result_and_bounded_ipc():
    assert evaluate(wrap_image_only_observer(lambda i:"x"*100_000)).decision=="UNKNOWN"


def test_network_ledger_empty():
    assert ATTEMPTS==[],ATTEMPTS

TESTS=[test_independent_image_only_contract,test_snow_rain_cloud_moon_wind_sea,
       test_reduced_visibility,test_no_image_time_or_source_claims,
       test_malformed_and_contradictory_observer,test_wrong_hash_legacy_still_rejected,
       test_hard_timeout_no_late_accept_or_leak,test_crash_and_bad_deadline,
       test_bad_large_result_and_bounded_ipc,
       test_legacy_evaluator_also_has_hard_timeout,
       test_legacy_wrapped_timeout_and_crash,
       test_worker_process_group_descendants_are_reaped,
       test_bounded_process_unavailable_fails_closed,
       test_network_ledger_empty]

if __name__=="__main__":
    for test in TESTS:
        test()
        print("PASS:",test.__name__)
    print(f"PASS: {len(TESTS)} KLD R5-B1 observer/hard-timeout cases; network attempts 0")
