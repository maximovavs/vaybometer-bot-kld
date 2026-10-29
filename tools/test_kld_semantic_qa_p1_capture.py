"""R5-G1 offline tests: only synthetic PNG/JPEG, not real Golden Set P1."""
from __future__ import annotations
from datetime import datetime, timezone
from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
import socket
import struct
import zlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from kld_semantic_qa_p1_capture import capture_generated_candidate as capture, capture_callback, invoke_injected_capture, CaptureRejected


def stop_network(self, *args, **kwargs):
    raise AssertionError("network forbidden")

ENV = {"GITHUB_REPOSITORY": "maximovavs/vaybometer-bot-kld", "GITHUB_RUN_ID": "12345678",
       "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40,
       "TELEGRAM_TOKEN_KLG": "SUPERSECRET", "OPENAI_API_KEY": "SUPERSECRET"}
TIME = datetime(2026, 10, 27, 11, 22, tzinfo=timezone.utc)
PROMPT_HASH = sha256(b"synthetic prompt, no secrets").hexdigest()


def create_image(path: Path, fmt: str = "PNG", meta=None) -> bytes:
    img = Image.new("RGB", (24, 32), (40, 72, 88)); out = BytesIO()
    img.save(out, format=fmt, **(meta or {})); data = out.getvalue(); path.write_bytes(data); return data


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.source = self.dir / "ai.png"
        self.data = create_image(self.source)
        self.args = dict(image_path=self.source, backend="pollinations", variation_attempt=0,
                         post_type="morning", target_date="2026-10-27",
                         prompt_metadata_sha256=PROMPT_HASH, output_dir=self.dir / "p1",
                         environment=ENV, now=TIME)

    def test_optional_hook_off_equivalence_and_error_isolation(self):
        self.assertIsNone(invoke_injected_capture(None, image_path=self.source))
        call = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("SECRET_BAD"))
        self.assertEqual(invoke_injected_capture(call, image_path=self.source),
                         {"status": "NOT_CAPTURED", "reason": "capture_failed"})
        bad = lambda **kwargs: {"status": "QUARANTINED", "image_sha256": "BAD"}
        self.assertEqual(invoke_injected_capture(bad),
                         {"status": "NOT_CAPTURED", "reason": "capture_failed"})

    def test_filesystem_failure_closed_and_no_fake_admission(self):
        with patch("kld_semantic_qa_p1_capture._write_once", side_effect=OSError("SECRET")):
            result = capture_callback(output_dir=self.dir / "p1", environment=ENV)(
                image_path=self.source, backend="pollinations", variation_attempt=0,
                post_type="morning", target_date="2026-10-27", prompt_metadata_sha256=PROMPT_HASH)
        self.assertEqual(result, {"status": "NOT_CAPTURED", "reason": "filesystem_error"})

    def test_jpeg_and_webp_evening_backend(self):
        for fmt, mime, backend in (("JPEG", "image/jpeg", "pollinations"),
                                   ("WEBP", "image/webp", "stable_horde")):
            with self.subTest(fmt=fmt):
                create_image(self.source, fmt=fmt)
                out = capture(**dict(self.args, backend=backend, post_type="evening"))
                record = json.loads((self.dir / "p1/records" / out["record_name"]).read_text())
                self.assertEqual(record["image_mime"], mime)
                self.assertEqual(record["post_type"], "evening")

    def test_symlink_output_directory_is_rejected(self):
        unsafe = self.dir / "output_link"
        unsafe.symlink_to(self.dir / "actual", target_is_directory=True)
        with self.assertRaisesRegex(CaptureRejected, "unsafe_directory"):
            capture(**dict(self.args, output_dir=unsafe))

    def test_capture_integrity_provenance_quarantine(self):
        out = capture(**self.args)
        self.assertEqual(out["status"], "QUARANTINED")
        self.assertEqual(out["image_sha256"], sha256(self.data).hexdigest())
        doc = json.loads((self.dir / "p1/records" / out["record_name"]).read_text())
        self.assertEqual(doc["workflow_run_id"], "12345678")
        self.assertEqual(doc["run_attempt"], 1)
        self.assertEqual(doc["repository"], ENV["GITHUB_REPOSITORY"])
        self.assertEqual(doc["prompt_metadata_sha256"], PROMPT_HASH)
        self.assertIsNone(doc["capture_artifact_digest"])
        self.assertFalse(doc["golden_set_admitted"])
        self.assertEqual((self.dir / "p1" / doc["image_file"]).read_bytes(), self.data)
        self.assertNotIn(b"SUPERSECRET", (self.dir / "p1/records" / out["record_name"]).read_bytes())

    def test_idempotent_and_dedup_blob_across_variations(self):
        one = capture(**self.args)
        self.assertEqual(capture(**self.args), one)
        two = capture(**dict(self.args, variation_attempt=1))
        self.assertNotEqual(two["record_name"], one["record_name"])
        self.assertEqual(len(list((self.dir / "p1/blobs").iterdir())), 1)
        self.assertEqual(len(list((self.dir / "p1/records").iterdir())), 2)

    def test_collision_rejected_without_overwrite(self):
        one = capture(**self.args)
        different = create_image(self.source, meta={})
        self.assertEqual(different, self.data)
        create_image(self.source, fmt="PNG")
        with patch("kld_semantic_qa_p1_capture._json_bytes", lambda record: b"tamper"):
            with self.assertRaisesRegex(CaptureRejected, "collision"):
                capture(**self.args)
        self.assertNotEqual((self.dir / "p1/records" / one["record_name"]).read_bytes(), b"tamper")

    def test_bad_run_context_is_fail_closed(self):
        for update in ({"GITHUB_SHA": "bad"}, {"GITHUB_RUN_ID": ""},
                       {"GITHUB_REPOSITORY": "another/repo"}, {"GITHUB_RUN_ATTEMPT": "0"}):
            with self.subTest(update=update):
                with self.assertRaises(CaptureRejected):
                    capture(**dict(self.args, environment={**ENV, **update}))
        self.assertFalse((self.dir / "p1").exists())

    def test_unsafe_files_and_metadata_rejected(self):
        link = self.dir / "link.png"
        link.symlink_to(self.source)
        for file in (link, self.dir / "missing.png"):
            with self.assertRaises(CaptureRejected):
                capture(**dict(self.args, image_path=file))
        pnginfo = PngImagePlugin.PngInfo(); pnginfo.add_text("secret", "PRIVATE")
        create_image(self.source, meta={"pnginfo": pnginfo})
        with self.assertRaisesRegex(CaptureRejected, "embedded_metadata"):
            capture(**self.args)
        self.assertFalse((self.dir / "p1").exists())

    def test_oversize_and_corrupt_rejected(self):
        self.source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024))
        with self.assertRaisesRegex(CaptureRejected, "oversize"):
            capture(**self.args)
        self.source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 30)
        with self.assertRaisesRegex(CaptureRejected, "invalid_image"):
            capture(**self.args)

    def test_failure_is_diagnostic_only_and_sanitized(self):
        self.source.unlink()
        with patch.object(socket.socket, "connect", stop_network):
            response = capture_callback(output_dir=self.dir / "p1", environment=ENV)(
                image_path=self.source, backend="pollinations", variation_attempt=0,
                post_type="morning", target_date="2026-10-27", prompt_metadata_sha256=PROMPT_HASH)
        self.assertEqual(response, {"status": "NOT_CAPTURED", "reason": "missing_file"})
        self.assertNotIn("SUPERSECRET", str(response))

    def test_no_credentials_or_prompts_and_modes(self):
        for changed in ({"backend": "unsupported"}, {"post_type": "weekly"},
                        {"prompt_metadata_sha256": "bad"}, {"variation_attempt": True}):
            with self.subTest(changed=changed):
                with self.assertRaises(CaptureRejected):
                    capture(**dict(self.args, **changed))
        self.assertFalse((self.dir / "p1").exists())

    def _must_reject_without_persisting(self, data: bytes, reason: str = "embedded_metadata"):
        self.source.write_bytes(data)
        with self.assertRaisesRegex(CaptureRejected, reason):
            capture(**self.args)
        self.assertFalse((self.dir / "p1").exists(), "untrusted bytes must not reach artifacts")

    def test_jpeg_all_nonstandard_app_segments_and_comment_are_rejected(self):
        clean = create_image(self.source, fmt="JPEG")
        canary = b"SYNTHETIC_PRIVATE_META_CANARY_1000"
        for marker in range(0xE1, 0xF0):
            with self.subTest(marker=hex(marker)):
                segment = b"\xff" + bytes([marker]) + (len(canary) + 2).to_bytes(2, "big") + canary
                self._must_reject_without_persisting(clean[:2] + segment + clean[2:])
        segment = b"\xff\xfe" + (len(canary) + 2).to_bytes(2, "big") + canary
        self._must_reject_without_persisting(clean[:2] + segment + clean[2:])

    def test_jpeg_app0_jfif_payload_and_after_scan_metadata_rejected(self):
        clean = create_image(self.source, fmt="JPEG")
        canary = b"SYNTHETIC_PRIVATE_META_CANARY_2000"
        # APP0 is a standard JFIF header, but should not become an opaque
        # carrier for extra bytes or a second untrusted APP0 segment.
        jfif = clean[6:20]
        self.assertEqual(len(jfif), 14)
        extra = b"\xff\xe0" + (len(jfif) + len(canary) + 2).to_bytes(2, "big") + jfif + canary
        self._must_reject_without_persisting(clean[:2] + extra + clean[2:])
        # A COM/APP marker in a progressive or appended scan section must not
        # escape the header-only scanner.
        seg = b"\xff\xe2" + (len(canary) + 2).to_bytes(2, "big") + canary
        self.assertTrue(clean.endswith(b"\xff\xd9"))
        self._must_reject_without_persisting(clean[:-2] + seg + clean[-2:])
        self._must_reject_without_persisting(clean + canary, "invalid_image")

    def test_png_unknown_ancillary_and_declared_metadata_rejected(self):
        clean = create_image(self.source, fmt="PNG")
        marker = b"SYNTHETIC_PRIVATE_META_CANARY_3000"
        for typ in (b"vpAg", b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"iCCP",
                    b"pHYs", b"sRGB", b"gAMA", b"cHRM"):
            with self.subTest(chunk=typ):
                payload = marker
                crc = zlib.crc32(typ + payload) & 0xffffffff
                chunk = struct.pack(">I", len(payload)) + typ + payload + struct.pack(">I", crc)
                self._must_reject_without_persisting(clean[:33] + chunk + clean[33:])
        self._must_reject_without_persisting(clean + marker, "invalid_image")

    def test_png_unknown_critical_rejected_and_broken_crc_rejected(self):
        clean = create_image(self.source, fmt="PNG")
        typ = b"MANG"
        payload = b"PRIVATE"
        crc = zlib.crc32(typ + payload) & 0xffffffff
        chunk = struct.pack(">I", len(payload)) + typ + payload + struct.pack(">I", crc)
        self._must_reject_without_persisting(clean[:33] + chunk + clean[33:], "invalid_image")
        damaged = bytearray(clean)
        damaged[-5] ^= 0x11
        self._must_reject_without_persisting(bytes(damaged), "invalid_image")

    def test_webp_unknown_metadata_and_reserved_chunks_rejected(self):
        clean = create_image(self.source, fmt="WEBP")
        marker = b"SYNTHETIC_PRIVATE_META_CANARY_4000"
        for fourcc in (b"EXIF", b"XMP ", b"ICCP", b"vpAg", b"P1SG", b"ANIM", b"ANMF"):
            with self.subTest(fourcc=fourcc):
                chunk = fourcc + len(marker).to_bytes(4, "little") + marker
                chunk += b"\x00" if len(marker) % 2 else b""
                changed = bytearray(clean + chunk)
                changed[4:8] = (len(changed) - 8).to_bytes(4, "little")
                self._must_reject_without_persisting(bytes(changed))
        self._must_reject_without_persisting(clean + marker, "invalid_image")

    def test_webp_vp8x_embedded_feature_flags_and_padding_rejected(self):
        clean = create_image(self.source, fmt="WEBP")
        for bad_feature in (0x02, 0x04, 0x08, 0x20, 0x80):
            with self.subTest(flag=hex(bad_feature)):
                vp8x = b"VP8X" + (10).to_bytes(4, "little") + bytes([bad_feature]) + b"\x00" * 9
                changed = bytearray(clean[:12] + vp8x + clean[12:])
                changed[4:8] = (len(changed) - 8).to_bytes(4, "little")
                self._must_reject_without_persisting(bytes(changed))

    def test_clean_three_formats_preserved_bitwise(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            with self.subTest(fmt=fmt):
                raw = create_image(self.source, fmt=fmt)
                record = capture(**dict(self.args, output_dir=self.dir / ("p1_" + fmt)))
                self.assertEqual(record["image_sha256"], sha256(raw).hexdigest())
                doc = json.loads((self.dir / ("p1_" + fmt) / "records" / record["record_name"]).read_text())
                self.assertEqual((self.dir / ("p1_" + fmt) / doc["image_file"]).read_bytes(), raw)
                self.assertFalse(doc["golden_set_admitted"])
                self.assertIsNone(doc["capture_artifact_digest"])

    def test_privacy_capture_rejection_does_not_escape_diagnostic_wrapper(self):
        clean = create_image(self.source, fmt="JPEG")
        marker = b"SYNTHETIC_PRIVATE_META_CANARY_CALLBACK"
        seg = b"\xff\xe2" + (len(marker) + 2).to_bytes(2, "big") + marker
        self.source.write_bytes(clean[:2] + seg + clean[2:])
        cb = capture_callback(output_dir=self.dir / "p1", environment=ENV)
        response = invoke_injected_capture(cb, image_path=self.source, backend="pollinations",
                    variation_attempt=0, post_type="morning", target_date="2026-10-27",
                    prompt_metadata_sha256=PROMPT_HASH)
        self.assertEqual(response, {"status": "NOT_CAPTURED", "reason": "embedded_metadata"})
        self.assertNotIn(marker.decode(), json.dumps(response))
        self.assertFalse((self.dir / "p1").exists())





class P1DeliveryFailureIsolationTests(unittest.TestCase):
    """Execute the P1 hook extracted from the actual delivery source AST.

    Surrounding dedup/fallback is covered by the existing visual suite; here
    we verify that the optional hook never blocks reaching the next call.
    """

    @staticmethod
    def _hook_code():
        import ast
        source = (ROOT / "tools/kld_visual_fixture_image.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        functions = [n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "execute_image_delivery"]
        assert len(functions) == 1
        target = None
        for n in ast.walk(functions[0]):
            if not isinstance(n, ast.If) or not isinstance(n.test, ast.Compare):
                continue
            if not isinstance(n.test.left, ast.Name) or n.test.left.id != "p1_capture":
                continue
            if not n.test.ops or not isinstance(n.test.ops[0], ast.IsNot):
                continue
            if not n.test.comparators or not isinstance(n.test.comparators[0], ast.Constant):
                continue
            if n.test.comparators[0].value is not None:
                continue
            target = n
        assert target is not None, "P1 hook must remain opt-in"
        # Safety: P1 injection must precede the existing candidate dedup check.
        tail = source.splitlines()[target.end_lineno:target.end_lineno + 4]
        assert any("duplicate = evaluate_candidate(" in line for line in tail), tail
        return compile(ast.fix_missing_locations(ast.Module(body=[target], type_ignores=[])),
                       "<exact-delivery-hook>", "exec")

    def _replay(self, *, capture, prompt="valid prompt", import_failure=False):
        import builtins
        import hashlib
        from types import SimpleNamespace
        source = self._hook_code()
        context = dict(
            p1_capture=capture,
            generation_kwargs={"prompt": prompt, "negative_prompt": ""},
            candidate={"variation_attempt": 0},
            args=SimpleNamespace(post_type="morning"),
            metadata={"target_date": "2026-10-29"},
            backend="pollinations",
            img_path="/tmp/synthetic-no-io.png",
            outcome={},
            json=json,
            hashlib=hashlib,
        )
        actual_import = builtins.__import__
        def deny_optional(name, *args, **kwargs):
            if name == "kld_semantic_qa_p1_capture":
                raise ImportError("synthetic optional capture dependency unavailable")
            return actual_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=deny_optional if import_failure else actual_import):
            exec(source, context)
        return context["outcome"]

    def test_hook_off_does_not_load_optional_module(self):
        actual = self._replay(capture=None, import_failure=True)
        self.assertEqual(actual, {})

    def test_optional_import_failure_is_diagnostic_not_delivery_failure(self):
        actual = self._replay(capture=lambda **kw: {"status": "QUARANTINED"}, import_failure=True)
        self.assertEqual(actual["p1_capture_results"],
                         [{"status": "NOT_CAPTURED", "reason": "capture_failed"}])

    def test_fingerprint_serialization_failure_is_diagnostic(self):
        # The fake provider can return a candidate even with an odd prompt.
        # P1 must not introduce a second publishing failure path.
        actual = self._replay(capture=lambda **kw: None, prompt=object())
        self.assertEqual(actual["p1_capture_results"],
                         [{"status": "NOT_CAPTURED", "reason": "capture_failed"}])

    def test_missing_prompt_is_diagnostic(self):
        # This exercises missing capture-only provenance without changing safety.
        source = self._hook_code()
        import hashlib
        from types import SimpleNamespace
        context = dict(p1_capture=lambda **kw: None, generation_kwargs={},
                       candidate={"variation_attempt": 0}, args=SimpleNamespace(post_type="morning"),
                       metadata={"target_date": "2026-10-29"}, backend="pollinations",
                       img_path="/tmp/synthetic-no-io.png", outcome={}, json=json, hashlib=hashlib)
        exec(source, context)
        self.assertEqual(context["outcome"]["p1_capture_results"],
                         [{"status": "NOT_CAPTURED", "reason": "capture_failed"}])

    def test_callback_failure_is_sanitized_and_nonblocking(self):
        def fail_callback(**kwargs):
            raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_LEAK")
        actual = self._replay(capture=fail_callback)
        self.assertEqual(actual["p1_capture_results"],
                         [{"status": "NOT_CAPTURED", "reason": "capture_failed"}])
        self.assertNotIn("SYNTHETIC_SECRET", str(actual))

    def test_callback_success_preserves_quarantine(self):
        actual = self._replay(capture=lambda **kw: {"status": "QUARANTINED", "image_sha256": "a" * 64})
        self.assertEqual(actual["p1_capture_results"],
                         [{"status": "QUARANTINED", "image_sha256": "a" * 64}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
