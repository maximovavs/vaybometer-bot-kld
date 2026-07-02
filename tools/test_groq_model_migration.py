from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PRIMARY_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"


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
    sys.path.insert(0, str(ROOT))
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


def main() -> None:
    tests = [
        test_no_deprecated_model_ids_in_runtime_files,
        test_default_groq_model_config,
        test_primary_failure_attempts_fallback_model,
        test_total_groq_failure_uses_local_blurb_fallback,
    ]
    for test in tests:
        test()
    print("PASS groq_model_migration")


if __name__ == "__main__":
    main()
