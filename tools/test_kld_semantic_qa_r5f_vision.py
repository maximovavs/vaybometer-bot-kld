"""R5-F image-only real-SDK transport candidate, offline mock only.

No client or provider is constructed from this test suite; all interactions
are fake and the existing R4 worker applies timeout and weather veto.
"""
from __future__ import annotations

import ast
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
ATTEMPTS=[]
def deny(*args, **kwargs):
    ATTEMPTS.append("network")
    raise AssertionError("KLD R5-F provider/network calls forbidden")
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny

from kld_semantic_qa_vision_provider import make_image_observer, _image_type
from kld_semantic_qa_observer import wrap_image_only_observer
from kld_visual_semantic_qa import evaluate_semantic_qa
from kld_semantic_qa_run_bridge import prepare_run_context, write_run_handoff, select_run_injection
from test_kld_semantic_qa_r5c_integration import IMAGE, cloud_response, observation
from test_kld_semantic_qa_r5_source import TARGET
from test_kld_semantic_qa_r5b_observer import facts as synthetic_facts


def result(weather="cloudy", **overrides):
    data=observation(weather, **overrides)
    data.pop("model_or_evaluator_version", None)
    return data


class FakeFactory:
    def __init__(self, raw):
        self.raw = raw

    def __call__(self):
        raw = self.raw
        class Client:
            class Chat:
                class Completions:
                    def create(self, *, model, messages):
                        assert model in {"gpt-4o-mini", "gemini-2.5-flash"}
                        assert len(messages)==2 and messages[0]["role"]=="system"
                        assert messages[1]["role"]=="user"
                        parts=messages[1]["content"]
                        assert len(parts)==2 and parts[0]=={"type":"text","text":"Inspect only the attached picture."}
                        url=parts[1]["image_url"]["url"]
                        assert url.startswith("data:image/png;base64,")
                        assert "Kaliningrad" not in str(messages)
                        assert TARGET not in str(messages)
                        assert "api_key" not in str(messages).lower()
                        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
                completions=Completions()
            chat=Chat()
        return Client()


def observer(raw=None, *, backend="openai", **overrides):
    keys={"openai":"OPENAI_API_KEY", "gemini":"GEMINI_API_KEY"}
    env={"KLD_SEMANTIC_QA_VISION_PROVIDER":backend,keys[backend]:"fake-token",**overrides}
    return make_image_observer(env, client_factory=FakeFactory(json.dumps(raw if raw is not None else result())))


def assess(image_only, *, weather="cloudy", visibility="clear", deadline=2.0):
    wrapped=wrap_image_only_observer(image_only)
    src=synthetic_facts(weather=weather,visibility=visibility)
    return evaluate_semantic_qa(wrapped,IMAGE,src,expected_target_date="2026-07-20",
                                expected_post_type="evening",timeout_s=deadline)


def check_off_and_cold():
    assert make_image_observer({}) is None
    assert make_image_observer({"OPENAI_API_KEY":"fake-token"}) is None
    assert make_image_observer({"KLD_SEMANTIC_QA_VISION_PROVIDER":"unknown","OPENAI_API_KEY":"fake-token"}) is None
    assert make_image_observer({"KLD_SEMANTIC_QA_VISION_PROVIDER":"openai"}) is None
    assert make_image_observer({"KLD_SEMANTIC_QA_VISION_PROVIDER":"openai","OPENAI_API_KEY":"fake-token",
        "KLD_SEMANTIC_QA_VISION_MODEL":"text-only-model"}) is None
    assert make_image_observer({"KLD_SEMANTIC_QA_VISION_PROVIDER":"gemini","GEMINI_API_KEY":"fake-token",
        "KLD_SEMANTIC_QA_VISION_MODEL":"gemini-3.7-flash"}) is None
    # Exactly one explicit image backend; no factory init on observer creation.
    def forbidden(): raise AssertionError("client created outside worker")
    o=make_image_observer({"KLD_SEMANTIC_QA_VISION_PROVIDER":"openai","OPENAI_API_KEY":"fake-token"},client_factory=forbidden)
    assert o is not None


def check_real_run_bound_bridge_and_image_only():
    with TemporaryDirectory() as tmp:
        env={}
        prepare_run_context(env)
        path=str(Path(tmp)/"handoff.json")
        write_run_handoff(path,cloud_response(),post_type="evening",target_date=TARGET,
                          publication_target_date=TARGET,environment=env)
        factory=observer()
        good=select_run_injection(enabled=True,source_file=path,post_type="evening",target_date=TARGET,
                                  image_observer=factory,environment=env)
        assert good.status=="ready_offline_injection",good
        verdict=evaluate_semantic_qa(good.evaluator,IMAGE,good.facts,
                      expected_target_date=TARGET,expected_post_type="evening",timeout_s=2.0)
        assert (verdict.decision,verdict.reason)==("ACCEPT","observed_consistent"),verdict
        absent=select_run_injection(enabled=True,source_file=path,post_type="evening",target_date=TARGET,
                                    image_observer=make_image_observer({}),environment=env)
        assert absent.status=="fail_closed" and absent.reason=="evaluator_unavailable"
        corrupted=Path(path).read_bytes().replace(b'"evening"',b'"morning"')
        Path(path).write_bytes(corrupted)
        bad=select_run_injection(enabled=True,source_file=path,post_type="evening",target_date=TARGET,
                                 image_observer=factory,environment=env)
        assert bad.status=="fail_closed",bad
        off=select_run_injection(enabled=False,source_file=path,post_type="evening",target_date=TARGET,
                                image_observer=factory,environment={})
        assert off.status=="disabled" and off.evaluator is None


def check_storm_precipitation_visibility_moon():
    cases=[
        (result("storm",wind_appearance="calm",sea_appearance="calm"),"storm","clear","REJECT"),
        (result("storm",wind_appearance="strong",sea_appearance="rough"),"storm","clear","ACCEPT"),
        (result("clear"),"rain","clear","REJECT"),
        (result("snow"),"rain","clear","REJECT"),
        (result("rain"),"snow","clear","REJECT"),
        (result("snow"),"snow","clear","ACCEPT"),
        (result("clear"),"clear","dense_fog","REJECT"),
        (result("fog",visibility_appearance="dense_fog"),"fog","dense_fog","ACCEPT"),
        (result("cloudy",moon_visibility="visible",moon_shape="full_disk",dominant_moon="yes"),"cloudy","clear","REJECT"),
    ]
    for raw,weather,visibility,expected in cases:
        v=assess(observer(raw),weather=weather,visibility=visibility)
        assert v.decision==expected,(weather,visibility,expected,v)


def check_malformed_and_ambiguous():
    for raw in ("not-json","```json {} ```","{}","[]","null",
                json.dumps({"decision":"ACCEPT","observations":{}}),
                json.dumps(dict(result(),weather_main="cloudy")),
                json.dumps(dict(result(),reason_codes=["err"],decision="ACCEPT")),
                json.dumps(dict(result(),observations=dict(result()["observations"],moon_shape="unknown")))):
        backend="openai"
        env={"KLD_SEMANTIC_QA_VISION_PROVIDER":backend,"OPENAI_API_KEY":"fake-token"}
        image_only=make_image_observer(env,client_factory=FakeFactory(raw))
        v=assess(image_only)
        assert v.decision=="UNKNOWN",(raw,v)
    assert assess(observer(result("cloudy"),backend="gemini")).accepted


def check_timeout_process_and_threads():
    def slow():
        class Client:
            class Comp:
                def create(self, **kwargs):
                    time.sleep(.55)
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result())))])
            chat=SimpleNamespace(completions=Comp())
        return Client()
    env={"KLD_SEMANTIC_QA_VISION_PROVIDER":"openai","OPENAI_API_KEY":"fake-token"}
    image_only=make_image_observer(env,client_factory=slow)
    verdict=assess(image_only,deadline=.1)
    assert (verdict.decision,verdict.reason)==("UNKNOWN","qa_timeout"),verdict
    with patch("kld_visual_semantic_qa.threading.active_count",return_value=2):
        verdict=assess(observer())
    assert (verdict.decision,verdict.reason)==("UNKNOWN","qa_unavailable"),verdict
    with patch("kld_visual_semantic_qa.os.name","nt"):
        verdict=assess(observer())
    assert verdict.decision=="UNKNOWN",verdict


def check_existing_client_factory_reuse():
    # Reuse the real gpt.py client entrypoints; patch their transport only.
    # This proves provider selection without instantiating an SDK client.
    import gpt
    factory=FakeFactory(json.dumps(result()))
    config={"KLD_SEMANTIC_QA_VISION_PROVIDER":"openai","OPENAI_API_KEY":"fake-token"}
    with patch.object(gpt,"_openai_client",factory),patch.object(gpt,"_gemini_openai_compat_client",side_effect=AssertionError("wrong backend")):
        chosen=make_image_observer(config)
        assert assess(chosen).accepted
    config={"KLD_SEMANTIC_QA_VISION_PROVIDER":"gemini","GEMINI_API_KEY":"fake-token"}
    with patch.object(gpt,"_gemini_openai_compat_client",factory),patch.object(gpt,"_openai_client",side_effect=AssertionError("wrong backend")):
        chosen=make_image_observer(config)
        assert assess(chosen).accepted
    with patch.object(gpt,"_openai_client",return_value=None):
        chosen=make_image_observer({"KLD_SEMANTIC_QA_VISION_PROVIDER":"openai","OPENAI_API_KEY":"fake-token"})
        assert assess(chosen).decision=="UNKNOWN"


def check_delivery_fallback_and_dedup():
    # The actual delivery ladder remains responsible for curated history,
    # dedup and send receipts. Only its image-generation dependency is faked.
    import test_kld_semantic_qa_seam as seam
    facts=synthetic_facts(weather="cloudy")
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        events=[]
        accepted=seam._deliver(
            root,generate=seam._generate(root),evaluate=seam._accept_all,
            cover_renderer=seam._cover_renderer(events),events=events,
            semantic_qa=wrap_image_only_observer(observer(result("cloudy"))),
            semantic_qa_facts=facts)
        assert accepted["result"]=="sent" and accepted["cover_attempted"] is False,accepted
        assert events==["photo:ai-1.branded.png","history"],events
    with TemporaryDirectory() as tmp:
        root=Path(tmp);events=[]
        rejected=seam._deliver(
            root,generate=seam._generate(root),evaluate=seam._accept_all,
            cover_renderer=seam._cover_renderer(events),events=events,
            semantic_qa=wrap_image_only_observer(observer(result("clear"))),
            semantic_qa_facts=facts)
        assert rejected["result"]=="fallback_sent" and rejected["backend"]=="local_informative_cover",rejected
        assert all(v["decision"] != "ACCEPT" for v in rejected["semantic_qa_results"]),rejected
        assert not any(e.startswith("photo:ai-") for e in events),events
        assert events==["cover","photo:cover.png","history"],events
    with TemporaryDirectory() as tmp:
        root=Path(tmp);events=[]
        # Dedup rejects AI before the observer: no fake provider call.
        def forbidden(_image):
            raise AssertionError("dedup must precede semantic provider")
        dedup=seam._ai_or_cover_evaluate(
            ai=lambda p:seam._duplicate(accepted=False,reason="exact_duplicate",distance=0),
            cover=seam._accept_all)
        rejected=seam._deliver(
            root,generate=seam._generate(root),evaluate=dedup,
            cover_renderer=seam._cover_renderer(events),events=events,
            semantic_qa=wrap_image_only_observer(forbidden),semantic_qa_facts=facts)
        assert not rejected.get("semantic_qa_results"),rejected
        assert not any(e.startswith("photo:ai-") for e in events),events


def check_cli_default_off_and_on_without_handoff_fail_closed():
    # Execute the actual CLI argument/control flow without calling an image
    # generator, a publisher or any provider. All output paths are temporary.
    import contextlib
    import io
    from tools import kld_visual_fixture_image as cli
    with TemporaryDirectory() as tmp:
        args=["--scenario","cloudy","--generate",
              "--result-file",str(Path(tmp)/"result.json"),
              "--prompt-metadata-file",str(Path(tmp)/"meta.json")]
        seen=[]
        def dry_execute(**kwargs):
            seen.append(kwargs)
            return {"result":"local_fixture","telegram_image_sent":False,"history_recorded":False}
        with patch.dict(os.environ,{"KLD_SEMANTIC_QA_ENABLED":"0"}),patch.object(cli,"execute_image_delivery",side_effect=dry_execute),contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(args)==0
        assert len(seen)==1 and seen[-1]["semantic_qa"] is None and seen[-1]["semantic_qa_facts"] is None
        seen.clear()
        with patch.dict(os.environ,{"KLD_SEMANTIC_QA_ENABLED":"1",
                                 "KLD_SEMANTIC_QA_VISION_PROVIDER":""}),patch.object(cli,"execute_image_delivery",side_effect=dry_execute),contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(args)==0
        assert len(seen)==1 and callable(seen[-1]["semantic_qa"])
        seen.clear()
        # ON and explicit backend still cannot use an unsigned or missing
        # collector handoff, regardless of key or backend configuration.
        with patch.dict(os.environ,{"KLD_SEMANTIC_QA_ENABLED":"1",
                                 "KLD_SEMANTIC_QA_VISION_PROVIDER":"openai",
                                 "OPENAI_API_KEY":"offline-fake"}),patch.object(cli,"execute_image_delivery",side_effect=dry_execute),contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(args)==0
        assert len(seen)==1 and callable(seen[-1]["semantic_qa"])
        assert seen[-1]["semantic_qa_facts"] is not None


def check_image_byte_gate():
    for value in (b"",b"not a picture",b"\x00"*100,IMAGE*200000):
        try: _image_type(value)
        except ValueError: pass
        else: raise AssertionError("malformed/oversized image accepted")
    assert _image_type(IMAGE)=="image/png"


def check_cli_gating_and_delivery_preservation():
    path=ROOT/"tools/kld_visual_fixture_image.py"
    body=path.read_text(encoding="utf-8")
    tree=ast.parse(body)
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="main")
    gated=[]
    for node in ast.walk(main):
        if isinstance(node,ast.If) and isinstance(node.test,ast.Name) and node.test.id=="semantic_enabled":
            gated.append(node)
    assert len(gated)==1
    gated_text=ast.get_source_segment(body,gated[0])
    assert "make_image_observer()" in gated_text
    assert "select_run_injection" in gated_text
    assert "semantic_source_file" in gated_text
    assert "image_observer=make_image_observer()" in gated_text
    assert "select_semantic_injection" in gated_text  # explicit OFF branch
    assert not any("make_image_observer" in ast.get_source_segment(body, n) for n in main.body if n is not gated[0] and isinstance(n,ast.Expr))


def main():
    cases=[check_off_and_cold,check_real_run_bound_bridge_and_image_only,
           check_storm_precipitation_visibility_moon,check_malformed_and_ambiguous,
           check_timeout_process_and_threads,check_existing_client_factory_reuse,check_delivery_fallback_and_dedup,check_cli_default_off_and_on_without_handoff_fail_closed,check_image_byte_gate,
           check_cli_gating_and_delivery_preservation]
    for test in cases:
        test()
        print("PASS",test.__name__)
    assert not ATTEMPTS,ATTEMPTS
    print(f"PASS KLD R5-F offline image-only provider: {len(cases)}/{len(cases)}; network=0")

if __name__=="__main__":main()
