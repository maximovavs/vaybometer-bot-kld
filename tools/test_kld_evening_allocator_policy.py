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

from kld_visual_dedup import KldVisualDuplicateResult  # noqa: E402
from kld_visual_policy import (  # noqa: E402
    KLD_VISUAL_POLICY_VERSION,
    SUMMER_VEGETATION_CUE,
    scene_policy_rejection,
)
from tools.kld_visual_fixture_image import (  # noqa: E402
    FIXTURES,
    _candidate_payloads,
    _rejection_fallback_reason,
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
        assert len(candidates) == 3
        assert len(set(scenes)) == 3
        assert set(scene_cooldown) == set(blocked)
        assert not (set(scenes) & set(blocked))


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
        assert len(outcome["candidate_pool"]) == 3
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
