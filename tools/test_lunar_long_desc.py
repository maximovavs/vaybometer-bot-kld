#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression for deterministic lunar long_desc completeness fallback."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Short texts are skipped so only the long_desc LLM path is exercised.
os.environ["GEN_SKIP_SHORT"] = "1"

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
    assert lunar.SKIP_SHORT is True
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

        # lunar_calendar.json is rolling production data, not an October
        # fixture: generate October 2026 offline through the real gpt_long
        # with a malformed LLM answer and no VoC transport.
        calls = []
        lunar.gpt_complete = lambda **kwargs: calls.append(kwargs) or MALFORMED
        with patch.object(lunar, "find_voc_intervals_for_month", return_value=[]):
            payload = asyncio.run(lunar.generate(2026, 10))
        assert calls, "October generation must exercise the real gpt_long path"
    finally:
        lunar.gpt_complete = original

    days = payload["days"]
    assert len(days) == 31 and all(day.startswith("2026-10-") for day in days), sorted(days)
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
