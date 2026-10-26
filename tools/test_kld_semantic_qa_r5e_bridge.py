"""R5-E isolated offline bridge security regression (no provider or Telegram)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import kld_semantic_qa_run_bridge as b

ATTEMPTS = []

def _no_net(*a, **kw):
    ATTEMPTS.append('network')
    raise AssertionError('network forbidden')


def _source():
    return {"timezone": "Europe/Kaliningrad", "hourly": {"weathercode": [95]},
            "_kld_r5_capture": {"raw_payload_sha256": "a" * 64}}


def _stub_source_module():
    m = types.ModuleType('kld_semantic_qa_source')
    def producer(response, *, target_date, post_type, publication_target_date):
        assert target_date == publication_target_date
        assert post_type in {"morning", "evening"}
        if response.get("_kld_r5_capture", {}).get("raw_payload_sha256") != "a" * 64:
            raise ValueError("invalid collector")
        return {"provenance": {"raw_payload_sha256": "a" * 64}}
    m.build_trusted_snapshot = producer
    return m


def _stub_wiring_module():
    m = types.ModuleType('kld_semantic_qa_wiring')
    class Injection:
        def __init__(self, evaluator, facts, status, reason):
            self.evaluator,self.facts,self.status,self.reason=evaluator,facts,status,reason
    def off(**kwargs):
        return Injection(None if not kwargs["enabled"] else (lambda *a: None), None,
                         'disabled' if not kwargs['enabled'] else 'fail_closed',
                         'flag_off' if not kwargs['enabled'] else 'facts_missing')
    def collector(**kwargs):
        assert kwargs['expected_raw_sha256'] == 'a' * 64
        assert kwargs['collector_run_id'] == kwargs['expected_run_id']
        return Injection(None if kwargs['image_observer'] is None else kwargs['image_observer'],
                         None, 'fail_closed' if kwargs['image_observer'] is None else 'ready_offline_injection',
                         'evaluator_unavailable' if kwargs['image_observer'] is None else '')
    m.Injection=Injection
    m.select_semantic_injection=off
    m.select_offline_collector_injection=collector
    return m


def run():
    total=0
    with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {
        'kld_semantic_qa_source': _stub_source_module(),
        'kld_semantic_qa_wiring': _stub_wiring_module(),
    }), patch.object(socket.socket,'connect',_no_net), patch.object(socket,'getaddrinfo',_no_net):
        path = str(Path(td)/'handoff.json')
        env={}
        assert b.select_run_injection(enabled=False,source_file='/nonexistent',post_type='morning',target_date='2026-10-25',environment=env).status=='disabled';total+=1
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=env).status=='fail_closed';total+=1
        run_id=b.prepare_run_context(env)
        assert len(run_id)==32 and len(env[b.RUN_KEY_ENV])==64;total+=1
        try:b.prepare_run_context(env);assert False
        except b.RunHandoffUnavailable:pass
        total+=1
        digest=b.write_run_handoff(path,_source(),post_type='morning',target_date='2026-10-25',publication_target_date='2026-10-25',environment=env)
        assert digest=='a'*64 and Path(path).stat().st_mode&0o777==0o600;total+=1
        verified=b.read_run_handoff(path,post_type='morning',target_date='2026-10-25',environment=env)
        assert verified.run_id==run_id and verified.raw_sha256==digest;total+=1
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=env).status=='fail_closed';total+=1
        fake=lambda image:{"decision":"ACCEPT"}
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',image_observer=fake,environment=env).status=='ready_offline_injection';total+=1
        original=Path(path).read_bytes()
        for change in [lambda x: x.replace(b'"timezone":"Europe/Kaliningrad"',b'"timezone":"UTC"'),
                       lambda x: x.replace(b'"target_date":"2026-10-25"',b'"target_date":"2026-10-26"'),
                       lambda x: x.replace(b'"run_id":"',b'"run_id":"0')]:
            Path(path).write_bytes(change(original))
            assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=env).status=='fail_closed'
            total+=1
        Path(path).write_bytes(original)
        old=dict(env);old[b.RUN_ID_ENV]='f'*32
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=old).status=='fail_closed';total+=1
        old=dict(env);old[b.RUN_KEY_ENV]='b'*64
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=old).status=='fail_closed';total+=1
        assert b.select_run_injection(enabled=True,source_file=path,post_type='evening',target_date='2026-10-25',environment=env).status=='fail_closed';total+=1
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-26',environment=env).status=='fail_closed';total+=1
        Path(path).write_text('{}',encoding='utf-8')
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=env).status=='fail_closed';total+=1
        Path(path).write_bytes(original)
        os.unlink(path)
        Path(path).symlink_to(Path(td)/'nonexistent')
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=env).status=='fail_closed';total+=1
        os.unlink(path)
        Path(path).write_bytes(b'X'*(b.MAX_HANDOFF_BYTES+1))
        assert b.select_run_injection(enabled=True,source_file=path,post_type='morning',target_date='2026-10-25',environment=env).status=='fail_closed';total+=1
        assert not ATTEMPTS;total+=1
    print(f'PASS R5-E run-bound provenance bridge: {total}/{total}; network=0')

if __name__=='__main__': run()
