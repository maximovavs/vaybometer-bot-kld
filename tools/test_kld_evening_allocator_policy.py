#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regressions for shared KLD evening prompt policy and provider allocator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kld_visual_dedup as dedup_module  # noqa: E402
from kld_visual_dedup import KldVisualDuplicateResult  # noqa: E402
from kld_visual_policy import (  # noqa: E402
    KLD_VISUAL_POLICY_VERSION,
    SUMMER_VEGETATION_CUE,
    scene_policy_rejection,
)
import tools.kld_visual_fixture_image as fixture_module  # noqa: E402
from tools.kld_visual_fixture_image import (  # noqa: E402
    FIXTURES,
    _candidate_payloads,
    _rejection_fallback_reason,
    _select_candidate_attempts,
    build_payload,
    execute_image_delivery,
)


def _args(root: Path, *, scenario: str = "storm") -> argparse.Namespace:
    return argparse.Namespace(
        scenario=scenario,
        message_file="",
        post_type="evening",
        generate=False,
        send_to_test=True,
        chat_id="test",
        caption="",
        history_namespace="test",
        result_file=str(root / "image_result.json"),
        prompt_metadata_file=str(root / "image_prompt_metadata.json"),
        cover_path=str(root / "cover.png"),
    )


def _ppm(path: Path, value: int = 90) -> str:
    width = 32
    height = 32
    raw = bytes([value, min(255, value + 20), min(255, value + 40)]) * width * height
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + raw)
    return str(path)


def _accepted() -> KldVisualDuplicateResult:
    return KldVisualDuplicateResult(
        accepted=True,
        reason="accepted",
        sha256="a" * 64,
        perceptual_hash="0" * 16,
        min_distance=24,
    )


def evening_prompt_is_scene_authoritative_and_summer_green() -> None:
    message = (
        "<b>🌅 Калининградская область завтра (08.08.2026)</b>\n"
        "🏙 Калининград — 22/15 °C • ☁️ облачно • 💨 4 м/с\n"
        "🌊 Балтийск — 20/15 °C • 🌊 19 °C\n"
        "#Калининград #погода\n"
    )
    payload = build_payload(message, "summer_evening", post_type="evening")
    prompt = payload["image_prompt"]
    assert "Base scene:" not in prompt
    assert "Controlled composition:" not in prompt
    assert prompt.count("Controlled scene:") == 1
    assert "Regional setting: one real Kaliningrad-region location" in prompt
    assert "Provider safety: no map, no satellite imagery" in prompt
    assert SUMMER_VEGETATION_CUE in prompt
    assert "dunes, pines, promenade, or Baltic sea horizon" not in prompt
    assert payload["metadata"]["prompt_version"].endswith("+" + KLD_VISUAL_POLICY_VERSION)
    assert f"prompt_version={payload['metadata']['prompt_version']}" in payload["cache_key"]


def winter_evening_prompt_does_not_force_summer_green() -> None:
    message = (
        "<b>🌅 Калининградская область завтра (08.12.2026)</b>\n"
        "🏙 Калининград — 3/-1 °C • ☁️ облачно • 💨 4 м/с\n"
        "#Калининград #погода\n"
    )
    payload = build_payload(message, "winter_evening", post_type="evening")
    assert SUMMER_VEGETATION_CUE not in payload["image_prompt"]
    assert "Controlled scene:" in payload["image_prompt"]
    assert "Base scene:" not in payload["image_prompt"]


def storm_allocator_keeps_last_three_scenes_hard_blocked() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.json"
        blocked = [
            "stormy_open_baltic",
            "baltiysk_breakwater",
            "svetlogorsk_cliff_coast",
        ]
        history_path.write_text(
            json.dumps(
                [
                    {"date": "2026-06-16", "scene_family": blocked[2], "composition": "old-c"},
                    {"date": "2026-06-17", "scene_family": blocked[1], "composition": "old-b"},
                    {"date": "2026-06-18", "scene_family": blocked[0], "composition": "old-a"},
                ]
            ),
            encoding="utf-8",
        )
        candidates, scene_cooldown, _ = _candidate_payloads(
            args=_args(root),
            message=FIXTURES["storm"],
            visibility_context=None,
            history_path=history_path,
            count=3,
        )
        scenes = [str(item["metadata"]["scene_family"]) for item in candidates]
        assert len(candidates) == 2
        assert len(set(scenes)) == 2
        assert set(scenes) == {"rainy_coastal_road", "elevated_baltic_overlook"}
        assert set(scene_cooldown) == set(blocked)
        assert not (set(scenes) & set(blocked))
        assert not any(item.get("composition_cooldown_relaxed") for item in candidates)


def composition_exhaustion_relaxes_oldest_only() -> None:
    blocked_scenes = [
        "kaliningrad_urban_coastal_view",
        "quiet_lagoon_coast",
        "zelenogradsk_promenade",
    ]
    blocked_compositions = [
        "open horizon with large sky",
        "pine-framed side composition",
        "foreground dune grass with open water behind",
        "wide diagonal shoreline composition",
    ]
    candidate_metadata = [
        (0, {"scene_family": blocked_scenes[0], "composition": "promenade railing foreground"}),
        (1, {"scene_family": blocked_scenes[1], "composition": "open horizon with large sky"}),
        (2, {"scene_family": blocked_scenes[2], "composition": "promenade railing foreground"}),
        (3, {"scene_family": "curonian_spit_dunes", "composition": "wide diagonal shoreline composition"}),
        (4, {"scene_family": "elevated_baltic_overlook", "composition": "pine-framed side composition"}),
    ]

    strict_attempts, _ = _select_candidate_attempts(
        candidate_metadata,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        count=3,
    )
    assert strict_attempts == [3]

    # Verify that no strict candidate exists before the helper's exhaustion fallback.
    strict_only = []
    for attempt, metadata in candidate_metadata:
        if metadata["scene_family"] in blocked_scenes:
            continue
        if metadata["composition"] in blocked_compositions:
            continue
        strict_only.append(attempt)
    assert strict_only == []

    attempts, relaxed = _select_candidate_attempts(
        candidate_metadata,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        count=3,
    )
    assert attempts == [3]
    assert relaxed == ["wide diagonal shoreline composition"]


def relaxed_candidate_reaches_provider_and_marks_final_gate() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.json"
        history_path.write_text("[]", encoding="utf-8")
        args = _args(root)
        initial = build_payload(FIXTURES["storm"], "storm", post_type="evening")
        candidate = {
            "variation_attempt": 17,
            "image_prompt": "offline prompt",
            "style_name": "offline-style",
            "cache_key": "offline-cache-key",
            "metadata": {
                "forecast_date": "2026-08-15",
                "target_date": "2026-08-15",
                "scene_family": "rainy_coastal_road",
                "composition": "wide diagonal shoreline composition",
                "prompt_version": "v6+kld_visual_policy_v4",
            },
            "composition_cooldown_relaxed": True,
            "composition_cooldown_relaxed_value": "wide diagonal shoreline composition",
            "composition_cooldown_relaxation_depth": 1,
        }
        events: list[str] = []
        captured: dict[str, object] = {}
        original_candidate_payloads = fixture_module._candidate_payloads
        try:
            fixture_module._candidate_payloads = lambda **kwargs: (
                [candidate],
                ["zelenogradsk_promenade", "quiet_lagoon_coast", "kaliningrad_urban_coastal_view"],
                [
                    "open horizon with large sky",
                    "pine-framed side composition",
                    "foreground dune grass with open water behind",
                    "wide diagonal shoreline composition",
                ],
            )

            def generate(**kwargs):
                events.append("provider")
                return _ppm(root / "candidate.ppm")

            def evaluate(path, **kwargs):
                captured.update(kwargs)
                return _accepted()

            outcome = fixture_module.execute_image_delivery(
                args=args,
                message=FIXTURES["storm"],
                initial_payload=initial,
                visibility_context=None,
                history_path=history_path,
                generate_image=generate,
                secondary_generate_image=None,
                evaluate_candidate=evaluate,
                cover_renderer=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cover not expected")),
                validate_cover=lambda *args, **kwargs: {"valid": True, "errors": []},
                send_photo=lambda *args, **kwargs: events.append("photo") or 321,
                record_publication=lambda **kwargs: events.append("history") or {"sha256": "c" * 64},
            )
        finally:
            fixture_module._candidate_payloads = original_candidate_payloads

        assert events == ["provider", "photo", "history"]
        assert outcome["provider_attempts"]
        assert outcome["candidate_pool"][0]["composition_cooldown_relaxed"] is True
        assert outcome["provider_attempts"][0]["composition_cooldown_relaxed"] is True
        assert captured["allow_composition_cooldown_relaxation"] is True


def composition_relaxation_does_not_weaken_other_guards() -> None:
    class Verdict:
        def __init__(self, valid: bool, reason: str = "accepted"):
            self.valid = valid
            self.reason = reason

        def to_dict(self):
            return {"valid": self.valid, "reason": self.reason}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        image_path = Path(_ppm(root / "candidate.ppm", 111))
        history_path = root / "history.json"
        original_guard = dedup_module.inspect_kld_provider_image
        try:
            dedup_module.inspect_kld_provider_image = lambda *args, **kwargs: Verdict(True)
            composition_history = [
                {"date": "2026-08-11", "scene_family": "quiet_lagoon_coast", "composition": "wide diagonal shoreline composition"},
                {"date": "2026-08-12", "scene_family": "zelenogradsk_promenade", "composition": "promenade railing foreground"},
                {"date": "2026-08-13", "scene_family": "baltiysk_breakwater", "composition": "breakwater perspective line"},
                {"date": "2026-08-14", "scene_family": "svetlogorsk_cliff_coast", "composition": "elevated overlook panorama"},
            ]
            history_path.write_text(json.dumps(composition_history), encoding="utf-8")
            strict = dedup_module.evaluate_kld_visual_candidate(
                image_path,
                date_value="2026-08-15",
                target_date="2026-08-15",
                post_type="evening",
                scene_family="rainy_coastal_road",
                composition="wide diagonal shoreline composition",
                prompt_version="test",
                history_path=history_path,
            )
            assert strict.reason == "composition_cooldown"
            relaxed = dedup_module.evaluate_kld_visual_candidate(
                image_path,
                date_value="2026-08-15",
                target_date="2026-08-15",
                post_type="evening",
                scene_family="rainy_coastal_road",
                composition="wide diagonal shoreline composition",
                prompt_version="test",
                history_path=history_path,
                allow_composition_cooldown_relaxation=True,
            )
            assert relaxed.accepted is True

            scene_history = composition_history + [
                {"date": "2026-08-15", "scene_family": "rainy_coastal_road", "composition": "open horizon with large sky"}
            ]
            history_path.write_text(json.dumps(scene_history), encoding="utf-8")
            scene_blocked = dedup_module.evaluate_kld_visual_candidate(
                image_path,
                date_value="2026-08-15",
                target_date="2026-08-15",
                post_type="evening",
                scene_family="rainy_coastal_road",
                composition="wide diagonal shoreline composition",
                prompt_version="test",
                history_path=history_path,
                allow_composition_cooldown_relaxation=True,
            )
            assert scene_blocked.reason == "scene_cooldown"

            perceptual = dedup_module.dhash_file(image_path)
            near_history = [
                {
                    "date": "2026-08-14",
                    "scene_family": "quiet_lagoon_coast",
                    "composition": "open horizon with large sky",
                    "sha256": "f" * 64,
                    "perceptual_hash": perceptual,
                }
            ]
            history_path.write_text(json.dumps(near_history), encoding="utf-8")
            near_blocked = dedup_module.evaluate_kld_visual_candidate(
                image_path,
                date_value="2026-08-15",
                target_date="2026-08-15",
                post_type="evening",
                scene_family="rainy_coastal_road",
                composition="wide diagonal shoreline composition",
                prompt_version="test",
                history_path=history_path,
                allow_composition_cooldown_relaxation=True,
            )
            assert near_blocked.reason == "near_duplicate"

            dedup_module.inspect_kld_provider_image = lambda *args, **kwargs: Verdict(False, "screen_or_ui_chrome")
            history_path.write_text("[]", encoding="utf-8")
            content_blocked = dedup_module.evaluate_kld_visual_candidate(
                image_path,
                date_value="2026-08-15",
                target_date="2026-08-15",
                post_type="evening",
                scene_family="rainy_coastal_road",
                composition="wide diagonal shoreline composition",
                prompt_version="test",
                history_path=history_path,
                allow_composition_cooldown_relaxation=True,
            )
            assert content_blocked.reason == "content_guard:screen_or_ui_chrome"
        finally:
            dedup_module.inspect_kld_provider_image = original_guard


def secondary_backend_rotates_same_fresh_candidate_pool() -> None:
    class PollinationsFailure(RuntimeError):
        backend = "pollinations"
        reason = "provider_failure"
        attempts = []

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.json"
        history_path.write_text(
            json.dumps(
                [
                    {"date": "2026-06-16", "scene_family": "stormy_open_baltic", "composition": "old-c"},
                    {"date": "2026-06-17", "scene_family": "baltiysk_breakwater", "composition": "old-b"},
                    {"date": "2026-06-18", "scene_family": "svetlogorsk_cliff_coast", "composition": "old-a"},
                ]
            ),
            encoding="utf-8",
        )
        args = _args(root)
        initial = build_payload(FIXTURES["storm"], "storm", post_type="evening")
        events: list[str] = []

        def pollinations(**kwargs):
            raise PollinationsFailure("offline provider failure")

        def stable_horde(**kwargs):
            events.append("stable_horde")
            return _ppm(root / "horde.ppm")

        outcome = execute_image_delivery(
            args=args,
            message=FIXTURES["storm"],
            initial_payload=initial,
            visibility_context=None,
            history_path=history_path,
            generate_image=pollinations,
            secondary_generate_image=stable_horde,
            evaluate_candidate=lambda *args, **kwargs: _accepted(),
            cover_renderer=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cover not expected")),
            validate_cover=lambda *args, **kwargs: {"valid": True, "errors": []},
            send_photo=lambda *args, **kwargs: events.append("photo") or 123,
            record_publication=lambda **kwargs: events.append("history") or {"sha256": "b" * 64},
        )
        assert outcome["backend"] == "stable_horde"
        assert len(outcome["candidate_pool"]) == 2
        first_pollinations = outcome["provider_attempts"][0]
        first_horde = outcome["provider_attempts"][1]
        assert first_pollinations["backend"] == "pollinations"
        assert first_horde["backend"] == "stable_horde"
        assert first_pollinations["scene_family"] == outcome["candidate_pool"][0]["scene_family"]
        assert first_horde["scene_family"] == outcome["candidate_pool"][1]["scene_family"]
        assert first_pollinations["scene_family"] != first_horde["scene_family"]
        assert not (set(outcome["scene_cooldown"]) & {item["scene_family"] for item in outcome["candidate_pool"]})
        assert events == ["stable_horde", "photo", "history"]


def final_scene_policy_is_hard_and_reasons_are_explicit() -> None:
    history = [
        {"date": "2026-08-05", "scene_family": "quiet_lagoon_coast"},
        {"date": "2026-08-06", "scene_family": "zelenogradsk_promenade"},
        {"date": "2026-08-07", "scene_family": "pine_forest_sea_path"},
    ]
    reason, matched = scene_policy_rejection(history, scene_family="pine_forest_sea_path")
    assert reason == "scene_cooldown"
    assert matched and matched["scene_family"] == "pine_forest_sea_path"
    assert _rejection_fallback_reason(["content_guard:screen_or_ui_chrome"]) == "semantic_mismatch"
    assert _rejection_fallback_reason(["scene_cooldown"]) == "scene_policy_rejected"
    assert _rejection_fallback_reason(["near_duplicate"]) == "near_duplicate"


TESTS = [
    evening_prompt_is_scene_authoritative_and_summer_green,
    winter_evening_prompt_does_not_force_summer_green,
    storm_allocator_keeps_last_three_scenes_hard_blocked,
    composition_exhaustion_relaxes_oldest_only,
    relaxed_candidate_reaches_provider_and_marks_final_gate,
    composition_relaxation_does_not_weaken_other_guards,
    secondary_backend_rotates_same_fresh_candidate_pool,
    final_scene_policy_is_hard_and_reasons_are_explicit,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD evening/allocator offline checks passed")


if __name__ == "__main__":
    main()
