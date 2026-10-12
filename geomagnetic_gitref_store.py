"""H2-B1 opt-in, injected Git-ref CAS model; no GitHub client or I/O here.

This module NEVER creates branches, resets refs, forces updates, retries writes,
fetches NOAA, or sends Telegram. A production transport and its authorization,
branch rules, ref protections and read-back protocol are NOT supplied here.

GitHub fast-forward-only ref update (force=False) is the compare-and-swap gate:
only a new commit whose sole parent is the inspected ref can advance it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal, Protocol

from geomagnetic_reconciliation import GeomagneticState
from geomagnetic_state_codec import CodecError, decode_state, encode_state, validate_transition

STATE_REF = "refs/heads/state/kld-geomag-v1"
STATE_PATH = "state/kld-geomagnetic-v1.json"
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


class StoreError(Exception):
    """Never replace a failed store read with an empty state."""


class MissingState(StoreError):
    """Missing ref or state blob; only a separately authorized bootstrap can create it."""


class StaleStore(StoreError):
    """A different writer advanced the ref or the state revision is stale."""


class UncertainCommit(StoreError):
    """Write acknowledgement ambiguous; inspect actual ref before any action."""


class GitRefConflict(StoreError):
    """Transport-reported non-fast-forward conflict (only 409/422 are STALE)."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Git ref conflict: {status}")


@dataclass(frozen=True, slots=True)
class CreatedCommit:
    sha: str
    parent_sha: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedSnapshot:
    head_sha: str
    state: GeomagneticState

    @property
    def version(self) -> int:
        return self.state.version


@dataclass(frozen=True, slots=True)
class CommitResult:
    status: Literal["NOOP", "COMMITTED"]
    head_sha: str
    state_version: int


class GitRefTransport(Protocol):
    """Injected provider interface. No implementation or remote credentials here."""

    def read_ref(self, ref: str) -> str | None: ...

    def read_snapshot(self, commit_sha: str, path: str) -> bytes | None: ...

    def create_state_commit(self, parent_sha: str, path: str, content: bytes) -> CreatedCommit: ...

    def update_ref(self, ref: str, new_commit_sha: str, *, force: bool) -> None: ...


def _sha(value: object) -> str:
    if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
        raise StoreError("untrusted or absent Git commit SHA")
    return value


class GitRefCASStore:
    """Explicitly dormant unless a caller invokes it with an injected transport."""

    def __init__(self, transport: GitRefTransport, *, environment: Literal["production", "test"]):
        if environment not in ("production", "test"):
            raise StoreError("invalid environment namespace")
        if transport is None:
            raise StoreError("injected Git transport required")
        self.transport = transport
        self.environment = environment

    def load_current(self) -> VerifiedSnapshot:
        try:
            head = self.transport.read_ref(STATE_REF)
        except Exception as exc:
            raise StoreError("Git ref lookup failed; no empty-state fallback") from exc
        if head is None:
            raise MissingState("state branch absent; bootstrap forbidden")
        head = _sha(head)
        try:
            raw = self.transport.read_snapshot(head, STATE_PATH)
        except Exception as exc:
            raise StoreError("pinned state snapshot lookup failed") from exc
        if raw is None:
            raise MissingState("state file absent; bootstrap forbidden")
        try:
            state = decode_state(raw, expected_environment=self.environment)
        except (CodecError, ValueError, TypeError) as exc:
            raise StoreError("state snapshot invalid; fail closed") from exc
        try:
            refreshed = self.transport.read_ref(STATE_REF)
        except Exception as exc:
            raise StoreError("ref freshness check unavailable") from exc
        if refreshed != head:
            raise StaleStore("state ref changed during pinned snapshot load")
        return VerifiedSnapshot(head, state)

    def commit_cas(self, *, expected_head_sha: str, expected_state_version: int,
                   new_state: GeomagneticState) -> CommitResult:
        expected = _sha(expected_head_sha)
        if type(expected_state_version) is not int or expected_state_version < 0:
            raise StaleStore("invalid expected state version")
        observed = self.load_current()
        if observed.head_sha != expected or observed.version != expected_state_version:
            raise StaleStore("stale Git head or state revision")
        change = validate_transition(observed.state, new_state)
        if not change:
            return CommitResult("NOOP", observed.head_sha, observed.version)
        # A creation is only an orphan candidate until the ref advances.
        payload = encode_state(new_state, environment=self.environment)
        try:
            created = self.transport.create_state_commit(expected, STATE_PATH, payload)
        except Exception as exc:
            raise UncertainCommit("state commit creation response ambiguous; do not retry") from exc
        if (not isinstance(created, CreatedCommit) or _sha(created.sha) == expected
                or created.parent_sha != expected
                or created.payload_sha256 != hashlib.sha256(payload).hexdigest()):
            raise UncertainCommit("created commit parent or content cannot be authenticated")
        try:
            self.transport.update_ref(STATE_REF, created.sha, force=False)
        except GitRefConflict as exc:
            if type(exc.status) is int and exc.status in (409, 422):
                raise StaleStore("non-fast-forward CAS conflict") from exc
            raise UncertainCommit("unexpected GitRefConflict status") from exc
        except Exception as exc:
            raise UncertainCommit("Git ref update response ambiguous; never blind retry") from exc
        # A response to PUT /ref alone is insufficient; verify state by pinned read-back.
        try:
            confirmed = self.load_current()
        except Exception as exc:
            raise UncertainCommit("post-update ref or snapshot confirmation failed") from exc
        if confirmed.head_sha != created.sha or confirmed.state != new_state:
            raise UncertainCommit("committed state not confirmed; inspect ref")
        return CommitResult("COMMITTED", created.sha, new_state.version)


__all__ = (
    "STATE_REF", "STATE_PATH", "StoreError", "MissingState", "StaleStore", "UncertainCommit",
    "GitRefConflict", "CreatedCommit", "VerifiedSnapshot", "CommitResult", "GitRefTransport", "GitRefCASStore",
)
