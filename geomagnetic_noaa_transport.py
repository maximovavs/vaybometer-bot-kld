"""Opt-in, fail-closed HTTPS adapter for NOAA's observed three-hour Kp product.

The imported A1 history engine only accepts provenance asserted by a caller.
This boundary is the *only* module that can make that assertion from an HTTP
response: a verified TLS connection to the pinned NOAA URL, no redirects, a
bounded JSON body, and an allowlisted observed-3h schema are mandatory.

Importing this module does no I/O. Calling `load_observed_3h_history` explicitly
is required; no production workflow imports or invokes it. A caller must inject
its UTC clock. The injectable HTTP function is a trusted test/integration seam;
a malicious replacement cannot be authenticated by this function.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from geomagnetic_history import OBSERVED_SOURCE, ParsedHistory, parse_observed_3h_history

NOAA_OBSERVED_3H_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
MAX_RESPONSE_BYTES = 512 * 1024
TIMEOUT_SECONDS = 10.0
_ALLOWED_FIELDS = frozenset({
    "time_tag", "Kp", "a_running", "station_count", "source", "kind",
    "type", "product", "status",
})
_PROVENANCE_FIELDS = {
    "source": frozenset({OBSERVED_SOURCE}),
    "kind": frozenset({"observed_3h"}),
    "type": frozenset({"observed", "observed_3h"}),
    "product": frozenset({"noaa-planetary-k-index", "noaa-planetary-k-index.json"}),
    "status": frozenset({"observed"}),
}
# NOAA's known header-based product has no row-level origin columns. Reject a
# future unexpected header until its provenance semantics have been reviewed.
_TABLE_COLUMNS = frozenset({"time_tag", "Kp", "a_running", "station_count"})
_UTC_TAG = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]00:00)?$")


class NoaaTransportError(ValueError):
    """HTTP trust or payload schema contract was not verified; do not use data."""


@dataclass(frozen=True, slots=True)
class NOAAResponse:
    status: int
    final_url: str
    body: bytes
    content_type: str = "application/json"
    redirect_count: int = 0


def _no_redirect_fetch(url: str, *, timeout_s: float, max_bytes: int) -> NOAAResponse:
    """One verified-TLS HTTPS GET; no redirects, retries, or unbounded reads."""
    from urllib import request

    class DenyRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = request.build_opener(DenyRedirect)
    req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with opener.open(req, timeout=timeout_s) as response:
        return NOAAResponse(
            status=response.status,
            final_url=response.geturl(),
            content_type=response.headers.get("Content-Type", ""),
            body=response.read(max_bytes + 1),
        )


def _utc_clock(clock: Callable[[], datetime]) -> datetime:
    if not callable(clock):
        raise NoaaTransportError("explicit UTC clock required")
    try:
        now = clock()
    except Exception as exc:
        raise NoaaTransportError("clock failed") from exc
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise NoaaTransportError("clock must return a UTC-aware datetime")
    return now.astimezone(timezone.utc)


def _interval_start(raw: object) -> datetime:
    if not isinstance(raw, str) or _UTC_TAG.fullmatch(raw) is None:
        raise NoaaTransportError("invalid observed-3h UTC timestamp")
    ts = raw.replace(" ", "T").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError as exc:
        raise NoaaTransportError("invalid observed-3h UTC timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.utcoffset() != timedelta(0) or dt.hour % 3 or dt.minute or dt.second or dt.microsecond:
        raise NoaaTransportError("observed-3h UTC boundary required")
    return dt


def _kp(raw: object) -> None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise NoaaTransportError("invalid Kp value")
    try:
        value = float(raw.strip().replace(",", ".")) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NoaaTransportError("invalid Kp value") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 9.0:
        raise NoaaTransportError("invalid Kp value")


def _parse_unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NoaaTransportError("duplicate JSON key")
        result[key] = value
    return result


def _refuse_nonfinite(value: str) -> None:
    raise NoaaTransportError("nonfinite JSON literal")


def _validate_schema(payload: object) -> None:
    if not isinstance(payload, list):
        raise NoaaTransportError("NOAA observed-3h root must be an array")
    if not payload:
        return  # Honest empty history => UNKNOWN, never presumed quiet.

    if isinstance(payload[0], list):
        fields = payload[0]
        if (not fields or not all(isinstance(f, str) for f in fields)
                or len(fields) != len(set(fields)) or not set(fields) <= _TABLE_COLUMNS
                or "time_tag" not in fields or "Kp" not in fields):
            raise NoaaTransportError("unrecognized observed-3h table header")
        ti, ki = fields.index("time_tag"), fields.index("Kp")
        for entry in payload[1:]:
            if not isinstance(entry, list) or len(entry) != len(fields):
                raise NoaaTransportError("malformed observed-3h table row")
            _interval_start(entry[ti])
            _kp(entry[ki])
        return

    for entry in payload:
        if not isinstance(entry, dict) or not {"time_tag", "Kp"} <= entry.keys():
            raise NoaaTransportError("malformed observed-3h object row")
        if not entry.keys() <= _ALLOWED_FIELDS:
            raise NoaaTransportError("unknown observed-3h fields")
        for key, allowed in _PROVENANCE_FIELDS.items():
            if key in entry and (not isinstance(entry[key], str) or entry[key] not in allowed):
                raise NoaaTransportError("untrusted observed-3h provenance")
        _interval_start(entry["time_tag"])
        _kp(entry["Kp"])


def load_observed_3h_history(
    *,
    clock: Callable[[], datetime],
    transport: Callable[..., NOAAResponse] | None = None,
) -> ParsedHistory:
    """Fetch the *one* approved NOAA product and pass verified rows into A1.

    Missing intervals, duplicates, conflicts, unsorted rows, and historical
    backfills pass through untouched to A1. Malformed and suspect transport
    responses fail closed rather than relabeling partial data as observed.
    """
    as_of = _utc_clock(clock)
    fetch = _no_redirect_fetch if transport is None else transport
    if not callable(fetch):
        raise NoaaTransportError("invalid transport")
    try:
        response = fetch(NOAA_OBSERVED_3H_URL, timeout_s=TIMEOUT_SECONDS, max_bytes=MAX_RESPONSE_BYTES)
    except Exception as exc:
        raise NoaaTransportError("NOAA observed-3h HTTPS request failed") from exc
    if not isinstance(response, NOAAResponse):
        raise NoaaTransportError("unexpected transport response")
    if (type(response.status) is not int or response.status != 200
            or type(response.final_url) is not str or response.final_url != NOAA_OBSERVED_3H_URL
            or type(response.redirect_count) is not int or response.redirect_count != 0):
        raise NoaaTransportError("untrusted NOAA HTTP response or redirect")
    if not isinstance(response.content_type, str) or response.content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise NoaaTransportError("unrecognized NOAA content type")
    if not isinstance(response.body, bytes) or not 0 < len(response.body) <= MAX_RESPONSE_BYTES:
        raise NoaaTransportError("empty or oversized NOAA response")
    try:
        payload = json.loads(response.body.decode("utf-8"), object_pairs_hook=_parse_unique_object,
                             parse_constant=_refuse_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise NoaaTransportError("invalid NOAA JSON") from exc
    _validate_schema(payload)
    # Provenance is asserted ONLY after all HTTPS, response and schema guards.
    return parse_observed_3h_history(payload, as_of_utc=as_of, source=OBSERVED_SOURCE)


__all__ = ["NOAA_OBSERVED_3H_URL", "MAX_RESPONSE_BYTES", "NOAAResponse",
           "NoaaTransportError", "load_observed_3h_history"]
