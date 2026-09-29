#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared image-first orchestration for KLD morning/evening publications."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from kld_visual_policy import scene_macro_family, seasonal_guard_label


FORMAT_V2_BEGIN = "===== FORMAT_V2 MESSAGE BEGIN ====="
FORMAT_V2_END = "===== FORMAT_V2 MESSAGE END ====="


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def _load_json(path: str | Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _visual_decision(
    result: Mapping[str, Any],
    prompt_metadata: Mapping[str, Any] | None,
    *,
    mode: str,
) -> dict[str, Any]:
    prompt_metadata = dict(prompt_metadata or {})
    metadata_raw = prompt_metadata.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}

    scene_family = str(result.get("selected_scene_family") or metadata.get("scene_family") or "")
    composition = str(result.get("selected_composition") or metadata.get("composition") or "")
    backend = str(result.get("backend") or "none")
    dedup_reason = str(result.get("dedup_reason") or "")
    telegram_image_sent = bool(result.get("telegram_image_sent"))

    if backend == "local_informative_cover":
        content_guard: dict[str, Any] = {
            "valid": None,
            "reason": "not_applicable_local_cover",
        }
    elif telegram_image_sent:
        content_guard = {"valid": True, "reason": "accepted"}
    elif dedup_reason.startswith("content_guard:"):
        content_guard = {
            "valid": False,
            "reason": dedup_reason.split(":", 1)[1],
        }
    else:
        content_guard = {"valid": None, "reason": "not_selected"}

    target_date = str(metadata.get("target_date") or metadata.get("forecast_date") or "")
    return {
        "backend": backend,
        "post_type": mode,
        "weather_main": str(metadata.get("weather_scenario") or "unknown"),
        "visibility_condition": str(metadata.get("visibility_condition") or "clear"),
        "wind_gust_category": str(metadata.get("wind_gust_category") or "wind_unknown"),
        "scene_route": str(metadata.get("scene_route") or "unknown"),
        "seasonal_guard": seasonal_guard_label(target_date),
        "scene_family": scene_family,
        "scene_macro_family": scene_macro_family(scene_family),
        "composition": composition,
        "content_guard": content_guard,
        "dedup": {
            "reason": dedup_reason or "unknown",
            "distance": result.get("dedup_distance"),
        },
        "fallback_reason": str(result.get("fallback_reason") or ""),
        "visual_policy_version": str(
            result.get("visual_policy_version")
            or prompt_metadata.get("visual_policy_version")
            or "unknown"
        ),
        "published": telegram_image_sent,
    }


def extract_format_v2_message(output: str) -> str:
    if FORMAT_V2_BEGIN not in output or FORMAT_V2_END not in output:
        raise ValueError("FORMAT_V2 MESSAGE block not found")
    block = output.split(FORMAT_V2_BEGIN, 1)[1].split(FORMAT_V2_END, 1)[0].strip()
    if not block:
        raise ValueError("FORMAT_V2 MESSAGE block is empty")
    return block


def run_image_first_publication(
    *,
    mode: str,
    preview_cmd: Sequence[str],
    image_cmd: Sequence[str],
    send_text: Callable[[str], Sequence[int] | None],
    message_path: str | Path = "format_v2_message.txt",
    preview_log_path: str | Path = "safe_test_post_preview.log",
    result_path: str | Path = "image_result.json",
    prompt_metadata_path: str | Path = "image_prompt_metadata.json",
    run_process: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Build text once, attempt an optional image, then send that exact text.

    Preview and text-send errors are mandatory failures. Every image-process
    outcome, including a script crash, is diagnostic-only after preview succeeds.
    """
    mode = str(mode).strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("mode must be morning or evening")

    result: dict[str, Any] = {
        "result": "not_attempted",
        "backend": "none",
        "error_type": "",
        "error_message": "",
        "telegram_image_sent": False,
        "telegram_image_message_id": None,
        "history_recorded": False,
        "cover_attempted": False,
        "provider_error": None,
        "dedup_results": [],
        "preview_succeeded": False,
        "text_sent": False,
        "telegram_text_message_ids": [],
        "mode": mode,
        "visual_decision": None,
    }
    _write_json(result_path, result)
    _write_json(
        prompt_metadata_path,
        {"status": "not_built", "mode": mode, "reason": "FORMAT_V2 preview has not completed"},
    )

    print(f"Building {mode} FORMAT_V2 for image-first mode:", " ".join(preview_cmd))
    try:
        preview = run_process(
            list(preview_cmd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        Path(preview_log_path).write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        result.update(error_type=type(exc).__name__, error_message=str(exc))
        _write_json(result_path, result)
        raise

    preview_output = str(preview.stdout or "")
    print(preview_output, end="")
    Path(preview_log_path).write_text(preview_output, encoding="utf-8")
    if int(preview.returncode) != 0:
        result.update(
            error_type="PreviewProcessError",
            error_message=f"FORMAT_V2 preview exited with {preview.returncode}",
            preview_returncode=int(preview.returncode),
        )
        _write_json(result_path, result)
        raise subprocess.CalledProcessError(int(preview.returncode), list(preview_cmd))

    try:
        block = extract_format_v2_message(preview_output)
    except ValueError as exc:
        result.update(error_type=type(exc).__name__, error_message=str(exc))
        _write_json(result_path, result)
        raise

    Path(message_path).write_text(block + "\n", encoding="utf-8")
    result["preview_succeeded"] = True
    _write_json(result_path, result)

    print(f"Running {mode} image send:", " ".join(image_cmd))
    try:
        image_process = run_process(list(image_cmd))
        image_returncode = int(image_process.returncode)
    except Exception as exc:
        image_returncode = -1
        result.update(
            result="failed_nonfatal",
            backend="none",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    else:
        tool_result = _load_json(result_path)
        if tool_result:
            result.update(tool_result)
        elif image_returncode != 0:
            result.update(
                result="failed_nonfatal",
                backend="none",
                error_type="ImageProcessError",
                error_message=f"image tool exited with {image_returncode}",
            )
    result["image_process_returncode"] = image_returncode
    result["visual_decision"] = _visual_decision(
        result,
        _load_json(prompt_metadata_path),
        mode=mode,
    )

    if image_returncode != 0 or not result.get("telegram_image_sent"):
        print("::warning::KLD image unavailable; text publication continued.")

    print(f"Sending {mode} extracted text after image...")
    try:
        message_ids = list(send_text(str(message_path)) or [])
    except Exception as exc:
        result.update(
            text_sent=False,
            text_error_type=type(exc).__name__,
            text_error_message=str(exc),
        )
        _write_json(result_path, result)
        raise

    result["text_sent"] = True
    result["telegram_text_message_ids"] = message_ids
    _write_json(result_path, result)
    print(f"KLD extracted text sent: chunks={len(message_ids) if message_ids else 1}")
    return result


__all__ = [
    "FORMAT_V2_BEGIN",
    "FORMAT_V2_END",
    "extract_format_v2_message",
    "run_image_first_publication",
]
