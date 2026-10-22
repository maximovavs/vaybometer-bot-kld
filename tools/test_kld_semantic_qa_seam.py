#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for the injection-only KLD semantic QA seam.

The real ``execute_image_delivery`` ladder is exercised with fixture
providers and an injected evaluator.  Production OFF-by-default; explicit opt-in without sourced facts fails closed.
"""

from __future__ import annotations

import ast
import json
import multiprocessing
import socket
import time
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

NETWORK_ATTEMPTS: list[object] = []


def _deny_network(self, address, *args, **kwargs):
    NETWORK_ATTEMPTS.append(address)
    raise OSError("network disabled in KLD semantic QA seam checks")


socket.socket.connect = _deny_network  # type: ignore[method-assign]
socket.socket.connect_ex = _deny_network  # type: ignore[method-assign]

from test_kld_image_first import (  # noqa: E402
    ROOT,
    _args,
    _bounded_cover_renderer,
    _cover_renderer,
    _duplicate,
    _image,
    _orchestrate,
    _record,
    _write_delivery_fixture,
    MESSAGE,
)
from kld_visual_semantic_qa import SCHEMA, evaluate_semantic_qa  # noqa: E402
from tools.kld_visual_fixture_image import build_payload, execute_image_delivery  # noqa: E402

TARGET_DATE = "2026-07-20"
FACTS = {
    "target_date": TARGET_DATE,
    "post_type": "evening",
    "weather_target_date": TARGET_DATE,
    "visibility_target_date": TARGET_DATE,
    "lunar_target_date": TARGET_DATE,
    "lunar_phase": "waxing_crescent",
    "visibility_condition": "reduced_visibility",
    "visibility_forecast_window": "tomorrow_morning",
    "forecast_window": "tomorrow_morning",
}
OBSERVATIONS = {
    "moon_visibility": "not_visible",
    "moon_shape": "not_applicable",
    "dominant_moon": "no",
    "visibility_appearance": "mist",
    "weather_appearance": "cloudy",
}


def _response(image: bytes, decision: str = "ACCEPT", **overrides):
    payload = {
        "qa_schema_version": SCHEMA,
        "model_or_evaluator_version": "fixture-evaluator-1",
        "raw_image_sha256": sha256(image).hexdigest(),
        "decision": decision,
        "reason_codes": [] if decision == "ACCEPT" else ["fixture_" + decision.lower()],
        "observations": dict(OBSERVATIONS),
    }
    payload.update(overrides)
    return payload


class _ProcessCalls:
    """Fork-safe call ledger. R4 assertions examine real child-side calls.

    No Manager TCP/UNIX socket or network access: use a one-way OS pipe and
    a shared integer. The evaluator increments in the child, then the parent
    reads exactly the recorded arguments after the child's bounded shutdown.
    """
    def __init__(self) -> None:
        self._reader, self._writer = multiprocessing.Pipe(duplex=False)
        self._counter = multiprocessing.Value("i", 0, lock=False)
        self._seen = []

    def append(self, args) -> None:
        self._writer.send(args)
        self._counter.value += 1

    def __len__(self) -> int:
        return self._counter.value

    def _drain(self) -> None:
        while self._reader.poll(0):
            self._seen.append(self._reader.recv())

    def __getitem__(self, index):
        self._drain()
        return self._seen[index]

    def __eq__(self, other):
        self._drain()
        return self._seen == other


def _evaluator(calls: _ProcessCalls, decide):
    def evaluate(image: bytes, facts):
        calls.append((image, dict(facts)))
        return decide(len(calls), image)

    return evaluate


def _ai_or_cover_evaluate(*, ai, cover):
    """Fixture dedup that tells AI candidates (ai-*.png) from local covers."""

    def evaluate(path, **_kwargs):
        return ai(path) if Path(path).name.startswith("ai-") else cover(path)

    return evaluate


def _generate(root: Path):
    count = 0

    def generate(**_kwargs):
        nonlocal count
        count += 1
        return _image(root / f"ai-{count}.png", (60 + count, 100, 140))

    return generate


def _deliver(root: Path, *, generate, evaluate, cover_renderer, events, semantic_qa=None,
             semantic_qa_facts=None, timeout_s=None, omit_qa=False, presentation_renderer=None,
             post_type="evening"):
    args = _args(root, post_type=post_type)
    visibility = {
        "visibility_condition": "reduced_visibility",
        "morning_min_visibility_m": 5500,
        "reported_visibility_threshold_m": 6000,
    }
    kwargs = {}
    if not omit_qa:
        kwargs.update(semantic_qa=semantic_qa, semantic_qa_facts=semantic_qa_facts)
        if timeout_s is not None:
            kwargs["semantic_qa_timeout_s"] = timeout_s
    if presentation_renderer is not None:
        kwargs["presentation_renderer"] = presentation_renderer
    return execute_image_delivery(
        args=args,
        message=MESSAGE,
        initial_payload=build_payload(MESSAGE, "test", post_type=post_type, visibility_context=visibility),
        visibility_context=visibility,
        history_path=root / "history.json",
        generate_image=generate,
        evaluate_candidate=evaluate,
        cover_renderer=cover_renderer,
        validate_cover=lambda *_a, **_k: {"valid": True, "errors": []},
        send_photo=lambda path, *_a, **_k: events.append("photo:" + Path(path).name) or 901,
        record_publication=_record(events),
        **kwargs,
    )


def _accept_all(*_args, **_kwargs):
    return _duplicate(accepted=True, reason="accepted", distance=22)


def qa_accept_publishes_hash_bound_raw_candidate() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        calls = _ProcessCalls()
        outcome = _deliver(
            root,
            generate=_generate(root),
            evaluate=_accept_all,
            cover_renderer=_cover_renderer(events),
            events=events,
            semantic_qa=_evaluator(calls, lambda _n, image: _response(image)),
            semantic_qa_facts=FACTS,
        )
        assert outcome["result"] == "sent" and outcome["backend"] == "pollinations"
        assert outcome["cover_attempted"] is False
        assert len(calls) == 1
        raw = (root / "ai-1.png").read_bytes()
        assert calls[0][0] == raw and calls[0][1]["target_date"] == TARGET_DATE
        qa = outcome["semantic_qa_results"]
        assert [item["decision"] for item in qa] == ["ACCEPT"]
        assert qa[0]["image_sha256"] == sha256(raw).hexdigest() == outcome["source_sha256"]
        assert qa[0]["image_sha256"] != outcome["published_sha256"]
        assert outcome["provider_attempts"][0]["semantic_qa"]["decision"] == "ACCEPT"
        assert events == ["photo:ai-1.branded.png", "history"]


def qa_reject_rotates_to_next_candidate() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        calls = _ProcessCalls()

        def decide(n, image):
            if n == 1:
                return _response(image, "REJECT", reason_codes=["moon_phase_conflict"])
            return _response(image)

        outcome = _deliver(
            root,
            generate=_generate(root),
            evaluate=_accept_all,
            cover_renderer=_cover_renderer(events),
            events=events,
            semantic_qa=_evaluator(calls, decide),
            semantic_qa_facts=FACTS,
        )
        assert outcome["result"] == "sent" and outcome["cover_attempted"] is False
        assert [item["decision"] for item in outcome["semantic_qa_results"]] == ["REJECT", "ACCEPT"]
        assert outcome["semantic_qa_results"][0]["reason"] == "moon_phase_conflict"
        attempts = outcome["provider_attempts"]
        assert len(attempts) == 2
        assert attempts[0]["scene_family"] != attempts[1]["scene_family"]
        assert outcome["source_sha256"] == sha256((root / "ai-2.png").read_bytes()).hexdigest()
        assert events == ["photo:ai-2.branded.png", "history"]


def _all_rejected(root: Path, decide, *, facts=FACTS, timeout_s=None, post_type="evening"):
    events: list[str] = []
    calls = _ProcessCalls()
    outcome = _deliver(
        root,
        generate=_generate(root),
        evaluate=_accept_all,
        cover_renderer=_cover_renderer(events),
        events=events,
        semantic_qa=_evaluator(calls, decide),
        semantic_qa_facts=facts,
        timeout_s=timeout_s,
        post_type=post_type,
    )
    return outcome, events, calls


def qa_unknown_on_every_candidate_uses_curated_fallback() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        outcome, events, calls = _all_rejected(root, lambda _n, image: _response(image, "UNKNOWN"))
        assert len(calls) == 3, "local cover must not be routed through semantic QA"
        assert outcome["result"] == "fallback_sent"
        assert outcome["backend"] == "local_informative_cover"
        assert outcome["local_cover_published"] is True
        assert outcome["fallback_reason"] == "semantic_qa_rejected"
        assert outcome["error_type"] == "CandidateRejected"
        assert "semantic_qa:UNKNOWN:fixture_unknown" in outcome["error_message"]
        assert [item["decision"] for item in outcome["semantic_qa_results"]] == ["UNKNOWN"] * 3
        assert events == ["cover", "photo:cover.png", "history"]


def malformed_timeout_and_failing_evaluators_fail_closed() -> None:
    def hang(_n, image):
        time.sleep(1.0)
        return _response(image)

    def boom(_n, _image):
        raise RuntimeError("fixture evaluator crash")

    bad_obs = dict(OBSERVATIONS, moon_shape="banana")
    full_disk = dict(OBSERVATIONS, moon_visibility="visible", moon_shape="full_disk", dominant_moon="yes")
    fog = dict(OBSERVATIONS, visibility_appearance="dense_fog")
    cases = [
        (hang, "UNKNOWN", "qa_timeout", {"timeout_s": 0.1}),
        (boom, "UNKNOWN", "qa_unavailable", {}),
        (lambda _n, _i: "ACCEPT", "UNKNOWN", "schema_error", {}),
        (lambda _n, i: _response(i, qa_schema_version="v0"), "UNKNOWN", "schema_error", {}),
        (lambda _n, i: _response(i, model_or_evaluator_version=""), "UNKNOWN", "schema_error", {}),
        (lambda _n, i: _response(i, raw_image_sha256="0" * 64), "UNKNOWN", "image_hash_mismatch", {}),
        (lambda _n, i: _response(i, decision="accept"), "UNKNOWN", "invalid_decision", {}),
        (lambda _n, i: _response(i, observations=None), "UNKNOWN", "missing_observations", {}),
        (lambda _n, i: _response(i, observations=bad_obs), "UNKNOWN", "invalid_observations", {}),
        (lambda _n, i: _response(i, reason_codes="ok"), "UNKNOWN", "invalid_reason_codes", {}),
        (lambda _n, i: _response(i, reason_codes=["invented_dense_fog"]), "UNKNOWN", "contradictory_verdict", {}),
        (lambda _n, i: _response(i, observations=dict(OBSERVATIONS, weather_appearance="ambiguous")),
         "UNKNOWN", "insufficient_visual_evidence", {}),
        (lambda _n, i: _response(i, observations=full_disk), "REJECT", "moon_phase_conflict", {}),
        (lambda _n, i: _response(i, observations=fog), "REJECT", "invented_dense_fog",
         {"facts": dict(FACTS, visibility_condition="clear")}),
    ]
    for decide, decision, reason, extra in cases:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            started = time.monotonic()
            outcome, events, calls = _all_rejected(root, decide, **extra)
            assert len(calls) == 3, reason
            results = outcome["semantic_qa_results"]
            assert [(item["decision"], item["reason"]) for item in results] == [(decision, reason)] * 3, (reason, results)
            assert outcome["backend"] == "local_informative_cover", reason
            assert outcome["fallback_reason"] == "semantic_qa_rejected", reason
            assert not any(event.startswith("photo:ai-") for event in events), reason
            if reason == "qa_timeout":
                assert time.monotonic() - started < 2.5, "timeout must bound each QA call"


def codex_p2_ambiguous_and_contradictory_accept_fail_closed() -> None:
    """Codex P2: no uncertain or contradictory ACCEPT can publish an AI image."""
    cases = (
        ("unclear_shape", dict(OBSERVATIONS, moon_visibility="visible", moon_shape="unclear"),
         "insufficient_visual_evidence"),
        ("unclear_dominance", dict(OBSERVATIONS, moon_visibility="visible", moon_shape="crescent",
                                   dominant_moon="unclear"), "insufficient_visual_evidence"),
        ("hidden_full_disk", dict(OBSERVATIONS, moon_visibility="not_visible", moon_shape="full_disk",
                                  dominant_moon="yes"), "contradictory_observations"),
        ("hidden_crescent", dict(OBSERVATIONS, moon_visibility="not_visible", moon_shape="crescent"),
         "contradictory_observations"),
        ("hidden_but_dominant", dict(OBSERVATIONS, moon_visibility="not_visible",
                                     moon_shape="not_applicable", dominant_moon="yes"),
         "contradictory_observations"),
        ("visible_without_shape", dict(OBSERVATIONS, moon_visibility="visible",
                                       moon_shape="not_applicable"), "contradictory_observations"),
    )
    for label, observations, reason in cases:
        image = ("codex-p2-" + label).encode("ascii")
        verdict = evaluate_semantic_qa(
            lambda raw, _facts: _response(raw, observations=observations), image, FACTS,
            expected_target_date=TARGET_DATE, expected_post_type="evening",
        )
        assert (verdict.decision, verdict.reason) == ("UNKNOWN", reason), (label, verdict)
        with TemporaryDirectory() as tmp:
            outcome, events, calls = _all_rejected(
                Path(tmp), lambda _n, raw: _response(raw, observations=observations)
            )
            assert len(calls) == 3, label
            assert all((item["decision"], item["reason"]) == ("UNKNOWN", reason)
                       for item in outcome["semantic_qa_results"]), (label, outcome)
            assert outcome["backend"] == "local_informative_cover", label
            assert outcome["fallback_reason"] == "semantic_qa_rejected", label
            assert not any(event.startswith("photo:ai-") for event in events), label

    valid_cases = (
        dict(OBSERVATIONS, moon_visibility="visible", moon_shape="crescent", dominant_moon="no"),
        dict(OBSERVATIONS, moon_visibility="not_visible", moon_shape="not_applicable",
             dominant_moon="no"),
    )
    for observations in valid_cases:
        verdict = evaluate_semantic_qa(
            lambda raw, _facts: _response(raw, observations=observations), b"valid-moon-control",
            FACTS, expected_target_date=TARGET_DATE, expected_post_type="evening",
        )
        assert (verdict.decision, verdict.reason) == ("ACCEPT", "observed_consistent"), verdict


def codex_p2_lunar_phase_shape_matrix_fail_closed() -> None:
    """R2m: all eight known phases must match a visible moon's shape."""
    valid_shapes = {
        "new": None,
        "waxing_crescent": "crescent",
        "first_quarter": "quarter",
        "waxing_gibbous": "gibbous",
        "full": "full_disk",
        "waning_gibbous": "gibbous",
        "last_quarter": "quarter",
        "waning_crescent": "crescent",
    }
    shapes = ("crescent", "quarter", "gibbous", "full_disk")
    for phase, allowed_shape in valid_shapes.items():
        facts = dict(FACTS, lunar_phase=phase)
        for shape in shapes:
            observations = dict(OBSERVATIONS, moon_visibility="visible", moon_shape=shape)
            verdict = evaluate_semantic_qa(
                lambda img, _f, observations=observations: _response(img, observations=observations),
                (phase + ":" + shape).encode("ascii"), facts,
                expected_target_date=TARGET_DATE, expected_post_type="evening",
            )
            expected = ("ACCEPT", "observed_consistent") if shape == allowed_shape else (
                "REJECT", "moon_phase_conflict"
            )
            assert (verdict.decision, verdict.reason) == expected, (phase, shape, verdict)
        # Correctly hidden Moon is compatible with every known phase.
        hidden = evaluate_semantic_qa(
            lambda img, _f: _response(img), phase.encode("ascii"), facts,
            expected_target_date=TARGET_DATE, expected_post_type="evening",
        )
        assert (hidden.decision, hidden.reason) == ("ACCEPT", "observed_consistent"), phase

    for phase in (None, "", "unknown", "unrecognised"):
        facts = dict(FACTS, lunar_phase=phase)
        image = b"lunar-phase-unknown"
        observations = dict(OBSERVATIONS, moon_visibility="visible", moon_shape="crescent")
        verdict = evaluate_semantic_qa(
            lambda raw, _f: _response(raw, observations=observations), image, facts,
            expected_target_date=TARGET_DATE, expected_post_type="evening",
        )
        assert (verdict.decision, verdict.reason) == ("UNKNOWN", "lunar_phase_unknown"), phase
        # A clearly moonless image asserts no phase-specific lunar fact.
        hidden = evaluate_semantic_qa(
            lambda raw, _f: _response(raw), image, facts,
            expected_target_date=TARGET_DATE, expected_post_type="evening",
        )
        assert (hidden.decision, hidden.reason) == ("ACCEPT", "observed_consistent"), phase

    # Exercise the genuine provider -> semantic veto -> curated fallback path.
    for phase, shape, expected_decision, expected_reason in (
        ("new", "crescent", "REJECT", "moon_phase_conflict"),
        ("first_quarter", "full_disk", "REJECT", "moon_phase_conflict"),
        ("full", "crescent", "REJECT", "moon_phase_conflict"),
        ("waning_gibbous", "quarter", "REJECT", "moon_phase_conflict"),
        ("unknown", "crescent", "UNKNOWN", "lunar_phase_unknown"),
    ):
        observations = dict(OBSERVATIONS, moon_visibility="visible", moon_shape=shape)
        facts = dict(FACTS, lunar_phase=phase)
        with TemporaryDirectory() as tmp:
            outcome, events, calls = _all_rejected(
                Path(tmp), lambda _n, raw: _response(raw, observations=observations), facts=facts,
            )
            assert len(calls) == 3
            assert all((item["decision"], item["reason"]) == (expected_decision, expected_reason)
                       for item in outcome["semantic_qa_results"]), (phase, shape, outcome)
            assert outcome["backend"] == "local_informative_cover"
            assert outcome["fallback_reason"] == "semantic_qa_rejected"
            assert not any(event.startswith("photo:ai-") for event in events)


def codex_p2_morning_dominant_moon_fail_closed() -> None:
    """R2r: a dominant Moon never passes semantic QA for a morning post."""
    morning_facts = dict(FACTS, post_type="morning", visibility_forecast_window="current_morning",
                         forecast_window="current_morning")
    dominant = dict(OBSERVATIONS, moon_visibility="visible", moon_shape="crescent",
                    dominant_moon="yes")
    controls = (
        ("morning", morning_facts, dominant, "REJECT", "morning_dominant_moon"),
        ("morning", morning_facts, dict(dominant, dominant_moon="no"),
         "REJECT", "morning_moon_visible"),
        ("evening", FACTS, dominant, "ACCEPT", "observed_consistent"),
        ("morning", dict(morning_facts, lunar_phase="unknown"), dominant,
         "UNKNOWN", "lunar_phase_unknown"),
        ("morning", dict(morning_facts, lunar_phase="full"), dominant,
         "REJECT", "moon_phase_conflict"),
        ("morning", morning_facts, OBSERVATIONS, "ACCEPT", "observed_consistent"),
    )
    for post_type, facts, observations, decision, reason in controls:
        result = evaluate_semantic_qa(
            lambda raw, _facts, observations=observations: _response(raw, observations=observations),
            b"morning-moon-control", facts, expected_target_date=TARGET_DATE,
            expected_post_type=post_type,
        )
        assert (result.decision, result.reason) == (decision, reason), (post_type, facts, result)

    # Temporal mismatch must prevent evaluator calls even for morning imagery.
    calls = []
    mismatched = evaluate_semantic_qa(
        lambda *_args: calls.append(True), b"wrong-window", morning_facts,
        expected_target_date=TARGET_DATE, expected_post_type="evening",
    )
    assert (mismatched.decision, mismatched.reason, calls) == ("UNKNOWN", "temporal_mismatch", [])

    # Exercise the real candidate -> veto -> curated fallback seam for morning.
    with TemporaryDirectory() as tmp:
        outcome, events, calls = _all_rejected(
            Path(tmp), lambda _n, raw: _response(raw, observations=dominant),
            facts=morning_facts, post_type="morning",
        )
        assert len(calls) == 3
        assert all((item["decision"], item["reason"]) == ("REJECT", "morning_dominant_moon")
                   for item in outcome["semantic_qa_results"]), outcome
        assert outcome["backend"] == "local_informative_cover"
        assert outcome["fallback_reason"] == "semantic_qa_rejected"
        assert not any(event.startswith("photo:ai-") for event in events)


def codex_c_morning_moon_and_visibility_window_fail_closed() -> None:
    """Any visible morning moon is vetoed; reduced visibility must show in its window."""
    morning = dict(FACTS, post_type="morning", visibility_forecast_window="current_morning",
                   forecast_window="current_morning")
    moon = dict(OBSERVATIONS, moon_visibility="visible", moon_shape="crescent", dominant_moon="no")

    def check(facts, observations, post_type, expected):
        result = evaluate_semantic_qa(
            lambda raw, _f, observations=observations: _response(raw, observations=observations),
            repr((sorted(facts.items()), sorted(observations.items()), post_type)).encode(), facts,
            expected_target_date=TARGET_DATE, expected_post_type=post_type,
        )
        assert (result.decision, result.reason) == expected, (facts, observations, post_type, result)

    check(morning, moon, "morning", ("REJECT", "morning_moon_visible"))
    check(morning, dict(moon, dominant_moon="yes"), "morning", ("REJECT", "morning_dominant_moon"))
    check(morning, OBSERVATIONS, "morning", ("ACCEPT", "observed_consistent"))
    check(dict(morning, lunar_phase="unknown"), moon, "morning", ("UNKNOWN", "lunar_phase_unknown"))
    check(dict(morning, lunar_phase="full"), moon, "morning", ("REJECT", "moon_phase_conflict"))
    check(FACTS, moon, "evening", ("ACCEPT", "observed_consistent"))
    check(FACTS, dict(moon, dominant_moon="yes"), "evening", ("ACCEPT", "observed_consistent"))

    normal = dict(OBSERVATIONS, visibility_appearance="normal")
    for condition in ("dense_fog", "fog", "mist", "reduced_visibility", "dust_haze"):
        same = dict(FACTS, visibility_condition=condition)
        check(same, normal, "evening", ("REJECT", "reduced_visibility_not_shown"))
        check(same, OBSERVATIONS, "evening", ("ACCEPT", "observed_consistent"))
        other_window = dict(same, forecast_window="current_morning")
        check(other_window, normal, "evening", ("UNKNOWN", "visibility_window_mismatch"))
        for window in (None, "", "none", "unknown_window"):
            missing = dict(same)
            if window is None:
                missing.pop("visibility_forecast_window")
            else:
                missing["visibility_forecast_window"] = window
            check(missing, normal, "evening", ("UNKNOWN", "visibility_unknown"))
    check(dict(FACTS, visibility_condition="clear", visibility_forecast_window="none"), normal,
          "evening", ("ACCEPT", "observed_consistent"))
    check(dict(FACTS, visibility_condition="mixed_visibility"), normal, "evening",
          ("ACCEPT", "observed_consistent"))
    for facts in (
        {k: v for k, v in FACTS.items() if k != "visibility_condition"},
        dict(FACTS, visibility_condition="unknown"),
        {k: v for k, v in FACTS.items() if k != "forecast_window"},
        dict(FACTS, forecast_window="none"),
        dict(FACTS, visibility_condition="clear", forecast_window=""),
    ):
        check(facts, OBSERVATIONS, "evening", ("UNKNOWN", "visibility_unknown"))
    check(dict(FACTS, visibility_condition="clear"), dict(OBSERVATIONS, visibility_appearance="dense_fog"),
          "evening", ("REJECT", "invented_dense_fog"))

    for facts, observations, post_type, expected in (
        (morning, moon, "morning", ("REJECT", "morning_moon_visible")),
        (FACTS, normal, "evening", ("REJECT", "reduced_visibility_not_shown")),
        ({k: v for k, v in FACTS.items() if k != "forecast_window"}, OBSERVATIONS, "evening",
         ("UNKNOWN", "visibility_unknown")),
    ):
        with TemporaryDirectory() as tmp:
            outcome, events, calls = _all_rejected(
                Path(tmp), lambda _n, raw, observations=observations: _response(raw, observations=observations),
                facts=facts, post_type=post_type,
            )
            assert len(calls) == 3, expected
            assert all((item["decision"], item["reason"]) == expected
                       for item in outcome["semantic_qa_results"]), (expected, outcome)
            assert outcome["backend"] == "local_informative_cover"
            assert outcome["fallback_reason"] == "semantic_qa_rejected"
            assert not any(event.startswith("photo:ai-") for event in events)



def codex_c1_forecast_window_fail_closed() -> None:
    """Unknown or mismatched windows veto ACCEPT, never invent weather facts."""
    normal = dict(OBSERVATIONS, visibility_appearance="normal")
    fog = dict(OBSERVATIONS, visibility_appearance="dense_fog")

    def check(facts, observed, decision, reason, *, raw_decision="ACCEPT"):
        image = repr((facts, observed, raw_decision)).encode("utf-8")
        result = evaluate_semantic_qa(
            lambda raw, _f: _response(raw, raw_decision, observations=observed),
            image, facts, expected_target_date=TARGET_DATE, expected_post_type="evening",
        )
        assert (result.decision, result.reason) == (decision, reason), (facts, observed, result)

    for condition in ("dense_fog", "fog", "mist", "reduced_visibility", "dust_haze"):
        base = dict(FACTS, visibility_condition=condition)
        check(base, normal, "REJECT", "reduced_visibility_not_shown")
        check(base, OBSERVATIONS, "ACCEPT", "observed_consistent")
        mismatched = dict(base, forecast_window="current_morning")
        for observed in (normal, OBSERVATIONS, fog):
            check(mismatched, observed, "UNKNOWN", "visibility_window_mismatch")
        for bad in (None, "", "none", "unknown", "unknown_window", 0, [], {}):
            check(dict(base, forecast_window=bad), normal, "UNKNOWN", "visibility_unknown")
            check(dict(base, visibility_forecast_window=bad), normal, "UNKNOWN", "visibility_unknown")

    for condition in ("clear", "mixed_visibility"):
        base = dict(FACTS, visibility_condition=condition)
        check(base, normal, "ACCEPT", "observed_consistent")
        check(dict(base, visibility_forecast_window="none"), normal, "ACCEPT", "observed_consistent")
        for bad in (None, "", "none", "unknown", "unknown_window", 0, [], {}):
            check(dict(base, forecast_window=bad), normal, "UNKNOWN", "visibility_unknown")
        check(dict(base, forecast_window="current_morning"), normal,
              "UNKNOWN", "visibility_window_mismatch")
        check(dict(base, visibility_forecast_window="unknown_window"), normal,
              "UNKNOWN", "visibility_unknown")
        check(dict(base, visibility_forecast_window=[]), normal,
              "UNKNOWN", "visibility_unknown")

    check(dict(FACTS, visibility_condition="clear"), fog, "REJECT", "invented_dense_fog")
    check(dict(FACTS, visibility_condition="clear", forecast_window=""), fog,
          "UNKNOWN", "visibility_unknown")
    check(dict(FACTS, visibility_condition="clear", forecast_window="current_morning"), fog,
          "UNKNOWN", "visibility_window_mismatch")
    for bad_condition in (None, "", "unknown", 0, [], {}):
        check(dict(FACTS, visibility_condition=bad_condition), normal,
              "UNKNOWN", "visibility_unknown")

    # Non-ACCEPT evaluator decisions remain authoritative after validation.
    check(dict(FACTS, forecast_window="unknown_window"), normal,
          "REJECT", "fixture_reject", raw_decision="REJECT")
    check(dict(FACTS, forecast_window="unknown_window"), normal,
          "UNKNOWN", "fixture_unknown", raw_decision="UNKNOWN")

    # Exercise the real seam, not only the direct verdict normalizer.
    for facts, observed, expected_reason in (
        (dict(FACTS, forecast_window="current_morning"), normal, "visibility_window_mismatch"),
        (dict(FACTS, forecast_window="unknown_window"), OBSERVATIONS, "visibility_unknown"),
        (dict(FACTS, visibility_condition="clear", forecast_window=""), fog, "visibility_unknown"),
    ):
        with TemporaryDirectory() as tmp:
            outcome, events, calls = _all_rejected(
                Path(tmp), lambda _n, raw, observed=observed: _response(raw, observations=observed),
                facts=facts,
            )
            assert len(calls) == 3
            assert all((v["decision"], v["reason"]) == ("UNKNOWN", expected_reason)
                       for v in outcome["semantic_qa_results"]), outcome
            assert outcome["backend"] == "local_informative_cover"
            assert outcome["fallback_reason"] == "semantic_qa_rejected"
            assert not any(event.startswith("photo:ai-") for event in events)

def temporal_mismatch_is_unknown_without_calling_evaluator() -> None:
    for facts in (
        dict(FACTS, target_date="2026-07-19"),
        dict(FACTS, post_type="morning"),
        dict(FACTS, weather_target_date="2026-07-21"),
        dict(FACTS, visibility_target_date="2026-07-19"),
        dict(FACTS, lunar_target_date=""),
        None,
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome, events, calls = _all_rejected(root, lambda _n, image: _response(image), facts=facts)
            assert calls == [], facts
            assert {item["reason"] for item in outcome["semantic_qa_results"]} == {"temporal_mismatch"}
            assert outcome["backend"] == "local_informative_cover"
            assert not any(event.startswith("photo:ai-") for event in events)


def unreadable_image_and_hash_are_bound_to_bytes() -> None:
    calls: list[object] = []
    verdict = evaluate_semantic_qa(lambda *a: calls.append(a), None, FACTS,
                                   expected_target_date=TARGET_DATE, expected_post_type="evening")
    assert (verdict.decision, verdict.reason, calls) == ("UNKNOWN", "image_unreadable", [])
    image = b"fixture-bytes"
    other = b"other-bytes"
    ok = evaluate_semantic_qa(lambda img, _f: _response(img), image, FACTS,
                              expected_target_date=TARGET_DATE, expected_post_type="evening")
    swapped = evaluate_semantic_qa(lambda _img, _f: _response(other), image, FACTS,
                                   expected_target_date=TARGET_DATE, expected_post_type="evening")
    assert ok.accepted and ok.image_sha256 == sha256(image).hexdigest()
    assert (swapped.decision, swapped.reason) == ("UNKNOWN", "image_hash_mismatch")


def upstream_guard_rejections_never_invoke_semantic_qa() -> None:
    for reason, fallback in (
        ("content_guard:summer_dry_steppe", "semantic_mismatch"),
        ("content_guard:season_snow_in_july", "semantic_mismatch"),
        ("scene_cooldown", "scene_policy_rejected"),
        ("near_duplicate", "near_duplicate"),
        ("exact_duplicate", "exact_duplicate"),
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events: list[str] = []
            calls = _ProcessCalls()
            outcome = _deliver(
                root,
                generate=_generate(root),
                evaluate=_ai_or_cover_evaluate(
                    ai=lambda _p: _duplicate(accepted=False, reason=reason, distance=3),
                    cover=lambda _p: _duplicate(accepted=True, reason="accepted", distance=30),
                ),
                cover_renderer=_cover_renderer(events),
                events=events,
                semantic_qa=_evaluator(calls, lambda _n, image: _response(image)),
                semantic_qa_facts=FACTS,
            )
            assert calls == [], reason
            assert "semantic_qa_results" not in outcome
            assert all("semantic_qa" not in item for item in outcome["provider_attempts"])
            assert outcome["fallback_reason"] == fallback, (reason, outcome["fallback_reason"])
            assert outcome["backend"] == "local_informative_cover"


def qa_rejections_keep_terminal_relaxation_for_older_near_duplicates() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        calls = _ProcessCalls()
        outcome = _deliver(
            root,
            generate=_generate(root),
            evaluate=_ai_or_cover_evaluate(
                ai=lambda _p: _duplicate(accepted=True, reason="accepted", distance=25),
                cover=lambda _p: _duplicate(
                    accepted=False, reason="near_duplicate", distance=0, matched_target_date="2026-07-19",
                ),
            ),
            cover_renderer=_bounded_cover_renderer(events),
            events=events,
            semantic_qa=_evaluator(calls, lambda _n, image: _response(image, "REJECT")),
            semantic_qa_facts=FACTS,
        )
        assert len(calls) == 3
        assert outcome["result"] == "fallback_sent"
        assert outcome["fallback_reason"] == "semantic_qa_rejected"
        assert outcome["dedup_reason"] == "near_duplicate_terminal_local_allowed"
        assert outcome["terminal_local_relaxation"]["used"] is True
        assert events == ["cover:kld_overcast_01", "cover:kld_overcast_02", "photo:cover.png", "history"]


def qa_rejections_with_blocked_covers_remain_text_only() -> None:
    for reason, matched in (("near_duplicate", TARGET_DATE), ("exact_duplicate", "2026-07-19")):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events: list[str] = []
            calls = _ProcessCalls()
            outcome = _deliver(
                root,
                generate=_generate(root),
                evaluate=_ai_or_cover_evaluate(
                    ai=lambda _p: _duplicate(accepted=True, reason="accepted", distance=25),
                    cover=lambda _p, r=reason, m=matched: _duplicate(
                        accepted=False, reason=r, distance=0, matched_target_date=m,
                    ),
                ),
                cover_renderer=_bounded_cover_renderer(events),
                events=events,
                semantic_qa=_evaluator(calls, lambda _n, image: _response(image, "UNKNOWN")),
                semantic_qa_facts=FACTS,
            )
            assert len(calls) == 3
            assert outcome["result"] == "skipped_duplicate", (reason, outcome["result"])
            assert outcome["telegram_image_sent"] is False and outcome["history_recorded"] is False
            assert not any(event.startswith("photo:") for event in events)
            final, order = _orchestrate(root, outcome)
            assert final["text_sent"] is True and order == ["preview", "image", "text"]


def qa_accept_keeps_presentation_failure_fallback() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        calls = _ProcessCalls()

        def fail_presentation(*_a, **_k):
            raise RuntimeError("fixture presentation failure")

        outcome = _deliver(
            root,
            generate=_generate(root),
            evaluate=_accept_all,
            cover_renderer=_cover_renderer(events),
            events=events,
            semantic_qa=_evaluator(calls, lambda _n, image: _response(image)),
            semantic_qa_facts=FACTS,
            presentation_renderer=fail_presentation,
        )
        assert len(calls) == 1
        assert outcome["fallback_reason"] == "presentation_failure"
        assert outcome["backend"] == "local_informative_cover"
        assert events == ["cover", "photo:cover.png", "history"]


def existing_receipts_skip_image_step_and_semantic_qa() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_delivery_fixture(root, image_id=711)
        outcome, order = _orchestrate(
            root,
            {"result": "sent", "telegram_image_sent": True, "telegram_image_message_id": 999},
            production=True,
        )
        assert order == ["preview", "text"], "image subprocess (and its QA) must not run"
        assert outcome["image_send_skipped_receipt"] is True
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_delivery_fixture(root, image_id=711, text_ids=[712])
        outcome, order = _orchestrate(root, {"result": "sent"}, production=True)
        assert outcome["result"] == "skipped_delivery_receipt_complete" and order == []


def default_without_evaluator_is_unchanged() -> None:
    def normalise(outcome, root):
        return json.loads(json.dumps(outcome, sort_keys=True, default=str).replace(str(root), "<ROOT>"))

    results = []
    for omit in (True, False):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events: list[str] = []
            outcome = _deliver(
                root,
                generate=_generate(root),
                evaluate=_accept_all,
                cover_renderer=_cover_renderer(events),
                events=events,
                omit_qa=omit,
                semantic_qa_facts=FACTS,
            )
            assert "semantic_qa_results" not in outcome
            assert all("semantic_qa" not in item for item in outcome["provider_attempts"])
            assert outcome["result"] == "sent" and events == ["photo:ai-1.branded.png", "history"]
            results.append(normalise(outcome, root))
    assert results[0] == results[1]


def production_call_site_off_default_and_fail_closed_without_sources() -> None:
    source = (ROOT / "tools" / "kld_visual_fixture_image.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "execute_image_delivery"
    ]
    assert len(calls) == 1
    # R4 explicitly injects the result of a selector. The production call site
    # must never directly construct an evaluator or promote an unset flag.
    kwargs = {kw.arg: kw.value for kw in calls[0].keywords}
    assert isinstance(kwargs["semantic_qa"], ast.Attribute)
    assert isinstance(kwargs["semantic_qa_facts"], ast.Attribute)
    assert isinstance(kwargs["semantic_qa"].value, ast.Name)
    assert kwargs["semantic_qa"].value.id == "semantic_injection"
    assert isinstance(kwargs["semantic_qa_facts"].value, ast.Name)
    assert kwargs["semantic_qa_facts"].value.id == "semantic_injection"
    selects = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and getattr(node.func, "id", "") == "select_semantic_injection"]
    assert len(selects) == 1, "only the known fail-closed selector may inject"
    selector_args = {kw.arg: kw.value for kw in selects[0].keywords}
    assert isinstance(selector_args["source_snapshot"], ast.Constant) and selector_args["source_snapshot"].value is None
    assert isinstance(selector_args["observer"], ast.Constant) and selector_args["observer"].value is None
    assert 'os.getenv("KLD_SEMANTIC_QA_ENABLED", "")' in source
    assert 'in {"1", "true", "yes", "on"}' in source
    from kld_semantic_qa_wiring import select_semantic_injection
    off = select_semantic_injection(enabled=False, source_snapshot=None,
        target_date=TARGET_DATE, post_type="evening", publication_target_date=TARGET_DATE, observer=None)
    assert off.status == "disabled" and off.evaluator is None and off.facts is None
    on = select_semantic_injection(enabled=True, source_snapshot=None,
        target_date=TARGET_DATE, post_type="evening", publication_target_date=TARGET_DATE, observer=None)
    assert on.status == "fail_closed" and callable(on.evaluator)
    result = evaluate_semantic_qa(on.evaluator, b"raw-image", on.facts,
        expected_target_date=TARGET_DATE, expected_post_type="evening")
    assert (result.decision, result.reason) == ("UNKNOWN", "qa_unavailable")
    seam = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "execute_image_delivery"
    )
    defaults = {arg.arg: value for arg, value in zip(seam.args.kwonlyargs, seam.args.kw_defaults)}
    assert isinstance(defaults["semantic_qa"], ast.Constant) and defaults["semantic_qa"].value is None
    importers = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if ".git" not in path.parts and "kld_visual_semantic_qa" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    production_importers = sorted(path for path in importers if not path.startswith("tools/test_"))
    assert production_importers == ["kld_semantic_qa_evaluator.py", "tools/kld_visual_fixture_image.py"], production_importers
    qa_source = (ROOT / "kld_visual_semantic_qa.py").read_text(encoding="utf-8")
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(qa_source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert imported <= {
        "__future__", "dataclasses", "hashlib", "typing", "json",
        "math", "multiprocessing", "os", "resource", "signal", "threading", "time",
    }, imported
    assert "concurrent" not in imported, "legacy unkillable thread pool forbidden"
    # Required imports for POSIX process isolation only; network clients,
    # provider SDKs and subprocess spawn are excluded by the exact allowlist.
    assert not ({"socket", "requests", "urllib", "http", "aiohttp", "openai",
                 "subprocess", "concurrent"} & imported)
    assert "ThreadPoolExecutor" not in qa_source
    assert "_kld_killable_image_observer" not in qa_source, "no marker bypass"
    for path in (ROOT / ".github" / "workflows").glob("*.y*ml"):
        assert "semantic_qa" not in path.read_text(encoding="utf-8") or path.name == "pr_checks.yml", path.name


def network_attempt_ledger_is_empty() -> None:
    assert NETWORK_ATTEMPTS == [], NETWORK_ATTEMPTS


TESTS = [
    qa_accept_publishes_hash_bound_raw_candidate,
    qa_reject_rotates_to_next_candidate,
    qa_unknown_on_every_candidate_uses_curated_fallback,
    malformed_timeout_and_failing_evaluators_fail_closed,
    codex_p2_ambiguous_and_contradictory_accept_fail_closed,
    codex_p2_lunar_phase_shape_matrix_fail_closed,
    codex_p2_morning_dominant_moon_fail_closed,
    codex_c_morning_moon_and_visibility_window_fail_closed,
    codex_c1_forecast_window_fail_closed,
    temporal_mismatch_is_unknown_without_calling_evaluator,
    unreadable_image_and_hash_are_bound_to_bytes,
    upstream_guard_rejections_never_invoke_semantic_qa,
    qa_rejections_keep_terminal_relaxation_for_older_near_duplicates,
    qa_rejections_with_blocked_covers_remain_text_only,
    qa_accept_keeps_presentation_failure_fallback,
    existing_receipts_skip_image_step_and_semantic_qa,
    default_without_evaluator_is_unchanged,
    production_call_site_off_default_and_fail_closed_without_sources,
    network_attempt_ledger_is_empty,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD semantic QA seam offline checks passed")


if __name__ == "__main__":
    main()
