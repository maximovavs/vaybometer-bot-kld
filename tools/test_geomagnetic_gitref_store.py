"""Fault-injected GitRef CAS contract tests; no real GitHub or remote transports."""
from __future__ import annotations
import hashlib
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import socket
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from geomagnetic_history import OBSERVED_SOURCE, parse_observed_3h_history
from geomagnetic_reconciliation import GeomagneticState,reconcile,record_delivery
from geomagnetic_state_codec import CodecError,encode_state
from geomagnetic_gitref_store import (GitRefCASStore,GitRefConflict,CreatedCommit,
    MissingState,StaleStore,StoreError,UncertainCommit,STATE_REF,STATE_PATH)
T=datetime(2026,10,12,12,tzinfo=timezone.utc)
START='a'*40


def storm(s=GeomagneticState()):
    h=parse_observed_3h_history([{'time_tag':'2026-10-12T09:00:00','Kp':6.67}],source=OBSERVED_SOURCE,as_of_utc=T)
    return reconcile(s,h,expected_version=s.version,as_of_utc=T).state


class FakeGit:
    def __init__(self,initial=None):
        self.ref=None if initial is None else START
        self.content={START:encode_state(initial,environment='test')} if initial is not None else {}
        self.commits={}
        self.reads=0
        self.creates=0
        self.updates=0
        self.fail=None
        self.fault_on_read=None
    def read_ref(self,ref):
        assert ref==STATE_REF
        self.reads+=1
        if self.fault_on_read == self.reads: raise TimeoutError('read timeout')
        return self.ref
    def read_snapshot(self,sha,path):
        assert path==STATE_PATH
        return self.content.get(sha)
    def create_state_commit(self,parent_sha,path,content):
        assert path==STATE_PATH
        self.creates+=1
        if self.fail=='create_timeout':raise TimeoutError('create commit timeout')
        sha=hashlib.sha1(parent_sha.encode()+content).hexdigest()
        self.commits[sha]=parent_sha
        self.content[sha]=content
        if self.fail=='wrong_parent':return CreatedCommit(sha,'f'*40,hashlib.sha256(content).hexdigest())
        if self.fail=='wrong_digest':return CreatedCommit(sha,parent_sha,'0'*64)
        return CreatedCommit(sha,parent_sha,hashlib.sha256(content).hexdigest())
    def update_ref(self,ref,new_commit_sha,*,force):
        assert ref==STATE_REF and force is False
        self.updates+=1
        if self.fail=='conflict409':raise GitRefConflict(409)
        if self.fail=='conflict422':raise GitRefConflict(422)
        if self.fail=='error403':raise GitRefConflict(403)
        if self.fail=='timeout_before':raise TimeoutError('ack missing')
        if self.fail=='wrong_readback':return
        if self.commits[new_commit_sha]!=self.ref:raise GitRefConflict(422)
        self.ref=new_commit_sha
        if self.fail=='timeout_after':raise TimeoutError('ack missing after update')


@pytest.fixture(autouse=True)
def strict_no_network(monkeypatch):
    def fail(*args,**kwargs):raise AssertionError('real network access prohibited')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(socket.socket,'connect_ex',fail)
    monkeypatch.setattr(socket,'create_connection',fail)
    monkeypatch.setattr(socket,'getaddrinfo',fail)


def store(state=GeomagneticState()):
    backend=FakeGit(state)
    return GitRefCASStore(backend,environment='test'),backend


def test_missing_branch_no_bootstrap():
    f=FakeGit()
    with pytest.raises(MissingState):GitRefCASStore(f,environment='test').load_current()
    assert (f.creates,f.updates)==(0,0)


def test_missing_blob_no_bootstrap():
    a,f=store();f.content.clear()
    with pytest.raises(MissingState):a.load_current()
    assert f.creates==0 and f.updates==0


def test_pinned_head_load_and_revision():
    a,_=store();x=a.load_current()
    assert x.head_sha==START and x.state==GeomagneticState() and x.version==0


def test_invalid_environment_rejected():
    with pytest.raises(StoreError):GitRefCASStore(FakeGit(),environment='cyprus')


def test_malformed_sha_fail_closed():
    a,f=store();f.ref='g'*40
    with pytest.raises(StoreError):a.load_current()


def test_corrupt_snapshot_fail_closed():
    a,f=store();f.content[START]=b'{"fake":true}'
    with pytest.raises(StoreError):a.load_current()


def test_wrong_environment_snapshot_rejected():
    a,f=store();f.content[START]=encode_state(GeomagneticState(),environment='production')
    with pytest.raises(StoreError):a.load_current()


def test_stale_head_detection():
    a,f=store()
    with pytest.raises(StaleStore):a.commit_cas(expected_head_sha='b'*40,expected_state_version=0,new_state=storm())
    assert (f.creates,f.updates)==(0,0)


def test_stale_version_detection():
    a,f=store()
    with pytest.raises(StaleStore):a.commit_cas(expected_head_sha=START,expected_state_version=2,new_state=storm())
    assert (f.creates,f.updates)==(0,0)


@pytest.mark.parametrize('bad',[True,'0',None,-1,1.5])
def test_expected_version_strict_type(bad):
    a,f=store()
    with pytest.raises(StaleStore):a.commit_cas(expected_head_sha=START,expected_state_version=bad,new_state=storm())
    assert f.creates==0


def test_noop_creates_no_commit():
    a,f=store();x=a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=GeomagneticState())
    assert x.status=='NOOP' and x.head_sha==START and (f.creates,f.updates)==(0,0)


def test_success_fast_forward_and_readback():
    a,f=store();x=a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert x.status=='COMMITTED' and x.head_sha==f.ref and x.state_version==1
    assert f.creates==1 and f.updates==1
    assert a.load_current().state==storm()


def test_two_writers_same_snapshot_only_one_wins():
    a,f=store();b=GitRefCASStore(f,environment='test')
    old_a=a.load_current();old_b=b.load_current()
    first=a.commit_cas(expected_head_sha=old_a.head_sha,expected_state_version=old_a.version,new_state=storm())
    assert first.status=='COMMITTED'
    with pytest.raises(StaleStore):b.commit_cas(expected_head_sha=old_b.head_sha,expected_state_version=old_b.version,new_state=storm())
    assert f.updates==1


def test_existing_state_never_reset_on_stale():
    a,f=store();a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    old_ref=f.ref
    with pytest.raises(StaleStore):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.ref==old_ref


@pytest.mark.parametrize('fault',['conflict409','conflict422'])
def test_api_nonfastforward_conflict_fails_stale(fault):
    a,f=store();f.fail=fault
    with pytest.raises(StaleStore,match='non-fast-forward'):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.ref==START and f.creates==1 and f.updates==1


@pytest.mark.parametrize('fault',['timeout_before','timeout_after','error403'])
def test_ambiguous_ref_ack_never_blind_retry(fault):
    a,f=store();f.fail=fault
    with pytest.raises(UncertainCommit):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.creates==1 and f.updates==1


def test_timeout_after_accept_must_not_be_called_stale():
    a,f=store();f.fail='timeout_after'
    with pytest.raises(UncertainCommit):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.ref!=START and a.load_current().state==storm()


@pytest.mark.parametrize('fault',['wrong_parent','wrong_digest'])
def test_invalid_commit_receipt_blocks_ref_update(fault):
    a,f=store();f.fail=fault
    with pytest.raises(UncertainCommit):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.updates==0 and f.ref==START


def test_create_commit_timeout_leaves_ref_untouched():
    a,f=store();f.fail='create_timeout'
    with pytest.raises(UncertainCommit):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.ref==START and f.updates==0


def test_post_update_wrong_ref_is_uncertain():
    a,f=store();f.fail='wrong_readback'
    with pytest.raises(UncertainCommit):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=storm())
    assert f.updates==1


def test_ref_changes_during_pinned_load():
    a,f=store()
    original=f.read_ref
    def moving(ref):
        v=original(ref)
        return 'c'*40 if f.reads==2 else v
    f.read_ref=moving
    with pytest.raises(StaleStore):a.load_current()


def test_read_ref_timeout_fails_closed():
    a,f=store();f.fault_on_read=1
    with pytest.raises(StoreError):a.load_current()
    assert f.creates==0


def test_read_ref_second_timeout_fails_closed():
    a,f=store();f.fault_on_read=2
    with pytest.raises(StoreError):a.load_current()


def test_bad_revision_after_read_rejected_before_commit():
    s=storm();a,f=store(s)
    changed=replace(s,version=s.version)
    assert a.commit_cas(expected_head_sha=START,expected_state_version=s.version,new_state=changed).status=='NOOP'
    assert f.updates==0


def test_state_version_change_without_payload_identity_fails():
    a,f=store()
    with pytest.raises(CodecError):a.commit_cas(expected_head_sha=START,expected_state_version=0,new_state=replace(GeomagneticState(),version=2))
    assert f.creates==0


def test_sent_evidence_cannot_disappear_during_cas():
    s=storm();sent=record_delivery(s,expected_version=s.version,event_key=s.outbox[0].event_key,status='SENT',telegram_message_id=111)
    a,f=store(sent)
    with pytest.raises(CodecError):a.commit_cas(expected_head_sha=START,expected_state_version=sent.version,
       new_state=replace(sent,version=sent.version+1,outbox=()))
    assert f.creates==0


def test_cas_after_existing_ack_preserves_message_id():
    s=storm();sent=record_delivery(s,expected_version=s.version,event_key=s.outbox[0].event_key,status='SENT',telegram_message_id=111)
    a,f=store(s)
    x=a.commit_cas(expected_head_sha=START,expected_state_version=s.version,new_state=sent)
    assert x.status=='COMMITTED' and a.load_current().state.outbox[0].telegram_message_id==111


def test_no_real_github_client_or_fs_imports():
    import ast
    for path in ['geomagnetic_gitref_store.py','geomagnetic_state_codec.py']:
        src=(ROOT/path).read_text()
        names=[a.name for x in ast.walk(ast.parse(src)) if isinstance(x,ast.Import) for a in x.names]
        names+=[x.module for x in ast.walk(ast.parse(src)) if isinstance(x,ast.ImportFrom)]
        assert not set(names)&{'requests','socket','urllib','http','os','subprocess','pathlib','sqlite3','telegram'}
