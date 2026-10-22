import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "tools" else Path(__file__).resolve().parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import hashlib
import socket
from kld_semantic_qa_evaluator import fail_closed_observer
from kld_semantic_qa_wiring import select_semantic_injection
from kld_visual_semantic_qa import evaluate_semantic_qa, SCHEMA
from test_kld_semantic_qa_r4_offline import make_snapshot, TARGET, IMAGE, observations, response

socket.socket.connect=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('network'))
checks=0

def check(flag, snapshot, observer, expected_status, expected_reason):
    global checks
    injection=select_semantic_injection(enabled=flag,source_snapshot=snapshot,target_date=TARGET,
        post_type='evening',publication_target_date=TARGET,observer=observer)
    assert (injection.status,injection.reason)==(expected_status,expected_reason),injection
    checks+=1
    return injection

j=check(False,None,None,'disabled','flag_off');assert j.evaluator is None
j=check(True,None,None,'fail_closed','weather_source_unavailable');assert j.evaluator is not None
v=evaluate_semantic_qa(j.evaluator,IMAGE,j.facts,expected_target_date=TARGET,expected_post_type='evening')
assert (v.decision,v.reason)==('UNKNOWN','qa_unavailable');checks+=1
snap=make_snapshot()
j=check(True,snap,None,'fail_closed','evaluator_unavailable')
# M3: verified facts without an evaluator must still inject the fail-closed
# observer. A None evaluator would make execute_image_delivery skip QA and
# publish the AI candidate; evaluate_semantic_qa alone cannot tell them apart.
assert j.facts is not None and j.evaluator is fail_closed_observer, j
v=evaluate_semantic_qa(j.evaluator,IMAGE,j.facts,expected_target_date=TARGET,expected_post_type='evening')
assert (v.decision,v.reason)==('UNKNOWN','qa_unavailable');checks+=1

def fake(image,facts):return response(image,observations(facts))
j=check(True,snap,fake,'ready_offline_injection','')
v=evaluate_semantic_qa(j.evaluator,IMAGE,j.facts,expected_target_date=TARGET,expected_post_type='evening')
assert (v.decision,v.reason)==('ACCEPT','observed_consistent');checks+=1
snap_bad=make_snapshot();snap_bad['weather']['target_date']='2026-10-24'
j=check(True,snap_bad,fake,'fail_closed','weather_target_mismatch')
assert j.evaluator is not None;checks+=1
print(f'PASS R4 injection selection: {checks}/9 checks (no providers; no Telegram; no network)')
