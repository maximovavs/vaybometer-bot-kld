"""H2-B2-C1: deterministic *offline* integration of H1/H2-A/H2-B1/H2-B2-A.

Only production parsers/reducers/codecs/CAS and H2-B2-A injected REST transport
are used; the GitHub API is supplied by its pre-existing in-memory FakeAPI.
Delivery acknowledgements are synthetic: no Telegram send or real state bootstrap.
A PENDING entry is a proposal, not authorization to deliver a notification.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ast
from pathlib import Path
import json
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geomagnetic_gitref_store import GitRefCASStore, MissingState, StaleStore, StoreError, UncertainCommit
from geomagnetic_history import kp_g_level, incident_candidates
from geomagnetic_noaa_transport import NOAA_OBSERVED_3H_URL, NOAAResponse, load_observed_3h_history, NoaaTransportError
from geomagnetic_reconciliation import GeomagneticState, reconcile, record_delivery
from geomagnetic_state_codec import CodecError, decode_state
from tools.test_geomagnetic_github_transport import BASE, setup

GOLDEN = json.loads((ROOT / 'tools/fixtures/kld_geomagnetic_e2e_v1.json').read_text(encoding='utf-8'))
AS_OF = datetime.fromisoformat(GOLDEN['as_of_utc'].replace('Z', '+00:00'))
ENV = GOLDEN['environment']


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError('H2-B2-C1 forbids real DNS, sockets and network')
    monkeypatch.setattr(socket, 'create_connection', fail)
    monkeypatch.setattr(socket, 'getaddrinfo', fail)
    monkeypatch.setattr(socket.socket, 'connect', fail)
    monkeypatch.setattr(socket.socket, 'connect_ex', fail)


def observed(rows, *, now=AS_OF):
    """Use actual H1 transport validation via an injected deterministic NOAA response."""
    def fake_noaa(url, *, timeout_s, max_bytes):
        assert url == NOAA_OBSERVED_3H_URL
        assert timeout_s > 0 and max_bytes > 0
        return NOAAResponse(status=200, final_url=url, body=json.dumps(rows).encode('utf-8'))
    return load_observed_3h_history(clock=lambda: now, transport=fake_noaa)


def row(start, kp):
    return {'time_tag': start, 'Kp': kp}


def commit_state(store, new_state):
    pinned = store.load_current()
    return store.commit_cas(expected_head_sha=pinned.head_sha,
                            expected_state_version=pinned.version, new_state=new_state)


def propose(store, rows, *, now=AS_OF):
    pinned = store.load_current()
    history = observed(rows, now=now)
    outcome = reconcile(pinned.state, history, expected_version=pinned.version, as_of_utc=now)
    committed = store.commit_cas(expected_head_sha=pinned.head_sha,
                                 expected_state_version=pinned.version, new_state=outcome.state)
    return history, outcome, committed


def test_fixture_is_explicitly_offline_and_level_complete():
    assert GOLDEN['schema'] == 'kld-geomagnetic-e2e-golden-v1'
    assert ENV == 'test' and AS_OF.tzinfo == timezone.utc
    assert [x['level'] for x in GOLDEN['storm_levels']] == ['G1', 'G2', 'G3', 'G4', 'G5']
    assert all(kp_g_level(x['kp']) == x['level'] for x in GOLDEN['storm_levels'])


@pytest.mark.parametrize('case', GOLDEN['storm_levels'], ids=lambda x: x['level'])
def test_h1_to_h2a_to_h2b1_to_h2b2a_commit_then_restart_without_duplicate(case):
    api, adapter, store = setup(ENV)
    history, result, committed = propose(store, [row(GOLDEN['recent_start'], case['kp'])])
    assert history.bars[0].g_level == case['level']
    assert incident_candidates(history)[0].peak_g_level == case['level']
    assert committed.status == 'COMMITTED'
    assert len(result.state.incidents) == 1 and len(result.state.outbox) == 1
    entry = result.state.outbox[0]
    assert entry.status == 'PENDING' and entry.transition == 'START'
    assert entry.g_level == case['level'] and entry.telegram_message_id is None
    assert api.patch_count == 1
    # A new store object simulates a fresh workflow invocation, not a new state branch.
    restarted = GitRefCASStore(adapter, environment=ENV)
    old = restarted.load_current()
    assert old.state == result.state and old.head_sha == committed.head_sha
    _, repeat, noop = propose(restarted, [row(GOLDEN['recent_start'], case['kp'])])
    assert noop.status == 'NOOP' and api.patch_count == 1
    assert repeat.state.outbox == (entry,)
    assert not [d for d in repeat.decisions if d.status == 'PROPOSED']
    assert all(path.startswith('repos/maximovavs/private-kld-state/') for _, path, _ in api.calls)


@pytest.mark.parametrize('receipt', ['SENT', 'UNCERTAIN'])
def test_synthetic_receipt_cas_is_durable_and_duplicate_proof(receipt):
    api, adapter, store = setup(ENV)
    rows = [row(GOLDEN['recent_start'], 6.0)]
    _, proposal, _ = propose(store, rows)
    key = proposal.state.outbox[0].event_key
    first = store.load_current()
    delivered = record_delivery(first.state, expected_version=first.version,
                                event_key=key, status=receipt,
                                telegram_message_id=GOLDEN['delivery_message_id'] if receipt == 'SENT' else None)
    assert commit_state(store, delivered).status == 'COMMITTED'
    restarted = GitRefCASStore(adapter, environment=ENV)
    assert restarted.load_current().state.outbox[0].status == receipt
    _, again, unchanged = propose(restarted, rows)
    assert unchanged.status == 'NOOP' and api.patch_count == 2
    assert again.state.outbox[0].status == receipt
    if receipt == 'SENT':
        assert again.state.outbox[0].telegram_message_id == GOLDEN['delivery_message_id']
    else:
        assert again.state.outbox[0].telegram_message_id is None
    # No synthetic recipient or send_message calls exist: receipt is a pure reducer.


def test_confirmed_start_enables_new_escalation_but_not_an_unconfirmed_repeat():
    api, adapter, store = setup(ENV)
    early = row(GOLDEN['earlier_start'], 5.0)
    latest = row(GOLDEN['recent_start'], 7.0)
    _, start, _ = propose(store, [early])
    start_key = start.state.outbox[0].event_key
    snap = store.load_current()
    ack = record_delivery(snap.state, expected_version=snap.version, event_key=start_key,
                          status='SENT', telegram_message_id=GOLDEN['delivery_message_id'])
    commit_state(store, ack)
    _, later, committed = propose(GitRefCASStore(adapter, environment=ENV), [early, latest])
    assert committed.status == 'COMMITTED'
    assert len(later.state.incidents) == 1
    assert len(later.state.outbox) == 2
    entries = {e.transition: e for e in later.state.outbox}
    assert entries['START'].status == 'SENT'
    assert entries['ESCALATE'].status == 'PENDING' and entries['ESCALATE'].g_level == 'G3'
    _, rerun, noop = propose(store, [early, latest])
    assert noop.status == 'NOOP' and not any(d.status == 'PROPOSED' for d in rerun.decisions)
    assert api.patch_count == 3


def test_unconfirmed_start_suppresses_standalone_escalation():
    api, _, store = setup(ENV)
    first = row(GOLDEN['earlier_start'], 5.0)
    later = row(GOLDEN['recent_start'], 7.0)
    propose(store, [first])
    _, second, commit = propose(store, [first, later])
    assert commit.status == 'COMMITTED'
    assert {e.transition for e in second.state.outbox} == {'START'}
    assert any(d.status == 'SUPPRESSED' and 'START not confirmed' in d.reason for d in second.decisions)
    assert api.patch_count == 2


def test_competing_writer_is_stale_no_second_ref_update():
    api, adapter, first = setup(ENV)
    second = GitRefCASStore(adapter, environment=ENV)
    old1, old2 = first.load_current(), second.load_current()
    payload = reconcile(old1.state, observed([row(GOLDEN['recent_start'], 5.0)]),
                        expected_version=old1.version, as_of_utc=AS_OF).state
    first.commit_cas(expected_head_sha=old1.head_sha, expected_state_version=old1.version, new_state=payload)
    with pytest.raises(StaleStore):
        second.commit_cas(expected_head_sha=old2.head_sha, expected_state_version=old2.version, new_state=payload)
    assert api.patch_count == 1 and first.load_current().state == payload


@pytest.mark.parametrize('fault', ['missing-ref', 'corrupt-blob', 'truncated-tree'])
def test_missing_or_corrupt_snapshot_cannot_publish_or_write(fault):
    api, _, store = setup(ENV)
    api.inject = fault
    with pytest.raises((StoreError, MissingState)):
        store.load_current()
    assert not [call for call in api.calls if call[0] != 'GET']


@pytest.mark.parametrize('fault', ['timeout-before', 'timeout-after', 'validation-422'])
def test_uncertain_ack_allows_readonly_inspection_not_retry(fault):
    api, _, store = setup(ENV)
    prior = store.load_current()
    candidate = reconcile(prior.state, observed([row(GOLDEN['recent_start'], 5.0)]),
                          expected_version=prior.version, as_of_utc=AS_OF).state
    api.inject = fault
    with pytest.raises(UncertainCommit):
        store.commit_cas(expected_head_sha=prior.head_sha, expected_state_version=prior.version,
                         new_state=candidate)
    assert api.patch_count == 1
    before = len(api.calls)
    api.inject = None
    recovered = store.load_current()
    assert all(method == 'GET' for method, _, _ in api.calls[before:])
    assert recovered.state == (candidate if fault == 'timeout-after' else prior.state)
    assert api.patch_count == 1


def test_old_observed_storm_is_stored_as_history_not_sent_as_alert():
    api, _, store = setup(ENV)
    history, result, committed = propose(store, [row(GOLDEN['historical_start'], 9.0)])
    assert history.bars and committed.status == 'COMMITTED'
    assert len(result.state.incidents) == 1 and not result.state.outbox
    assert any(d.status == 'SUPPRESSED' for d in result.decisions)
    assert api.patch_count == 1


def test_wrong_noaa_provenance_fails_before_state_commit():
    api, _, store = setup(ENV)
    prior = store.load_current()
    def malicious(url, *, timeout_s, max_bytes):
        return NOAAResponse(status=200, final_url=url,
                            body=json.dumps([{'time_tag': GOLDEN['recent_start'], 'Kp': 8.0,
                                              'source': 'untrusted-estimate'}]).encode())
    with pytest.raises(NoaaTransportError):
        load_observed_3h_history(clock=lambda: AS_OF, transport=malicious)
    assert store.load_current() == prior and api.patch_count == 0


def test_wrong_environment_never_uses_test_state_for_production():
    api, adapter, test_store = setup(ENV)
    with pytest.raises(StoreError):
        GitRefCASStore(adapter, environment='production')
    assert test_store.load_current().version == 0 and api.patch_count == 0


def test_no_hidden_output_or_implicit_real_credentials():
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
             for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, (ast.Attribute, ast.Name))}
    forbidden = {'send_message', 'send_photo', 'workflow_dispatch',
                 'create_branch', 'create_state_repository'}
    assert not calls.intersection(forbidden)
    assert not {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} & {'GITHUB_TOKEN'}
