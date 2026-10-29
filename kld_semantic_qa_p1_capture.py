"""R5-G1 offline-only P1 image capture adapter. NOT WIRED TO PRODUCTION.

No provider/Telegram/network calls. A pipeline caller must explicitly inject
capture_generated_candidate; without injection the publication path is unchanged.

Records are *quarantined pre-upload evidence*, NOT P1 Golden Set admissions:
GitHub's external artifact digest is unknowable before artifact upload. Only a
separate, authorized, verified artifact-stage may populate that field.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import struct
from typing import Any, Mapping

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 24_000_000
REPO = "maximovavs/vaybometer-bot-kld"
SCHEMA = "kld_ai_p1_capture_preupload_v1"
HEX40 = re.compile(r"[a-f0-9]{40}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
RUN = re.compile(r"[1-9][0-9]{0,19}\Z")
BACKENDS = frozenset({"pollinations", "stable_horde"})
MODES = frozenset({"morning", "evening"})


class CaptureRejected(ValueError):
    """Safe deterministic reject; caller may log only the category, not secrets."""


def _normal_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CaptureRejected("missing_file") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CaptureRejected("unsafe_file")


def _check_dir(root: Path) -> None:
    # No untrusted directory traversal, no symlink components, no world-write.
    if root.is_symlink():
        raise CaptureRejected("unsafe_directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for segment in (root, *root.parents):
        if segment.is_symlink():
            raise CaptureRejected("unsafe_directory")
    if not root.is_dir():
        raise CaptureRejected("unsafe_directory")
    root.chmod(0o700)


def _mime(data: bytes) -> tuple[str, str]:
    """Strict metadata-free container allowlists, validated again by Pillow.

    Raw original bytes are preserved if admitted. In particular, image-library
    EXIF getters are *not* sufficient: unrecognised APPn / ancillary chunks can
    carry private data while an image still decodes successfully.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        pos = 8
        first = True
        saw_idat = False
        saw_iend = False
        permitted = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS"}
        while pos + 12 <= len(data):
            size = struct.unpack(">I", data[pos:pos + 4])[0]
            typ = data[pos + 4:pos + 8]
            if size > MAX_BYTES or pos + 12 + size > len(data):
                raise CaptureRejected("invalid_image")
            if typ not in permitted:
                # Unknown ancillary chunks, including iTXt, vpAg, eXIf,
                # iCCP, sRGB, pHYs and private chunks, are untrusted.
                raise CaptureRejected("embedded_metadata" if typ[:1].islower() else "invalid_image")
            if first and (typ != b"IHDR" or size != 13):
                raise CaptureRejected("invalid_image")
            if not first and typ == b"IHDR":
                raise CaptureRejected("invalid_image")
            first = False
            if typ == b"IDAT":
                saw_idat = True
            if typ == b"IEND":
                if size != 0:
                    raise CaptureRejected("invalid_image")
                saw_iend = True
                pos += 12
                break
            pos += size + 12
        if not saw_iend or not saw_idat or pos != len(data):
            raise CaptureRejected("invalid_image")
        return "image/png", "png"

    if data.startswith(b"\xff\xd8"):
        pos = 2
        in_scan = False
        saw_scan = False
        saw_jfif = False
        # Decoder syntax, not a metadata carrier: frame/tables/restart interval.
        structural = set(range(0xC0, 0xD0)) - {0xC8}
        structural.update((0xDB, 0xDD, 0xDC))
        while pos < len(data):
            if in_scan:
                # JPEG entropy uses FF00 byte stuffing; FF D0-D7 are restart
                # markers. All other FF markers end the entropy segment.
                while pos < len(data):
                    ff = data.find(b"\xff", pos)
                    if ff < 0:
                        raise CaptureRejected("invalid_image")
                    j = ff + 1
                    while j < len(data) and data[j] == 0xFF:
                        j += 1
                    if j == len(data):
                        raise CaptureRejected("invalid_image")
                    value = data[j]
                    if value == 0 or 0xD0 <= value <= 0xD7:
                        pos = j + 1
                        continue
                    pos = j - 1  # next iteration parses that marker
                    in_scan = False
                    break
                continue
            if data[pos] != 0xFF:
                raise CaptureRejected("invalid_image")
            marker_pos = pos + 1
            while marker_pos < len(data) and data[marker_pos] == 0xFF:
                marker_pos += 1
            if marker_pos == len(data):
                raise CaptureRejected("invalid_image")
            marker = data[marker_pos]
            pos = marker_pos + 1
            if marker == 0xD9:  # end of image, no appended payload
                if not saw_scan or pos != len(data):
                    raise CaptureRejected("invalid_image")
                return "image/jpeg", "jpg"
            if marker == 0xFE or 0xE0 <= marker <= 0xEF:
                if marker != 0xE0:
                    raise CaptureRejected("embedded_metadata")
            elif marker != 0xDA and marker not in structural:
                raise CaptureRejected("invalid_image")
            if pos + 2 > len(data):
                raise CaptureRejected("invalid_image")
            seglen = int.from_bytes(data[pos:pos + 2], "big")
            if seglen < 2 or pos + seglen > len(data):
                raise CaptureRejected("invalid_image")
            if marker == 0xE0:
                # Only the exact standard zero-thumbnail JFIF header is
                # allowed; APP0 may otherwise embed arbitrary opaque bytes.
                payload = data[pos + 2:pos + seglen]
                if (saw_jfif or saw_scan or len(payload) != 14
                        or payload[:5] != b"JFIF\x00"
                        or payload[5:7] not in (b"\x01\x01", b"\x01\x02")
                        or payload[7] > 2 or not int.from_bytes(payload[8:10], "big")
                        or not int.from_bytes(payload[10:12], "big")
                        or payload[12:] != b"\x00\x00"):
                    raise CaptureRejected("embedded_metadata")
                saw_jfif = True
            pos += seglen
            if marker == 0xDA:
                saw_scan = True
                in_scan = True
        raise CaptureRejected("invalid_image")

    if len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        # RIFF size must exclude exactly the 8-byte RIFF header: no trailer.
        if int.from_bytes(data[4:8], "little") != len(data) - 8:
            raise CaptureRejected("invalid_image")
        pos = 12
        chunks = []
        allowed = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH"}
        while pos + 8 <= len(data):
            typ = data[pos:pos + 4]
            length = int.from_bytes(data[pos + 4:pos + 8], "little")
            if length > MAX_BYTES or pos + 8 + length + (length % 2) > len(data):
                raise CaptureRejected("invalid_image")
            if typ not in allowed:
                # Reject EXIF/XMP/ICCP and *all* unknown chunks, including
                # custom FourCC metadata and animation subcontainers.
                raise CaptureRejected("embedded_metadata")
            if typ == b"VP8X":
                flags = data[pos + 8:pos + 8 + length]
                if len(flags) != 10:
                    raise CaptureRejected("invalid_image")
                # ICC, EXIF, XMP, animation or reserved flags -> reject;
                # optional alpha-only feature is allowed.
                if flags[0] & ~0x10 or any(flags[1:4]):
                    raise CaptureRejected("embedded_metadata")
            if length % 2 and data[pos + 8 + length] != 0:
                raise CaptureRejected("embedded_metadata")
            chunks.append(typ)
            pos += 8 + length + (length % 2)
        if (pos != len(data) or not chunks or chunks.count(b"VP8 ") + chunks.count(b"VP8L") != 1
                or (b"VP8X" in chunks and chunks[0] != b"VP8X")
                or len(chunks) != len(set(chunks))):
            raise CaptureRejected("invalid_image")
        return "image/webp", "webp"
    raise CaptureRejected("invalid_image")


def _json_bytes(doc: Mapping[str, Any]) -> bytes:
    return (json.dumps(doc, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def _write_once(path: Path, data: bytes) -> bool:
    """No overwrite of existing evidence; consistent exact repeats are idempotent."""
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except FileExistsError:
        _normal_file(path)
        if path.read_bytes() == data:
            return False
        raise CaptureRejected("collision")


def capture_generated_candidate(
    *, image_path: str | Path, backend: str, variation_attempt: int,
    post_type: str, target_date: str, prompt_metadata_sha256: str,
    output_dir: str | Path, environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Explicit injection only; no env-based activation or workflow wiring.

    The prompt hash MUST be computed from the exact prompt for this candidate,
    never from the published text. Return only allowlisted, harmless fields.
    """
    env = os.environ if environment is None else environment
    run = str(env.get("GITHUB_RUN_ID", ""))
    attempt = str(env.get("GITHUB_RUN_ATTEMPT", ""))
    commit = str(env.get("GITHUB_SHA", ""))
    if env.get("GITHUB_REPOSITORY") != REPO or not RUN.fullmatch(run) or not RUN.fullmatch(attempt):
        raise CaptureRejected("unverified_run_context")
    if not HEX40.fullmatch(commit):
        raise CaptureRejected("unverified_commit")
    if backend not in BACKENDS or post_type not in MODES:
        raise CaptureRejected("invalid_producer")
    if type(variation_attempt) is not int or not 0 <= variation_attempt < 256:
        raise CaptureRejected("invalid_variation")
    if not HEX64.fullmatch(str(prompt_metadata_sha256)):
        raise CaptureRejected("invalid_prompt_digest")
    try:
        date.fromisoformat(target_date)
    except (ValueError, TypeError) as exc:
        raise CaptureRejected("invalid_target_date") from exc
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() != timezone.utc.utcoffset(moment):
        raise CaptureRejected("invalid_capture_time")
    img = Path(image_path)
    _normal_file(img)
    if img.stat().st_size > MAX_BYTES:
        raise CaptureRejected("oversize")
    data = img.read_bytes()
    if not 0 < len(data) <= MAX_BYTES:
        raise CaptureRejected("oversize")
    mime, suffix = _mime(data)
    # Pillow is a repository dependency. No permissive magic-bytes-only accepts.
    try:
        from PIL import Image, UnidentifiedImageError
        with Image.open(BytesIO(data)) as image:
            if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_PIXELS:
                raise CaptureRejected("invalid_image")
            if image.format not in {"PNG", "JPEG", "WEBP"} or Image.MIME.get(image.format) != mime:
                raise CaptureRejected("invalid_image")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            if image.getexif():
                raise CaptureRejected("embedded_metadata")
    except CaptureRejected:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise CaptureRejected("invalid_image") from exc

    digest = sha256(data).hexdigest()
    root = Path(output_dir)
    _check_dir(root)
    blobs, records = root / "blobs", root / "records"
    _check_dir(blobs)
    _check_dir(records)
    blob_name = f"{digest}.{suffix}"
    record_name = f"run{run}-a{attempt}-{post_type}-v{variation_attempt}-{backend}.json"
    record = {
        "schema": SCHEMA, "repository": REPO, "workflow_run_id": run,
        "run_attempt": int(attempt), "commit_sha": commit,
        "backend": backend, "variation_attempt": variation_attempt,
        "post_type": post_type, "target_date": target_date,
        "prompt_metadata_sha256": prompt_metadata_sha256,
        "image_sha256": digest, "image_mime": mime, "image_bytes": len(data),
        "image_file": "blobs/" + blob_name,
        "captured_at_utc": moment.isoformat().replace("+00:00", "Z"),
        "capture_artifact_digest": None,  # assigned only after real upload
        "golden_set_admitted": False,
        "provenance_status": "QUARANTINED_PENDING_ARTIFACT_DIGEST_AND_REVIEW",
    }
    # Write once. If record creation fails the blob is orphaned, never admitted.
    _write_once(blobs / blob_name, data)
    _write_once(records / record_name, _json_bytes(record))
    return {"status": "QUARANTINED", "image_sha256": digest,
            "record_name": record_name, "image_bytes": len(data)}


def capture_callback(*, output_dir: str | Path, environment: Mapping[str, str] | None = None):
    """Optional callback constructor; caller must explicitly inject it."""
    def callback(**kwargs: Any) -> dict[str, Any]:
        try:
            return capture_generated_candidate(output_dir=output_dir, environment=environment, **kwargs)
        except (CaptureRejected, OSError) as exc:
            # Never log input paths, provider diagnostics, tokens or exception text.
            return {"status": "NOT_CAPTURED", "reason": exc.args[0] if isinstance(exc, CaptureRejected) else "filesystem_error"}
    return callback


def invoke_injected_capture(callback, **kwargs: Any) -> dict[str, Any] | None:
    """Side-effect isolation for an injected optional capture at producer return.

    None means the entire legacy OFF path is unchanged. Never allow an observer
    failure or hostile callback output to change image validation/publication.
    """
    if callback is None:
        return None
    try:
        result = callback(**kwargs)
        if not isinstance(result, Mapping):
            return {"status": "NOT_CAPTURED", "reason": "invalid_callback_result"}
        if result.get("status") == "QUARANTINED" and HEX64.fullmatch(str(result.get("image_sha256", ""))):
            return {"status": "QUARANTINED", "image_sha256": result["image_sha256"]}
        reason = result.get("reason")
        safe = {"missing_file", "unsafe_file", "unsafe_directory", "embedded_metadata", "invalid_image",
                "invalid_producer", "oversize", "collision", "filesystem_error", "unverified_run_context",
                "unverified_commit", "invalid_variation", "invalid_prompt_digest", "invalid_target_date",
                "invalid_capture_time"}
        return {"status": "NOT_CAPTURED", "reason": reason if reason in safe else "capture_failed"}
    except Exception:  # failure of diagnostic path is never publisher failure
        return {"status": "NOT_CAPTURED", "reason": "capture_failed"}
