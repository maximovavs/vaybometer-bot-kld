"""Dormant H2-B2-A GitHub Git Database API transport with an injected HTTP client.

No credentials, HTTP stack, socket, default repository, bootstrap, or workflow
entry point is supplied. Same-repository access is TEST-only and requires a
verified immutable KLD repository identity and a scoped HTTPS requester.
Only the configured environment's branch and one canonical state file are used.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol

from geomagnetic_gitref_store import (
    CreatedCommit, GitRefConflict, StoreError, UncertainCommit,
    STATE_PATH, state_ref_for_environment,
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_KLD_CODE_REPO = "maximovavs/vaybometer-bot-kld"
_KLD_CODE_REPO_ID = 993750565  # Immutable GitHub repository ID, fresh-verified in A2.
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status: int
    body: bytes


class HTTPRequester(Protocol):
    def __call__(self, method: str, path: str, body: bytes | None) -> HTTPResponse: ...


def _sha(value: Any) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise StoreError("invalid Git object SHA")
    return value


def _sha1(kind: str, data: bytes) -> str:
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in pairs:
        if k in result:
            raise StoreError("ambiguous duplicate GitHub API field")
        result[k] = v
    return result


def _load(body: bytes) -> dict[str, Any]:
    if type(body) is not bytes or not body or len(body) > _MAX_RESPONSE_BYTES:
        raise StoreError("GitHub response size/type rejected")
    try:
        decoded = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise StoreError("GitHub response is invalid JSON") from exc
    if type(decoded) is not dict:
        raise StoreError("GitHub response is not an object")
    return decoded


def _body(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


class GitHubRESTTransport:
    """Injected, explicitly scoped, fail-closed Git Database transport.

    Returned 409/422 errors are called STALE only when a *fresh* ref read proves
    that another writer advanced the head. Otherwise the outcome is uncertain.
    """

    def __init__(self, *, repo_full_name: str, environment: str, requester: HTTPRequester):
        if (type(repo_full_name) is not str or _REPO.fullmatch(repo_full_name) is None
                or any(part in (".", "..") for part in repo_full_name.split("/"))):
            raise StoreError("invalid explicit state repository")
        if environment not in ("production", "test") or type(environment) is not str:
            raise StoreError("unapproved state namespace")
        same_repo = repo_full_name.lower() == _KLD_CODE_REPO
        if same_repo and (repo_full_name != _KLD_CODE_REPO or environment != "test"):
            raise StoreError("same-repository transport requires canonical KLD TEST namespace")
        if requester is None or not callable(requester):
            raise StoreError("injected HTTP requester required")
        self.repo_full_name = repo_full_name
        self.environment = environment
        self.state_ref = state_ref_for_environment(environment)
        self.state_path = STATE_PATH
        self._requester = requester
        self._root = f"repos/{repo_full_name}"
        if same_repo:
            # Fail closed *before* any Git object or ref mutation. This GET
            # authenticates the repository identity using the injected requester.
            _, meta = self._call("GET", "")
            perms = meta.get("permissions")
            if (type(meta.get("id")) is not int or meta["id"] != _KLD_CODE_REPO_ID
                    or meta.get("full_name") != _KLD_CODE_REPO
                    or meta.get("private") is not False
                    or meta.get("archived") is not False
                    or meta.get("disabled") is not False
                    or meta.get("default_branch") != "main"
                    or type(perms) is not dict or perms.get("push") is not True):
                raise StoreError("same-repository TEST identity/write capability unverified")

    def _call(self, method: str, endpoint: str, payload: dict[str, Any] | None = None,
              *, allowed: tuple[int, ...] = (200,)) -> tuple[int, dict[str, Any]]:
        path = self._root + endpoint
        try:
            response = self._requester(method, path, None if payload is None else _body(payload))
        except Exception as exc:
            raise UncertainCommit("GitHub API request or acknowledgement unavailable") from exc
        if not isinstance(response, HTTPResponse) or type(response.status) is not int:
            raise StoreError("invalid HTTP response")
        if response.status not in allowed:
            if response.status in (403, 404):
                raise StoreError(f"GitHub API permission/object unavailable ({response.status})")
            raise StoreError(f"unexpected GitHub HTTP status ({response.status})")
        return response.status, _load(response.body)

    def _ref_endpoint(self, ref: str) -> str:
        if ref != self.state_ref:
            raise StoreError("state ref is outside environment allowlist")
        return "/git/ref/" + ref.removeprefix("refs/")

    def read_ref(self, ref: str) -> str | None:
        url = self._ref_endpoint(ref)
        try:
            response = self._requester("GET", self._root + url, None)
        except Exception as exc:
            raise StoreError("Git ref lookup unavailable") from exc
        if not isinstance(response, HTTPResponse) or type(response.status) is not int:
            raise StoreError("invalid Git ref response")
        if response.status == 404:
            return None
        if response.status != 200:
            raise StoreError(f"Git ref lookup failed ({response.status})")
        p = _load(response.body)
        if p.get("ref") != ref or type(p.get("object")) is not dict or p["object"].get("type") != "commit":
            raise StoreError("wrong Git ref or object type")
        return _sha(p["object"].get("sha"))

    def _get_commit(self, sha: str, *, require_verified: bool = False) -> tuple[str, tuple[str, ...]]:
        sha = _sha(sha)
        _, p = self._call("GET", "/git/commits/" + sha)
        if _sha(p.get("sha")) != sha or type(p.get("tree")) is not dict:
            raise StoreError("unexpected commit identity")
        if require_verified:
            verification = p.get("verification")
            if (type(verification) is not dict
                    or verification.get("verified") is not True
                    or verification.get("reason") != "valid"):
                raise StoreError("created Git commit is not GitHub verified/valid")
        tree = _sha(p["tree"].get("sha"))
        ps = p.get("parents")
        if type(ps) is not list or len(ps) > 1 or any(type(x) is not dict for x in ps):
            raise StoreError("invalid Git commit ancestry")
        parents = tuple(_sha(x.get("sha")) for x in ps)
        if sha in parents:
            raise StoreError("cyclic Git commit")
        return tree, parents

    def _get_tree_blob_sha(self, tree_sha: str, path: str) -> str:
        tree_sha = _sha(tree_sha)
        if path != self.state_path:
            raise StoreError("unapproved state path")
        _, p = self._call("GET", "/git/trees/" + tree_sha + "?recursive=1")
        if _sha(p.get("sha")) != tree_sha or p.get("truncated") is not False:
            raise StoreError("Git tree missing, truncated or mismatched")
        items = p.get("tree")
        if type(items) is not list:
            raise StoreError("malformed Git tree")
        non_trees = []
        for item in items:
            if type(item) is not dict or type(item.get("path")) is not str:
                raise StoreError("invalid Git tree entry")
            _sha(item.get("sha"))
            if item.get("type") == "tree":
                if item["path"] != "state" or item.get("mode") != "040000":
                    raise StoreError("extra Git tree directory")
            elif item.get("type") == "blob":
                if item.get("mode") != "100644":
                    raise StoreError("unexpected Git blob mode")
                non_trees.append(item)
            else:
                raise StoreError("unexpected Git object in state tree")
        if len(items) != 2 or len(non_trees) != 1 or non_trees[0]["path"] != self.state_path:
            raise StoreError("extra or missing state tree entries")
        return _sha(non_trees[0]["sha"])

    def _get_blob(self, sha: str) -> bytes:
        sha = _sha(sha)
        _, p = self._call("GET", "/git/blobs/" + sha)
        if _sha(p.get("sha")) != sha or p.get("encoding") != "base64" or type(p.get("content")) is not str:
            raise StoreError("Git blob identity/encoding mismatch")
        try:
            content = base64.b64decode("".join(p["content"].split()), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise StoreError("malformed Git blob base64") from exc
        if type(p.get("size")) is not int or p["size"] != len(content) or _sha1("blob", content) != sha:
            raise StoreError("Git blob content does not match object SHA")
        return content

    def read_snapshot(self, commit_sha: str, path: str) -> bytes | None:
        if path != self.state_path:
            raise StoreError("wrong state snapshot path")
        tree, _ = self._get_commit(commit_sha)
        return self._get_blob(self._get_tree_blob_sha(tree, path))

    def create_state_commit(self, parent_sha: str, path: str, content: bytes) -> CreatedCommit:
        parent_sha = _sha(parent_sha)
        if path != self.state_path or type(content) is not bytes or not content:
            raise StoreError("invalid state write path/payload")
        # Independently pin and verify the predecessor's expected state tree.
        predecessor_tree, _ = self._get_commit(parent_sha)
        self._get_blob(self._get_tree_blob_sha(predecessor_tree, path))
        blob_sha = _sha1("blob", content)
        _, blob = self._call("POST", "/git/blobs", {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"}, allowed=(201,))
        if _sha(blob.get("sha")) != blob_sha:
            raise StoreError("created Git blob identity unverified")
        _, tree = self._call("POST", "/git/trees", {"base_tree": predecessor_tree, "tree": [
            {"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}]}, allowed=(201,))
        new_tree = _sha(tree.get("sha"))
        if new_tree == predecessor_tree or self._get_tree_blob_sha(new_tree, path) != blob_sha:
            raise StoreError("created state tree not verified")
        _, commit = self._call("POST", "/git/commits", {"message": "Advance isolated KLD geomagnetic state",
            "tree": new_tree, "parents": [parent_sha]}, allowed=(201,))
        result_sha = _sha(commit.get("sha"))
        if result_sha == parent_sha:
            raise StoreError("new commit aliases parent")
        actual_tree, parents = self._get_commit(result_sha, require_verified=True)
        if actual_tree != new_tree or parents != (parent_sha,):
            raise StoreError("created commit ancestry/tree unverified")
        if self._get_blob(self._get_tree_blob_sha(new_tree, path)) != content:
            raise StoreError("created snapshot content unverified")
        return CreatedCommit(result_sha, parent_sha, hashlib.sha256(content).hexdigest())

    def update_ref(self, ref: str, new_commit_sha: str, *, force: bool) -> None:
        endpoint = self._ref_endpoint(ref)
        if force is not False:
            raise StoreError("force or invalid force argument forbidden")
        new_commit_sha = _sha(new_commit_sha)
        new_tree, parents = self._get_commit(new_commit_sha)
        if len(parents) != 1 or self._get_tree_blob_sha(new_tree, self.state_path) is None:
            raise StoreError("updated head must have single parent and state file")
        predecessor = parents[0]
        expected_current = self.read_ref(ref)
        if expected_current != predecessor:
            raise GitRefConflict(409)
        try:
            response = self._requester("PATCH", self._root + endpoint,
                _body({"sha": new_commit_sha, "force": False}))
        except Exception as exc:
            raise UncertainCommit("ref update acknowledgement unavailable; do not retry") from exc
        if not isinstance(response, HTTPResponse) or type(response.status) is not int:
            raise UncertainCommit("malformed ref update acknowledgement")
        if response.status in (409, 422):
            # Stale only if independently observed new head; all other 409/422
            # (e.g. GitHub validation errors) have an ambiguous outcome.
            try:
                current = self.read_ref(ref)
            except Exception as exc:
                raise UncertainCommit("conflict outcome cannot be reconciled") from exc
            if current is not None and current not in (predecessor, new_commit_sha):
                raise GitRefConflict(response.status)
            raise UncertainCommit("conflict/validation response not proven stale")
        if response.status != 200:
            raise UncertainCommit(f"ref update response not confirmed ({response.status})")
        p = _load(response.body)
        if p.get("ref") != ref or type(p.get("object")) is not dict or p["object"].get("type") != "commit" or _sha(p["object"].get("sha")) != new_commit_sha:
            raise UncertainCommit("GitHub response confirmed wrong ref/commit")


__all__ = ("HTTPResponse", "HTTPRequester", "GitHubRESTTransport")
