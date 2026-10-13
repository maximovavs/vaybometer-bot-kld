"""Dormant, TEST-ONLY, fail-closed initial Git ref bootstrap.

All I/O is through a caller-provided requester. No credentials, HTTP client,
branch creation in the code repository, production activation or automatic retry.
A separately authorized runner is required before this module can write anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
from typing import Literal

from geomagnetic_github_transport import (
    GitHubRESTTransport, HTTPRequester, HTTPResponse, _body, _load, _sha, _sha1,
)
from geomagnetic_gitref_store import (
    GitRefCASStore, StoreError, UncertainCommit, STATE_PATH, STATE_REF_TEST,
)
from geomagnetic_reconciliation import GeomagneticState
from geomagnetic_state_codec import decode_state, encode_state

# Constants verified against the existing H2-B2-B1 immutable Golden Set.
INITIAL_SHA256 = "dc3e014aa28ab6ab581ae374a0a2050deae6b3ab7a03ba18b2fd0bebaf086842"
INITIAL_BLOB_SHA = "2ad1b8a364c953af1fc5b78d1e33bf807861a8e9"
INITIAL_TREE_SHA = "c6b821d8d746ebeb5d63cc6c8f4a91a8f80fde58"


class ExistingTestRef(StoreError):
    """Ref already exists: no initialization or reset is allowed."""


@dataclass(frozen=True, slots=True)
class BootstrapReceipt:
    status: Literal["CONFIRMED"]
    ref: str
    head_sha: str
    snapshot_sha256: str
    repository_id: int


def _exchange(requester: HTTPRequester, method: str, path: str,
              payload: dict | None = None) -> HTTPResponse:
    try:
        response = requester(method, path, None if payload is None else _body(payload))
    except Exception as exc:
        raise UncertainCommit("bootstrap GitHub acknowledgement unavailable; never retry automatically") from exc
    if not isinstance(response, HTTPResponse) or type(response.status) is not int or type(response.body) is not bytes:
        raise UncertainCommit("malformed bootstrap GitHub acknowledgement; never retry automatically")
    return response


def _require_repo_identity(requester: HTTPRequester, root: str,
                           *, repo_full_name: str, repository_id: int) -> None:
    response = _exchange(requester, "GET", root)
    if response.status != 200:
        raise StoreError("repository access not independently verified")
    data = _load(response.body)
    permissions = data.get("permissions")
    if (type(data.get("id")) is not int or data["id"] != repository_id
            or data.get("full_name") != repo_full_name
            or type(data.get("private")) is not bool
            or data.get("archived") is not False
            or data.get("disabled") is not False
            or not isinstance(data.get("default_branch"), str)
            or not data["default_branch"]
            or type(permissions) is not dict or permissions.get("push") is not True):
        raise StoreError("state repository identity or scoped write capability unverified")


def _verify_root(transport: GitHubRESTTransport, head: str, canonical: bytes) -> None:
    tree, parents = transport._get_commit(head)
    if parents != () or tree != INITIAL_TREE_SHA:
        raise StoreError("initial commit is not a canonical parentless root")
    if transport._get_tree_blob_sha(tree, STATE_PATH) != INITIAL_BLOB_SHA:
        raise StoreError("initial Git tree does not contain the pinned blob")
    if transport.read_snapshot(head, STATE_PATH) != canonical:
        raise StoreError("initial Git snapshot differs from canonical test payload")


def _confirm_ref(transport: GitHubRESTTransport, root_sha: str,
                 canonical: bytes, repository_id: int) -> BootstrapReceipt:
    # This function never creates objects or modifies a ref, including on 201.
    try:
        head = transport.read_ref(STATE_REF_TEST)
        if head != root_sha:
            raise StoreError("created test ref is missing or points to a different commit")
        _verify_root(transport, root_sha, canonical)
        snap = GitRefCASStore(transport, environment="test").load_current()
        if (snap.head_sha != root_sha or snap.state != GeomagneticState()
                or transport.read_snapshot(root_sha, STATE_PATH) != canonical):
            raise StoreError("independent CAS read-back did not confirm test bootstrap")
    except Exception as exc:
        raise UncertainCommit("bootstrap result not independently confirmed; no retry") from exc
    return BootstrapReceipt("CONFIRMED", STATE_REF_TEST, root_sha, INITIAL_SHA256, repository_id)


def bootstrap_test_state(*, repo_full_name: str, repository_id: int,
                         environment: str, requester: HTTPRequester) -> BootstrapReceipt:
    """One-shot test ref creation; any uncertain outcome ends in a read-only check.

    The caller must explicitly authorize every actual invocation. No production
    environment is accepted, even if the injected requester has broad rights.
    """
    if environment != "test" or type(environment) is not str:
        raise StoreError("production bootstrap permanently forbidden")
    if type(repository_id) is not int or repository_id <= 0:
        raise StoreError("expected immutable state repository ID required")
    transport = GitHubRESTTransport(repo_full_name=repo_full_name,
                                   environment=environment, requester=requester)
    root = f"repos/{repo_full_name}"
    # A 404 means absent only after this independently authenticated identity
    # and write-permission check. Missing permissions deliberately fail closed.
    _require_repo_identity(requester, root, repo_full_name=repo_full_name,
                           repository_id=repository_id)
    if transport.read_ref(STATE_REF_TEST) is not None:
        raise ExistingTestRef("test ref already exists; never reset or replace")

    canonical = encode_state(GeomagneticState(), environment="test")
    if (hashlib.sha256(canonical).hexdigest() != INITIAL_SHA256
            or _sha1("blob", canonical) != INITIAL_BLOB_SHA
            or decode_state(canonical, expected_environment="test") != GeomagneticState()):
        raise StoreError("initial snapshot does not match pinned canonical identity")

    blob = _exchange(requester, "POST", root + "/git/blobs", {
        "content": base64.b64encode(canonical).decode("ascii"), "encoding": "base64"})
    if blob.status != 201 or _sha(_load(blob.body).get("sha")) != INITIAL_BLOB_SHA:
        raise StoreError("Git blob creation unverified; no ref write")
    # No base_tree: only the state file exists in the resulting root tree.
    tree_resp = _exchange(requester, "POST", root + "/git/trees", {"tree": [
        {"path": STATE_PATH, "mode": "100644", "type": "blob", "sha": INITIAL_BLOB_SHA}]})
    if tree_resp.status != 201 or _sha(_load(tree_resp.body).get("sha")) != INITIAL_TREE_SHA:
        raise StoreError("Git root tree creation unverified; no ref write")
    # Verify all tree entries and blob through an independent GET before commit.
    if (transport._get_tree_blob_sha(INITIAL_TREE_SHA, STATE_PATH) != INITIAL_BLOB_SHA
            or transport._get_blob(INITIAL_BLOB_SHA) != canonical):
        raise StoreError("Git root tree read-back invalid; no ref write")

    commit_resp = _exchange(requester, "POST", root + "/git/commits", {
        "message": "Initialize isolated KLD geomagnetic TEST state",
        "tree": INITIAL_TREE_SHA, "parents": []})
    if commit_resp.status != 201:
        raise StoreError("initial root commit creation unverified; no ref write")
    root_sha = _sha(_load(commit_resp.body).get("sha"))
    _verify_root(transport, root_sha, canonical)

    # Exactly one CREATE ref call. ANY acknowledgement, including timeout, 422,
    # 201, or malformed response, is followed ONLY by independent GET/read-back.
    # A response itself is never proof of successful bootstrap.
    acknowledgement = None
    try:
        acknowledgement = _exchange(requester, "POST", root + "/git/refs", {
            "ref": STATE_REF_TEST, "sha": root_sha})
    except UncertainCommit:
        pass
    verified = _confirm_ref(transport, root_sha, canonical, repository_id)
    # An explicit permission/server rejection is not an accepted CREATE result,
    # even when independent reads observe the expected ref. A lost response is
    # distinguishable from an explicit HTTP error; neither is ever retried.
    if acknowledgement is not None and acknowledgement.status not in (201, 422):
        raise UncertainCommit("unexpected ref-create HTTP status; no automatic retry")
    return verified


__all__ = ("BootstrapReceipt", "ExistingTestRef", "bootstrap_test_state")
