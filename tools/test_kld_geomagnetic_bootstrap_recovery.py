"""KLD H2-B2-B1 Golden Set: OFFLINE bootstrap/recovery gates, no live state.

This test module has no bootstrap operation. All writes are to an in-memory
fake; a missing ref/blob cannot silently turn into a new empty snapshot.
Explicit owner authorization is still required for real state-repo bootstrap.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geomagnetic_gitref_store import (
    CreatedCommit, GitRefCASStore, MissingState, StaleStore, StoreError,
    UncertainCommit, STATE_PATH, state_ref_for_environment,
)
from geomagnetic_history import OBSERVED_SOURCE, parse_observed_3h_history
from geomagnetic_reconciliation import GeomagneticState, reconcile, record_delivery
from geomagnetic_state_codec import CodecError, decode_state, encode_state

FIXTURE_PATH = ROOT / "tools/fixtures/kld_geomagnetic_bootstrap_recovery_v1.json"
GOLDEN = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
AS_OF = datetime.fromisoformat(GOLDEN["as_of_utc"].replace("Z", "+00:00"))
INITIAL_SHA = "a" * 40  # Fake only; not an actual Git object or branch.


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("Golden Set prohibits DNS, sockets and real HTTP")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


class MemoryGit:
    """Minimal deterministic, explicitly environment-scoped fake CAS transport."""
    def __init__(self, environment="test", state=GeomagneticState()):
        self.state_ref = state_ref_for_environment(environment)
        self.head = INITIAL_SHA if state is not None else None
        self.snapshots = {INITIAL_SHA: encode_state(state, environment=environment)} if state is not None else {}
        self.parents = {}
        self.creates = 0
        self.updates = 0
        self.fault = None

    def read_ref(self, ref):
        assert ref == self.state_ref
        return self.head

    def read_snapshot(self, sha, path):
        assert path == STATE_PATH
        return self.snapshots.get(sha)

    def create_state_commit(self, parent_sha, path, content):
        assert path == STATE_PATH and type(content) is bytes
        self.creates += 1
        sha = hashlib.sha1(parent_sha.encode("ascii") + content).hexdigest()
        self.parents[sha] = parent_sha
        self.snapshots[sha] = content
        return CreatedCommit(sha, parent_sha, hashlib.sha256(content).hexdigest())

    def update_ref(self, ref, new_commit_sha, *, force):
        assert ref == self.state_ref and force is False
        self.updates += 1
        if self.fault == "before":
            raise TimeoutError("fake ACK lost before ref update")
        if self.parents.get(new_commit_sha) != self.head:
            raise RuntimeError("fake competing writer")
        self.head = new_commit_sha
        if self.fault == "after":
            raise TimeoutError("fake ACK lost after ref update")


def history(row):
    return parse_observed_3h_history([row], as_of_utc=AS_OF, source=OBSERVED_SOURCE)


def proposed_storm():
    result = reconcile(GeomagneticState(), history(GOLDEN["storm_observation"]),
                       expected_version=0, as_of_utc=AS_OF)
    assert result.state.version == 1 and len(result.state.outbox) == 1
    assert result.state.outbox[0].status == "PENDING"
    return result.state


def test_golden_fixture_contract():
    assert GOLDEN["schema"] == "kld-geomagnetic-bootstrap-recovery-golden-v1"
    assert GOLDEN["region"] == "kld"
    assert AS_OF.tzinfo == timezone.utc
    cases = GOLDEN["cases"]
    assert len(cases) == len(set(cases)) == 15
    assert cases == [
        "canonical-test-snapshot", "canonical-production-snapshot", "missing-ref-fail-closed",
        "missing-blob-fail-closed", "corrupted-snapshot-fail-closed", "wrong-namespace-fail-closed",
        "corrupt-integrity-fail-closed", "historical-replay-suppressed", "two-writers-one-winner",
        "ambiguous-before-update-readonly", "ambiguous-after-update-readonly", "sent-receipt-immutable",
        "uncertain-outbox-nonretryable", "version-reset-rejected", "injected-transport-required",
    ]


@pytest.mark.parametrize("env", ["test", "production"])
def test_canonical_initial_snapshot_is_explicit_and_pinned(env):
    golden = GOLDEN["initial_snapshots"][env]
    encoded = encode_state(GeomagneticState(), environment=env)
    assert hashlib.sha256(encoded).hexdigest() == golden["sha256"]
    assert state_ref_for_environment(env) == golden["ref"]
    assert decode_state(encoded, expected_environment=env) == GeomagneticState()
    assert encode_state(decode_state(encoded, expected_environment=env), environment=env) == encoded
    other = "production" if env == "test" else "test"
    with pytest.raises(CodecError):
        decode_state(encoded, expected_environment=other)
    # Encoding a proposal must not implicitly create a state branch or deliver a message.


def test_missing_ref_never_bootstraps():
    backend = MemoryGit(state=None)
    with pytest.raises(MissingState):
        GitRefCASStore(backend, environment="test").load_current()
    assert backend.creates == backend.updates == 0 and backend.head is None


def test_missing_blob_never_resets():
    backend = MemoryGit()
    backend.snapshots.clear()
    with pytest.raises(MissingState):
        GitRefCASStore(backend, environment="test").load_current()
    assert (backend.head, backend.creates, backend.updates) == (INITIAL_SHA, 0, 0)


@pytest.mark.parametrize("invalid", [b'broken-json', b'{"payload":{},"integrity":{}}'])
def test_corrupted_snapshot_fails_closed(invalid):
    backend = MemoryGit()
    backend.snapshots[INITIAL_SHA] = invalid
    with pytest.raises(StoreError):
        GitRefCASStore(backend, environment="test").load_current()
    assert backend.creates == backend.updates == 0


def test_wrong_namespace_fails_closed():
    backend = MemoryGit()
    backend.snapshots[INITIAL_SHA] = encode_state(GeomagneticState(), environment="production")
    with pytest.raises(StoreError):
        GitRefCASStore(backend, environment="test").load_current()
    with pytest.raises(StoreError):
        GitRefCASStore(MemoryGit(environment="production"), environment="test")
    assert backend.creates == backend.updates == 0


def test_corrupt_integrity_fails_closed():
    backend = MemoryGit()
    blob = json.loads(backend.snapshots[INITIAL_SHA])
    blob["integrity"]["digest"] = "0" * 64
    backend.snapshots[INITIAL_SHA] = json.dumps(blob).encode("utf-8")
    with pytest.raises(StoreError):
        GitRefCASStore(backend, environment="test").load_current()
    assert backend.creates == backend.updates == 0


def test_historical_start_not_replayed_during_recovery():
    stale = history(GOLDEN["historical_observation"])
    result = reconcile(GeomagneticState(), stale, expected_version=0, as_of_utc=AS_OF)
    assert not result.state.outbox
    assert not [d for d in result.decisions if d.status == "PROPOSED"]
    assert any(d.status == "SUPPRESSED" for d in result.decisions)


def test_two_writers_only_one_advances():
    backend = MemoryGit()
    first = GitRefCASStore(backend, environment="test")
    second = GitRefCASStore(backend, environment="test")
    a, b = first.load_current(), second.load_current()
    result = first.commit_cas(expected_head_sha=a.head_sha, expected_state_version=a.version,
                              new_state=proposed_storm())
    assert result.status == "COMMITTED"
    with pytest.raises(StaleStore):
        second.commit_cas(expected_head_sha=b.head_sha, expected_state_version=b.version,
                          new_state=proposed_storm())
    assert backend.updates == backend.creates == 1
    assert first.load_current().state == proposed_storm()


@pytest.mark.parametrize("position", ["before", "after"])
def test_ambiguous_commit_requires_readonly_reconciliation(position):
    backend = MemoryGit()
    store = GitRefCASStore(backend, environment="test")
    original = store.load_current()
    candidate = proposed_storm()
    backend.fault = position
    with pytest.raises(UncertainCommit):
        store.commit_cas(expected_head_sha=original.head_sha,
                         expected_state_version=original.version, new_state=candidate)
    assert backend.creates == backend.updates == 1
    # Operator's READ-ONLY reconciliation, NOT an automated second attempt.
    inspected = store.load_current()
    assert inspected.state == (GeomagneticState() if position == "before" else candidate)
    assert backend.creates == backend.updates == 1
    assert inspected.head_sha == (INITIAL_SHA if position == "before" else backend.head)


def _receipt(status):
    initial = proposed_storm()
    key = initial.outbox[0].event_key
    return record_delivery(initial, expected_version=initial.version, event_key=key,
                           status=status, telegram_message_id=917 if status == "SENT" else None)


@pytest.mark.parametrize("status", ["SENT", "UNCERTAIN"])
def test_receipts_cannot_be_erased_or_reopened(status):
    old = _receipt(status)
    backend = MemoryGit(state=old)
    store = GitRefCASStore(backend, environment="test")
    replacement = replace(old, version=old.version + 1,
                          outbox=(replace(old.outbox[0], status="PENDING", telegram_message_id=None),))
    with pytest.raises(CodecError):
        store.commit_cas(expected_head_sha=INITIAL_SHA, expected_state_version=old.version,
                         new_state=replacement)
    assert backend.creates == backend.updates == 0
    assert store.load_current().state == old
    if status == "SENT":
        same = record_delivery(old, expected_version=old.version,
                               event_key=old.outbox[0].event_key,
                               status="SENT", telegram_message_id=917)
        assert same == old


def test_version_reset_rejected():
    backend = MemoryGit(state=proposed_storm())
    store = GitRefCASStore(backend, environment="test")
    with pytest.raises(CodecError):
        store.commit_cas(expected_head_sha=INITIAL_SHA, expected_state_version=1,
                         new_state=GeomagneticState())
    assert backend.creates == backend.updates == 0


def test_no_unscoped_transport_or_implicit_bootstrap():
    with pytest.raises(StoreError):
        GitRefCASStore(None, environment="test")
    with pytest.raises(StoreError):
        GitRefCASStore(MemoryGit(), environment="other")
    assert state_ref_for_environment("test") != state_ref_for_environment("production")
