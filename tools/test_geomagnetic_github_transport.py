"""H2-B2-A isolated GitHub API transport: entirely in-memory, zero real HTTP."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geomagnetic_gitref_store import (
    GitRefCASStore, GitRefConflict, MissingState, StaleStore, StoreError,
    UncertainCommit, STATE_REF, STATE_REF_TEST, STATE_PATH,
)
from geomagnetic_reconciliation import GeomagneticState
from geomagnetic_state_codec import encode_state
from geomagnetic_github_transport import GitHubRESTTransport, HTTPResponse

REPO = 'maximovavs/private-kld-state'
BASE = 'a' * 40


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def body(p):
    return json.dumps(p, separators=(',', ':'), ensure_ascii=True).encode()


def sha(s):
    return hashlib.sha1(s.encode()).hexdigest()


class FakeAPI:
    def __init__(self, *, environment='test'):
        self.ref = STATE_REF_TEST if environment == 'test' else STATE_REF
        self.head = BASE
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.calls = []
        self.inject = None
        self.patch_count = 0
        content = encode_state(GeomagneticState(), environment=environment)
        self.blobs[blob_sha(content)] = content
        tree = sha('tree-initial')
        self.trees[tree] = blob_sha(content)
        self.commits[BASE] = (tree, [])

    def __call__(self, method, path, payload):
        assert path.startswith('repos/' + REPO + '/'), path
        self.calls.append((method, path, payload))
        endpoint = path[len('repos/' + REPO):]
        if self.inject == 'request-timeout' and method != 'GET':
            raise TimeoutError('no request ack')
        if endpoint == '/git/ref/' + self.ref.removeprefix('refs/'):
            if method == 'GET':
                if self.inject == 'missing-ref':return HTTPResponse(404, body({'message': 'Not Found'}))
                return HTTPResponse(200, body({'ref': self.ref, 'object': {'type': 'commit', 'sha': self.head}}))
            if method == 'PATCH':
                self.patch_count += 1
                req = json.loads(payload)
                assert req['force'] is False
                if self.inject == 'timeout-before':raise TimeoutError('patch timed out')
                if self.inject in ('conflict-409','conflict-422','validation-422','forbidden-403'):
                    if self.inject.startswith('conflict-'):
                        other = sha('competing-commit')
                        self.commits[other] = (self.commits[self.head][0], [self.head])
                        self.head = other
                    code = int(self.inject.split('-')[-1])
                    return HTTPResponse(code, body({'message': 'API error'}))
                assert self.commits[req['sha']][1] == [self.head]
                self.head = req['sha']
                if self.inject == 'timeout-after':raise TimeoutError('patch timed out after accept')
                if self.inject == 'bad-patch-ack':return HTTPResponse(200, body({'ref': self.ref, 'object': {'type': 'commit', 'sha': BASE}}))
                return HTTPResponse(200, body({'ref': self.ref, 'object': {'type': 'commit', 'sha': self.head}}))
        if endpoint.startswith('/git/commits/') and method == 'GET':
            ident = endpoint.rsplit('/', 1)[-1]
            if ident not in self.commits:return HTTPResponse(404, body({'message': 'Not Found'}))
            tree, parents = self.commits[ident]
            return HTTPResponse(200, body({'sha': ident, 'tree': {'sha': tree}, 'parents': [{'sha': x} for x in parents]}))
        if endpoint.startswith('/git/trees/') and method == 'GET':
            ident = endpoint[len('/git/trees/'):].split('?')[0]
            if ident not in self.trees:return HTTPResponse(404, body({'message': 'Not Found'}))
            blob = self.trees[ident]
            entries = [{'path': 'state', 'mode': '040000', 'type': 'tree', 'sha': sha('directory')},
                       {'path': STATE_PATH, 'mode': '100644', 'type': 'blob', 'sha': blob}]
            if self.inject == 'extra-file':entries.append({'path': 'secret.txt', 'mode':'100644','type':'blob','sha':blob})
            if self.inject == 'bad-tree-mode':entries[-1]['mode']='100755'
            if self.inject == 'truncated-tree':return HTTPResponse(200, body({'sha':ident,'truncated':True,'tree':entries}))
            return HTTPResponse(200, body({'sha':ident,'truncated':False,'tree':entries}))
        if endpoint.startswith('/git/blobs/') and method == 'GET':
            ident = endpoint.rsplit('/', 1)[-1]
            if ident not in self.blobs:return HTTPResponse(404,body({'message':'Not Found'}))
            content = self.blobs[ident]
            if self.inject == 'corrupt-blob':content += b'!'
            if self.inject == 'bad-base64':return HTTPResponse(200,body({'sha':ident,'size':1,'encoding':'base64','content':'***'}))
            return HTTPResponse(200, body({'sha':ident,'size':len(content),'encoding':'base64','content':base64.b64encode(content).decode()}))
        if endpoint == '/git/blobs' and method == 'POST':
            obj = json.loads(payload)
            assert obj['encoding'] == 'base64'
            data = base64.b64decode(obj['content'])
            digest = blob_sha(data)
            self.blobs[digest] = data
            if self.inject == 'wrong-post-blob':digest='f'*40
            return HTTPResponse(201,body({'sha':digest}))
        if endpoint == '/git/trees' and method == 'POST':
            obj = json.loads(payload)
            assert obj['base_tree'] == self.commits[self.head][0]
            assert len(obj['tree']) == 1 and obj['tree'][0]['path'] == STATE_PATH
            assert obj['tree'][0]['mode'] == '100644'
            ident = sha('new-tree' + obj['tree'][0]['sha'])
            self.trees[ident] = obj['tree'][0]['sha']
            if self.inject == 'wrong-created-tree':self.trees[ident] = self.trees[self.commits[self.head][0]]
            return HTTPResponse(201,body({'sha':ident}))
        if endpoint == '/git/commits' and method == 'POST':
            obj = json.loads(payload)
            assert obj['parents'] == [self.head]
            assert obj['tree'] in self.trees
            ident = sha('new-commit' + obj['tree'])
            self.commits[ident] = (obj['tree'],obj['parents'])
            if self.inject == 'wrong-created-parent':self.commits[ident]=(obj['tree'],['f'*40])
            return HTTPResponse(201,body({'sha':ident}))
        return HTTPResponse(404,body({'message':'unknown path'}))


def setup(environment='test'):
    backend = FakeAPI(environment=environment)
    adapter = GitHubRESTTransport(repo_full_name=REPO, environment=environment, requester=backend)
    store = GitRefCASStore(adapter, environment=environment)
    return backend, adapter, store


def changed_state():
    return replace(GeomagneticState(), version=1)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args, **kwargs):raise AssertionError('real network forbidden')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(socket.socket,'connect_ex',fail)
    monkeypatch.setattr(socket,'getaddrinfo',fail)
    monkeypatch.setattr(socket,'create_connection',fail)


@pytest.mark.parametrize('repo',[None,'','maximovavs/vaybometer-bot-kld','MAXIMOVAVS/VAYBOMETER-BOT-KLD',
                                '/foo','foo/','../repo','foo/..','foo/bar/extra','foo bar/repo'])
def test_require_explicit_isolated_repo(repo):
    with pytest.raises(StoreError):GitHubRESTTransport(repo_full_name=repo, environment='test', requester=FakeAPI())

@pytest.mark.parametrize('environment',['productionx','',None,'TEST',123])
def test_environment_allowlist(environment):
    with pytest.raises(StoreError):GitHubRESTTransport(repo_full_name=REPO,environment=environment,requester=FakeAPI())


def test_requester_required():
    with pytest.raises(StoreError):GitHubRESTTransport(repo_full_name=REPO,environment='test',requester=None)


def test_test_prod_independent_refs():
    _, test, _ = setup('test')
    _, prod, _ = setup('production')
    assert test.state_ref == STATE_REF_TEST
    assert prod.state_ref == STATE_REF
    assert test.state_ref != prod.state_ref


def test_reject_wrong_environment_store():
    _, adapter, _ = setup('test')
    with pytest.raises(StoreError):GitRefCASStore(adapter,environment='production')


def test_pinned_read_and_namespace():
    api, _, store = setup()
    assert store.load_current().head_sha == BASE
    assert store.load_current().state == GeomagneticState()
    assert all(path.startswith('repos/' + REPO + '/') for _,path,_ in api.calls)


def test_noop_without_writes():
    api, _, store = setup()
    result=store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=GeomagneticState())
    assert result.status == 'NOOP'
    assert not [x for x in api.calls if x[0] != 'GET']


def test_success_commit_and_ref_validation():
    api, _, store = setup()
    result=store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=changed_state())
    assert result.status=='COMMITTED' and result.state_version==1
    assert api.head==result.head_sha and api.patch_count==1
    assert store.load_current().state==changed_state()
    assert [x[0] for x in api.calls if x[0] != 'GET']==['POST','POST','POST','PATCH']


def test_two_concurrent_writers_only_one_cas():
    api, adapter, store = setup()
    other=GitRefCASStore(adapter, environment='test')
    before=other.load_current()
    assert store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=changed_state()).status=='COMMITTED'
    with pytest.raises(StaleStore):other.commit_cas(expected_head_sha=before.head_sha,expected_state_version=0,new_state=changed_state())
    assert api.patch_count==1


@pytest.mark.parametrize('fault',['missing-ref','extra-file','bad-tree-mode','truncated-tree','corrupt-blob','bad-base64'])
def test_corrupt_snapshot_and_missing_branch_are_fail_closed(fault):
    api,_,store=setup()
    api.inject=fault
    with pytest.raises((StoreError,MissingState)):store.load_current()
    assert not [x for x in api.calls if x[0] != 'GET']


def test_unknown_ref_disallowed():
    _, adapter,_=setup()
    with pytest.raises(StoreError):adapter.read_ref('refs/heads/main')
    with pytest.raises(StoreError):adapter.read_ref(STATE_REF)


def test_wrong_path_disallowed():
    _, adapter,_=setup()
    with pytest.raises(StoreError):adapter.read_snapshot(BASE,'README.md')
    with pytest.raises(StoreError):adapter.create_state_commit(BASE,'README.md',b'evil')


def test_no_force_push():
    _,adapter,_=setup()
    with pytest.raises(StoreError):adapter.update_ref(STATE_REF_TEST,BASE,force=True)


@pytest.mark.parametrize('fault',['wrong-post-blob','wrong-created-tree','wrong-created-parent','request-timeout'])
def test_invalid_object_creation_blocks_ref_update(fault):
    api,_,store=setup()
    api.inject=fault
    with pytest.raises((StoreError,UncertainCommit)):store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=changed_state())
    assert api.head==BASE and api.patch_count==0


@pytest.mark.parametrize('fault',['conflict-409','conflict-422'])
def test_confirmed_stale_when_other_writer_advanced(fault):
    api,_,store=setup();api.inject=fault
    with pytest.raises(StaleStore):store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=changed_state())
    assert api.head!=BASE and api.patch_count==1


@pytest.mark.parametrize('fault',['validation-422','forbidden-403','timeout-before','timeout-after','bad-patch-ack'])
def test_ambiguous_ack_without_replay(fault):
    api,_,store=setup();api.inject=fault
    with pytest.raises(UncertainCommit):store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=changed_state())
    assert api.patch_count==1


def test_timeout_after_accepted_reconciles_read_only():
    api,_,store=setup();api.inject='timeout-after'
    with pytest.raises(UncertainCommit):store.commit_cas(expected_head_sha=BASE,expected_state_version=0,new_state=changed_state())
    after_calls=len(api.calls)
    assert api.head!=BASE
    api.inject=None
    assert store.load_current().state==changed_state()
    assert all(op=='GET' for op,_,_ in api.calls[after_calls:])


def test_no_real_io_stack():
    import ast
    src=(ROOT/'geomagnetic_github_transport.py').read_text()
    modules=[x.name for stmt in ast.walk(ast.parse(src)) if isinstance(stmt,ast.Import) for x in stmt.names]
    modules += [stmt.module for stmt in ast.walk(ast.parse(src)) if isinstance(stmt,ast.ImportFrom)]
    assert not set(modules)&{'requests','socket','urllib','http','os','subprocess','sqlite3','telegram'}
