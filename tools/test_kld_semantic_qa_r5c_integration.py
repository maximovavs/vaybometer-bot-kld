"""R5-C1 OFFLINE-ONLY direct-collector to image-only observer integration.

No production CLI changes, provider calls, Telegram, network or sidecar IO.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import timedelta
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from hashlib import sha256

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ATTEMPTS=[]
def deny(*args,**kwargs):
    ATTEMPTS.append("network")
    raise AssertionError("R5-C1 offline forbids network")
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.create_connection=deny

from kld_semantic_qa_source import canonical_sha256
from kld_semantic_qa_wiring import select_offline_collector_injection
from kld_visual_semantic_qa import evaluate_semantic_qa
from test_kld_semantic_qa_r5_source import NOW, TARGET, response, stamp
from test_kld_semantic_qa_r5b_observer import source as observation

IMAGE=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGOMyqtlIAUwkaR6VMOohiGlAQAU7wFlSaaaRwAAAABJRU5ErkJggg==")
RUN="kld-offline-collector-0001"


def cloud_response():
    p=response()
    p["hourly"]["weathercode"]=[3]*24  # WMO 3: overcast, not partly cloudy
    stamp(p)
    return p


def make(*, data=None, date=TARGET, post="evening", expected_date=None,
         run=RUN, expect_run=RUN, expected_digest=None, enabled=True,
         observer=None, now=NOW):
    raw=data if data is not None else cloud_response()
    trusted_digest=expected_digest or cloud_response()["_kld_r5_capture"]["raw_payload_sha256"]
    return select_offline_collector_injection(
        enabled=enabled, collector_response=raw, collector_run_id=run,
        expected_run_id=expect_run, expected_raw_sha256=trusted_digest,
        target_date=date, post_type=post,
        publication_target_date=expected_date if expected_date is not None else date,
        image_observer=observer if observer is not None else (lambda i: observation()),
        now_utc=now,
    )


def decide(injection, *, post="evening", date=TARGET, timeout=1.0, image=IMAGE):
    assert injection.evaluator is not None
    return evaluate_semantic_qa(
        injection.evaluator,image,injection.facts or {},
        expected_target_date=date,expected_post_type=post,timeout_s=timeout,
    )


def check_valid_direct_collector_image():
    j=make()
    assert (j.status,j.reason)==("ready_offline_injection",""),j
    verdict=decide(j)
    assert (verdict.decision,verdict.reason)==("ACCEPT","observed_consistent"),verdict
    assert verdict.image_sha256==sha256(IMAGE).hexdigest()


def check_flag_off_never_calls_observer():
    j=make(enabled=False,observer=lambda i:(_ for _ in ()).throw(AssertionError("ran")))
    assert j.status=="disabled" and j.evaluator is None and j.facts is None


def check_no_unsigned_sidecar_trust():
    j=make(data="/tmp/old_snapshot.json")
    assert j.status=="fail_closed" and j.reason=="collector_source_unavailable",j
    assert decide(j).decision=="UNKNOWN"


def check_missing_and_tampered_sources():
    raw=cloud_response()
    for data,reason in (({},"collector_digest_mismatch"),
                        (dict(raw,_kld_r5_capture=None),"collector_digest_mismatch")):
        j=make(data=data)
        assert j.status=="fail_closed" and j.reason==reason,(j,reason)
    raw=cloud_response();raw["hourly"]["visibility"][5]=100.0
    j=make(data=raw)
    assert (j.status,j.reason)==("fail_closed","weather_payload_hash_mismatch"),j
    assert decide(j).decision=="UNKNOWN"


def check_stale_and_future():
    for delta in (-3,3):
        raw=cloud_response();stamp(raw,retrieved=NOW+timedelta(hours=delta))
        j=make(data=raw,expected_digest=raw["_kld_r5_capture"]["raw_payload_sha256"])
        assert j.status=="fail_closed" and j.reason=="weather_source_stale_or_future",j


def check_mixed_provider():
    raw=cloud_response();raw["_kld_r5_capture"]["source_id"]="openweather"
    j=make(data=raw)
    assert (j.status,j.reason)==("fail_closed","weather_source_unavailable"),j


def check_cross_run_and_digest():
    wrong=make(run="older-run")
    assert (wrong.status,wrong.reason)==("fail_closed","collector_run_mismatch"),wrong
    assert decide(wrong).decision=="UNKNOWN"
    wrong=make(expected_digest="f"*64)
    assert (wrong.status,wrong.reason)==("fail_closed","collector_digest_mismatch"),wrong
    assert decide(wrong).decision=="UNKNOWN"
    empty=make(expect_run="")
    assert (empty.status,empty.reason)==("fail_closed","collector_run_mismatch")


def check_target_and_post_temporal_guards():
    mismatch=make(expected_date="2099-01-01")
    assert (mismatch.status,mismatch.reason)==("fail_closed","publication_target_mismatch"),mismatch
    mismatch=make(post="morning")
    assert (mismatch.status,mismatch.reason)==("fail_closed","weather_publication_window_mismatch"),mismatch
    mismatch=make(date="2099-01-01",expected_date="2099-01-01")
    assert mismatch.status=="fail_closed",mismatch


def check_missing_evaluator():
    j=select_offline_collector_injection(enabled=True,collector_response=cloud_response(),
        collector_run_id=RUN,expected_run_id=RUN,
        expected_raw_sha256=cloud_response()["_kld_r5_capture"]["raw_payload_sha256"],
        target_date=TARGET,post_type="evening",publication_target_date=TARGET,
        image_observer=None,now_utc=NOW)
    assert (j.status,j.reason)==("fail_closed","evaluator_unavailable"),j
    assert decide(j).decision=="UNKNOWN"


def check_visual_safety_veto():
    # WMO=3 is confirmed overcast, not partial cloud cover. A contradictory
    # clear/rain/snow/storm scene must never get ACCEPT from a cloudy forecast.
    unsafe_accepts=[]
    for visible in ("clear", "rain", "snow", "storm"):
        j=make(observer=lambda i,visible=visible:observation(
            visible,wind_appearance="strong" if visible=="storm" else "calm",
            sea_appearance="rough" if visible=="storm" else "calm"))
        verdict=decide(j)
        if verdict.decision=="ACCEPT":
            unsafe_accepts.append(visible)
    assert not unsafe_accepts, ("WMO 3 overcast erroneously ACCEPTs",unsafe_accepts)
    j=make(observer=lambda i:observation("cloudy"))
    assert decide(j).accepted
    j=make(observer=lambda i:observation("cloudy",moon_visibility="visible",moon_shape="crescent"))
    # Moon phase comes from source; never unconditionally accept visible Moon.
    assert decide(j).decision in {"REJECT","UNKNOWN"}


def check_bad_observations_crash():
    for raw in ("{bad-json",{"decision":"ACCEPT"},None):
        j=make(observer=lambda i,raw=raw:raw)
        assert decide(j).decision=="UNKNOWN"
    j=make(observer=lambda i:os._exit(3))
    assert decide(j).decision=="UNKNOWN"


def check_timeout_and_late_accept():
    def slow(i):
        time.sleep(.55)
        return observation()
    j=make(observer=slow)
    t=time.monotonic();v=decide(j,timeout=.1)
    assert (v.decision,v.reason)==("UNKNOWN","qa_timeout"),v
    assert time.monotonic()-t<.7
    assert not multiprocessing.active_children()
    j=make(observer=slow)
    assert decide(j,timeout=.35).decision=="UNKNOWN"
    assert not multiprocessing.active_children()


def check_mismatched_sha_and_image_binding():
    j=make()
    assert decide(j,image=IMAGE+b"x").image_sha256==sha256(IMAGE+b"x").hexdigest()
    assert decide(j,image=IMAGE+b"x").accepted  # hash bound to exact incoming bytes
    # A forged digest in an independent observation is prohibited by strict
    # keys; the normaliser produces SHA itself from the raw image.
    j=make(observer=lambda i:dict(observation(),raw_image_sha256="0"*64))
    assert decide(j).decision=="UNKNOWN"


def check_multithread_fork_fails_closed():
    e=threading.Event();t=threading.Thread(target=e.wait,daemon=True)
    t.start()
    try:
        assert threading.active_count()>1
        j=make();v=decide(j)
        assert (v.decision,v.reason)==("UNKNOWN","qa_unavailable"),v
        assert not multiprocessing.active_children()
    finally:
        e.set();t.join(timeout=1)
    assert not t.is_alive()


def spawn_probe_child(conn):
    try:conn.send("spawned")
    finally:conn.close()


def check_spawn_alternative_probe():
    # Feasibility only, not a drop-in worker replacement: production injected
    # closures/lambdas are not picklable under spawn. No safety change here.
    if "spawn" not in multiprocessing.get_all_start_methods():return
    import importlib
    module=importlib.import_module("tools.test_kld_semantic_qa_r5c_integration")
    e=threading.Event();t=threading.Thread(target=e.wait,daemon=True);t.start()
    r,w=multiprocessing.get_context("spawn").Pipe(duplex=False)
    p=multiprocessing.get_context("spawn").Process(target=module.spawn_probe_child,args=(w,))
    try:
        p.start();w.close();assert r.poll(4),"spawn worker did not respond"
        assert r.recv()=="spawned"
        p.join(timeout=2);assert p.exitcode==0,p.exitcode
    finally:
        if p.pid is not None and p.is_alive():p.kill();p.join(timeout=1)
        r.close();w.close();e.set();t.join(timeout=1)
    assert not multiprocessing.active_children()


def check_cli_remains_unwired_and_read_only():
    image_src=(ROOT/"tools/kld_visual_fixture_image.py").read_text(encoding="utf-8")
    wf=(ROOT/".github/workflows/daily_post_klg.yml").read_text(encoding="utf-8")
    assert "source_snapshot=None" in image_src and "observer=None" in image_src
    assert "KLD_SEMANTIC_QA_ENABLED" in image_src
    assert "if semantic_enabled:" in image_src
    assert "--semantic-facts-out" in wf and "--semantic-source-file" in wf
    assert "prepare_run_context(os.environ)" in wf
    assert not ATTEMPTS



def check_verified_weather_visual_matrix():
    # Real R5-A collector facts for each source category, not prompt labels.
    # Every case must either be positively grounded, be rejected for a
    # contradiction, or remain UNKNOWN when the image lacks conclusive cues.
    codes={"clear":0,"cloudy":3,"rain":61,"snow":71,"storm":95,
           "partly_cloudy":2,"drizzle":51,"fog":45}
    matrix={
        "clear":  {"clear":"ACCEPT","cloudy":"REJECT","rain":"REJECT",
                   "snow":"REJECT","storm":"REJECT"},
        "cloudy": {"clear":"REJECT","cloudy":"ACCEPT","rain":"REJECT",
                   "snow":"REJECT","storm":"REJECT"},
        "rain":   {"clear":"REJECT","cloudy":"UNKNOWN","rain":"ACCEPT",
                   "snow":"REJECT","storm":"REJECT"},
        "snow":   {"clear":"REJECT","cloudy":"UNKNOWN","rain":"REJECT",
                   "snow":"ACCEPT","storm":"REJECT"},
        "storm":  {"clear":"REJECT","cloudy":"REJECT","rain":"REJECT",
                   "snow":"REJECT","storm":"ACCEPT"},
    }
    cases=0
    for forecast,visible_results in matrix.items():
        data=response()
        data["hourly"]["weathercode"]=[codes[forecast]]*24
        stamp(data)
        digest=data["_kld_r5_capture"]["raw_payload_sha256"]
        for appearance,expected in visible_results.items():
            # All explicit storm appearances carry strong wind + rough sea;
            # non-storm appearances do not claim storm-level wind or seas.
            # For the storm source, calm visuals are a decisive contradiction.
            opts={"wind_appearance":"strong","sea_appearance":"rough"} if appearance=="storm" else {}
            j=make(data=data, expected_digest=digest,
                   observer=lambda image,appearance=appearance,opts=opts:
                            observation(appearance,**opts))
            assert j.status=="ready_offline_injection",(forecast,appearance,j)
            assert j.facts["weather_main"]==forecast,(forecast,j.facts["weather_main"])
            verdict=decide(j)
            assert verdict.decision==expected,(forecast,appearance,expected,verdict)
            cases+=1
    # Genuinely uncertain appearance must never be accepted, including for
    # verified storms. Partial cloud cover cannot prove perfect overcast/clear.
    unknowns=[("partly_cloudy","clear"),("partly_cloudy","cloudy"),
              ("rain","drizzle"),("drizzle","rain"),
              ("storm","other"),("cloudy","other")]
    for forecast,appearance in unknowns:
        data=response();data["hourly"]["weathercode"]=[codes[forecast]]*24;stamp(data)
        opts={"wind_appearance":"strong","sea_appearance":"rough"} if forecast=="storm" else {}
        j=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
               observer=lambda i,appearance=appearance,opts=opts:observation(appearance,**opts))
        assert (decide(j).decision)=="UNKNOWN",(forecast,appearance,decide(j))
        cases+=1
    # A weather-derived WMO 95 thunderstorm cannot be downgraded into a
    # wind-only storm just because its image shows wind and rough seas.
    data=response();data["hourly"]["weathercode"]=[95]*24;stamp(data)
    j=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
           observer=lambda i:observation("clear",wind_appearance="strong",sea_appearance="rough"))
    assert j.facts["storm_signal"]=="confirmed_weather"
    assert decide(j).decision=="REJECT","Confirmed WMO thunderstorm accepted clear sky"
    print(f"PASS source-derived weather matrix: {cases} fixed scenarios + thunderstorm guard")


def check_thunderstorm_vs_unresolved_windstorm():
    # WMO thunder codes are factual evidence, not generic strong-wind labels.
    # When gusts dominate the producer, the v2 facts do not retain enough
    # provenance to rule out a simultaneous thunder code: UNKNOWN, not ACCEPT.
    for code in (95,96,99):
        data=response();data["hourly"]["weathercode"]=[code]*24;stamp(data)
        j=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
               observer=lambda i:observation("clear",wind_appearance="strong",sea_appearance="rough"))
        assert (j.facts["weather_main"],j.facts["storm_signal"])==("storm","confirmed_weather")
        v=decide(j)
        assert (v.decision,v.reason)==("REJECT","confirmed_storm_weather_not_shown"),(code,v)
        # Storm depicted with strong wind and rough sea is still compatible.
        j=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
               observer=lambda i:observation("storm",wind_appearance="strong",sea_appearance="rough"))
        assert decide(j).accepted,(code,decide(j))
    for code in (3,95):
        data=response();data["hourly"]["weathercode"]=[code]*24
        data["hourly"]["wind_gusts_10m"]=[72.0]*24  # 20 m/s, guaranteed gust-triggered
        stamp(data)
        clear=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
                   observer=lambda i:observation("clear",wind_appearance="strong",sea_appearance="rough"))
        assert (clear.facts["weather_main"],clear.facts["storm_signal"])==("storm","confirmed_gust")
        v=decide(clear)
        assert (v.decision,v.reason)==("UNKNOWN","storm_origin_unresolved"),(code,v)
        visual_storm=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
                          observer=lambda i:observation("storm",wind_appearance="strong",sea_appearance="rough"))
        assert decide(visual_storm).accepted,code
    # Warnings without an authenticated type cannot prove wind-only weather.
    j=make(data=data,expected_digest=data["_kld_r5_capture"]["raw_payload_sha256"],
           observer=lambda i:observation("clear",wind_appearance="strong",sea_appearance="rough"))
    facts=dict(j.facts,storm_signal="confirmed_warning")
    v=evaluate_semantic_qa(j.evaluator,IMAGE,facts,expected_target_date=TARGET,
                            expected_post_type="evening",timeout_s=1.0)
    assert (v.decision,v.reason)==("UNKNOWN","storm_origin_unresolved"),v


TESTS=[
    check_valid_direct_collector_image,check_flag_off_never_calls_observer,
    check_no_unsigned_sidecar_trust,check_missing_and_tampered_sources,
    check_stale_and_future,check_mixed_provider,check_cross_run_and_digest,
    check_target_and_post_temporal_guards,check_missing_evaluator,
    check_visual_safety_veto,check_bad_observations_crash,
    check_timeout_and_late_accept,check_mismatched_sha_and_image_binding,
    check_multithread_fork_fails_closed,check_spawn_alternative_probe,
    check_cli_remains_unwired_and_read_only,check_verified_weather_visual_matrix,
    check_thunderstorm_vs_unresolved_windstorm,
]
if __name__=="__main__":
    for test in TESTS:
        test();print("PASS",test.__name__,flush=True)
    assert not ATTEMPTS
    print(f"PASS R5-C1 offline source-to-observer integration: {len(TESTS)}/{len(TESTS)}; network=0")
