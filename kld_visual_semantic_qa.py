#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Injection-only semantic QA verdict normalisation for KLD AI images.

Inactive in production: nothing constructs an evaluator, and
``execute_image_delivery`` only calls this when a caller injects one.
The evaluator is an untrusted dependency.  Only a well-formed,
hash-bound, temporally aligned ACCEPT may let an already guard-accepted
candidate continue.  Every other outcome is REJECT or UNKNOWN, and the
caller treats both as a hard candidate rejection.
"""
from __future__ import annotations

import json
import math
import multiprocessing
import os
import signal
import threading
import time
from multiprocessing.connection import Connection
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping

SCHEMA = "kld_independent_semantic_qa_v1"
DECISIONS = ("ACCEPT", "REJECT", "UNKNOWN")
DEFAULT_TIMEOUT_S = 20.0
_OBSERVATIONS = {
    "moon_visibility": {"not_visible", "visible", "unclear"},
    "moon_shape": {"crescent", "quarter", "gibbous", "full_disk", "unclear", "not_applicable"},
    "dominant_moon": {"yes", "no", "unclear"},
    "visibility_appearance": {"normal", "mist", "dense_fog", "ambiguous"},
    "weather_appearance": {"cloudy", "rain", "drizzle", "snow", "fog", "storm", "clear", "other", "ambiguous"},
}
_CONFLICT_CODES = {"moon_phase_conflict", "invented_dense_fog", "other_weather_conflict"}
# Only apparent shape can be checked by the current evaluator schema.
# Waxing/waning orientation and illumination remain outside this contract.
_PHASE_SHAPES = {
    "new": frozenset(),
    "waxing_crescent": frozenset({"crescent"}),
    "first_quarter": frozenset({"quarter"}),
    "waxing_gibbous": frozenset({"gibbous"}),
    "full": frozenset({"full_disk"}),
    "waning_gibbous": frozenset({"gibbous"}),
    "last_quarter": frozenset({"quarter"}),
    "waning_crescent": frozenset({"crescent"}),
}
# Vocabulary of visual_context_kld.VisibilityCondition / VisibilityForecastWindow.
_VISIBILITY_CONDITIONS = frozenset(
    {"dense_fog", "fog", "mist", "reduced_visibility", "dust_haze", "mixed_visibility", "clear"}
)
_REDUCED_VISIBILITY = frozenset({"dense_fog", "fog", "mist", "reduced_visibility", "dust_haze"})
_VISIBILITY_WINDOWS = frozenset({"current_morning", "tomorrow_morning"})


@dataclass(frozen=True)
class SemanticQaVerdict:
    decision: str
    reason: str
    image_sha256: str
    evaluator_version: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision == "ACCEPT"

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "image_sha256": self.image_sha256,
            "evaluator_version": self.evaluator_version,
        }


# Do not pickle/unpickle arbitrary observer results over IPC. Reject oversized JSON.
_MAX_REPLY_BYTES = 65_536
_MAX_QA_SECONDS = 20.0


def _qa_child(write_end: Connection, evaluator: Callable[..., Any], image: bytes,
              facts: Mapping[str, Any], cpu_seconds: int) -> None:
    try:
        # A separate process group lets timeout cleanup kill spawned descendants.
        if os.name == "posix":
            os.setsid()
        try:
            import resource
            soft_cpu, hard_cpu = resource.getrlimit(resource.RLIMIT_CPU)
            bound = max(1, int(cpu_seconds))
            if soft_cpu == resource.RLIM_INFINITY or soft_cpu > bound:
                resource.setrlimit(resource.RLIMIT_CPU, (bound, hard_cpu))
            # Independent observer is offline at this stage; cap worker virtual
            # address space without reducing an already tighter limit.
            if hasattr(resource, "RLIMIT_AS"):
                old_soft, old_hard = resource.getrlimit(resource.RLIMIT_AS)
                cap = 2 * 1024 * 1024 * 1024
                if old_hard != resource.RLIM_INFINITY:
                    cap = min(cap, old_hard)
                if old_soft == resource.RLIM_INFINITY or old_soft > cap:
                    resource.setrlimit(resource.RLIMIT_AS, (cap, old_hard))
        except (ImportError, OSError, ValueError):
            pass  # The process deadline is enforced by the parent regardless.
        raw = evaluator(image, dict(facts))
        if not isinstance(raw, Mapping):
            raw = {"_invalid_type": True}
        payload = json.dumps(dict(raw), ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_REPLY_BYTES:
            payload = b'{}'
        write_end.send_bytes(payload)
    except BaseException:
        # A crash or failure is UNKNOWN. No worker tracebacks or secrets cross IPC.
        pass
    finally:
        write_end.close()


def _stop_worker(process: multiprocessing.Process) -> None:
    """Bounded cleanup for the child and its process group. No late work."""
    if process.pid is None:
        return
    if os.name == "posix":
        # A worker may have returned while a descendant remains alive.
        # Killing its dedicated group also covers this fast-return case.
        # Before setsid(), PGID==parent PGID, not worker.pid: killpg(pid)
        # therefore cannot accidentally target the parent's process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.is_alive():
                process.kill()
    elif process.is_alive():
        process.kill()
    process.join(timeout=0.3)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.3)


def _call_with_timeout(evaluator: Callable[..., Any], image: bytes,
                       facts: Mapping[str, Any], timeout_s: float) -> Any:
    """Run every injected evaluator in a killable POSIX child.

    There is no thread-based legacy fallback: if process isolation is not
    available, fail closed. The parent controls the wall-clock deadline
    and discards late results.
    """
    if (not isinstance(timeout_s, (float, int)) or isinstance(timeout_s, bool)
            or not math.isfinite(timeout_s) or not 0 < timeout_s <= _MAX_QA_SECONDS):
        raise TimeoutError("invalid observer deadline")
    if os.name != "posix" or "fork" not in multiprocessing.get_all_start_methods():
        raise RuntimeError("killable observer worker unavailable")
    if threading.active_count() != 1:
        raise RuntimeError("fork is unsafe while other threads are running")
    ctx = multiprocessing.get_context("fork")
    read_end, write_end = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_qa_child,
                         args=(write_end, evaluator, image, dict(facts), int(timeout_s) + 2),
                         daemon=True, name="kld-semantic-qa-observer")
    try:
        started = time.monotonic()
        worker.start()
        write_end.close()
        remaining = max(0.0, timeout_s - (time.monotonic() - started))
        if not read_end.poll(remaining):
            raise TimeoutError("observer deadline expired")
        payload = read_end.recv_bytes(maxlength=_MAX_REPLY_BYTES)
        # A reply that arrives after deadline can never promote to ACCEPT.
        if time.monotonic() - started > timeout_s:
            raise TimeoutError("observer replied after deadline")
        return json.loads(payload.decode("utf-8"))
    except TimeoutError:
        raise
    except (OSError, EOFError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("observer child failed") from exc
    finally:
        _stop_worker(worker) if worker.pid is not None else None
        read_end.close()
        write_end.close()


def evaluate_semantic_qa(
    evaluator: Callable[[bytes, Mapping[str, Any]], Any],
    image: bytes | None,
    facts: Mapping[str, Any],
    *,
    expected_target_date: str,
    expected_post_type: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> SemanticQaVerdict:
    if not isinstance(image, bytes) or not image:
        return SemanticQaVerdict("UNKNOWN", "image_unreadable", "")
    digest = sha256(image).hexdigest()

    def verdict(decision: str, reason: str, version: str = "") -> SemanticQaVerdict:
        return SemanticQaVerdict(decision, reason, digest, version)

    # Temporal alignment: facts must describe the exact post being illustrated.
    if not isinstance(facts, Mapping):
        return verdict("UNKNOWN", "facts_missing")
    if str(facts.get("target_date") or "") != str(expected_target_date or "") or not expected_target_date:
        return verdict("UNKNOWN", "temporal_mismatch")
    if str(facts.get("post_type") or "") != str(expected_post_type or ""):
        return verdict("UNKNOWN", "temporal_mismatch")
    for key in ("weather_target_date", "visibility_target_date", "lunar_target_date"):
        if key in facts and str(facts.get(key) or "") != str(expected_target_date):
            return verdict("UNKNOWN", "temporal_mismatch")
    try:
        raw = _call_with_timeout(evaluator, image, facts, timeout_s)
    except TimeoutError:
        return verdict("UNKNOWN", "qa_timeout")
    except Exception:
        return verdict("UNKNOWN", "qa_unavailable")
    if not isinstance(raw, Mapping):
        return verdict("UNKNOWN", "schema_error")
    version = raw.get("model_or_evaluator_version")
    if raw.get("qa_schema_version") != SCHEMA or not isinstance(version, str) or not version:
        return verdict("UNKNOWN", "schema_error")
    if raw.get("raw_image_sha256") != digest:
        return verdict("UNKNOWN", "image_hash_mismatch", version)
    decision = raw.get("decision")
    if decision not in DECISIONS:
        return verdict("UNKNOWN", "invalid_decision", version)
    observations = raw.get("observations")
    if not isinstance(observations, Mapping):
        return verdict("UNKNOWN", "missing_observations", version)
    for key, allowed in _OBSERVATIONS.items():
        value = observations.get(key)
        if not isinstance(value, str) or value not in allowed:
            return verdict("UNKNOWN", "invalid_observations", version)
    codes = raw.get("reason_codes")
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        return verdict("UNKNOWN", "invalid_reason_codes", version)
    if decision == "ACCEPT":
        if _CONFLICT_CODES.intersection(codes):
            return verdict("UNKNOWN", "contradictory_verdict", version)
        if any(value in {"unclear", "ambiguous"} for value in observations.values()):
            return verdict("UNKNOWN", "insufficient_visual_evidence", version)
        moon_visibility = observations["moon_visibility"]
        moon_shape = observations["moon_shape"]
        dominant_moon = observations["dominant_moon"]
        if moon_visibility == "not_visible" and (
            moon_shape != "not_applicable" or dominant_moon == "yes"
        ):
            return verdict("UNKNOWN", "contradictory_observations", version)
        if moon_visibility == "visible" and moon_shape == "not_applicable":
            return verdict("UNKNOWN", "contradictory_observations", version)
        lunar = str(facts.get("lunar_phase") or "")
        if moon_visibility == "visible":
            # Unknown or missing source phase cannot substantiate any visible moon.
            if lunar not in _PHASE_SHAPES:
                return verdict("UNKNOWN", "lunar_phase_unknown", version)
            if moon_shape not in _PHASE_SHAPES[lunar]:
                return verdict("REJECT", "moon_phase_conflict", version)
            # Editorial morning contract: no visible moon at all, dominant or not.
            if expected_post_type == "morning":
                reason = "morning_dominant_moon" if dominant_moon == "yes" else "morning_moon_visible"
                return verdict("REJECT", reason, version)
        # A visible-weather claim is meaningful only for the pictured forecast
        # window.  Unknown window labels cannot substantiate an ACCEPT.
        visibility = facts.get("visibility_condition")
        image_window = facts.get("forecast_window")
        if (not isinstance(visibility, str) or visibility not in _VISIBILITY_CONDITIONS
                or not isinstance(image_window, str) or image_window not in _VISIBILITY_WINDOWS):
            return verdict("UNKNOWN", "visibility_unknown", version)
        visibility_window = facts.get("visibility_forecast_window")
        if visibility in _REDUCED_VISIBILITY:
            if not isinstance(visibility_window, str) or visibility_window not in _VISIBILITY_WINDOWS:
                return verdict("UNKNOWN", "visibility_unknown", version)
            if visibility_window != image_window:
                return verdict("UNKNOWN", "visibility_window_mismatch", version)
            if observations["visibility_appearance"] == "normal":
                return verdict("REJECT", "reduced_visibility_not_shown", version)
        elif visibility_window not in (None, "", "none"):
            # For clear/mixed conditions a dedicated visibility window may be
            # absent, but any explicitly supplied one must also be aligned.
            if not isinstance(visibility_window, str) or visibility_window not in _VISIBILITY_WINDOWS:
                return verdict("UNKNOWN", "visibility_unknown", version)
            if visibility_window != image_window:
                return verdict("UNKNOWN", "visibility_window_mismatch", version)
        if visibility == "clear" and observations["visibility_appearance"] == "dense_fog":
            return verdict("REJECT", "invented_dense_fog", version)
    return verdict(decision, codes[0] if codes else "observed_consistent", version)


__all__ = ["SCHEMA", "SemanticQaVerdict", "evaluate_semantic_qa", "DEFAULT_TIMEOUT_S"]
