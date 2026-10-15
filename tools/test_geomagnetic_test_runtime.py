"""Deterministic offline TEST runtime integration; no DNS, provider, or Telegram I/O."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geomagnetic_test_runtime import run_test_once
from geomagnetic_gitref_store import (
    GitRefCASStore, MissingState, StaleStore, UncertainCommit, VerifiedSnapshot,
)
from geomagnetic_noaa_transport import NOAA_OBSERVED_3H_URL, NOAAResponse, NoaaTransportError
from geomagnetic_reconciliation import record_delivery
from tools.test_geomagnetic_github_transport import BASE, SameRepoAPI, same_repo_setup, FakePGPSigner

AS_OF = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
RECENT = "2026-10-14 06:00:00"
OLD = "2026-10-12 06:00:00"


@pytest.fixture(autouse=True)
def network_denied(monkeypatch):
    def deny(*_args, **_kwargs):
        raise AssertionError("TEST runtime regressions forbid external network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)


def observed(rows, *, status=200, url=NOAA_OBSERVED_3H_URL):
    calls = []
    def transport(request_url, *, timeout_s, max_bytes):
        calls.append(request_url)
        assert request_url == NOAA_OBSERVED_3H_URL and timeout_s > 0 and max_bytes > 0
        return NOAAResponse(status=status, final_url=url, body=json.dumps(rows).encode())
    return transport, calls


def row(tag=RECENT, kp=5.0):
    return {"time_tag": tag, "Kp": kp}


def run(api, rows, *, now=AS_OF):
    noaa, calls = observed(rows)
    receipt = run_test_once(clock=lambda: now, noaa_transport=noaa,
                            github_requester=api, commit_signer=FakePGPSigner())
    assert calls == [NOAA_OBSERVED_3H_URL]
    return receipt


@pytest.mark.parametrize("kp,level", [(5.0,"G1"),(6.0,"G2"),(7.0,"G3"),(8.0,"G4"),(9.0,"G5")])
def test_g_levels_are_persisted_without_delivery(kp, level):
    api = SameRepoAPI()
    receipt = run(api, [row(kp=kp)])
    assert receipt.status == "COMMITTED" and receipt.state_version == 1
    assert receipt.proposed_events == 1 and receipt.observed_intervals == 1
    _, _, store = same_repo_setup(api)
    state = store.load_current().state
    assert len(state.incidents) == len(state.outbox) == 1
    assert state.incidents[0].peak_g_level == level
    assert state.outbox[0].g_level == level
    assert state.outbox[0].status == "PENDING"
    assert state.outbox[0].telegram_message_id is None
    assert api.delegate.patch_count == 1
    root = "repos/maximovavs/vaybometer-bot-kld"
    assert all(path == root or path.startswith(root + "/")
               for _, path, _ in api.calls)


def test_replay_is_noop_without_new_commit_or_duplicate_event():
    api = SameRepoAPI()
    first = run(api, [row(kp=6.0)])
    count = len([c for c in api.calls if c[0] != "GET"])
    repeat = run(api, [row(kp=6.0)])
    assert repeat.status == "NOOP" and repeat.head_sha == first.head_sha
    assert repeat.state_version == first.state_version
    assert repeat.proposed_events == 0
    assert len([c for c in api.calls if c[0] != "GET"]) == count
    assert api.delegate.patch_count == 1


def test_missing_test_ref_never_bootstraps_or_fetches_noaa():
    api = SameRepoAPI()
    api.delegate.inject = "missing-ref"
    noaa, calls = observed([row()])
    with pytest.raises(MissingState):
        run_test_once(clock=lambda: AS_OF, noaa_transport=noaa,
                      github_requester=api)
    assert calls == []
    assert api.delegate.patch_count == 0
    assert not [m for m, _, _ in api.calls if m != "GET"]


def test_empty_noaa_history_cannot_fabricate_quiet_or_write():
    api = SameRepoAPI()
    receipt = run(api, [])
    assert receipt.status == "NOOP" and receipt.observed_intervals == 0
    assert not [m for m, _, _ in api.calls if m != "GET"]


def test_historical_storm_is_stored_without_retroactive_alert():
    api = SameRepoAPI()
    receipt = run(api, [row(OLD, 7.0)])
    assert receipt.status == "COMMITTED" and receipt.proposed_events == 0
    _, _, store = same_repo_setup(api)
    state = store.load_current().state
    assert state.intervals and state.incidents
    assert not state.outbox


@pytest.mark.parametrize("status,url", [(503,NOAA_OBSERVED_3H_URL),
                                            (200,"https://untrusted.example/observed.json")])
def test_untrusted_noaa_response_rejected_before_git_write(status, url):
    api = SameRepoAPI()
    noaa, calls = observed([row()], status=status, url=url)
    with pytest.raises(NoaaTransportError):
        run_test_once(clock=lambda: AS_OF, noaa_transport=noaa,
                      github_requester=api)
    assert calls == [NOAA_OBSERVED_3H_URL]
    assert api.delegate.head == BASE and api.delegate.patch_count == 0
    assert not [m for m, _, _ in api.calls if m != "GET"]


def test_malformed_observation_rejected_without_cas_write():
    api = SameRepoAPI()
    with pytest.raises(NoaaTransportError):
        run(api, [row(kp="nan")])
    assert api.delegate.head == BASE and api.delegate.patch_count == 0


@pytest.mark.parametrize("verification", [None, {},
    {"verified": False,"reason":"unsigned"},
    {"verified": True,"reason":"invalid"}])
def test_unsigned_or_invalid_commit_fails_before_ref_update(verification):
    api = SameRepoAPI()
    api.delegate.commit_verification = verification
    with pytest.raises(UncertainCommit):
        run(api, [row(kp=5.0)])
    assert api.delegate.head == BASE and api.delegate.patch_count == 0
    assert [m for m, _, _ in api.calls if m != "GET"] == ["POST"] * 3


def test_verification_http_failure_does_not_update_ref():
    api = SameRepoAPI()
    api.delegate.inject = "verify-get-503"
    with pytest.raises(UncertainCommit):
        run(api, [row(kp=5.0)])
    assert api.delegate.head == BASE and api.delegate.patch_count == 0


@pytest.mark.parametrize("fault,exception", [("conflict-409", StaleStore),
                                                 ("timeout-before", UncertainCommit),
                                                 ("timeout-after", UncertainCommit)])
def test_stale_or_uncertain_is_not_retried(fault, exception):
    api = SameRepoAPI()
    api.delegate.inject = fault
    with pytest.raises(exception):
        run(api, [row(kp=5.0)])
    assert api.delegate.patch_count <= 1
    assert sum(m == "POST" for m, _, _ in api.calls) <= 3


def test_existing_sent_receipt_survives_reprocessing():
    api = SameRepoAPI()
    run(api, [row(kp=7.0)])
    _, _, store = same_repo_setup(api)
    snapshot = store.load_current()
    event_key = snapshot.state.outbox[0].event_key
    updated = record_delivery(snapshot.state, expected_version=snapshot.version,
                              event_key=event_key, status="SENT", telegram_message_id=123)
    store.commit_cas(expected_head_sha=snapshot.head_sha,
                     expected_state_version=snapshot.version, new_state=updated)
    before_writes = len([m for m, _, _ in api.calls if m != "GET"])
    reread = run(api, [row(kp=7.0)])
    assert reread.status == "NOOP" and reread.proposed_events == 0
    assert len([m for m, _, _ in api.calls if m != "GET"]) == before_writes
    assert store.load_current().state.outbox[0].telegram_message_id == 123
    assert store.load_current().state.outbox[0].status == "SENT"



@pytest.mark.parametrize("phase", ["COMMITTED", "NOOP"])
@pytest.mark.parametrize("divergence", ["head", "version", "state"])
def test_independent_readback_detects_mismatch_without_retry(monkeypatch, phase, divergence):
    """K1: final read-back must use a fresh store and verify all three fields.

    The first store handles the pinned pre-read and CAS, including its own
    in-CAS confirmation. Only the second, independent store reports divergence.
    Removing the independent confirmation from run_test_once must fail this test.
    """
    import geomagnetic_test_runtime as runtime

    api = SameRepoAPI()
    original_store_type = runtime.GitRefCASStore
    created_stores = []
    independent_reads = []

    def make_store(transport, *, environment):
        instance = original_store_type(transport, environment=environment)
        created_stores.append(instance)
        if len(created_stores) == 2:
            actual_read = instance.load_current

            def divergent_read():
                snapshot = actual_read()
                independent_reads.append(snapshot)
                if divergence == "head":
                    return VerifiedSnapshot("f" * 40, snapshot.state)
                if divergence == "version":
                    return VerifiedSnapshot(snapshot.head_sha, replace(
                        snapshot.state, version=snapshot.version + 1))
                return VerifiedSnapshot(snapshot.head_sha, replace(
                    snapshot.state, next_incident_seq=snapshot.state.next_incident_seq + 1))

            instance.load_current = divergent_read
        return instance

    monkeypatch.setattr(runtime, "GitRefCASStore", make_store)
    rows = [row(kp=6.0)] if phase == "COMMITTED" else []
    noaa, calls = observed(rows)
    with pytest.raises(UncertainCommit, match="independent TEST state read-back"):
        runtime.run_test_once(clock=lambda: AS_OF, noaa_transport=noaa,
                              github_requester=api, commit_signer=FakePGPSigner())
    assert calls == [NOAA_OBSERVED_3H_URL]
    assert len(created_stores) == 2
    assert len(independent_reads) == 1
    # A mismatch after the operation is UNCERTAIN, not a retry opportunity.
    assert api.delegate.patch_count == (1 if phase == "COMMITTED" else 0)
    writes = [method for method, _, _ in api.calls if method != "GET"]
    assert writes == (["POST", "POST", "POST", "PATCH"] if phase == "COMMITTED" else [])

def test_no_credentials_and_no_implicit_provider_calls():
    import ast
    source = (ROOT / "geomagnetic_test_runtime.py").read_text(encoding="utf-8")
    imports = {x.module for x in ast.walk(ast.parse(source))
               if isinstance(x, ast.ImportFrom)}
    imports.update(alias.name for x in ast.walk(ast.parse(source))
                   if isinstance(x, ast.Import) for alias in x.names)
    assert not imports.intersection({"requests","urllib","socket","telegram","os","subprocess"})
    api = SameRepoAPI()
    with pytest.raises(ValueError):
        run_test_once(clock=None, noaa_transport=lambda *_a: None,
                      github_requester=api)
    assert not api.calls


def test_missing_runtime_signer_fails_before_any_git_write():
    api = SameRepoAPI()
    noaa, calls = observed([row(kp=6.0)])
    with pytest.raises(UncertainCommit):
        run_test_once(clock=lambda: AS_OF, noaa_transport=noaa,
                      github_requester=api)
    assert calls == [NOAA_OBSERVED_3H_URL]
    assert not [m for m, _, _ in api.calls if m != 'GET']
