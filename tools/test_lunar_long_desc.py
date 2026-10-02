#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression for deterministic lunar long_desc completeness fallback."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("GEN_SKIP_SHORT", "1")

import gen_lunar_calendar as lunar  # noqa: E402


MALFORMED = "В этот период природная энергия помогает легко завер"
EXPECTED_DATES = {
    "2026-10-01",
    "2026-10-02",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
    "2026-10-31",
}


def main() -> None:
    fallback = lunar.FALLBACK_LONG["Убывающая Луна"]

    original = lunar.gpt_complete
    try:
        lunar.gpt_complete = lambda **_kwargs: MALFORMED
        result = asyncio.run(lunar.gpt_long("Убывающая Луна", ""))
        assert result == fallback, result

        complete = "В этот период спокойно завершаем дела и наводим порядок."
        lunar.gpt_complete = lambda **_kwargs: complete
        result = asyncio.run(lunar.gpt_long("Убывающая Луна", ""))
        assert result == complete, result
    finally:
        lunar.gpt_complete = original

    payload = json.loads((ROOT / "lunar_calendar.json").read_text("utf-8"))
    days = payload.get("days", payload)
    affected = {
        date
        for date, rec in days.items()
        if rec.get("phase_name") == "Убывающая Луна"
        and rec.get("long_desc") == fallback
    }
    assert affected == EXPECTED_DATES, (affected, EXPECTED_DATES)
    assert all(rec.get("long_desc") != MALFORMED for rec in days.values())

    print("OK: KLD lunar long_desc completeness guard passed")


if __name__ == "__main__":
    main()
