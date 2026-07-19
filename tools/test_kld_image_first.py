#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production-like offline checks for nonblocking KLD image-first publishing."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kld_image_first import FORMAT_V2_BEGIN, FORMAT_V2_END, run_image_first_publication  # noqa: E402
from kld_informative_cover import (  # noqa: E402
    RENDERER_VERSION,
    extract_kld_cover_facts,
    render_kld_informative_cover,
)
from kld_visual_dedup import KldVisualDuplicateResult  # noqa: E402
from tools.kld_visual_fixture_image import (  # noqa: E402
    _load_visibility_context_file,
    build_payload,
    execute_image_delivery,
)


MESSAGE = """<b>🌅 Калининградская область завтра (20.07.2026)</b>
🏙 Калининград — 18/12 °C • ☁️ облачно • 💨 3 м/с • порывы до 10 м/с
🌫 Видимость: завтра утром местами снижена; около 5500 м.
🌊 Балтийск: 17/13 °C • 🌊 20°C • волна 0.4 м
#Калининград #погода
"""


def _args(root: Path, *, post_type: str = "evening") -> argparse.Namespace:
    message_path = root / "format_v2_message.txt"
    message_path.write_text(MESSAGE, encoding="utf-8")
    return argparse.Namespace(
        scenario="",
        message_file=str(message_path),
        visibility_context_file="",
        post_type=post_type,
        generate=False,
        send_to_test=True,
        chat_id="test",
        caption="test caption",
        history_namespace="test",
        result_file=str(root / "image_result.json"),
        prompt_metadata_file=str(root / "image_prompt_metadata.json"),
        cover_path=str(root / "cover.png"),
    )


def _image(path: Path, color: tuple[int, int, int] = (70, 110, 140)) -> str:
    from PIL import Image

    Image.new("RGB", (32, 32), color).save(path)
    return str(path)


def _duplicate(*, accepted: bool, reason: str) -> KldVisualDuplicateResult:
    return KldVisualDuplicateResult(
        accepted=accepted,
        reason=reason,
        sha256="a" * 64,
        perceptual_hash="0" * 16,
        min_distance=12,
    )


def _cover_renderer(path_events: list[str], *, fail: bool = False):
    def render(message: str, *, post_type: str, visibility_context, output_path: str):
        path_events.append("cover")
        if fail:
            raise RuntimeError("cover renderer failed")
        _image(Path(output_path), (155, 165, 170))
        return {"renderer_version": RENDERER_VERSION, "facts": ["ВИДИМОСТЬ УТРОМ СНИЖЕНА"]}

    return render


def _record(events: list[str]):
    def record(**kwargs):
        events.append("history")
        return {"sha256": "b" * 64, "scene_family": kwargs["scene_family"]}

    return record


def _run_delivery(
    root: Path,
    *,
    generate,
    evaluate,
    cover_renderer,
    send_photo,
    record,
    post_type: str = "evening",
):
    args = _args(root, post_type=post_type)
    visibility = {
        "visibility_condition": "reduced_visibility",
        "morning_min_visibility_m": 5500,
        "reported_visibility_threshold_m": 6000,
    }
    payload = build_payload(MESSAGE, "test", post_type=post_type, visibility_context=visibility)
    return execute_image_delivery(
        args=args,
        message=MESSAGE,
        initial_payload=payload,
        visibility_context=visibility,
        history_path=root / "history.json",
        generate_image=generate,
        evaluate_candidate=evaluate,
        cover_renderer=cover_renderer,
        send_photo=send_photo,
        record_publication=record,
    )


def _orchestrate(
    root: Path,
    image_outcome: dict[str, object],
    *,
    mode: str = "evening",
    preview_returncode: int = 0,
    text_error: Exception | None = None,
):
    events: list[str] = []
    result_path = root / "image_result.json"
    preview_output = f"diagnostic\n{FORMAT_V2_BEGIN}\n{MESSAGE}{FORMAT_V2_END}\n"

    def runner(cmd, **kwargs):
        if cmd[0] == "preview":
            events.append("preview")
            return SimpleNamespace(returncode=preview_returncode, stdout=preview_output)
        events.append("image")
        result_path.write_text(json.dumps(image_outcome), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def send_text(path: str):
        events.append("text")
        assert Path(path).read_text(encoding="utf-8").strip() == MESSAGE.strip()
        if text_error:
            raise text_error
        return [501]

    outcome = run_image_first_publication(
        mode=mode,
        preview_cmd=["preview"],
        image_cmd=["image"],
        send_text=send_text,
        message_path=root / "format_v2_message.txt",
        preview_log_path=root / "safe_test_post_preview.log",
        result_path=result_path,
        prompt_metadata_path=root / "image_prompt_metadata.json",
        run_process=runner,
    )
    return outcome, events


def pollinations_failure_uses_cover_and_text_once() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def generate(**kwargs):
            raise RuntimeError("Pollinations exhausted retries")

        outcome = _run_delivery(
            root,
            generate=generate,
            evaluate=lambda *args, **kwargs: _duplicate(accepted=True, reason="accepted"),
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 101,
            record=_record(events),
        )
        assert outcome["result"] == "fallback_sent"
        assert outcome["backend"] == "local_informative_cover"
        assert outcome["cover_attempted"] is True
        assert events == ["cover", "photo", "history"]

        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True
        assert order == ["preview", "image", "text"]


def exact_duplicates_are_nonfatal_and_text_once() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def generate(**kwargs):
            return _image(root / "ai.png")

        def evaluate(path, **kwargs):
            if str(path).endswith("cover.png"):
                return _duplicate(accepted=True, reason="accepted")
            return _duplicate(accepted=False, reason="exact_duplicate")

        outcome = _run_delivery(
            root,
            generate=generate,
            evaluate=evaluate,
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 102,
            record=_record(events),
        )
        assert outcome["result"] == "fallback_sent"
        assert len([item for item in outcome["dedup_results"] if item["backend"] == "pollinations"]) == 3
        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True and order.count("text") == 1


def send_photo_failure_does_not_record_history_and_text_once() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def send_photo(*args, **kwargs):
            events.append("photo")
            raise RuntimeError("Telegram send_photo failed")

        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: _image(root / "ai.png"),
            evaluate=lambda *args, **kwargs: _duplicate(accepted=True, reason="accepted"),
            cover_renderer=_cover_renderer(events),
            send_photo=send_photo,
            record=_record(events),
        )
        assert outcome["result"] == "failed_nonfatal"
        assert outcome["telegram_image_sent"] is False
        assert outcome["history_recorded"] is False
        assert "history" not in events
        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True and order.count("text") == 1


def cover_failure_keeps_text_and_no_stale_image() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
            evaluate=lambda *args, **kwargs: _duplicate(accepted=True, reason="accepted"),
            cover_renderer=_cover_renderer(events, fail=True),
            send_photo=lambda *args, **kwargs: events.append("photo") or 103,
            record=_record(events),
        )
        assert outcome["result"] == "failed_nonfatal"
        assert outcome["telegram_image_sent"] is False
        assert outcome["history_recorded"] is False
        assert not (root / "cover.png").exists()
        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True and order == ["preview", "image", "text"]


def successful_ai_image_is_before_text_and_records_history() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: _image(root / "ai.png"),
            evaluate=lambda *args, **kwargs: _duplicate(accepted=True, reason="accepted"),
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 104,
            record=_record(events),
        )
        assert outcome["result"] == "sent"
        assert outcome["telegram_image_sent"] is True
        assert outcome["history_recorded"] is True
        assert events == ["photo", "history"]
        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True
        assert order == ["preview", "image", "text"]


def preview_failure_sends_nothing_and_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            _orchestrate(root, {}, preview_returncode=7)
        except subprocess.CalledProcessError as exc:
            assert exc.returncode == 7
        else:
            raise AssertionError("preview failure must propagate")
        result = json.loads((root / "image_result.json").read_text(encoding="utf-8"))
        assert result["preview_succeeded"] is False
        assert result["text_sent"] is False
        prompt_metadata = json.loads((root / "image_prompt_metadata.json").read_text(encoding="utf-8"))
        assert prompt_metadata["status"] == "not_built"


def text_send_failure_remains_fatal() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            _orchestrate(
                root,
                {"result": "failed_nonfatal", "telegram_image_sent": False},
                text_error=RuntimeError("Telegram text send failed"),
            )
        except RuntimeError as exc:
            assert "text send failed" in str(exc)
        else:
            raise AssertionError("text send failure must propagate")
        result = json.loads((root / "image_result.json").read_text(encoding="utf-8"))
        assert result["text_sent"] is False
        assert result["text_error_type"] == "RuntimeError"


def morning_uses_same_nonblocking_image_behavior() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        outcome, events = _orchestrate(
            root,
            {"result": "failed_nonfatal", "telegram_image_sent": False, "cover_attempted": True},
            mode="morning",
        )
        assert outcome["mode"] == "morning"
        assert outcome["text_sent"] is True
        assert events == ["preview", "image", "text"]


def visibility_sidecar_actuals_and_safe_fallback() -> None:
    facts = extract_kld_cover_facts(
        MESSAGE,
        post_type="evening",
        visibility_context={
            "visibility_condition": "fog",
            "morning_min_visibility_m": 850,
            "reported_visibility_threshold_m": 1500,
        },
    )
    assert facts["weather"]["fog"] is True
    assert facts["actual_values"]["visibility_m"] == 850
    assert "1500" not in " ".join(facts["facts"])

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        corrupt = root / "visibility.json"
        corrupt.write_text("{broken", encoding="utf-8")
        assert _load_visibility_context_file(str(corrupt)) is None
        fallback = extract_kld_cover_facts(MESSAGE, post_type="evening", visibility_context=None)
        assert all("1500" not in fact for fact in fallback["facts"])


def local_cover_is_png_1080_and_weather_factual() -> None:
    rainy = MESSAGE.replace("☁️ облачно", "🌧 дождь")
    with TemporaryDirectory() as tmp:
        output = Path(tmp) / "cover.png"
        metadata = render_kld_informative_cover(
            rainy,
            post_type="evening",
            visibility_context={"visibility_condition": "reduced_visibility"},
            output_path=output,
        )
        from PIL import Image

        with Image.open(output) as image:
            assert image.format == "PNG"
            assert image.size == (1080, 1080)
        assert metadata["renderer_version"] == RENDERER_VERSION
        assert metadata["title"] == "КАЛИНИНГРАД ЗАВТРА"
        assert metadata["weather"]["rain"] is True
        assert len(metadata["facts"]) <= 3

    dry_caution = MESSAGE + "\n⚠️ Нюанс: вероятность дождя лучше проверить утром.\n"
    dry_facts = extract_kld_cover_facts(dry_caution, post_type="evening")
    assert dry_facts["weather"]["rain"] is False

    ranged_wind = MESSAGE.replace("💨 3 м/с", "💨 Ветер: 3–5 м/с")
    ranged_facts = extract_kld_cover_facts(ranged_wind, post_type="evening")
    assert any("3–5 М/С" in fact for fact in ranged_facts["facts"])
    assert ranged_facts["actual_values"]["wind_mps"] is None


def storm_and_precipitation_truth_are_independent() -> None:
    base = """<b>🌅 Калининградская область завтра (21.07.2026)</b>
🏙 Калининград — 20/14 °C • ☁️ облачно • 💨 5 м/с
#Калининград #погода
"""
    scenarios = {
        "dry_storm": (
            base + "Штормовое предупреждение: штормовой ветер, без осадков.\n",
            {"explicit_storm": True, "actual_precipitation": False, "rain": False, "thunderstorm": False},
        ),
        "negated_storm": (
            base + "Штормовых предупреждений нет; преимущественно сухо.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False, "thunderstorm": False},
        ),
        "negated_thunderstorm": (
            base + "Грозы не ожидаются; дождя не ожидается.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False, "thunderstorm": False},
        ),
        "thunderstorm_without_rain": (
            base + "⛈ Гроза, без осадков.\n",
            {"explicit_storm": True, "actual_precipitation": False, "rain": False, "thunderstorm": True},
        ),
        "rain_without_storm": (
            base.replace("☁️ облачно", "🌧 дождь"),
            {"explicit_storm": False, "actual_precipitation": True, "rain": True, "thunderstorm": False},
        ),
        "drizzle_without_rain": (
            base.replace("☁️ облачно", "морось"),
            {
                "explicit_storm": False,
                "actual_precipitation": True,
                "rain": False,
                "drizzle": True,
                "thunderstorm": False,
            },
        ),
        "storm_and_rain": (
            base + "Штормовое предупреждение: сильный ветер.\n🌧 Дождь подтверждён.\n",
            {"explicit_storm": True, "actual_precipitation": True, "rain": True, "thunderstorm": False},
        ),
        "editorial_storm": (
            base
            + "✨ VayboMeter завтра: шторм и дождь требуют внимания.\n"
            + "⚠️ Главный нюанс: шторм у воды.\n"
            + "✅ План: дождь проверить утром.\n"
            + "🎯 Уверенность: гроза возможна.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False, "thunderstorm": False},
        ),
        "uncertain_rain": (
            base + "Вероятность дождя проверить утром.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False, "thunderstorm": False},
        ),
        "dry_severe_wind": (
            base.replace("💨 5 м/с", "💨 9 м/с • порывы до 17.5 м/с") + "Преимущественно сухо.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False, "thunderstorm": False},
        ),
    }

    from PIL import Image

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, (message, expected) in scenarios.items():
            output = root / f"{name}.png"
            metadata = render_kld_informative_cover(
                message,
                post_type="evening",
                output_path=output,
            )
            weather = metadata["weather"]
            assert all(
                flag in weather
                for flag in (
                    "explicit_storm",
                    "actual_precipitation",
                    "rain",
                    "drizzle",
                    "snow",
                    "thunderstorm",
                    "strong_wind",
                )
            )
            for flag, value in expected.items():
                assert weather[flag] is value, (name, flag, weather)

            rain_expected = expected["rain"]
            storm_expected = expected["explicit_storm"]
            assert metadata["rain_graphics"] is rain_expected, name
            assert metadata["lightning_graphics"] is storm_expected, name
            assert bool(metadata["graphics"]["rain_lines"]) is rain_expected, name
            assert bool(metadata["graphics"]["lightning_line"]) is storm_expected, name

            with Image.open(output) as image:
                assert image.size == (1080, 1080)
                embedded_weather = json.loads(image.info["weather_flags"])
                embedded_graphics = json.loads(image.info["graphics"])
                assert embedded_weather["explicit_storm"] is storm_expected, name
                assert embedded_weather["rain"] is rain_expected, name
                assert image.info["actual_precipitation"] == str(expected["actual_precipitation"]).lower()
                assert image.info["rain_graphics"] == str(rain_expected).lower()
                assert image.info["lightning_graphics"] == str(storm_expected).lower()
                crop = image.crop((0, 590, 1080, 850))
                pixel_source = getattr(crop, "get_flattened_data", crop.getdata)
                pixels = list(pixel_source())
                rain_pixels = pixels.count(tuple(embedded_graphics["rain_color"]))
                lightning_pixels = pixels.count(tuple(embedded_graphics["lightning_color"]))
                assert (rain_pixels > 0) is rain_expected, (name, rain_pixels)
                assert (lightning_pixels > 0) is storm_expected, (name, lightning_pixels)

            if name == "dry_storm":
                assert weather["strong_wind"] is True
                assert metadata["facts"][0] == "ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ"
            if name == "dry_severe_wind":
                assert weather["strong_wind"] is True
                assert metadata["graphics"]["wind_arcs"]
            if name == "rain_without_storm":
                assert metadata["facts"][0] == "ДОЖДЬ МЕСТАМИ"


def drizzle_rain_and_snow_icons_keep_factual_intensity() -> None:
    base = """<b>🌅 Калининградская область завтра (22.07.2026)</b>
🏙 Калининград — 19/13 °C • ☁️ облачно • 💨 4 м/с
#Калининград #погода
"""
    scenarios = {
        "production_drizzle_icon": (
            base.replace("☁️ облачно", "🌦 морось"),
            {"actual_precipitation": True, "rain": False, "drizzle": True, "snow": False},
        ),
        "drizzle_word_only": (
            base.replace("☁️ облачно", "морось"),
            {"actual_precipitation": True, "rain": False, "drizzle": True, "snow": False},
        ),
        "rain_icon_and_word": (
            base.replace("☁️ облачно", "🌧 дождь"),
            {"actual_precipitation": True, "rain": True, "drizzle": False, "snow": False},
        ),
        "showers_icon_and_rain_word": (
            base.replace("☁️ облачно", "🌦 дождь"),
            {"actual_precipitation": True, "rain": True, "drizzle": False, "snow": False},
        ),
        "showers_icon_without_word": (
            base.replace("☁️ облачно", "🌦"),
            {"actual_precipitation": True, "rain": True, "drizzle": False, "snow": False},
        ),
        "uncertain_drizzle": (
            base + "Морось возможна.\n",
            {"actual_precipitation": False, "rain": False, "drizzle": False, "snow": False},
        ),
        "negated_drizzle": (
            base + "Морось не ожидается.\n",
            {"actual_precipitation": False, "rain": False, "drizzle": False, "snow": False},
        ),
        "editorial_drizzle": (
            base + "⚠️ Главный нюанс: морось проверить утром.\n",
            {"actual_precipitation": False, "rain": False, "drizzle": False, "snow": False},
        ),
        "confirmed_snow": (
            base.replace("☁️ облачно", "❄ снег"),
            {"actual_precipitation": True, "rain": False, "drizzle": False, "snow": True},
        ),
        "uncertain_snow": (
            base + "Снег возможен.\n",
            {"actual_precipitation": False, "rain": False, "drizzle": False, "snow": False},
        ),
    }

    from PIL import Image

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, (message, expected) in scenarios.items():
            output = root / f"{name}.png"
            metadata = render_kld_informative_cover(message, post_type="evening", output_path=output)
            weather = metadata["weather"]
            for flag, value in expected.items():
                assert weather[flag] is value, (name, flag, weather)

            rain_expected = expected["rain"]
            drizzle_expected = expected["drizzle"] and not rain_expected
            snow_expected = expected["snow"]
            assert metadata["rain_graphics"] is rain_expected, name
            assert metadata["drizzle_graphics"] is drizzle_expected, name
            assert metadata["snow_graphics"] is snow_expected, name

            with Image.open(output) as image:
                embedded_weather = json.loads(image.info["weather_flags"])
                embedded_graphics = json.loads(image.info["graphics"])
                assert embedded_weather["rain"] is rain_expected, name
                assert embedded_weather["drizzle"] is expected["drizzle"], name
                assert embedded_weather["snow"] is snow_expected, name
                assert image.info["rain_graphics"] == str(rain_expected).lower(), name
                assert image.info["drizzle_graphics"] == str(drizzle_expected).lower(), name
                assert image.info["snow_graphics"] == str(snow_expected).lower(), name

                crop = image.crop((0, 590, 1080, 850))
                pixel_source = getattr(crop, "get_flattened_data", crop.getdata)
                pixels = list(pixel_source())
                rain_pixels = pixels.count(tuple(embedded_graphics["rain_color"]))
                drizzle_pixels = pixels.count(tuple(embedded_graphics["drizzle_color"]))
                snow_pixels = pixels.count(tuple(embedded_graphics["snow_color"]))
                assert (rain_pixels > 0) is rain_expected, (name, rain_pixels)
                assert (drizzle_pixels > 0) is drizzle_expected, (name, drizzle_pixels)
                assert (snow_pixels > 0) is snow_expected, (name, snow_pixels)

            if name == "production_drizzle_icon":
                assert metadata["facts"][0] == "МОРОСЬ МЕСТАМИ"
                assert metadata["graphics"]["drizzle_lines"]
                assert not metadata["graphics"]["rain_lines"]
            if name == "confirmed_snow":
                assert metadata["facts"][0] == "СНЕГ МЕСТАМИ"


TESTS = [
    pollinations_failure_uses_cover_and_text_once,
    exact_duplicates_are_nonfatal_and_text_once,
    send_photo_failure_does_not_record_history_and_text_once,
    cover_failure_keeps_text_and_no_stale_image,
    successful_ai_image_is_before_text_and_records_history,
    preview_failure_sends_nothing_and_fails,
    text_send_failure_remains_fatal,
    morning_uses_same_nonblocking_image_behavior,
    visibility_sidecar_actuals_and_safe_fallback,
    local_cover_is_png_1080_and_weather_factual,
    storm_and_precipitation_truth_are_independent,
    drizzle_rain_and_snow_icons_keep_factual_intensity,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD image-first offline checks passed")


if __name__ == "__main__":
    main()
