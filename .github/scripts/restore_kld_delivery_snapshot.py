#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore KLD production delivery receipts from the newest valid Actions snapshot."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import zipfile


SNAPSHOT_PREFIX = "kld-delivery-snapshot-"


def _parse_time(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_ints(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool) and item > 0]


def _valid_receipt(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    try:
        date.fromisoformat(str(data.get("target_date") or ""))
    except ValueError:
        return False
    if str(data.get("post_type") or "") not in {"morning", "evening"}:
        return False
    if data.get("chat_type") != "production":
        return False
    if not str(data.get("chat_id") or "").strip():
        return False
    image_delivered = data.get("image_delivered") is True
    text_delivered = data.get("text_delivered") is True
    if not (image_delivered or text_delivered):
        return False
    if image_delivered:
        message_id = data.get("telegram_image_message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            return False
        if not str(data.get("image_sent_at_utc") or "").strip():
            return False
    if text_delivered:
        if not _positive_ints(data.get("telegram_text_message_ids")):
            return False
        if not str(data.get("text_sent_at_utc") or "").strip():
            return False
    return _parse_time(data.get("updated_at_utc")) is not None


def _load(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _restore_from_root(source_root: Path, destination: Path) -> tuple[int, int]:
    valid = 0
    restored = 0
    for source in sorted(source_root.rglob("*.json")):
        try:
            payload = _load(source)
        except Exception:
            continue
        if not _valid_receipt(payload):
            continue
        valid += 1
        target = destination / f"{payload['target_date']}-{payload['post_type']}.json"
        try:
            local = _load(target)
        except Exception:
            local = None
        local_time = _parse_time(local.get("updated_at_utc")) if _valid_receipt(local) else None
        snapshot_time = _parse_time(payload.get("updated_at_utc"))
        if local_time is not None and snapshot_time is not None and local_time >= snapshot_time:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        restored += 1
    return valid, restored


def _gh_json(path: str) -> dict[str, Any]:
    raw = subprocess.check_output(["gh", "api", path], text=True)
    return json.loads(raw)


def _artifact_candidates() -> list[dict[str, Any]]:
    repo = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        print("KLD delivery restore: cache-only (GitHub context unavailable).", file=sys.stderr)
        return []
    artifacts = _gh_json(f"repos/{repo}/actions/artifacts?per_page=100").get("artifacts", [])
    candidates = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and not item.get("expired")
        and str(item.get("name") or "").startswith(SNAPSHOT_PREFIX)
    ]
    candidates.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return candidates


def main() -> int:
    repo = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    destination = Path(os.getenv("KLD_DELIVERY_DIR", ".cache/kld_delivery"))
    destination.mkdir(parents=True, exist_ok=True)
    candidates = _artifact_candidates()
    if not candidates:
        return 0

    with tempfile.TemporaryDirectory(prefix="kld_delivery_snapshot_") as tmp_name:
        tmp = Path(tmp_name)
        for artifact in candidates[:20]:
            artifact_id = int(artifact["id"])
            zip_path = tmp / f"{artifact_id}.zip"
            extract_dir = tmp / str(artifact_id)
            try:
                with zip_path.open("wb") as fh:
                    subprocess.check_call(
                        ["gh", "api", f"repos/{repo}/actions/artifacts/{artifact_id}/zip"],
                        stdout=fh,
                    )
                extract_dir.mkdir()
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(extract_dir)
                valid, restored = _restore_from_root(extract_dir, destination)
                print(
                    "KLD delivery snapshot checked: "
                    f"artifact_id={artifact_id}; valid_receipts={valid}; restored={restored}"
                )
                if valid:
                    return 0
            except Exception as exc:
                print(
                    f"Skipping KLD delivery snapshot artifact id={artifact_id}: "
                    f"{exc.__class__.__name__}: {exc}",
                    file=sys.stderr,
                )
    print("KLD delivery restore: no valid snapshot artifact found; cache-only state retained.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
