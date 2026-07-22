#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production-like offline checks for nonblocking KLD image-first publishing."""

from __future__ import annotations

import argparse
import json
import os
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
    _factual_weather_truth,
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
            {"explicit_storm": True, "actual_precipitation": False, "rain": False,
             "thunderstorm": False, "storm_gust": False, "storm_badge": True, "severe_weather": True},
        ),
        "negated_storm": (
            base + "Штормовых предупреждений нет; преимущественно сухо.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False,
             "thunderstorm": False, "storm_gust": False, "storm_badge": False, "severe_weather": False},
        ),
        "negated_thunderstorm": (
            base + "Грозы не ожидаются; дождя не ожидается.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False,
             "thunderstorm": False, "storm_gust": False, "storm_badge": False, "severe_weather": False},
        ),
        "thunderstorm_without_rain": (
            # ⛈/гроза is NOT "шторм": explicit_storm and storm_badge stay False,
            # thunderstorm is True, and the derived severe_weather umbrella is True.
            base + "⛈ Гроза, без осадков.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False,
             "thunderstorm": True, "storm_gust": False, "storm_badge": False, "severe_weather": True},
        ),
        "rain_without_storm": (
            base.replace("☁️ облачно", "🌧 дождь"),
            {"explicit_storm": False, "actual_precipitation": True, "rain": True,
             "thunderstorm": False, "storm_gust": False, "storm_badge": False, "severe_weather": False},
        ),
        "drizzle_without_rain": (
            base.replace("☁️ облачно", "морось"),
            {
                "explicit_storm": False,
                "actual_precipitation": True,
                "rain": False,
                "drizzle": True,
                "thunderstorm": False,
                "storm_gust": False,
                "storm_badge": False,
                "severe_weather": False,
            },
        ),
        "storm_and_rain": (
            base + "Штормовое предупреждение: сильный ветер.\n🌧 Дождь подтверждён.\n",
            {"explicit_storm": True, "actual_precipitation": True, "rain": True,
             "thunderstorm": False, "storm_gust": False, "storm_badge": True, "severe_weather": True},
        ),
        "editorial_storm": (
            base
            + "✨ VayboMeter завтра: шторм и дождь требуют внимания.\n"
            + "⚠️ Главный нюанс: шторм у воды.\n"
            + "✅ План: дождь проверить утром.\n"
            + "🎯 Уверенность: гроза возможна.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False,
             "thunderstorm": False, "storm_gust": False, "storm_badge": False, "severe_weather": False},
        ),
        "uncertain_rain": (
            base + "Вероятность дождя проверить утром.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False,
             "thunderstorm": False, "storm_gust": False, "storm_badge": False, "severe_weather": False},
        ),
        "dry_severe_wind": (
            # 17.5 м/с gusts are at/above the storm threshold, so this is a
            # gust-driven storm even without the word "шторм": storm_gust and
            # storm_badge and severe_weather are True, but explicit_storm and
            # thunderstorm stay False (no lightning).
            base.replace("💨 5 м/с", "💨 9 м/с • порывы до 17.5 м/с") + "Преимущественно сухо.\n",
            {"explicit_storm": False, "actual_precipitation": False, "rain": False,
             "thunderstorm": False, "storm_gust": True, "storm_badge": True, "severe_weather": True},
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
                    "storm_gust",
                    "storm_badge",
                    "severe_weather",
                    "strong_wind",
                )
            )
            for flag, value in expected.items():
                assert weather[flag] is value, (name, flag, weather)

            rain_expected = expected["rain"]
            storm_expected = expected["explicit_storm"]
            # Lightning graphics follow the thunderstorm flag, not explicit_storm:
            # a storm-only day draws no lightning; a thunderstorm-only day does.
            thunderstorm_expected = expected["thunderstorm"]
            assert metadata["rain_graphics"] is rain_expected, name
            assert metadata["lightning_graphics"] is thunderstorm_expected, name
            assert bool(metadata["graphics"]["rain_lines"]) is rain_expected, name
            assert bool(metadata["graphics"]["lightning_line"]) is thunderstorm_expected, name

            with Image.open(output) as image:
                assert image.size == (1080, 1080)
                embedded_weather = json.loads(image.info["weather_flags"])
                embedded_graphics = json.loads(image.info["graphics"])
                assert embedded_weather["explicit_storm"] is storm_expected, name
                assert embedded_weather["thunderstorm"] is thunderstorm_expected, name
                assert embedded_weather["storm_gust"] is expected["storm_gust"], name
                assert embedded_weather["storm_badge"] is expected["storm_badge"], name
                assert embedded_weather["severe_weather"] is expected["severe_weather"], name
                assert embedded_weather["rain"] is rain_expected, name
                assert image.info["explicit_storm"] == str(storm_expected).lower()
                assert image.info["thunderstorm"] == str(thunderstorm_expected).lower()
                assert image.info["storm_gust"] == str(expected["storm_gust"]).lower()
                assert image.info["storm_badge"] == str(expected["storm_badge"]).lower()
                assert image.info["severe_weather"] == str(expected["severe_weather"]).lower()
                assert image.info["actual_precipitation"] == str(expected["actual_precipitation"]).lower()
                assert image.info["rain_graphics"] == str(rain_expected).lower()
                assert image.info["lightning_graphics"] == str(thunderstorm_expected).lower()
                crop = image.crop((0, 590, 1080, 850))
                pixel_source = getattr(crop, "get_flattened_data", crop.getdata)
                pixels = list(pixel_source())
                rain_pixels = pixels.count(tuple(embedded_graphics["rain_color"]))
                lightning_pixels = pixels.count(tuple(embedded_graphics["lightning_color"]))
                assert (rain_pixels > 0) is rain_expected, (name, rain_pixels)
                assert (lightning_pixels > 0) is thunderstorm_expected, (name, lightning_pixels)

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
        "negated_snow_will_not_be": (
            # Regression: "снега не будет" was not covered by the old negation
            # list (only "снег не ожидается" / "без снега" / etc.), so a July
            # rain-only day picked up a phantom "снег" fact from this phrasing.
            base + "Снега не будет.\n",
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
            if snow_expected:
                display_expected = "snow"
            elif rain_expected:
                display_expected = "rain"
            elif drizzle_expected:
                display_expected = "drizzle"
            else:
                display_expected = "none"
            assert weather["precipitation_display"] == display_expected, name
            assert metadata["precipitation_display"] == display_expected, name
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
                assert image.info["precipitation_display"] == display_expected, name
                assert embedded_graphics["precipitation_display"] == display_expected, name

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


def july_rain_day_with_hedged_snow_mention_has_no_snow_fact() -> None:
    # Regression for the reported 21.07 (+17/+13 °C) cover: the text post said
    # only "🌧 дождь" for the day, but an editorial line hedging that snow was
    # not expected ("снега точно не будет") slipped past the old negation list
    # and got rendered as "СНЕГ И ДОЖДЬ МЕСТАМИ" on the cover.
    message = """<b>🌅 Калининградская область завтра (21.07.2026)</b>
✨ VayboMeter завтра: 7.4/10 — тёплый летний день; днём дождь местами.
🏙 Калининград — 17/13 °C • 🌧 дождь • 💨 5 м/с
⚠️ Нюанс: несмотря на похолодание к ночи, снега точно не будет.
#Калининград #погода
"""
    metadata = extract_kld_cover_facts(message, post_type="evening")
    weather = metadata["weather"]
    assert weather["snow"] is False, weather
    assert weather["rain"] is True, weather
    assert weather["precipitation_display"] == "rain", weather
    assert metadata["facts"][0] == "ДОЖДЬ МЕСТАМИ", metadata["facts"]
    assert not any("СНЕГ" in fact for fact in metadata["facts"]), metadata["facts"]


def precipitation_negation_handles_modifiers_between_term_and_negation() -> None:
    # Regression: the negation regex required the negation suffix to sit
    # immediately after the term (single whitespace, no filler words), so
    # hedged phrasings like "снега точно не будет" or "снега, скорее всего,
    # не будет" slipped through as positive mentions. These lines are plain
    # factual sentences (not "Нюанс:"-prefixed), so they exercise the negation
    # regex itself rather than the editorial-line skip.
    cases = {
        "snow_certainly_not": ("Снега точно не будет.", {"snow": False, "actual_precipitation": False}),
        "snow_probably_not": (
            "Снега, скорее всего, не будет.",
            {"snow": False, "actual_precipitation": False},
        ),
        "snow_probability_low": (
            "Вероятность снега невысока.",
            {"snow": False, "actual_precipitation": False},
        ),
        "rain_risk_minimal": ("Риск дождя минимален.", {"rain": False, "actual_precipitation": False}),
        "snow_confirmed_evening": ("Снег будет вечером.", {"snow": True, "actual_precipitation": True}),
    }
    for name, (message, expected) in cases.items():
        facts = _factual_weather_truth(message)
        for flag, value in expected.items():
            assert facts[flag] is value, (name, flag, facts)

    # A negation in one clause must not cancel a genuine confirmation in a
    # different clause on the same line.
    two_clause = _factual_weather_truth("Снега не будет утром. Вечером ожидается снег.")
    assert two_clause["snow"] is True, two_clause
    assert two_clause["actual_precipitation"] is True, two_clause


def mixed_precipitation_statements_keep_types_independent() -> None:
    # Regression: _factual_weather_truth used a single global
    # "precipitation_negated or precipitation_uncertain -> drop the whole
    # clause" gate, so "Дождь будет, снега не будет." lost the real rain
    # along with the negated snow. Each type (rain, drizzle, snow, generic
    # precipitation) must now be evaluated independently: a negation of one
    # type must not cancel evidence of a different type in the same clause.
    cases = {
        "rain_confirmed_snow_negated": (
            "Дождь будет, снега не будет.",
            {"rain": True, "snow": False, "actual_precipitation": True},
        ),
        "snow_confirmed_rain_negated": (
            "Снег будет, дождя не ожидается.",
            {"snow": True, "rain": False, "actual_precipitation": True},
        ),
        "drizzle_confirmed_rain_negated": (
            "Морось будет, дождя не будет.",
            {"drizzle": True, "rain": False, "actual_precipitation": True},
        ),
        "rain_negated_drizzle_uncertain": (
            "Дождя не будет, возможна морось.",
            {"rain": False, "drizzle": False, "actual_precipitation": False},
        ),
        "rain_negated_drizzle_confirmed": (
            "Дождя не будет, морось ожидается.",
            {"rain": False, "drizzle": True, "actual_precipitation": True},
        ),
        "snow_negated_morning_confirmed_evening": (
            "Снега не будет утром; вечером ожидается снег.",
            {"snow": True, "actual_precipitation": True},
        ),
    }
    for name, (message, expected) in cases.items():
        facts = _factual_weather_truth(message)
        for flag, value in expected.items():
            assert facts[flag] is value, (name, flag, facts)


def precipitation_uncertainty_binds_to_nearest_type() -> None:
    # Regression: an uncertainty cue preceding a type ("возможна морось",
    # "Дождь возможен") used to reach across a comma to a *following* type,
    # so "Дождь возможен, снег ожидается." wrongly marked snow uncertain too.
    # The cue must bind to the nearest type only.
    cases = {
        "rain_uncertain_snow_confirmed": (
            "Дождь возможен, снег ожидается.",
            {"rain": False, "snow": True, "actual_precipitation": True},
        ),
        "snow_uncertain_rain_confirmed": (
            "Снег возможен, дождь ожидается.",
            {"snow": False, "rain": True, "actual_precipitation": True},
        ),
        "drizzle_uncertain_rain_confirmed": (
            "Морось возможна, дождь ожидается.",
            {"drizzle": False, "rain": True, "actual_precipitation": True},
        ),
        "rain_uncertain_drizzle_confirmed": (
            "Дождь возможен, морось ожидается.",
            {"rain": False, "drizzle": True, "actual_precipitation": True},
        ),
    }
    for name, (message, expected) in cases.items():
        facts = _factual_weather_truth(message)
        for flag, value in expected.items():
            assert facts[flag] is value, (name, flag, facts)


def precipitation_active_exclusion_verb_is_not_negation() -> None:
    # Regression: a bare "исключ\w*" treated "Снег исключил движение." (snow
    # is the actor) as a negation. Only the passive participle "исключён/
    # исключена" (the fact was removed from the forecast) denies precipitation.
    cases = {
        "rain_actor_excluded_walk": ("Дождь исключил прогулку.", {"rain": True, "actual_precipitation": True}),
        "snow_actor_excluded_traffic": ("Снег исключил движение.", {"snow": True, "actual_precipitation": True}),
        "snow_fact_excluded_from_forecast": (
            "Снег исключён из прогноза.",
            {"snow": False, "actual_precipitation": False},
        ),
    }
    for name, (message, expected) in cases.items():
        facts = _factual_weather_truth(message)
        for flag, value in expected.items():
            assert facts[flag] is value, (name, flag, facts)


def storm_and_thunderstorm_are_independent_per_clause() -> None:
    # explicit_storm means a confirmed "шторм" word ONLY; thunderstorm means a
    # confirmed "гроза"/⛈ ONLY. Neither flag raises the other — a confirmed
    # thunderstorm no longer sets explicit_storm, and a confirmed storm no
    # longer sets thunderstorm. The umbrella "either severe phenomenon" case
    # is the separate derived severe_weather flag.
    cases = {
        # 1
        "storm_negated_thunderstorm_confirmed": (
            "Шторма не будет, гроза ожидается.",
            {"explicit_storm": False, "thunderstorm": True, "severe_weather": True},
        ),
        # 2
        "thunderstorm_negated_storm_confirmed": (
            "Грозы не будет, шторм ожидается.",
            {"explicit_storm": True, "thunderstorm": False, "severe_weather": True},
        ),
        # 3
        "storm_uncertain_thunderstorm_confirmed": (
            "Шторм возможен, гроза ожидается.",
            {"explicit_storm": False, "thunderstorm": True, "severe_weather": True},
        ),
        # 4
        "storm_confirmed_thunderstorm_uncertain": (
            "Шторм ожидается, гроза возможна.",
            {"explicit_storm": True, "thunderstorm": False, "severe_weather": True},
        ),
        # 5
        "storm_and_thunderstorm_both_confirmed": (
            "Шторм и гроза ожидаются.",
            {"explicit_storm": True, "thunderstorm": True, "severe_weather": True},
        ),
    }
    for name, (message, expected) in cases.items():
        facts = _factual_weather_truth(message)
        for flag, value in expected.items():
            assert facts[flag] is value, (name, flag, facts)


def storm_and_thunderstorm_flags_drive_graphics_independently() -> None:
    # End-to-end: metadata stores explicit_storm and thunderstorm as separate
    # booleans, and neither raises the other's graphic — a storm-only day draws
    # no lightning, a thunderstorm-only day does.
    from PIL import Image

    base = """<b>🌅 Калининградская область завтра (21.07.2026)</b>
🏙 Калининград — 20/14 °C • ☁️ облачно • 💨 5 м/с
#Калининград #погода
"""
    scenarios = {
        "storm_only": (
            base + "Штормовое предупреждение: штормовой ветер, без осадков.\n",
            {"explicit_storm": True, "thunderstorm": False, "severe_weather": True},
        ),
        "thunderstorm_only": (
            base + "⛈ Гроза, без осадков.\n",
            {"explicit_storm": False, "thunderstorm": True, "severe_weather": True},
        ),
    }
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, (message, expected) in scenarios.items():
            output = root / f"{name}.png"
            metadata = render_kld_informative_cover(message, post_type="evening", output_path=output)
            weather = metadata["weather"]
            assert weather["explicit_storm"] is expected["explicit_storm"], name
            assert weather["thunderstorm"] is expected["thunderstorm"], name
            assert weather["severe_weather"] is expected["severe_weather"], name
            # Lightning is present iff thunderstorm, regardless of explicit_storm.
            assert metadata["lightning_graphics"] is expected["thunderstorm"], name
            with Image.open(output) as image:
                embedded = json.loads(image.info["weather_flags"])
                assert embedded["explicit_storm"] is expected["explicit_storm"], name
                assert embedded["thunderstorm"] is expected["thunderstorm"], name
                assert embedded["severe_weather"] is expected["severe_weather"], name
                lightning_pixels = image.crop((0, 590, 1080, 850)).getdata()
                lc = tuple(json.loads(image.info["graphics"])["lightning_color"])
                assert (list(lightning_pixels).count(lc) > 0) is expected["thunderstorm"], name


def storm_badge_uses_word_or_gust_threshold_not_strong_wind() -> None:
    # The "ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ" cover badge must fire on a confirmed storm
    # word OR a gust at/above STORM_GUST_MS, matching format_v2 /
    # safe_test_post / post_kld. It must NOT rely on strong_wind, which starts
    # at 12 м/с — below the storm threshold. thunderstorm drives lightning
    # only, never the storm badge.
    import importlib

    import weather_text
    import kld_informative_cover

    def _wind_message(gust_ms: float) -> str:
        return (
            "<b>🌅 Калининградская область завтра (21.07.2026)</b>\n"
            f"🏙 Калининград — 20/14 °C • ☁️ облачно • 💨 8 м/с • порывы до {gust_ms} м/с\n"
            "#Калининград #погода\n"
        )

    def _facts(message: str) -> dict:
        return kld_informative_cover.extract_kld_cover_facts(message, post_type="evening")

    def _has_storm_badge(message: str) -> bool:
        return "ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ" in _facts(message)["facts"]

    def _lightning(message: str) -> bool:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            meta = render_kld_informative_cover(
                message, post_type="evening", output_path=Path(tmp) / "c.png"
            )
            return bool(meta["lightning_graphics"])

    # A. 14 м/с, no storm word: not a storm at all, but strong_wind may be True.
    a = _facts(_wind_message(14))["weather"]
    assert a["explicit_storm"] is False
    assert a["thunderstorm"] is False
    assert a["storm_gust"] is False
    assert a["storm_badge"] is False
    assert a["severe_weather"] is False
    assert a["strong_wind"] is True  # 14 >= 12, but below the storm threshold
    assert _has_storm_badge(_wind_message(14)) is False
    assert _lightning(_wind_message(14)) is False

    # B. 15 м/с at the default threshold of 15: gust-driven storm, no lightning.
    b = _facts(_wind_message(15))["weather"]
    assert b["explicit_storm"] is False
    assert b["thunderstorm"] is False
    assert b["storm_gust"] is True
    assert b["storm_badge"] is True
    assert b["severe_weather"] is True
    assert _has_storm_badge(_wind_message(15)) is True
    assert _lightning(_wind_message(15)) is False

    # C/D. Raise the threshold to 16 and reload the shared module: 15 м/с is no
    # longer a storm, 16 м/с is. The cover reads the threshold live from
    # weather_text, so no reload of kld_informative_cover is required.
    old_value = os.environ.get("STORM_GUST_MS")
    try:
        os.environ["STORM_GUST_MS"] = "16"
        importlib.reload(weather_text)
        assert weather_text.STORM_GUST_MS == 16.0

        c = _facts(_wind_message(15))["weather"]
        assert c["storm_gust"] is False
        assert c["storm_badge"] is False
        assert _has_storm_badge(_wind_message(15)) is False

        d = _facts(_wind_message(16))["weather"]
        assert d["storm_gust"] is True
        assert d["storm_badge"] is True
        assert _has_storm_badge(_wind_message(16)) is True
    finally:
        if old_value is None:
            os.environ.pop("STORM_GUST_MS", None)
        else:
            os.environ["STORM_GUST_MS"] = old_value
        importlib.reload(weather_text)
        assert weather_text.STORM_GUST_MS == (float(old_value) if old_value is not None else 15.0)

    # E. Thunderstorm, weak wind: thunderstorm + severe_weather + lightning, but
    # no storm word and no storm badge.
    thunder_msg = (
        "<b>🌅 Калининградская область завтра (21.07.2026)</b>\n"
        "🏙 Калининград — 20/14 °C • 💨 4 м/с\n"
        "Гроза ожидается.\n"
        "#Калининград #погода\n"
    )
    e = _facts(thunder_msg)["weather"]
    assert e["explicit_storm"] is False
    assert e["thunderstorm"] is True
    assert e["storm_gust"] is False
    assert e["storm_badge"] is False
    assert e["severe_weather"] is True
    assert _has_storm_badge(thunder_msg) is False
    assert _lightning(thunder_msg) is True

    # F. Storm word, weak wind, no thunderstorm: storm badge, no lightning.
    storm_msg = (
        "<b>🌅 Калининградская область завтра (21.07.2026)</b>\n"
        "🏙 Калининград — 20/14 °C • 💨 4 м/с\n"
        "Шторм ожидается.\n"
        "#Калининград #погода\n"
    )
    f = _facts(storm_msg)["weather"]
    assert f["explicit_storm"] is True
    assert f["thunderstorm"] is False
    assert f["storm_gust"] is False
    assert f["storm_badge"] is True
    assert f["severe_weather"] is True
    assert _has_storm_badge(storm_msg) is True
    assert _lightning(storm_msg) is False


def mixed_regional_precipitation_keeps_text_and_graphics_aligned() -> None:
    base = """<b>🌅 Калининградская область завтра (23.07.2026)</b>
🏙 Калининград — 18/12 °C • ☁️ облачно • 💨 5 м/с
"""
    scenarios = {
        "mixed_regional_rain_drizzle": (
            base
            + "Светлогорск — 16/12 °C • 🌦 морось\n"
            + "Пионерский — 16/12 °C • 🌦 морось\n"
            + "Мамоново — 19/13 °C • 🌧 дождь\n",
            {"rain": True, "drizzle": True, "snow": False},
            "rain_and_drizzle",
            "ДОЖДЬ И МОРОСЬ МЕСТАМИ",
            (True, False, False),
        ),
        "drizzle_only": (
            base + "Светлогорск — 16/12 °C • 🌦 морось\n",
            {"rain": False, "drizzle": True, "snow": False},
            "drizzle",
            "МОРОСЬ МЕСТАМИ",
            (False, True, False),
        ),
        "rain_only": (
            base + "Мамоново — 19/13 °C • 🌧 дождь\n",
            {"rain": True, "drizzle": False, "snow": False},
            "rain",
            "ДОЖДЬ МЕСТАМИ",
            (True, False, False),
        ),
        "snow_and_rain": (
            base + "Черняховск — 2/-1 °C • ❄ снег\nМамоново — 3/0 °C • 🌧 дождь\n",
            {"rain": True, "drizzle": False, "snow": True},
            "mixed_snow_rain",
            "СНЕГ И ДОЖДЬ МЕСТАМИ",
            (True, False, True),
        ),
        "snow_and_drizzle": (
            base + "Черняховск — 2/-1 °C • ❄ снег\nСветлогорск — 3/0 °C • 🌦 морось\n",
            {"rain": False, "drizzle": True, "snow": True},
            "snow_and_drizzle",
            "СНЕГ И МОРОСЬ МЕСТАМИ",
            (False, True, True),
        ),
        "snow_only": (
            base + "Черняховск — 2/-1 °C • ❄ снег\n",
            {"rain": False, "drizzle": False, "snow": True},
            "snow",
            "СНЕГ МЕСТАМИ",
            (False, False, True),
        ),
    }

    from PIL import Image

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, (message, expected_flags, display, fact, expected_graphics) in scenarios.items():
            output = root / f"{name}.png"
            metadata = render_kld_informative_cover(message, post_type="evening", output_path=output)
            weather = metadata["weather"]
            assert weather["actual_precipitation"] is True, name
            for flag, value in expected_flags.items():
                assert weather[flag] is value, (name, flag, weather)
            assert weather["precipitation_display"] == display, name
            assert metadata["precipitation_display"] == display, name
            assert metadata["facts"][0] == fact, (name, metadata["facts"])

            rain_graphics, drizzle_graphics, snow_graphics = expected_graphics
            assert metadata["rain_graphics"] is rain_graphics, name
            assert metadata["drizzle_graphics"] is drizzle_graphics, name
            assert metadata["snow_graphics"] is snow_graphics, name

            with Image.open(output) as image:
                embedded_weather = json.loads(image.info["weather_flags"])
                embedded_graphics = json.loads(image.info["graphics"])
                assert image.info["precipitation_display"] == display, name
                assert embedded_weather["precipitation_display"] == display, name
                assert embedded_graphics["precipitation_display"] == display, name
                crop = image.crop((0, 590, 1080, 850))
                pixel_source = getattr(crop, "get_flattened_data", crop.getdata)
                pixels = list(pixel_source())
                rain_pixels = pixels.count(tuple(embedded_graphics["rain_color"]))
                drizzle_pixels = pixels.count(tuple(embedded_graphics["drizzle_color"]))
                snow_pixels = pixels.count(tuple(embedded_graphics["snow_color"]))
                assert (rain_pixels > 0) is rain_graphics, (name, rain_pixels)
                assert (drizzle_pixels > 0) is drizzle_graphics, (name, drizzle_pixels)
                assert (snow_pixels > 0) is snow_graphics, (name, snow_pixels)


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
    july_rain_day_with_hedged_snow_mention_has_no_snow_fact,
    precipitation_negation_handles_modifiers_between_term_and_negation,
    mixed_precipitation_statements_keep_types_independent,
    precipitation_uncertainty_binds_to_nearest_type,
    precipitation_active_exclusion_verb_is_not_negation,
    storm_and_thunderstorm_are_independent_per_clause,
    storm_and_thunderstorm_flags_drive_graphics_independently,
    storm_badge_uses_word_or_gust_threshold_not_strong_wind,
    mixed_regional_precipitation_keeps_text_and_graphics_aligned,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD image-first offline checks passed")


if __name__ == "__main__":
    main()
