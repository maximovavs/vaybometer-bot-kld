"""Test-only bootstrap fault-injection Golden Set. No GitHub HTTP or remote writes."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geomagnetic_github_bootstrap as bootstrap
from geomagnetic_github_bootstrap import bootstrap_test_state, ExistingTestRef
from geomagnetic_github_transport import HTTPResponse, GitHubRESTTransport, _sha1
from geomagnetic_gitref_store import GitRefCASStore, STATE_PATH, STATE_REF_TEST, StoreError, UncertainCommit
from geomagnetic_reconciliation import GeomagneticState
from geomagnetic_state_codec import encode_state
from tools.test_geomagnetic_github_transport import (FakeAPI, REPO, KLD_REPO,
    KLD_REPO_ID, blob_sha, body, sha, FakePGPSigner)
from geomagnetic_github_transport import (
    GitSigningIdentity, _signing_identity_fields, _unsigned_commit_payload)

EXPECTED_ID = 4571  # Synthetic immutable repository identity; never a live GitHub ID.


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Real network access is prohibited by bootstrap Golden Set')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


class BootstrapAPI(FakeAPI):
    """Extends the *existing* H2-B2-A in-memory API; never performs real HTTP."""
    def __init__(self):
        super().__init__(environment='test')
        self.head = None
        self.repo_id = EXPECTED_ID
        self.repo_name = REPO
        self.allow_push = True
        self.archived = False
        self.disabled = False
        self.fault = None
        self.create_ref_attempts = 0
        self.ref_commit = None
        self.corrupt_readback = False
        self.mismatched_tree = False

    def __call__(self, method, path, payload):
        assert path.startswith('repos/' + REPO), path
        endpoint = path[len('repos/' + REPO):]
        if method == 'GET' and endpoint == '':
            self.calls.append((method, path, payload))
            if self.fault == 'repo-404':
                return HTTPResponse(404, body({'message': 'Not Found'}))
            if self.fault == 'repo-403':
                return HTTPResponse(403, body({'message': 'Forbidden'}))
            return HTTPResponse(200, body({'id': self.repo_id, 'full_name': self.repo_name,
                                          'private': True, 'archived': self.archived,
                                          'disabled': self.disabled, 'default_branch': 'main',
                                          'permissions': {'push': self.allow_push}}))
        if method == 'GET' and endpoint == '/git/ref/' + STATE_REF_TEST.removeprefix('refs/'):
            self.calls.append((method, path, payload))
            if self.fault == 'read-ref-timeout' or (self.fault == 'readback-ref-timeout' and self.head is not None):
                raise TimeoutError('no permission to treat a timeout as missing ref')
            if self.head is None:
                return HTTPResponse(404, body({'message': 'Not Found'}))
            return HTTPResponse(200, body({'ref': STATE_REF_TEST,
                                           'object': {'type':'commit','sha':self.head}}))
        if method == 'POST' and endpoint == '/git/blobs':
            result = super().__call__(method, path, payload)
            if self.fault == 'wrong-blob-sha':
                return HTTPResponse(201, body({'sha': 'f'*40}))
            return result
        if method == 'POST' and endpoint == '/git/trees':
            self.calls.append((method, path, payload))
            req = json.loads(payload)
            assert 'base_tree' not in req
            assert len(req['tree']) == 1
            assert req['tree'][0] == {'path': STATE_PATH, 'mode': '100644',
                                       'type': 'blob', 'sha':bootstrap.INITIAL_BLOB_SHA}
            self.trees[bootstrap.INITIAL_TREE_SHA] = bootstrap.INITIAL_BLOB_SHA
            if self.fault == 'wrong-tree-sha':
                return HTTPResponse(201, body({'sha': 'f'*40}))
            return HTTPResponse(201, body({'sha':bootstrap.INITIAL_TREE_SHA}))
        if method == 'POST' and endpoint == '/git/commits':
            self.calls.append((method, path, payload))
            req = json.loads(payload)
            assert req['parents'] == [] and req['tree'] == bootstrap.INITIAL_TREE_SHA
            assert 'signature' in req and req['author'] == req['committer']
            # B5: independently reconstruct the parentless Git payload.
            # Never call the production _unsigned_commit_payload helper.
            from datetime import datetime, timezone
            ident = req['author']
            when = datetime.fromisoformat(ident['date'].replace('Z', '+00:00'))
            assert when.tzinfo == timezone.utc
            who = f"{ident['name']} <{ident['email']}> {int(when.timestamp())} +0000"
            assert req['message'] == "Initialize isolated KLD geomagnetic TEST state\n"
            expected_root = (f"tree {bootstrap.INITIAL_TREE_SHA}\nauthor {who}\n"
                             f"committer {who}\n\n{req['message']}")
            assert "\nparent " not in expected_root
            new = sha('test-root-commit')
            self.signed_payloads[new] = (req['signature'], expected_root)
            self.commits[new] = (req['tree'], [])
            if self.fault == 'wrong-commit-sha':
                return HTTPResponse(201, body({'sha':'f'*40}))
            if self.fault == 'bad-root-parent':
                self.commits[new] = (req['tree'], ['f'*40])
            if self.fault == 'bad-root-tree':
                self.commits[new] = ('f'*40, [])
            self.ref_commit = new
            return HTTPResponse(201, body({'sha':new}))
        if method == 'POST' and endpoint == '/git/refs':
            self.calls.append((method, path, payload))
            self.create_ref_attempts += 1
            req = json.loads(payload)
            assert req == {'ref': STATE_REF_TEST, 'sha': self.ref_commit}
            if self.fault in ('timeout-before','422-absent','201-absent'):
                if self.fault == 'timeout-before':
                    raise TimeoutError('ref creation was not accepted')
                return HTTPResponse(422 if self.fault.startswith('422') else 201, body({'message':'ambiguous'}))
            if self.fault == '403-ours':
                self.head = self.ref_commit
                return HTTPResponse(403, body({'message':'Permission denied'}))
            if self.fault == '422-other':
                other = sha('another-actor-created-ref')
                self.commits[other] = (bootstrap.INITIAL_TREE_SHA, [])
                self.head = other
                return HTTPResponse(422, body({'message':'already exists'}))
            self.head = self.ref_commit
            if self.fault == 'timeout-after':
                raise TimeoutError('accepted ref, missing ack')
            if self.fault == '422-ours':
                return HTTPResponse(422, body({'message':'unexpected 422'}))
            if self.fault == '201-bad-ack':
                return HTTPResponse(201, b'not JSON')
            return HTTPResponse(201, body({'ref':STATE_REF_TEST,
                                            'object':{'sha':self.ref_commit,'type':'commit'}}))
        if method == 'GET' and endpoint.startswith('/git/blobs/') and self.corrupt_readback and self.head is not None:
            self.calls.append((method, path, payload))
            sha_obj = endpoint.rsplit('/',1)[-1]
            return HTTPResponse(200, body({'sha':sha_obj,'size':1,'encoding':'base64','content':'IQ=='}))
        return super().__call__(method, path, payload)


def call(api, *, environment='test', repository_id=EXPECTED_ID, repo_full_name=REPO):
    return bootstrap_test_state(repo_full_name=repo_full_name,
                                repository_id=repository_id, environment=environment,
                                requester=api, commit_signer=FakePGPSigner())


class SameRepoBootstrapAPI(BootstrapAPI):
    """Zero-network KLD metadata and URL wrapper for the existing bootstrap fake."""
    def __init__(self, **kwargs):
        super().__init__()
        self.live_calls = []
        self.metadata_overrides = kwargs

    def __call__(self, method, path, payload):
        self.live_calls.append((method, path, payload))
        root = 'repos/' + KLD_REPO
        assert path == root or path.startswith(root + '/'), path
        mapped = 'repos/' + REPO + path[len(root):]
        result = super().__call__(method, mapped, payload)
        if path == root and result.status == 200:
            obj = json.loads(result.body)
            obj.update({'id': KLD_REPO_ID, 'full_name': KLD_REPO, 'private': False})
            obj.update(self.metadata_overrides)
            return HTTPResponse(200, body(obj))
        return result


def test_same_repo_orphan_test_bootstrap_and_readback():
    api = SameRepoBootstrapAPI()
    result = bootstrap_test_state(repo_full_name=KLD_REPO,
                                  repository_id=KLD_REPO_ID,
                                  environment='test', requester=api, commit_signer=FakePGPSigner())
    assert result.status == 'CONFIRMED'
    assert result.repository_id == KLD_REPO_ID
    assert api.create_ref_attempts == 1
    assert api.commits[result.head_sha][1] == []  # parentless orphan root
    assert all(path.startswith('repos/' + KLD_REPO) for _, path, _ in api.live_calls)
    assert not any('/git/ref/heads/main' in path for _, path, _ in api.live_calls)
    assert not any(method in ('PUT','PATCH','DELETE') for method, _, _ in api.live_calls)
    with pytest.raises(ExistingTestRef):
        bootstrap_test_state(repo_full_name=KLD_REPO,
                             repository_id=KLD_REPO_ID,
                             environment='test', requester=api, commit_signer=FakePGPSigner())
    assert api.create_ref_attempts == 1


@pytest.mark.parametrize('kwargs', [
    {'repository_id': KLD_REPO_ID + 1},
    {'metadata_overrides': {'id': KLD_REPO_ID + 1}},
    {'metadata_overrides': {'permissions': {'push': False}}},
    {'metadata_overrides': {'private': True}},
])
def test_same_repo_bootstrap_wrong_identity_denied(kwargs):
    overrides = kwargs.get('metadata_overrides', {})
    api = SameRepoBootstrapAPI(**overrides)
    with pytest.raises(StoreError):
        bootstrap_test_state(repo_full_name=KLD_REPO,
                             repository_id=kwargs.get('repository_id', KLD_REPO_ID),
                             environment='test', requester=api, commit_signer=FakePGPSigner())
    assert not any(method != 'GET' for method, _, _ in api.live_calls)


@pytest.mark.parametrize('fault,expected', [
    ('timeout-before', 'UNCERTAIN'), ('timeout-after', 'CONFIRMED'),
    ('422-other', 'UNCERTAIN'), ('422-ours', 'CONFIRMED'),
])
def test_same_repo_bootstrap_uncertain_ref_no_replay(fault, expected):
    api = SameRepoBootstrapAPI()
    api.fault = fault
    if expected == 'UNCERTAIN':
        with pytest.raises(UncertainCommit):
            bootstrap_test_state(repo_full_name=KLD_REPO,
                                 repository_id=KLD_REPO_ID,
                                 environment='test', requester=api, commit_signer=FakePGPSigner())
    else:
        assert bootstrap_test_state(repo_full_name=KLD_REPO,
                                    repository_id=KLD_REPO_ID,
                                    environment='test', requester=api, commit_signer=FakePGPSigner()).status == expected
    assert api.create_ref_attempts == 1
    assert not any('/git/ref/heads/main' in path for _, path, _ in api.live_calls)


def no_prohibited_mutations(api):
    assert not any(method in {'PATCH','DELETE','PUT'} for method, _, _ in api.calls)
    assert api.create_ref_attempts <= 1
    assert all(path.startswith('repos/' + REPO) for _, path, _ in api.calls)


def test_initial_contract_matches_frozen_test_snapshot():
    raw = encode_state(GeomagneticState(),environment='test')
    assert len(raw) == 292
    assert hashlib.sha256(raw).hexdigest() == bootstrap.INITIAL_SHA256
    assert blob_sha(raw) == bootstrap.INITIAL_BLOB_SHA
    subtree = _sha1('tree',b'100644 kld-geomagnetic-v1.json\0'+bytes.fromhex(bootstrap.INITIAL_BLOB_SHA))
    root = _sha1('tree',b'40000 state\0'+bytes.fromhex(subtree))
    assert root == bootstrap.INITIAL_TREE_SHA


def test_success_and_existing_ref_is_never_reinitialized():
    api = BootstrapAPI()
    result = call(api)
    assert result.status == 'CONFIRMED' and result.head_sha == api.head == api.ref_commit
    assert result.ref == STATE_REF_TEST and result.repository_id == EXPECTED_ID
    adapter = GitHubRESTTransport(repo_full_name=REPO, environment='test', requester=api, commit_signer=FakePGPSigner())
    assert GitRefCASStore(adapter,environment='test').load_current().state == GeomagneticState()
    assert api.create_ref_attempts == 1
    previous = len(api.calls)
    with pytest.raises(ExistingTestRef):
        call(api)
    assert all(method == 'GET' for method, _, _ in api.calls[previous:])
    no_prohibited_mutations(api)


@pytest.mark.parametrize('environment', ['production','prod','TEST',None,''])
def test_production_or_invalid_environment_rejected_before_any_io(environment):
    api = BootstrapAPI()
    with pytest.raises(StoreError):
        call(api, environment=environment)
    assert not api.calls


@pytest.mark.parametrize('bad_id', [None,True,0,-1,'4571'])
def test_unpinned_repository_id_rejected_before_io(bad_id):
    api = BootstrapAPI()
    with pytest.raises(StoreError):call(api,repository_id=bad_id)
    assert not api.calls


@pytest.mark.parametrize('fault', ['repo-404','repo-403'])
def test_no_ref_probe_without_verified_repo_access(fault):
    api=BootstrapAPI();api.fault=fault
    with pytest.raises(StoreError):call(api)
    assert len(api.calls)==1 and api.calls[0][0]=='GET'
    no_prohibited_mutations(api)


@pytest.mark.parametrize('attr,value', [
    ('repo_id',999),('repo_name','other/repo'),('allow_push',False),
    ('archived',True),('disabled',True)
])
def test_repo_identity_or_writer_permission_mismatch_refused(attr,value):
    api=BootstrapAPI();setattr(api,attr,value)
    with pytest.raises(StoreError):call(api)
    assert len(api.calls)==1 and api.calls[0][0]=='GET'
    no_prohibited_mutations(api)


def test_missing_ref_read_not_assumed_on_timeout():
    api=BootstrapAPI();api.fault='read-ref-timeout'
    with pytest.raises(StoreError):call(api)
    assert len(api.calls)==2 and api.create_ref_attempts==0


def test_snapshot_pin_mismatch_blocks_before_blob_creation(monkeypatch):
    api=BootstrapAPI()
    monkeypatch.setattr(bootstrap,'INITIAL_SHA256','0'*64)
    with pytest.raises(StoreError):call(api)
    assert not [x for x in api.calls if x[0]!='GET']


@pytest.mark.parametrize('fault', [
    'wrong-blob-sha','wrong-tree-sha','wrong-commit-sha','bad-root-parent','bad-root-tree'
])
def test_untrusted_created_object_never_creates_ref(fault):
    api=BootstrapAPI();api.fault=fault
    with pytest.raises(StoreError):call(api)
    assert api.create_ref_attempts == 0
    no_prohibited_mutations(api)


@pytest.mark.parametrize('fault,accepted', [
    ('timeout-before',False),('timeout-after',True),('422-absent',False),
    ('422-ours',True),('422-other',False),('201-absent',False),
    ('201-bad-ack',True)
])
def test_ambiguous_creation_never_blind_retries(fault,accepted):
    api=BootstrapAPI();api.fault=fault
    if accepted:
        result=call(api)
        assert result.status=='CONFIRMED' and result.head_sha==api.ref_commit
    else:
        with pytest.raises(UncertainCommit):call(api)
    assert api.create_ref_attempts==1
    no_prohibited_mutations(api)


def test_mismatched_readback_after_ref_creation_fails_closed():
    api=BootstrapAPI();api.corrupt_readback=True
    with pytest.raises(UncertainCommit):call(api)
    assert api.create_ref_attempts==1
    no_prohibited_mutations(api)


def test_explicit_forbidden_create_ack_never_counts_as_success():
    api=BootstrapAPI();api.fault='403-ours'
    with pytest.raises(UncertainCommit):call(api)
    assert api.head==api.ref_commit and api.create_ref_attempts==1
    no_prohibited_mutations(api)


def test_unreadable_ref_after_creation_fails_closed():
    api=BootstrapAPI();api.fault='readback-ref-timeout'
    with pytest.raises(UncertainCommit):call(api)
    assert api.create_ref_attempts==1
    no_prohibited_mutations(api)


def test_existing_noncanonical_ref_rejected_without_writes():
    api=BootstrapAPI();api.head=sha('foreign')
    with pytest.raises(ExistingTestRef):call(api)
    assert not any(method=='POST' for method,_,_ in api.calls)


def test_no_implicit_io_stack_or_real_sender_imports():
    tree=__import__('ast').parse(Path(bootstrap.__file__).read_text(encoding='utf-8'))
    imported=[]
    for node in __import__('ast').walk(tree):
        if isinstance(node,__import__('ast').Import):imported.extend(z.name for z in node.names)
        if isinstance(node,__import__('ast').ImportFrom):imported.append(node.module)
    assert not set(imported)&{'requests','urllib','socket','os','subprocess','telegram','http.client'}
    assert 'bootstrap_test_state' in bootstrap.__all__


def test_bootstrap_requires_signer_before_creating_any_git_object():
    api = BootstrapAPI()
    with pytest.raises(StoreError):
        bootstrap_test_state(repo_full_name=REPO, repository_id=EXPECTED_ID,
                             environment='test', requester=api)
    assert not any(method != 'GET' for method, _, _ in api.calls)
    assert api.create_ref_attempts == 0


@pytest.mark.parametrize('fault', ['signature', 'payload'])
def test_bootstrap_pins_github_signed_payload_before_ref_create(fault):
    api = BootstrapAPI()
    api.corrupt_signed_field = fault
    with pytest.raises(StoreError):
        call(api)
    assert api.create_ref_attempts == 0
    assert not any(method == 'POST' and path.endswith('/git/refs')
                   for method, path, _ in api.calls)


def test_bootstrap_rejects_unsigned_or_invalid_root_without_ref_create():
    api = BootstrapAPI()
    api.commit_verification = {'verified': False, 'reason': 'unsigned'}
    with pytest.raises(StoreError):
        call(api)
    assert api.create_ref_attempts == 0


def test_b5_root_commit_signature_payload_is_independently_parentless():
    from datetime import datetime, timezone
    api = BootstrapAPI()
    result = call(api)
    commits = [json.loads(raw) for method, path, raw in api.calls
               if method == 'POST' and path.endswith('/git/commits')]
    assert len(commits) == 1
    posted = commits[0]
    assert posted['parents'] == []
    assert posted['tree'] == bootstrap.INITIAL_TREE_SHA
    assert posted['author'] == posted['committer']
    identity = posted['author']
    stamp = datetime.fromisoformat(identity['date'].replace('Z', '+00:00'))
    assert stamp.tzinfo == timezone.utc
    who = f"{identity['name']} <{identity['email']}> {int(stamp.timestamp())} +0000"
    expected = (f"tree {bootstrap.INITIAL_TREE_SHA}\nauthor {who}\n"
                f"committer {who}\n\nInitialize isolated KLD geomagnetic TEST state\n")
    assert '\nparent ' not in expected
    assert api.signed_payloads[result.head_sha] == (posted['signature'], expected)
