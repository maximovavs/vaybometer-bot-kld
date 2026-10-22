"""R4 contract probes, deterministic, offline; no provider/Telegram interactions."""
from __future__ import annotations
import hashlib
import sys
import json
import socket
from pathlib import Path
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "tools" else Path(__file__).resolve().parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from kld_semantic_qa_facts import build_verified_facts, FactsUnavailable
from kld_semantic_qa_evaluator import apply_weather_veto, wrap_observer, fail_closed_observer
from kld_visual_semantic_qa import evaluate_semantic_qa, SCHEMA


def forbid_network(*a, **kw):
    raise AssertionError('unexpected external network')
socket.socket.connect = forbid_network
socket.socket.connect_ex = forbid_network
socket.create_connection = forbid_network

IMAGE=b'R4-raw-image-original-before-overlay'
TARGET='2026-10-23'


def make_snapshot(*, post_type='evening', weather='cloudy', visibility='reduced_visibility', lunar='waxing_crescent', storm='none'):
    window='tomorrow_morning' if post_type=='evening' else 'current_morning'
    common={'target_date':TARGET,'source_status':'verified','source_id':'test-fixture','retrieved_at_utc':'2026-10-22T06:00:00Z'}
    return {'weather':dict(common,window=window,weather_main=weather,storm_signal=storm),
            'visibility':dict(common,window=window,visibility_condition=visibility),
            'lunar':dict(common,lunar_phase=lunar)}


def observations(facts):
    window=facts['forecast_window']
    return dict(moon_visibility='not_visible',moon_shape='not_applicable',dominant_moon='no',
                visibility_appearance='mist' if facts['visibility_condition']!='clear' else 'normal',
                weather_appearance='cloudy',pictured_weather_window=window,pictured_visibility_window=window,
                wind_appearance='calm',sea_appearance='calm')


def response(image, obs):
    return {'qa_schema_version':SCHEMA,'model_or_evaluator_version':'offline-independent-r4',
            'raw_image_sha256':hashlib.sha256(image).hexdigest(), 'decision':'ACCEPT',
            'reason_codes':[], 'observations':obs}


def test_case(caseid, facts, obs, expected):
    fake=wrap_observer(lambda image, src: response(image,obs))
    verdict=evaluate_semantic_qa(fake,IMAGE,facts,expected_target_date=TARGET,
                                expected_post_type=facts['post_type'])
    got=(verdict.decision,verdict.reason)
    assert got==expected, (caseid,got,expected)


def go():
    data=json.loads((Path(__file__).resolve().parent / 'fixtures' / 'kld_semantic_qa_r3_golden_set.json').read_text(encoding='utf-8'))
    base=build_verified_facts(make_snapshot(),target_date=TARGET,post_type='evening',publication_target_date=TARGET)
    count=0
    for case in data['cases']:
        if not case['id'].startswith('R3-') or int(case['id'][-2:])>9:continue
        c=case['id']; b=deepcopy(base); b.update(case['facts'])
        obs=observations(b);obs.update(case['observations'])
        if c=='R3-01': obs.update(pictured_weather_window='tomorrow_morning',pictured_visibility_window='tomorrow_morning')
        if c=='R3-02': obs.update(pictured_weather_window='tomorrow_morning')
        if c=='R3-03': obs.update(pictured_weather_window='tomorrow_morning')
        if c=='R3-04': obs.update(pictured_weather_window='current_morning')
        if c=='R3-05':
            snap=make_snapshot();snap['visibility']['source_status']='missing'
            try:build_verified_facts(snap,target_date=TARGET,post_type='evening',publication_target_date=TARGET)
            except FactsUnavailable as exc: assert exc.reason=='visibility_source_unavailable'
            else:raise AssertionError(c)
            count+=1;continue
        if c=='R3-06':
            snap=make_snapshot();snap['lunar']['source_status']='missing'
            try:build_verified_facts(snap,target_date=TARGET,post_type='evening',publication_target_date=TARGET)
            except FactsUnavailable as exc: assert exc.reason=='lunar_source_unavailable'
            else:raise AssertionError(c)
            count+=1;continue
        if c=='R3-07':
            try:build_verified_facts(make_snapshot(),target_date=TARGET,post_type='evening',publication_target_date='2026-10-24')
            except FactsUnavailable as exc: assert exc.reason=='publication_target_mismatch'
            else:raise AssertionError(c)
            count+=1;continue
        if c=='R3-08':obs['pictured_visibility_window']='unknown'
        if c=='R3-09':obs.update(pictured_weather_window='tomorrow_morning',pictured_visibility_window='tomorrow_morning')
        b['facts_version']='kld_visual_semantic_facts_v2'; b['publication_target_date']=TARGET
        if c in {'R3-01','R3-09'}:b['storm_signal']='confirmed_gust'
        if c in {'R3-02','R3-03'}:b['weather_window']='tomorrow_morning'
        if c=='R3-04':b['weather_window']='tomorrow_morning'
        if c=='R3-01':obs['wind_appearance']='calm';obs['sea_appearance']='calm'
        if c=='R3-09':obs['wind_appearance']='unclear'
        expected=case['expected'];test_case(c,b,obs,(expected['decision'],expected['reason']))
        count+=1
    # 12 extra cases: upstream veto, moon, hash, weather and sourcing invariants
    checks=[]
    def check(variations, observations_diff, exp):
        f=dict(base, **variations);o=dict(observations(f),**observations_diff)
        test_case('extra-'+str(len(checks)),f,o,exp);checks.append(True)
    check({}, {}, ('ACCEPT','observed_consistent'))
    check({}, {'moon_visibility':'visible','moon_shape':'full_disk'},('REJECT','moon_phase_conflict'))
    check({'post_type':'morning','forecast_window':'current_morning','weather_window':'current_morning','visibility_forecast_window':'current_morning'},
          {'moon_visibility':'visible','moon_shape':'crescent','pictured_weather_window':'current_morning','pictured_visibility_window':'current_morning'},('REJECT','morning_moon_visible'))
    check({'visibility_condition':'clear','visibility_forecast_window':'none'}, {'visibility_appearance':'dense_fog'}, ('REJECT','invented_reduced_visibility'))
    check({'visibility_condition':'clear','visibility_forecast_window':'none'}, {'visibility_appearance':'mist'}, ('REJECT','invented_reduced_visibility'))
    check({'weather_main':'rain'}, {'weather_appearance':'cloudy'}, ('UNKNOWN','precipitation_visual_unknown'))
    check({'weather_main':'snow'}, {'weather_appearance':'clear'}, ('REJECT','forecast_snow_not_shown'))
    check({'weather_main':'clear','visibility_condition':'clear','visibility_forecast_window':'none'}, {'weather_appearance':'cloudy','visibility_appearance':'normal'}, ('REJECT','unexpected_overcast'))
    check({'storm_signal':'confirmed_gust','weather_main':'storm'}, {'weather_appearance':'other','wind_appearance':'strong','sea_appearance':'rough'}, ('ACCEPT','observed_consistent'))
    check({'storm_signal':'confirmed_gust','weather_main':'storm'}, {'weather_appearance':'other','wind_appearance':'unclear'}, ('UNKNOWN','storm_visual_unknown'))
    check({}, {'pictured_weather_window':'unknown'}, ('UNKNOWN','image_window_unknown'))
    check({}, {'pictured_visibility_window':'current_morning'}, ('UNKNOWN','visibility_window_mismatch'))
    # Missing valid schema sources, dates and raw-hash integrity are upstream fail closed
    for target_field in ('weather','lunar','visibility'):
        snapshot=make_snapshot();snapshot[target_field]['source_id']=''
        try:build_verified_facts(snapshot,target_date=TARGET,post_type='evening',publication_target_date=TARGET)
        except FactsUnavailable:pass
        else:raise AssertionError(target_field)
    fake=wrap_observer(lambda image,facts: dict(response(image,observations(facts)),raw_image_sha256='0'*64))
    assert evaluate_semantic_qa(fake,IMAGE,base,expected_target_date=TARGET,
                                expected_post_type='evening').reason=='image_hash_mismatch'
    fail=evaluate_semantic_qa(fail_closed_observer,IMAGE,base,expected_target_date=TARGET,expected_post_type='evening')
    assert (fail.decision,fail.reason)==('UNKNOWN','qa_unavailable')
    print(f'PASS: R3 post_wiring scalar policy cases {count}/9; new R4 policy matrix {len(checks)}/12; plus 5 sourcing/hash/failclosed guards. OFFLINE network attempted: 0')
if __name__=='__main__':go()
