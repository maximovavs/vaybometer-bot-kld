"""Offline H2-B1 canonical codec regressions; imports only pure stdlib modules."""
from __future__ import annotations
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pathlib
import sys
import pytest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from geomagnetic_history import OBSERVED_SOURCE,parse_observed_3h_history
from geomagnetic_reconciliation import GeomagneticState,reconcile,record_delivery,OutboxEntry,IntervalRecord
from geomagnetic_state_codec import CodecError,encode_state,decode_state,validate_transition,MAX_SNAPSHOT_BYTES
T=datetime(2026,10,12,12,tzinfo=timezone.utc)


def mk_state(kp=6.67):
    rows=[{'time_tag':'2026-10-12T09:00:00','Kp':kp}]
    h=parse_observed_3h_history(rows,source=OBSERVED_SOURCE,as_of_utc=T)
    return reconcile(GeomagneticState(),h,expected_version=0,as_of_utc=T).state


def resign(data:bytes,edit):
    doc=json.loads(data)
    edit(doc)
    packed=json.dumps(doc['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    doc['integrity']['digest']=hashlib.sha256(packed).hexdigest()
    return json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def test_empty_roundtrip():
    s=GeomagneticState()
    assert decode_state(encode_state(s,environment='test'),expected_environment='test')==s


def test_storm_roundtrip_immutability_and_unicode():
    s=mk_state()
    assert decode_state(encode_state(s,environment='production'),expected_environment='production')==s


def test_digest_determinism():
    s=mk_state()
    assert encode_state(s,environment='test')==encode_state(s,environment='test')


@pytest.mark.parametrize('kp',[0.0,0.01,1.3333333333333,4.999999999,5.0,6.67,7.123456789012345,9.0,-0.0])
def test_numeric_roundtrip_uses_unrounded_float(kp):
    if kp==0.0:
        obj=GeomagneticState(intervals=(IntervalRecord(T-timedelta(hours=3),(float(kp),),'OBSERVED'),))
    else:
        obj=GeomagneticState(intervals=(IntervalRecord(T-timedelta(hours=3),(float(kp),),'OBSERVED'),))
    got=decode_state(encode_state(obj,environment='test'),expected_environment='test')
    assert got.intervals[0].observed_values[0].hex()==float(kp).hex()


def test_ack_sentinel_roundtrip_and_protected_identity():
    first=mk_state()
    key=first.outbox[0].event_key
    sent=record_delivery(first,expected_version=first.version,event_key=key,status='SENT',telegram_message_id=112)
    decoded=decode_state(encode_state(sent,environment='production'),expected_environment='production')
    assert decoded==sent
    assert decoded.outbox[0].telegram_message_id==112
    assert validate_transition(first,sent)


def test_uncertain_outcome_roundtrip():
    first=mk_state()
    uncertain=record_delivery(first,expected_version=first.version,event_key=first.outbox[0].event_key,status='UNCERTAIN')
    assert decode_state(encode_state(uncertain,environment='test'),expected_environment='test')==uncertain


def test_same_state_noop():
    x=mk_state()
    assert validate_transition(x,x) is False


@pytest.mark.parametrize('environment',['',None,'prod','cyprus','PRODUCTION'])
def test_invalid_environment_rejected(environment):
    with pytest.raises(CodecError):encode_state(GeomagneticState(),environment=environment)


def test_namespace_cannot_cross_test_production():
    data=encode_state(mk_state(),environment='production')
    with pytest.raises(CodecError,match='namespace'):decode_state(data,expected_environment='test')


def test_region_wrong_rejected():
    with pytest.raises(CodecError):encode_state(GeomagneticState(),region='cyprus',environment='test')


def test_missing_snapshot_never_bootstraps():
    for bad in [b'',None,'{}',b'null']:
        with pytest.raises(CodecError):decode_state(bad,expected_environment='test')


def test_trailing_newline_noncanonical():
    with pytest.raises(CodecError,match='non-canonical'):decode_state(encode_state(mk_state(),environment='test')+b'\n',expected_environment='test')


def test_json_whitespace_noncanonical():
    raw=encode_state(mk_state(),environment='test')
    with pytest.raises(CodecError,match='non-canonical'):decode_state(raw.replace(b',',b', '),expected_environment='test')


def test_reordered_json_fields_rejected_even_with_valid_digest():
    raw=encode_state(mk_state(),environment='test')
    doc=json.loads(raw)
    x=json.dumps(doc,ensure_ascii=False).encode()
    assert x!=raw
    with pytest.raises(CodecError,match='non-canonical'):decode_state(x,expected_environment='test')


def test_duplicate_json_key_detected():
    b=encode_state(mk_state(),environment='test')
    dup=b.replace(b'"payload":',b'"payload":{},"payload":',1)
    with pytest.raises(CodecError):decode_state(dup,expected_environment='test')


def test_unknown_storage_schema_fails():
    b=resign(encode_state(mk_state(),environment='test'),lambda d:d['payload'].__setitem__('storage_format_version',2))
    with pytest.raises(CodecError,match='storage schema'):decode_state(b,expected_environment='test')


def test_unknown_inner_schema_fails():
    b=resign(encode_state(mk_state(),environment='test'),lambda d:d['payload']['state'].__setitem__('schema_version',2))
    with pytest.raises(CodecError):decode_state(b,expected_environment='test')


def test_bad_sha256_fails():
    b=encode_state(mk_state(),environment='test')
    assert b'6.67' in b
    with pytest.raises(CodecError,match='integrity'):decode_state(b.replace(b'6.67',b'6.68'),expected_environment='test')


def test_bad_digest_algorithm_fails():
    x=resign(encode_state(mk_state(),environment='test'),lambda d:d['integrity'].__setitem__('algorithm','sha1'))
    with pytest.raises(CodecError):decode_state(x,expected_environment='test')


def test_truncated_json_fails():
    with pytest.raises(CodecError):decode_state(encode_state(mk_state(),environment='test')[:-30],expected_environment='test')


def test_snapshot_length_bound():
    with pytest.raises(CodecError):decode_state(b' '*(MAX_SNAPSHOT_BYTES+1),expected_environment='test')


@pytest.mark.parametrize('evil',[b'NaN',b'Infinity',b'-Infinity'])
def test_nonfinite_json_rejected(evil):
    b=encode_state(mk_state(),environment='test').replace(b'6.67',evil)
    with pytest.raises(CodecError):decode_state(b,expected_environment='test')


def test_extra_field_rejected_with_valid_digest():
    b=resign(encode_state(mk_state(),environment='test'),lambda d:d['payload']['state'].__setitem__('unexpected',1))
    with pytest.raises(CodecError,match='fields'):decode_state(b,expected_environment='test')


def test_noncanonical_utc_offset_rejected_with_valid_digest():
    def change(d):d['payload']['state']['intervals'][0]['interval_start_utc']='2026-10-12T12:00:00+03:00'
    b=resign(encode_state(mk_state(),environment='test'),change)
    with pytest.raises(CodecError):decode_state(b,expected_environment='test')


def test_noncanonical_datetime_precision_rejected():
    def change(d):d['payload']['state']['intervals'][0]['interval_start_utc']='2026-10-12T09:00:00.000000Z'
    with pytest.raises(CodecError):decode_state(resign(encode_state(mk_state(),environment='test'),change),expected_environment='test')


def test_invalid_event_key_rejected_even_with_valid_digest():
    def change(d):d['payload']['state']['outbox'][0]['event_key']='kld-geomag-00000001:CLOSE'
    with pytest.raises(CodecError,match='outbox key'):decode_state(resign(encode_state(mk_state(),environment='test'),change),expected_environment='test')


def test_bad_provenance_rejected_with_valid_digest():
    def change(d):d['payload']['state']['intervals'][0]['source']='swpc_estimated_1m'
    with pytest.raises(CodecError):decode_state(resign(encode_state(mk_state(),environment='test'),change),expected_environment='test')


def test_forged_sent_message_id_rejected():
    def change(d):
        d['payload']['state']['outbox'][0]['status']='SENT'
        d['payload']['state']['outbox'][0]['telegram_message_id']=True
    with pytest.raises(CodecError):decode_state(resign(encode_state(mk_state(),environment='test'),change),expected_environment='test')


def test_next_identity_regression_rejected_with_valid_digest():
    def change(d):d['payload']['state']['next_incident_seq']=1
    with pytest.raises(CodecError):decode_state(resign(encode_state(mk_state(),environment='test'),change),expected_environment='test')


def test_bool_state_version_rejected():
    def change(d):d['payload']['state']['version']=True
    with pytest.raises(CodecError):decode_state(resign(encode_state(mk_state(),environment='test'),change),expected_environment='test')


def test_outbox_must_be_sorted():
    s=mk_state()
    a=s.outbox[0]
    other=OutboxEntry(f'{a.incident_id}:ESCALATE:G2',a.incident_id,'ESCALATE','G2')
    with pytest.raises(CodecError):encode_state(replace(s,outbox=(a,other)),environment='test')


def test_stored_receipts_never_drop():
    s=mk_state(); ack=record_delivery(s,expected_version=s.version,event_key=s.outbox[0].event_key,status='SENT',telegram_message_id=101)
    with pytest.raises(CodecError,match='outbox'):validate_transition(ack,replace(ack,version=ack.version+1,outbox=()))


def test_sent_receipt_never_downgraded():
    s=mk_state(); ack=record_delivery(s,expected_version=s.version,event_key=s.outbox[0].event_key,status='SENT',telegram_message_id=101)
    with pytest.raises(CodecError,match='SENT'):validate_transition(ack,replace(ack,version=ack.version+1,outbox=s.outbox))


def test_sent_receipt_message_id_never_changed():
    s=mk_state(); ack=record_delivery(s,expected_version=s.version,event_key=s.outbox[0].event_key,status='SENT',telegram_message_id=101)
    changed=replace(ack.outbox[0],telegram_message_id=102)
    with pytest.raises(CodecError,match='SENT'):validate_transition(ack,replace(ack,version=ack.version+1,outbox=(changed,)))


def test_uncertain_never_downgraded_to_pending():
    s=mk_state(); uncertain=record_delivery(s,expected_version=s.version,event_key=s.outbox[0].event_key,status='UNCERTAIN')
    with pytest.raises(CodecError,match='UNCERTAIN'):validate_transition(uncertain,replace(uncertain,version=uncertain.version+1,outbox=s.outbox))


def test_state_version_regression_rejected():
    s=mk_state()
    with pytest.raises(CodecError,match='revision'):validate_transition(s,replace(s,next_incident_seq=20))


def test_identity_sequence_regression_rejected():
    s=mk_state()
    with pytest.raises(CodecError):validate_transition(s,replace(s,version=s.version+1,next_incident_seq=1))


def test_alias_loss_rejected():
    s=mk_state(); altered=replace(s.incidents[0],candidate_ids=())
    with pytest.raises(CodecError):validate_transition(s,replace(s,version=s.version+1,incidents=(altered,)))


def test_interval_pruning_not_authorized():
    s=mk_state()
    with pytest.raises(CodecError,match='interval lost'):validate_transition(s,replace(s,version=s.version+1,intervals=()))


def test_observation_silent_rewrite_forbidden():
    s=mk_state(); r=replace(s.intervals[0],observed_values=(8.0,))
    with pytest.raises(CodecError,match='observation'):validate_transition(s,replace(s,version=s.version+1,intervals=(r,)))


def test_observation_conflict_transition_allowed():
    s=mk_state(); r=replace(s.intervals[0],observed_values=(6.67,8.0),status='CONFLICT')
    assert validate_transition(s,replace(s,version=s.version+1,intervals=(r,))) is True


def test_review_flag_and_aliases_roundtrip():
    s=mk_state(); inc=replace(s.incidents[0],review_required=True)
    altered=replace(s,incidents=(inc,))
    assert decode_state(encode_state(altered,environment='test'),expected_environment='test')==altered
