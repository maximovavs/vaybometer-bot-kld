#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Groq migration and missing-weather fallback regression checks."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIMARY_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

from format_v2 import build_format_v2  # noqa: E402
from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import _apply_format_v2_safe_postprocess, _finalize_kld_morning_safe_text  # noqa: E402


def _deprecated_model_ids() -> tuple[str, str]:
    return ("llama-" + "3.3-70b-versatile", "llama-" + "3.1-8b-instant")


def _tracked_runtime_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    exts = {".py", ".yml", ".yaml", ".md"}
    names = {".env.example", ".env"}
    out: list[Path] = []
    for rel in proc.stdout.splitlines():
        path = ROOT / rel
        if path.suffix in exts or path.name in names:
            out.append(path)
    return out


def test_no_deprecated_model_ids_in_runtime_files() -> None:
    offenders: list[str] = []
    deprecated = _deprecated_model_ids()
    for path in _tracked_runtime_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for model_id in deprecated:
            if model_id in text:
                offenders.append(str(path.relative_to(ROOT)))
                break
    assert not offenders, "deprecated Groq model IDs remain in: " + ", ".join(offenders)


def _import_gpt_fresh():
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "GROQ_FALLBACK_MODEL",
    ):
        os.environ.pop(name, None)
    sys.modules.pop("gpt", None)
    import gpt  # type: ignore

    return gpt


class _FakeGroqClient:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures
        self.calls: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, *, model: str, messages, temperature: float, max_tokens: int):
        self.calls.append(model)
        if model in self.failures:
            raise RuntimeError("429 rate limit")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="Если завтра что-то пойдёт не так, вините ветер!\nПейте воду\nДышите ровно\nЛожитесь раньше"
                    )
                )
            ]
        )


def _force_groq_only(gpt, client: _FakeGroqClient) -> None:
    gpt.OPENAI_KEY = ""
    gpt.GEMINI_KEY = ""
    gpt.GROQ_KEY = "test"
    gpt.GROQ_MODEL = PRIMARY_MODEL
    gpt.GROQ_FALLBACK_MODEL = FALLBACK_MODEL
    gpt.GROQ_MODELS = [PRIMARY_MODEL, FALLBACK_MODEL]
    gpt._OPENAI_DISABLED_FOR_RUN = False
    gpt._GEMINI_DISABLED_FOR_RUN = False
    gpt._groq_client = lambda: client


def test_default_groq_model_config() -> None:
    gpt = _import_gpt_fresh()
    assert gpt.GROQ_MODEL == PRIMARY_MODEL
    assert gpt.GROQ_FALLBACK_MODEL == FALLBACK_MODEL
    assert gpt.GROQ_MODELS == [PRIMARY_MODEL, FALLBACK_MODEL]


def test_primary_failure_attempts_fallback_model() -> None:
    gpt = _import_gpt_fresh()
    client = _FakeGroqClient(failures={PRIMARY_MODEL})
    _force_groq_only(gpt, client)

    text = gpt.gpt_complete("test prompt", temperature=0.7, max_tokens=80)

    assert client.calls == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert isinstance(text, str)
    assert text.startswith("Если завтра")


def test_total_groq_failure_uses_local_blurb_fallback() -> None:
    gpt = _import_gpt_fresh()
    client = _FakeGroqClient(failures={PRIMARY_MODEL, FALLBACK_MODEL})
    _force_groq_only(gpt, client)

    summary, tips = gpt.gpt_blurb("жара")

    assert client.calls == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert isinstance(summary, str)
    assert 0 < len(summary) < 180
    assert isinstance(tips, list)
    assert len(tips) == 3
    assert all(isinstance(tip, str) and tip for tip in tips)


MISSING_CORE_MORNING = """<b>🌅 Калининградская область: погода на сегодня (03.07.2026)</b>
✨ VayboMeter: 8.6/10 — хорошо.
🌡 По области: тепло; у Балтики свежее и ветренее.
💬 По-человечески: редкий день, когда погода почти не спорит с планами.
Погода: 🏙 Калининград — н/д • ясно • 💨 н/д • 🔷 н/д.
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Почти полная Луна, ♐ (96%)
✨ 96% освещённости — эмоции ярче обычного.
💚 В плюсе: планы, обучение.
⚫ VoC: 18:20–19:10.
✅ План: ⏰ Планируйте поездки заранее; 🙅 Избегайте стрессовых новостей; 😌 Лёгкая растяжка перед сном.
#Калининград #погода #здоровье #сегодня #море
"""


def _with_env(names: tuple[str, ...]):
    old_env = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = "1"
    return old_env


def _restore_env(old_env: dict[str, str | None]) -> None:
    for name, value in old_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def kld_missing_core_morning_fails_closed() -> None:
    env_names = (
        "FORMAT_V2",
        "MORNING_BEST_WINDOW",
        "MORNING_VAYBOMETER_SCORE",
        "MORNING_SMART_PLAN",
        "FORMAT_V2_SENSOR_LINE",
        "FORMAT_V2_ASTRO_CLEANUP",
    )
    old_env = _with_env(env_names)
    try:
        legacy_result = sanitize_post_text(MISSING_CORE_MORNING)
        v2 = build_format_v2("Калининградская область", "morning", legacy_result.text)
        final_text = _apply_format_v2_safe_postprocess(v2, MISSING_CORE_MORNING, legacy_result.text, "morning")
        final_text = sanitize_post_text(final_text).text
        final_text = _finalize_kld_morning_safe_text(final_text, MISSING_CORE_MORNING, legacy_result.text, "morning")
    finally:
        _restore_env(old_env)

    assert "⚠️ Данные по Калининграду обновились не полностью; проверяем источник." in final_text
    assert not re.search(r"VayboMeter:\s*\d", final_text)
    assert "хорошо" not in final_text.lower()
    assert "погода почти не спорит" not in final_text
    assert "Планируйте поездки заранее" not in final_text
    assert "Избегайте стрессовых новостей" not in final_text
    assert "Лёгкая растяжка перед сном" not in final_text
    assert "✅ План: перед выходом проверьте актуальный прогноз; пост обновится после восстановления данных." in final_text
    assert final_text.splitlines()[-1] == "#Калининград #погода #здоровье #сегодня #море"


def main() -> None:
    tests = [
        test_no_deprecated_model_ids_in_runtime_files,
        test_default_groq_model_config,
        test_primary_failure_attempts_fallback_model,
        test_total_groq_failure_uses_local_blurb_fallback,
        kld_missing_core_morning_fails_closed,
    ]
    for test in tests:
        test()
    print("PASS groq_model_migration")


if __name__ == "__main__":
    main()
