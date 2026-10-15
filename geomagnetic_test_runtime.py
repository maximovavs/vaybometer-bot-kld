"""Explicit, dependency-injected KLD geomagnetic TEST-only one-shot runner.

No CLI, credentials, default HTTP clients, scheduler or Telegram delivery path.
A trusted caller supplies the UTC clock, NOAA response transport and GitHub
requester. This module never issues a retry or creates a state branch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from geomagnetic_github_transport import GitHubRESTTransport, HTTPRequester, GitCommitSigner
from geomagnetic_gitref_store import GitRefCASStore, UncertainCommit
from geomagnetic_noaa_transport import NOAAResponse, load_observed_3h_history
from geomagnetic_reconciliation import reconcile

_KLD_REPO = "maximovavs/vaybometer-bot-kld"


@dataclass(frozen=True, slots=True)
class TestRunReceipt:
    status: str
    head_sha: str
    state_version: int
    observed_intervals: int
    proposed_events: int
    requires_review: bool


def run_test_once(
    *,
    clock: Callable[[], datetime],
    noaa_transport: Callable[..., NOAAResponse],
    github_requester: HTTPRequester,
    commit_signer: GitCommitSigner | None = None,
) -> TestRunReceipt:
    """Reconcile one validated NOAA response into TEST state, with read-back.

    PENDING outbox events are stored only, never sent. STALE / UNCERTAIN and
    source/provenance errors propagate; none are retried or treated as quiet.
    """
    if not callable(clock) or not callable(noaa_transport) or not callable(github_requester):
        raise ValueError("explicit clock, NOAA transport and GitHub requester required")

    def new_store() -> GitRefCASStore:
        transport = GitHubRESTTransport(
            repo_full_name=_KLD_REPO, environment="test", requester=github_requester,
            commit_signer=commit_signer,
        )
        return GitRefCASStore(transport, environment="test")

    store = new_store()
    pinned = store.load_current()  # Missing or invalid branch is never bootstrapped.
    history = load_observed_3h_history(clock=clock, transport=noaa_transport)
    outcome = reconcile(
        pinned.state, history,
        expected_version=pinned.version, as_of_utc=history.as_of_utc,
    )
    committed = store.commit_cas(
        expected_head_sha=pinned.head_sha,
        expected_state_version=pinned.version,
        new_state=outcome.state,
    )
    # A new transport/store gives independent, pinned read-back even on NOOP.
    confirmed = new_store().load_current()
    if (committed.status not in ("COMMITTED", "NOOP")
            or confirmed.head_sha != committed.head_sha
            or confirmed.version != committed.state_version
            or confirmed.state != outcome.state):
        raise UncertainCommit("independent TEST state read-back did not match; no retry")
    return TestRunReceipt(
        status=committed.status,
        head_sha=confirmed.head_sha,
        state_version=confirmed.version,
        observed_intervals=len(history.bars),
        proposed_events=sum(d.status == "PROPOSED" for d in outcome.decisions),
        requires_review=outcome.requires_review,
    )


__all__ = ("TestRunReceipt", "run_test_once")
