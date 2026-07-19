#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static workflow checks for persistent KLD visual dedup history."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github" / "workflows" / "daily_post_klg.yml"
SAFE_TEST = ROOT / ".github" / "workflows" / "safe_test_post.yml"
IMAGE_FIRST = ROOT / "kld_image_first.py"
IMAGE_TOOL = ROOT / "tools" / "kld_visual_fixture_image.py"


def _read(path: Path) -> str:
    return path.read_text("utf-8")


def _assert(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def _block(text: str, start: str, end: str | None = None) -> str:
    start_idx = text.index(start)
    end_idx = text.index(end, start_idx) if end else len(text)
    return text[start_idx:end_idx]


def test_daily_visual_history_cache() -> None:
    text = _read(DAILY)
    _assert("daily_cache_action", "uses: actions/cache@v4" in text)
    _assert("daily_prod_path", "path: .cache/kld_visual_history_prod.json" in text)
    _assert("daily_test_path", "path: .cache/kld_visual_history_test.json" in text)
    _assert(
        "daily_prod_unique_key",
        "key: kld-visual-history-prod-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}" in text,
    )
    _assert(
        "daily_test_unique_key",
        "key: kld-visual-history-test-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}" in text,
    )
    _assert("daily_prod_prefix", "kld-visual-history-prod-" in text)
    _assert("daily_test_prefix", "kld-visual-history-test-" in text)
    _assert("daily_namespace_arg", "--history-namespace\", history_namespace" in text)
    _assert("daily_schedule_morning_unchanged", "cron: '30 0 * * *'" in text)
    _assert("daily_schedule_evening_unchanged", "cron: '0 14 * * *'" in text)
    _assert("daily_schedule_fx_unchanged", "cron: '0 8 * * *'" in text)
    print("PASS daily_visual_history_cache")


def test_safe_test_visual_history_cache_and_checkbox() -> None:
    text = _read(SAFE_TEST)
    generic_cache = _block(text, "      - name: Restore .cache", "      - name: Restore KLD visual history (prod)")
    _assert("safe_generic_cache_keeps_cache_dir", "path: |\n            .cache" in generic_cache)
    _assert(
        "safe_generic_cache_excludes_prod_visual_history",
        "!.cache/kld_visual_history_prod.json" in generic_cache,
    )
    _assert(
        "safe_generic_cache_excludes_test_visual_history",
        "!.cache/kld_visual_history_test.json" in generic_cache,
    )
    _assert("safe_prod_path", "path: .cache/kld_visual_history_prod.json" in text)
    _assert("safe_test_path", "path: .cache/kld_visual_history_test.json" in text)
    _assert(
        "safe_test_unique_key",
        "key: kld-visual-history-test-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}" in text,
    )
    _assert("safe_test_prefix", "kld-visual-history-test-" in text)
    _assert("safe_checkbox_declared", "send_image_to_test:" in text)
    _assert("safe_checkbox_invokes_tool", "python tools/kld_visual_fixture_image.py" in text)
    _assert("safe_tool_uses_test_namespace", "--history-namespace test" in text)
    _assert(
        "safe_history_restore_before_image_generation",
        text.index("      - name: Restore KLD visual history (test)") < text.index("python tools/kld_visual_fixture_image.py"),
    )
    _assert("safe_inline_imagegen_removed", "import imagegen" not in text)
    print("PASS safe_test_visual_history_cache_and_checkbox")


def test_evening_waits_for_morning_without_losing_dispatch_paths() -> None:
    text = _read(DAILY)
    evening = _block(text, "  evening:", "  noon_fx:")
    _assert("evening_needs_morning", "needs: morning" in evening)
    _assert("evening_always_condition", "always() &&" in evening)
    _assert("evening_schedule_still_allowed", "github.event.schedule == '0 14 * * *'" in evening)
    _assert("evening_manual_still_allowed", "github.event.inputs.run_evening" in evening)
    _assert("evening_no_morning_gate", "github.event.inputs.run_morning" not in evening)
    print("PASS evening_waits_for_morning_without_losing_dispatch_paths")


def test_image_first_visibility_sidecar_wiring() -> None:
    text = _read(DAILY)
    morning = _block(text, "  morning:", "  evening:")
    evening = _block(text, "  evening:", "  noon_fx:")
    sidecar_out = '"--visibility-context-out", "format_v2_visibility_context.json"'
    sidecar_in = '"--visibility-context-file", "format_v2_visibility_context.json"'

    _assert("sidecar_out_both_jobs", text.count(sidecar_out) == 2)
    _assert("sidecar_in_both_jobs", text.count(sidecar_in) == 2)
    for name, block in (("morning", morning), ("evening", evening)):
        _assert(f"{name}_preview_writes_sidecar", sidecar_out in block)
        _assert(f"{name}_image_reads_sidecar", sidecar_in in block)
        _assert(
            f"{name}_sidecar_written_before_image",
            block.index(sidecar_out) < block.index(sidecar_in),
        )
        _assert(f"{name}_shared_orchestrator", "run_image_first_publication(" in block)
        _assert(f"{name}_text_callback", "send_text=lambda text_path:" in block)
    print("PASS image_first_visibility_sidecar_wiring")


def test_image_failure_is_nonblocking_and_diagnostics_are_always_uploaded() -> None:
    text = _read(DAILY)
    helper = _read(IMAGE_FIRST)
    image_tool = _read(IMAGE_TOOL)
    morning = _block(text, "  morning:", "  evening:")
    evening = _block(text, "  evening:", "  noon_fx:")

    _assert("no_image_check_true", "subprocess.run(img_cmd, check=True)" not in text)
    _assert("shared_orchestrator_twice", text.count("run_image_first_publication(") == 2)
    _assert("helper_runs_image_before_text", helper.index("run_process(list(image_cmd))") < helper.index("send_text(str(message_path))"))
    _assert("helper_warns_and_continues", "KLD image unavailable; text publication continued" in helper)
    _assert("text_failure_reraised", "text_error_type" in helper and "raise\n" in helper)
    _assert("expected_duplicate_not_system_exit", "only exact duplicate candidates; refusing to send" not in image_tool)
    _assert("structured_result", '"fallback_sent"' in image_tool and '"failed_nonfatal"' in image_tool)

    for name, block in (("morning", morning), ("evening", evening)):
        _assert(f"{name}_artifact_always", "if: always()" in block)
        _assert(f"{name}_artifact_action", "uses: actions/upload-artifact@v4" in block)
        for artifact in (
            "image_result.json",
            "image_prompt_metadata.json",
            "safe_test_post_preview.log",
            "format_v2_message.txt",
            "format_v2_visibility_context.json",
        ):
            _assert(f"{name}_artifact_{artifact}", artifact in block)
        _assert(f"{name}_no_secret_env_artifact", "env:" not in _block(block, "publication diagnostics"))
    print("PASS image_failure_is_nonblocking_and_diagnostics_are_always_uploaded")


def test_simulated_manual_morning_evening_history_chain() -> None:
    cache_store: dict[str, list[str]] = {}
    prefix = "kld-visual-history-prod-"
    run_id = "12345"
    run_attempt = "1"

    def restore(primary_key: str) -> list[str]:
        if primary_key in cache_store:
            return list(cache_store[primary_key])
        candidates = [(key, value) for key, value in cache_store.items() if key.startswith(prefix)]
        if not candidates:
            return []
        key, value = candidates[-1]
        _assert("sim_restore_prefix_key", key.startswith(prefix), key)
        return list(value)

    def save(primary_key: str, value: list[str]) -> None:
        cache_store[primary_key] = list(value)

    morning_key = f"{prefix}{run_id}-{run_attempt}-morning"
    evening_key = f"{prefix}{run_id}-{run_attempt}-evening"
    morning_history = restore(morning_key)
    morning_history.append("A")
    save(morning_key, morning_history)

    evening_history = restore(evening_key)
    _assert("sim_evening_restores_morning_entry", evening_history == ["A"], evening_history)
    evening_history.append("B")
    save(evening_key, evening_history)

    final_history = restore(evening_key)
    _assert("sim_final_history_has_both_entries", final_history == ["A", "B"], final_history)
    print("PASS simulated_manual_morning_evening_history_chain")


def test_pillow_is_bounded_dependency() -> None:
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    _assert("pillow_bound", "Pillow>=10,<12" in requirements)
    print("PASS pillow_is_bounded_dependency")


TESTS = [
    test_daily_visual_history_cache,
    test_safe_test_visual_history_cache_and_checkbox,
    test_evening_waits_for_morning_without_losing_dispatch_paths,
    test_image_first_visibility_sidecar_wiring,
    test_image_failure_is_nonblocking_and_diagnostics_are_always_uploaded,
    test_simulated_manual_morning_evening_history_chain,
    test_pillow_is_bounded_dependency,
]


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} KLD visual workflow checks passed")


if __name__ == "__main__":
    main()
