"""R5-E end-to-end authenticated REAL collector fixture -> image-only fake.

Zero providers/Telegram/network. Existing source and observer are imported,
not stubbed. Image input is a fixed PNG fixture.
"""
from __future__ import annotations

from copy import deepcopy
import ast
import json
import logging
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT / "tools")]

def refuse(*args, **kwargs):
    raise AssertionError("network forbidden")
socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.create_connection = refuse

from kld_semantic_qa_run_bridge import prepare_run_context, write_run_handoff, select_run_injection
from kld_visual_semantic_qa import evaluate_semantic_qa
from test_kld_semantic_qa_r5c_integration import IMAGE, cloud_response, observation
from test_kld_semantic_qa_r5_source import TARGET, stamp


def main():
    count=0
    with TemporaryDirectory() as tmp:
        path=str(Path(tmp)/"capture.json")
        env={}
        run=prepare_run_context(env)
        original=cloud_response()
        digest=write_run_handoff(path,original,post_type="evening",
             target_date=TARGET,publication_target_date=TARGET,environment=env)
        assert digest==original["_kld_r5_capture"]["raw_payload_sha256"];count+=1
        good=select_run_injection(enabled=True,source_file=path,post_type="evening",
                 target_date=TARGET,image_observer=lambda image:observation(),environment=env)
        assert good.status=="ready_offline_injection" and good.evaluator is not None;count+=1
        verdict=evaluate_semantic_qa(good.evaluator,IMAGE,good.facts,
                      expected_target_date=TARGET,expected_post_type="evening",timeout_s=3.0)
        assert verdict.accepted,(verdict.decision,verdict.reason);count+=1
        absent=select_run_injection(enabled=True,source_file=path,post_type="evening",target_date=TARGET,environment=env)
        assert absent.status=="fail_closed" and absent.reason=="evaluator_unavailable";count+=1
        # Visibility contradictions are rejected by the same real R4/5 validator.
        bad=select_run_injection(enabled=True,source_file=path,post_type="evening",target_date=TARGET,
                  image_observer=lambda image:observation("clear"),environment=env)
        v=evaluate_semantic_qa(bad.evaluator,IMAGE,bad.facts,expected_target_date=TARGET,expected_post_type="evening",timeout_s=3.0)
        assert not v.accepted,(v.decision,v.reason);count+=1
        # Signing an altered WMO payload with a valid key does not override source checks.
        source=deepcopy(original)
        source["hourly"]["weathercode"][4]=800
        try:
            write_run_handoff(path,source,post_type="evening",target_date=TARGET,publication_target_date=TARGET,environment=env)
        except Exception:pass
        else:raise AssertionError("invalid WMO accepted")
        count+=1
        raw=Path(path).read_bytes()
        obj=json.loads(raw)
        obj["collector_response"]["timezone"]="UTC"
        Path(path).write_text(json.dumps(obj),encoding="utf-8")
        reject=select_run_injection(enabled=True,source_file=path,post_type="evening",target_date=TARGET,
                                     image_observer=lambda image:observation(),environment=env)
        assert reject.status=="fail_closed";count+=1
        Path(path).write_bytes(raw)
        reject=select_run_injection(enabled=True,source_file=path,post_type="morning",target_date=TARGET,
                                     image_observer=lambda image:observation(),environment=env)
        assert reject.status=="fail_closed";count+=1
        off=select_run_injection(enabled=False,source_file=path,post_type="evening",target_date=TARGET,
                                     image_observer=lambda image:observation(),environment={})
        assert off.status=="disabled" and off.evaluator is None;count+=1
        # Run the exact production preview writer, but inject real source data,
        # avoiding weather providers, publisher, Telegram and file-based trust.
        source_ast=ast.parse((ROOT/"safe_test_post.py").read_text(encoding="utf-8"))
        fn=next(node for node in source_ast.body if isinstance(node,ast.FunctionDef) and node.name=="_write_semantic_facts_sidecar")
        writer_scope={"Path":Path,"json":json,"logging":logging}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),"safe_test_post.py","exec"),writer_scope)
        writer=writer_scope["_write_semantic_facts_sidecar"]
        preview_path=str(Path(tmp)/"preview_handoff.json")
        with patch.dict(os.environ,env):
            assert writer(preview_path,original,mode="evening",target_date=TARGET,
                          publication_target_date=TARGET,semantic_enabled=True);count+=1
        v=select_run_injection(enabled=True,source_file=preview_path,post_type="evening",target_date=TARGET,
                                image_observer=lambda image:observation(),environment=env)
        assert v.status=="ready_offline_injection";count+=1
        with patch.dict(os.environ,env):
            assert not writer(preview_path,None,mode="evening",target_date=TARGET,
                              publication_target_date=TARGET,semantic_enabled=True)
        assert not Path(preview_path).exists();count+=1
    print(f"PASS R5-E authenticated REAL source to image-only observer: {count}/{count}; network=0")

if __name__ == "__main__":main()
