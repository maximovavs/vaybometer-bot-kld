#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production-like offline checks for nonblocking KLD image-first publishing."""

from __future__ import annotations

import argparse
import base64
import io
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

from kld_image_first import (  # noqa: E402
    FORMAT_V2_BEGIN,
    FORMAT_V2_END,
    is_valid_kld_delivery_receipt,
    kld_delivery_path,
    load_kld_delivery_receipt,
    run_image_first_publication,
)
import imagegen  # noqa: E402
from kld_informative_cover import (  # noqa: E402
    RENDERER_VERSION,
    _factual_weather_truth,
    extract_kld_cover_facts,
    render_kld_informative_cover,
    validate_kld_cover_semantics,
)
from kld_visual_dedup import KldVisualDuplicateResult  # noqa: E402
from curated_fallback_kld import (  # noqa: E402
    render_curated_cover,
    select_asset as select_curated_asset,
)
from tools.kld_visual_fixture_image import (  # noqa: E402
    _load_visibility_context_file,
    _send_and_record,
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


def _duplicate(
    *,
    accepted: bool,
    reason: str,
    distance: int | None = 12,
    matched_target_date: str = "",
) -> KldVisualDuplicateResult:
    return KldVisualDuplicateResult(
        accepted=accepted,
        reason=reason,
        sha256="a" * 64,
        perceptual_hash="0" * 16,
        min_distance=distance,
        matched_entry=(
            {"target_date": matched_target_date}
            if matched_target_date
            else None
        ),
    )


def _cover_renderer(path_events: list[str], *, fail: bool = False):
    def render(message: str, *, post_type: str, visibility_context, output_path: str):
        path_events.append("cover")
        if fail:
            raise RuntimeError("cover renderer failed")
        _image(Path(output_path), (155, 165, 170))
        return {"renderer_version": RENDERER_VERSION, "facts": ["ВИДИМОСТЬ УТРОМ СНИЖЕНА"]}

    return render


def _bounded_cover_renderer(
    path_events: list[str],
    candidates: tuple[str, ...] = ("kld_overcast_01", "kld_overcast_02"),
):
    def render(
        message: str,
        *,
        post_type: str,
        visibility_context,
        output_path: str,
        curated_asset_id: str | None = None,
    ):
        asset_id = str(curated_asset_id or candidates[0])
        if asset_id not in candidates:
            raise RuntimeError(f"fixture asset not eligible: {asset_id}")
        path_events.append(f"cover:{asset_id}")
        color = (155 + candidates.index(asset_id) * 10, 165, 170)
        _image(Path(output_path), color)
        return {
            "renderer_version": RENDERER_VERSION,
            "facts": ["ВИДИМОСТЬ УТРОМ СНИЖЕНА"],
            "date": "20.07.2026",
            "curated_asset_id": asset_id,
            "curated_pool": list(candidates),
            "curated_candidates": list(candidates),
        }

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
    secondary_generate=None,
    validate_cover=None,
    provider_diagnostics=None,
    presentation_renderer=None,
):
    args = _args(root, post_type=post_type)
    visibility = {
        "visibility_condition": "reduced_visibility",
        "morning_min_visibility_m": 5500,
        "reported_visibility_threshold_m": 6000,
    }
    payload = build_payload(MESSAGE, "test", post_type=post_type, visibility_context=visibility)
    delivery_kwargs = {}
    if presentation_renderer is not None:
        delivery_kwargs["presentation_renderer"] = presentation_renderer
    return execute_image_delivery(
        args=args,
        message=MESSAGE,
        initial_payload=payload,
        visibility_context=visibility,
        history_path=root / "history.json",
        generate_image=generate,
        secondary_generate_image=secondary_generate,
        provider_diagnostics=provider_diagnostics,
        evaluate_candidate=evaluate,
        cover_renderer=cover_renderer,
        validate_cover=validate_cover or (lambda *args, **kwargs: {"valid": True, "errors": []}),
        send_photo=send_photo,
        record_publication=record,
        **delivery_kwargs,
    )


def _orchestrate(
    root: Path,
    image_outcome: dict[str, object],
    *,
    mode: str = "evening",
    preview_returncode: int = 0,
    text_error: Exception | None = None,
    production: bool = False,
    production_chat_id: str = "-1001234567890",
    target_date: str = "2026-07-20",
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
        production=production,
        production_chat_id=production_chat_id if production else "",
        target_date=target_date if production else "",
        delivery_dir=root / "kld_delivery",
    )
    return outcome, events



def _write_delivery_fixture(
    root: Path,
    *,
    target_date: str = "2026-07-20",
    post_type: str = "evening",
    chat_id: str = "-1001234567890",
    image_id: int | None = None,
    text_ids: list[int] | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "target_date": target_date,
        "post_type": post_type,
        "chat_type": "production",
        "chat_id": chat_id,
        "image_delivered": bool(image_id),
        "telegram_image_message_id": image_id,
        "image_sent_at_utc": "2026-07-19T14:00:01Z" if image_id else "",
        "text_delivered": bool(text_ids),
        "telegram_text_message_ids": list(text_ids or []),
        "text_sent_at_utc": "2026-07-19T14:00:02Z" if text_ids else "",
        "run_id": "fixture",
        "run_attempt": "1",
        "updated_at_utc": "2026-07-19T14:00:02Z",
    }
    path = kld_delivery_path(target_date, post_type, delivery_dir=root / "kld_delivery")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def production_delivery_first_repeat_and_partial_states_are_idempotent() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        image_outcome = {
            "result": "sent",
            "backend": "pollinations",
            "telegram_image_sent": True,
            "telegram_image_message_id": 601,
            "history_recorded": True,
        }
        first, first_order = _orchestrate(root, image_outcome, production=True)
        assert first_order == ["preview", "image", "text"]
        receipt = load_kld_delivery_receipt(
            target_date="2026-07-20",
            post_type="evening",
            production_chat_id="-1001234567890",
            delivery_dir=root / "kld_delivery",
        )
        assert is_valid_kld_delivery_receipt(
            receipt,
            target_date="2026-07-20",
            post_type="evening",
            production_chat_id="-1001234567890",
        )
        assert receipt["image_delivered"] is True and receipt["text_delivered"] is True
        second, second_order = _orchestrate(root, image_outcome, production=True)
        assert second["result"] == "skipped_delivery_receipt_complete"
        assert second["image_send_skipped_receipt"] is True
        assert second["text_send_skipped_receipt"] is True
        assert second_order == []

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_delivery_fixture(root, text_ids=[701])
        image_only, order = _orchestrate(
            root,
            {"result": "sent", "telegram_image_sent": True, "telegram_image_message_id": 702},
            production=True,
        )
        assert order == ["preview", "image"]
        assert image_only["text_send_skipped_receipt"] is True

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_delivery_fixture(root, image_id=711)
        text_only, order = _orchestrate(
            root,
            {"result": "sent", "telegram_image_sent": True, "telegram_image_message_id": 999},
            production=True,
        )
        assert order == ["preview", "text"]
        assert text_only["image_send_skipped_receipt"] is True
        receipt = load_kld_delivery_receipt(
            target_date="2026-07-20",
            post_type="evening",
            production_chat_id="-1001234567890",
            delivery_dir=root / "kld_delivery",
        )
        assert receipt["telegram_image_message_id"] == 711
        assert receipt["telegram_text_message_ids"] == [501]


def production_delivery_keys_test_channel_and_trigger_are_isolated() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_delivery_fixture(root, image_id=721, text_ids=[722])

        _different_date, order_date = _orchestrate(
            root,
            {"result": "sent", "telegram_image_sent": True, "telegram_image_message_id": 723},
            production=True,
            target_date="2026-07-21",
        )
        _different_type, order_type = _orchestrate(
            root,
            {"result": "sent", "telegram_image_sent": True, "telegram_image_message_id": 724},
            production=True,
            mode="morning",
        )
        test_run, order_test = _orchestrate(
            root,
            {"result": "sent", "telegram_image_sent": True, "telegram_image_message_id": 725},
            production=False,
        )
        assert order_date == ["preview", "image", "text"]
        assert order_type == ["preview", "image", "text"]
        assert order_test == ["preview", "image", "text"]
        assert test_run["text_sent"] is True

        old_event = os.environ.get("GITHUB_EVENT_NAME")
        try:
            os.environ["GITHUB_EVENT_NAME"] = "schedule"
            scheduled, scheduled_order = _orchestrate(root, {}, production=True)
            os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
            dispatched, dispatched_order = _orchestrate(root, {}, production=True)
        finally:
            if old_event is None:
                os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                os.environ["GITHUB_EVENT_NAME"] = old_event
        assert scheduled["result"] == "skipped_delivery_receipt_complete"
        assert dispatched["result"] == "skipped_delivery_receipt_complete"
        assert scheduled_order == []
        assert dispatched_order == []

def visual_target_date_propagates_through_provider_and_fallback() -> None:
    visibility = {
        "visibility_condition": "reduced_visibility",
        "morning_min_visibility_m": 5500,
        "reported_visibility_threshold_m": 6000,
    }
    payload = build_payload(MESSAGE, "test", post_type="evening", visibility_context=visibility)
    metadata = payload["metadata"]
    assert metadata["forecast_date"] == "2026-07-20"
    assert metadata["target_date"] == "2026-07-20"

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        evaluated: list[tuple[str, str, str]] = []
        recorded: list[tuple[str, str, str]] = []

        def evaluate(path, **kwargs):
            evaluated.append(
                (str(kwargs["date_value"]), str(kwargs["target_date"]), str(kwargs["scene_family"]))
            )
            return _duplicate(accepted=True, reason="accepted", distance=20)

        def record(**kwargs):
            recorded.append(
                (str(kwargs["date_value"]), str(kwargs["target_date"]), str(kwargs["scene_family"]))
            )
            return {"sha256": "b" * 64, "scene_family": kwargs["scene_family"]}

        provider_outcome = _run_delivery(
            root,
            generate=lambda **kwargs: _image(root / "provider.png"),
            evaluate=evaluate,
            cover_renderer=_cover_renderer([]),
            send_photo=lambda *args, **kwargs: 201,
            record=record,
        )
        assert provider_outcome["backend"] == "pollinations"
        assert evaluated == [("2026-07-20", "2026-07-20", provider_outcome["selected_scene_family"])]
        assert recorded == [("2026-07-20", "2026-07-20", provider_outcome["selected_scene_family"])]

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        evaluated = []
        recorded = []
        events: list[str] = []

        def evaluate_fallback(path, **kwargs):
            evaluated.append(
                (str(kwargs["date_value"]), str(kwargs["target_date"]), str(kwargs["scene_family"]))
            )
            return _duplicate(accepted=True, reason="accepted", distance=20)

        def record_fallback(**kwargs):
            recorded.append(
                (str(kwargs["date_value"]), str(kwargs["target_date"]), str(kwargs["scene_family"]))
            )
            return {"sha256": "b" * 64, "scene_family": kwargs["scene_family"]}

        fallback_outcome = _run_delivery(
            root,
            generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
            evaluate=evaluate_fallback,
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: 202,
            record=record_fallback,
        )
        assert fallback_outcome["backend"] == "local_informative_cover"
        assert evaluated == [("2026-07-20", "2026-07-20", "local_informative_cover")]
        assert recorded == [("2026-07-20", "2026-07-20", "local_informative_cover")]


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
            assert image.size == (1080, 1350)
            assert image.info["catalog_version"] == RENDERER_VERSION
            assert image.info["curated_asset_id"] == metadata["curated_asset_id"]
        assert metadata["renderer_version"] == RENDERER_VERSION
        assert metadata["title"] == "КАЛИНИНГРАД ЗАВТРА"
        assert metadata["weather"]["rain"] is True
        assert metadata["curated_scenario"] == "rain_evening"
        assert metadata["curated_asset_id"].startswith("kld_")
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
        "dry_storm": (base + "Штормовое предупреждение: штормовой ветер, без осадков.\n", True, False, False),
        "negated_storm": (base + "Штормовых предупреждений нет; преимущественно сухо.\n", False, False, False),
        "thunderstorm_without_rain": (base + "⛈ Гроза, без осадков.\n", False, False, True),
        "rain_without_storm": (base.replace("☁️ облачно", "🌧 дождь"), False, True, False),
        "storm_and_rain": (base + "Штормовое предупреждение: сильный ветер.\n🌧 Дождь подтверждён.\n", True, True, False),
    }
    with TemporaryDirectory() as tmp:
        for name, (message, storm, rain, thunder) in scenarios.items():
            output = Path(tmp) / f"{name}.png"
            metadata = render_kld_informative_cover(message, post_type="evening", output_path=output)
            weather = metadata["weather"]
            assert weather["explicit_storm"] is storm, (name, weather)
            assert weather["rain"] is rain, (name, weather)
            assert weather["thunderstorm"] is thunder, (name, weather)
            assert metadata["curated_asset_id"].startswith("kld_")
            assert validate_kld_cover_semantics(message, metadata, post_type="evening")["valid"] is True
            if storm or thunder:
                assert metadata["curated_scenario"] == "strong_wind"
            elif rain:
                assert metadata["curated_scenario"] == "rain_evening"


def drizzle_rain_and_snow_icons_keep_factual_intensity() -> None:
    base = """<b>🌅 Калининградская область завтра (22.07.2026)</b>
🏙 Калининград — 19/13 °C • ☁️ облачно • 💨 4 м/с
#Калининград #погода
"""
    scenarios = {
        "drizzle": (base.replace("☁️ облачно", "🌦 морось"), "drizzle", "rain_evening"),
        "rain": (base.replace("☁️ облачно", "🌧 дождь"), "rain", "rain_evening"),
        "snow": (base.replace("☁️ облачно", "❄ снег"), "snow", "snow"),
        "uncertain_snow": (base + "Снег возможен.\n", "none", "overcast"),
        "negated_snow": (base + "Снега не будет.\n", "none", "overcast"),
    }
    with TemporaryDirectory() as tmp:
        for name, (message, display, scenario) in scenarios.items():
            metadata = render_kld_informative_cover(
                message,
                post_type="evening",
                output_path=Path(tmp) / f"{name}.png",
            )
            assert metadata["weather"]["precipitation_display"] == display, (name, metadata["weather"])
            assert metadata["precipitation_display"] == display, name
            assert metadata["curated_scenario"] == scenario, (name, metadata["curated_scenario"])
            assert validate_kld_cover_semantics(message, metadata, post_type="evening")["valid"] is True

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
    base = """<b>🌅 Калининградская область завтра (21.07.2026)</b>
🏙 Калининград — 20/14 °C • ☁️ облачно • 💨 4 м/с
#Калининград #погода
"""
    cases = {
        "storm_only": (base + "Шторм ожидается.\n", True, False),
        "thunder_only": (base + "Гроза ожидается.\n", False, True),
        "both": (base + "Шторм и гроза ожидаются.\n", True, True),
        "neither": (base + "Шторм не ожидается. Гроза не ожидается.\n", False, False),
    }
    with TemporaryDirectory() as tmp:
        for name, (message, storm, thunder) in cases.items():
            metadata = render_kld_informative_cover(
                message,
                post_type="evening",
                output_path=Path(tmp) / f"{name}.png",
            )
            weather = metadata["weather"]
            assert weather["explicit_storm"] is storm, (name, weather)
            assert weather["thunderstorm"] is thunder, (name, weather)
            if storm or thunder:
                assert metadata["curated_scenario"] == "strong_wind"
            assert metadata["lightning_graphics"] is thunder

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
        "rain_drizzle": (
            base + "Светлогорск — 16/12 °C • 🌦 морось\nМамоново — 19/13 °C • 🌧 дождь\n",
            "rain_and_drizzle",
            "ДОЖДЬ И МОРОСЬ МЕСТАМИ",
        ),
        "snow_rain": (
            base + "Черняховск — 2/-1 °C • ❄ снег\nМамоново — 3/0 °C • 🌧 дождь\n",
            "mixed_snow_rain",
            "СНЕГ И ДОЖДЬ МЕСТАМИ",
        ),
        "snow_drizzle": (
            base + "Черняховск — 2/-1 °C • ❄ снег\nСветлогорск — 3/0 °C • 🌦 морось\n",
            "snow_and_drizzle",
            "СНЕГ И МОРОСЬ МЕСТАМИ",
        ),
    }
    with TemporaryDirectory() as tmp:
        for name, (message, display, fact) in scenarios.items():
            metadata = render_kld_informative_cover(
                message,
                post_type="evening",
                output_path=Path(tmp) / f"{name}.png",
            )
            assert metadata["weather"]["precipitation_display"] == display, name
            assert metadata["facts"][0] == fact, (name, metadata["facts"])
            assert metadata["curated_asset_id"].startswith("kld_")
            assert validate_kld_cover_semantics(message, metadata, post_type="evening")["valid"] is True

def production_decorative_snow_headers_are_not_weather_evidence() -> None:
    cases = {
        "21_july_rain": (
            """<b>🌅 Калининградская область завтра (21.07.2026)</b>
🏙 Калининград: 17/13 °C • 🌧 дождь • 💨 3.5 м/с • порывы до 14 м/с
❄️ Самые прохладные ночи
Светлогорск: 16/12 °C • 🌧 дождь
#Калининград #погода
""",
            "rain",
            "ДОЖДЬ МЕСТАМИ",
        ),
        "24_july_drizzle": (
            """<b>🌅 Калининградская область завтра (24.07.2026)</b>
🏙 Калининград: 21/14 °C • 🌦 морось • 💨 2.1 м/с • порывы до 7 м/с
❄️ Самые прохладные ночи
Пионерский: 19/13 °C • 🌦 морось
#Калининград #погода
""",
            "drizzle",
            "МОРОСЬ МЕСТАМИ",
        ),
        "25_july_cloudy": (
            """<b>🌅 Калининградская область завтра (25.07.2026)</b>
🏙 Калининград: 23/15 °C • ☁️ облачно • 💨 3.3 м/с • порывы до 8 м/с
❄️ Самые прохладные ночи
Черняховск: 21/12 °C • ☁️ облачно
#Калининград #погода
""",
            "none",
            "",
        ),
    }
    for name, (message, display, first_fact) in cases.items():
        metadata = extract_kld_cover_facts(message, post_type="evening")
        weather = metadata["weather"]
        assert weather["snow"] is False, (name, weather)
        assert weather["precipitation_display"] == display, (name, weather)
        assert not any("СНЕГ" in fact for fact in metadata["facts"]), (name, metadata["facts"])
        if first_fact:
            assert metadata["facts"][0] == first_fact, (name, metadata["facts"])

    decorative_only = _factual_weather_truth("❄️ Самые прохладные ночи")
    assert decorative_only["snow"] is False
    assert decorative_only["actual_precipitation"] is False
    editorial_only = _factual_weather_truth(
        "💬 Настрой на завтра: лучше оставить расписанию немного места для дождевого окна."
    )
    assert editorial_only["rain"] is False
    assert editorial_only["actual_precipitation"] is False

    positive_structured_icon = _factual_weather_truth(
        "Калининград: 23/15 °C • ❄ • облачно"
    )
    assert positive_structured_icon["snow"] is False
    explicit_word = _factual_weather_truth(
        "Калининград: 1/-2 °C • снег"
    )
    assert explicit_word["snow"] is True


def local_cover_semantic_validation_blocks_tampering() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "cover.png"
        metadata = render_kld_informative_cover(
            MESSAGE,
            post_type="evening",
            visibility_context={
                "visibility_condition": "reduced_visibility",
                "morning_min_visibility_m": 5500,
                "reported_visibility_threshold_m": 6000,
            },
            output_path=path,
        )
        valid = validate_kld_cover_semantics(
            MESSAGE,
            metadata,
            post_type="evening",
            visibility_context={
                "visibility_condition": "reduced_visibility",
                "morning_min_visibility_m": 5500,
                "reported_visibility_threshold_m": 6000,
            },
        )
        assert valid["valid"] is True, valid
        tampered = json.loads(json.dumps(metadata, ensure_ascii=False))
        tampered["facts"][0] = "СНЕГ МЕСТАМИ"
        tampered["weather"]["snow"] = True
        invalid = validate_kld_cover_semantics(
            MESSAGE,
            tampered,
            post_type="evening",
            visibility_context={
                "visibility_condition": "reduced_visibility",
                "morning_min_visibility_m": 5500,
                "reported_visibility_threshold_m": 6000,
            },
        )
        assert invalid["valid"] is False
        assert any("display facts" in error or "snow" in error for error in invalid["errors"])




def local_cover_variants_rotate_across_adjacent_dates() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        assets: set[str] = set()
        explicit_override_checked = False
        for day in range(20, 32):
            date_value = f"{day:02d}.07.2026"
            message = MESSAGE.replace(
                "🌫 Видимость: завтра утром местами снижена; около 5500 м.\n",
                "",
            ).replace("20.07.2026", date_value)
            metadata = render_kld_informative_cover(
                message,
                post_type="evening",
                visibility_context=None,
                output_path=root / f"cover-{day}.png",
            )
            assets.add(str(metadata["curated_asset_id"]))
            assert metadata["curated_candidates"][0] == metadata["curated_asset_id"]
            assert set(metadata["curated_candidates"]) == set(metadata["curated_pool"])
            if not explicit_override_checked and len(metadata["curated_candidates"]) > 1:
                alternate_asset = metadata["curated_candidates"][1]
                alternate = render_kld_informative_cover(
                    message,
                    post_type="evening",
                    visibility_context=None,
                    output_path=root / f"cover-{day}-alternate.png",
                    curated_asset_id=alternate_asset,
                )
                assert alternate["curated_asset_id"] == alternate_asset
                try:
                    render_kld_informative_cover(
                        message,
                        post_type="evening",
                        visibility_context=None,
                        output_path=root / f"cover-{day}-invalid.png",
                        curated_asset_id="kld_not_in_pool",
                    )
                except RuntimeError as exc:
                    assert "not eligible for scenario" in str(exc)
                else:
                    raise AssertionError("non-pool curated asset override must be rejected")
                explicit_override_checked = True
            valid = validate_kld_cover_semantics(
                message,
                metadata,
                post_type="evening",
                visibility_context=None,
            )
            assert valid["valid"] is True, valid
            from PIL import Image
            with Image.open(metadata["path"]) as rendered:
                assert rendered.size == (1080, 1350)
        assert assets <= {"kld_overcast_01", "kld_overcast_02"}
        assert len(assets) == 2
        assert explicit_override_checked is True


def curated_overlay_safe_zones_cover_all_assets() -> None:
    from PIL import Image

    manifest = json.loads(
        (ROOT / "assets" / "fallback" / "kld" / "manifest.json").read_text("utf-8")
    )
    geometry = manifest["overlay_geometry"]
    assert set(geometry) == set(manifest["asset_order"])

    cases = {
        "clear": (9, "morning", 17, 10, "ясно", 3, 5),
        "sunset": (9, "evening", 17, 10, "ясно; закат", 3, 5),
        "overcast": (9, "morning", 14, 9, "пасмурно", 3, 5),
        "rain_day": (9, "morning", 12, 8, "дождь", 4, 7),
        "rain_evening": (9, "evening", 12, 8, "дождь", 4, 7),
        "fog": (9, "morning", 11, 8, "туман утром", 2, 4),
        "snow": (1, "morning", -2, -6, "снег", 3, 5),
        "heavy_snow": (1, "morning", -3, -7, "снег; сильный ветер", 10, 14),
        "frost": (1, "morning", -8, -13, "ясно; мороз", 3, 5),
        "frost_evening": (1, "evening", -8, -13, "ясно; мороз; закат", 3, 5),
        "slush": (1, "morning", 2, 0, "снег и дождь", 4, 7),
        "strong_wind": (4, "morning", 10, 6, "сильный ветер", 10, 14),
        "windy_autumn": (10, "morning", 11, 7, "сильный ветер", 10, 14),
        "winter_overcast": (1, "morning", 4, 2, "пасмурно", 3, 5),
        "night": (9, "evening", 12, 8, "ясно; ночь", 3, 5),
    }
    failing_assets = {
        "kld_sunset_01",
        "kld_rain_day_01",
        "kld_rain_evening_01",
        "kld_snow_02",
        "kld_blizzard_01",
        "kld_slush_01",
        "kld_windy_autumn_01",
        "kld_night_01",
    }

    def contains(outer: list[int], inner: list[int]) -> bool:
        return (
            outer[0] <= inner[0] <= inner[2] <= outer[2]
            and outer[1] <= inner[1] <= inner[3] <= outer[3]
        )

    def overlaps(left: list[int], right: list[int] | None) -> bool:
        if right is None:
            return False
        return not (
            left[2] <= right[0]
            or right[2] <= left[0]
            or left[3] <= right[1]
            or right[3] <= left[1]
        )

    exercised: set[str] = set()
    headlines = ("КАЛИНИНГРАД СЕГОДНЯ", "КАЛИНИНГРАД ЗАВТРА")
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for scenario, pool in manifest["scenario_pools"].items():
            month, post_type, temp_max, temp_min, condition, wind, gust = cases[scenario]
            seen: set[str] = set()
            for day in range(1, 29):
                date_value = f"{day:02d}.{month:02d}.2026"
                message = (
                    f"Калининград {'завтра' if post_type == 'evening' else 'сегодня'} ({date_value})\n"
                    f"🏙 Калининград — {temp_max}/{temp_min} °C • {condition} • "
                    f"ветер {wind} м/с, порывы {gust} м/с\n"
                )
                visibility = (
                    {
                        "visibility_condition": "fog",
                        "current_visibility_m": 500,
                        "humidity_pct": 98,
                        "weather_code": 45,
                    }
                    if scenario == "fog"
                    else None
                )
                metadata = extract_kld_cover_facts(
                    message,
                    post_type=post_type,
                    visibility_context=visibility,
                )
                if scenario == "night":
                    metadata["visual_period"] = "night"
                actual_scenario, asset_id, eligible = select_curated_asset(
                    metadata,
                    post_type=post_type,
                    source_text=message,
                )
                assert actual_scenario == scenario, (scenario, actual_scenario, metadata)
                assert asset_id in pool
                assert list(eligible) == pool
                if asset_id in seen:
                    continue

                for headline_index, headline in enumerate(headlines):
                    render_metadata = dict(metadata)
                    render_metadata["title"] = headline
                    output = root / f"{asset_id}-{headline_index}.png"
                    result = render_curated_cover(
                        render_metadata,
                        post_type=post_type,
                        source_text=message,
                        output_path=output,
                    )
                    assert result["curated_scenario"] == scenario
                    assert result["curated_asset_id"] == asset_id
                    assert result["curated_asset_id"] in result["curated_pool"]
                    assert result["canvas"] == [1080, 1350]
                    with Image.open(output) as rendered:
                        assert rendered.size == (1080, 1350)

                    valid = validate_kld_cover_semantics(
                        message,
                        result,
                        post_type=post_type,
                        visibility_context=visibility,
                    )
                    assert valid["valid"] is True, (asset_id, headline, valid)

                    asset_geometry = geometry[asset_id]
                    assert result["title_safe_bbox"] == asset_geometry["title_safe"]
                    assert result["facts_safe_bbox"] == asset_geometry["facts_safe"]
                    title_bbox = result["title_layout"]["bbox"]
                    assert contains(asset_geometry["title_safe"], title_bbox), (
                        asset_id,
                        headline,
                        title_bbox,
                        asset_geometry["title_safe"],
                    )
                    assert not overlaps(title_bbox, result["branding_layout"]["bbox"])
                    assert not overlaps(title_bbox, result["date_layout"]["bbox"])
                    for fact in result["fact_layout"]:
                        for fact_bbox in fact["bboxes"]:
                            assert contains(asset_geometry["facts_safe"], fact_bbox), (
                                asset_id,
                                fact_bbox,
                                asset_geometry["facts_safe"],
                            )

                seen.add(asset_id)
                exercised.add(asset_id)
                if seen == set(pool):
                    break
            assert seen == set(pool), (scenario, seen, pool)

    assert exercised == set(manifest["asset_order"])
    assert failing_assets <= exercised


def curated_slush_requires_mixed_near_freezing() -> None:
    near_freezing = """<b>🌅 Калининградская область завтра (25.01.2026)</b>
🏙 Калининград — 2/0 °C • ❄ снег
Мамоново — 3/0 °C • 🌧 дождь
#Калининград #погода
"""
    warm_mixed = near_freezing.replace("2/0 °C", "8/5 °C").replace("3/0 °C", "8/5 °C")
    with TemporaryDirectory() as tmp:
        slush = render_kld_informative_cover(
            near_freezing,
            post_type="evening",
            output_path=Path(tmp) / "slush.png",
        )
        warm = render_kld_informative_cover(
            warm_mixed,
            post_type="evening",
            output_path=Path(tmp) / "warm.png",
        )
        assert slush["weather"]["precipitation_display"] == "mixed_snow_rain"
        assert slush["curated_scenario"] == "slush"
        assert slush["curated_asset_id"] == "kld_slush_01"
        assert warm["curated_scenario"] != "slush"

def invalid_local_cover_is_not_sent_and_text_remains_nonblocking() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
            evaluate=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dedup must not run")),
            cover_renderer=_cover_renderer(events),
            validate_cover=lambda *args, **kwargs: {
                "valid": False,
                "errors": ["display facts differ from source"],
            },
            send_photo=lambda *args, **kwargs: events.append("photo") or 999,
            record=_record(events),
        )
        assert outcome["result"] == "failed_nonfatal"
        assert outcome["error_type"] == "InvalidLocalCover"
        assert outcome["telegram_image_sent"] is False
        assert outcome["local_cover_published"] is False
        assert events == ["cover"]
        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True
        assert order == ["preview", "image", "text"]


def second_backend_runs_after_pollinations_exhaustion_with_diagnostics() -> None:
    class ProviderFailure(RuntimeError):
        backend = "pollinations"
        reason = "provider_failure"
        attempts = [
            {"attempt": 1, "http_status": 500, "exception_type": "HTTPError"},
            {"attempt": 2, "http_status": None, "exception_type": "TimeoutError"},
            {"attempt": 3, "http_status": 502, "exception_type": "HTTPError"},
        ]

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        secondary_calls: list[dict[str, object]] = []

        def secondary_generate(**kwargs):
            secondary_calls.append(dict(kwargs))
            return _image(root / "horde.png", (55, 95, 145))

        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: (_ for _ in ()).throw(ProviderFailure("Pollinations exhausted")),
            secondary_generate=secondary_generate,
            provider_diagnostics=lambda backend: {
                "backend": backend,
                "http_attempt_count": 4,
                "attempts": [
                    {"attempt": 1, "stage": "submit", "http_status": 202},
                    {"attempt": 2, "stage": "check", "http_status": 200},
                    {"attempt": 3, "stage": "status", "http_status": 200},
                    {"attempt": 4, "stage": "image_download", "http_status": 200},
                ],
            },
            evaluate=lambda *args, **kwargs: _duplicate(accepted=True, reason="accepted", distance=22),
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 105,
            record=_record(events),
        )
        assert outcome["backend"] == "stable_horde"
        assert outcome["telegram_image_sent"] is True
        assert outcome["cover_attempted"] is False
        assert [item["backend"] for item in outcome["provider_attempts"]] == [
            "pollinations",
            "stable_horde",
        ]
        assert outcome["provider_attempts"][0]["http_attempt_count"] == 3
        assert outcome["provider_attempts"][1]["http_attempt_count"] == 4
        assert (
            outcome["provider_attempts"][0]["scene_family"]
            != outcome["provider_attempts"][1]["scene_family"]
        )
        assert (
            outcome["provider_attempts"][0]["composition"]
            != outcome["provider_attempts"][1]["composition"]
        )
        assert outcome["fallback_reason"] == "provider_failure"
        assert outcome["http_attempt_count"] == 7
        assert len(secondary_calls) == 1
        assert str(secondary_calls[0]["prompt"]).startswith(
            "Kaliningrad region, Baltic Sea coast, July summer."
        )
        assert "dry yellow living grass" in str(secondary_calls[0]["negative_prompt"])
        assert len(str(secondary_calls[0]["prompt"]).split()) < 100
        assert outcome["provider_attempts"][1]["prompt_contract"] == {
            "profile": "kld_short_positive_negative",
            "positive_words": len(str(secondary_calls[0]["prompt"]).split()),
            "negative_words": len(str(secondary_calls[0]["negative_prompt"]).split()),
            "negative_separated": True,
        }
        assert outcome["selected_scene_family"]
        assert outcome["selected_composition"]
        assert outcome["selected_cache_key"]
        assert events == ["photo", "history"]


def semantic_rejection_rotates_to_next_candidate() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        generated = 0

        def generate(**kwargs):
            nonlocal generated
            generated += 1
            return _image(root / f"semantic-{generated}.png", (70 + generated, 110, 140))

        evaluated = 0

        def evaluate(path, **kwargs):
            nonlocal evaluated
            evaluated += 1
            if evaluated == 1:
                return _duplicate(
                    accepted=False,
                    reason="content_guard:summer_dry_steppe",
                    distance=18,
                )
            return _duplicate(accepted=True, reason="accepted", distance=20)

        outcome = _run_delivery(
            root,
            generate=generate,
            evaluate=evaluate,
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 109,
            record=_record(events),
        )
        assert outcome["backend"] == "pollinations"
        assert outcome["cover_attempted"] is False
        assert len(outcome["provider_attempts"]) == 2
        assert outcome["provider_attempts"][0]["dedup_reason"] == (
            "content_guard:summer_dry_steppe"
        )
        assert outcome["provider_attempts"][1]["dedup_reason"] == "accepted"
        assert outcome["provider_attempts"][0]["scene_family"] != (
            outcome["provider_attempts"][1]["scene_family"]
        )
        assert events == ["photo", "history"]


def provider_branding_rejections_exhaust_to_local_cover() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        generated = 0

        def generate(**kwargs):
            nonlocal generated
            generated += 1
            return _image(root / f"rejected-{generated}.png", (80 + generated, 105, 130))

        def evaluate(path, **kwargs):
            if str(path).endswith("cover.png"):
                return _duplicate(accepted=True, reason="accepted", distance=20)
            return _duplicate(
                accepted=False,
                reason="content_guard:provider_branding_watermark",
                distance=20,
            )

        outcome = _run_delivery(
            root,
            generate=generate,
            evaluate=evaluate,
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 110,
            record=_record(events),
        )
        assert len(outcome["provider_attempts"]) == 3
        assert all(
            item["dedup_reason"] == "content_guard:provider_branding_watermark"
            for item in outcome["provider_attempts"]
        )
        assert outcome["fallback_reason"] == "semantic_mismatch"
        assert outcome["backend"] == "local_informative_cover"
        assert outcome["local_cover_published"] is True
        assert events == ["cover", "photo", "history"]


def near_duplicate_candidates_are_hard_rejected_and_rotate_scene() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        generated = 0

        def generate(**kwargs):
            nonlocal generated
            generated += 1
            return _image(root / f"ai-{generated}.png", (70 + generated, 110, 140))

        def evaluate(path, **kwargs):
            if str(path).endswith("cover.png"):
                return _duplicate(accepted=True, reason="accepted", distance=18)
            return _duplicate(accepted=False, reason="near_duplicate", distance=3)

        outcome = _run_delivery(
            root,
            generate=generate,
            evaluate=evaluate,
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 106,
            record=_record(events),
        )
        ai_attempts = [item for item in outcome["provider_attempts"] if item["backend"] == "pollinations"]
        assert len(ai_attempts) == 3
        assert len({item["scene_family"] for item in ai_attempts}) == 3
        assert len({item["composition"] for item in ai_attempts}) == 3
        assert all(item.get("dedup_reason") == "near_duplicate" for item in ai_attempts)
        assert outcome["backend"] == "local_informative_cover"
        assert outcome["fallback_reason"] == "near_duplicate"
        assert outcome["local_cover_published"] is True
        assert events == ["cover", "photo", "history"]


def near_duplicate_local_cover_is_not_published() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
            evaluate=lambda *args, **kwargs: _duplicate(
                accepted=False,
                reason="near_duplicate",
                distance=2,
            ),
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 107,
            record=_record(events),
        )
        assert outcome["result"] == "skipped_duplicate"
        assert outcome["telegram_image_sent"] is False
        assert outcome["local_cover_published"] is False
        assert outcome["dedup_reason"] == "near_duplicate"
        assert outcome["dedup_distance"] == 2
        assert events == ["cover"]



def duplicate_first_local_cover_retries_bounded_alternate() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []
        evaluated = 0

        def fail_provider(**_kwargs):
            raise RuntimeError("provider down")

        def evaluate(*_args, **_kwargs):
            nonlocal evaluated
            evaluated += 1
            if evaluated == 1:
                return _duplicate(accepted=False, reason="near_duplicate", distance=0)
            return _duplicate(accepted=True, reason="accepted", distance=18)

        outcome = _run_delivery(
            root,
            generate=fail_provider,
            secondary_generate=fail_provider,
            evaluate=evaluate,
            cover_renderer=_bounded_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 207,
            record=_record(events),
        )
        assert outcome["result"] == "fallback_sent"
        assert outcome["backend"] == "local_informative_cover"
        assert outcome["telegram_image_sent"] is True
        assert outcome["local_cover_published"] is True
        assert outcome["cover_metadata"]["curated_asset_id"] == "kld_overcast_02"
        assert events == [
            "cover:kld_overcast_01",
            "cover:kld_overcast_02",
            "photo",
            "history",
        ]
        local_checks = [
            item for item in outcome["dedup_results"]
            if item["backend"] == "local_informative_cover"
        ]
        assert len(local_checks) == 2
        final, order = _orchestrate(root, outcome)
        assert final["text_sent"] is True
        assert order == ["preview", "image", "text"]


def all_local_cover_variants_near_duplicate_use_terminal_local_image() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def fail_provider(**_kwargs):
            raise RuntimeError("provider down")

        outcome = _run_delivery(
            root,
            generate=fail_provider,
            secondary_generate=fail_provider,
            evaluate=lambda *_args, **_kwargs: _duplicate(
                accepted=False,
                reason="near_duplicate",
                distance=0,
                matched_target_date="2026-07-19",
            ),
            cover_renderer=_bounded_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 208,
            record=_record(events),
        )
        assert outcome["result"] == "fallback_sent"
        assert outcome["telegram_image_sent"] is True
        assert outcome["local_cover_published"] is True
        assert outcome["history_recorded"] is True
        assert outcome["terminal_local_relaxation"]["used"] is True
        assert outcome["terminal_local_relaxation"]["matched_target_date"] == "2026-07-19"
        assert events[:2] == ["cover:kld_overcast_01", "cover:kld_overcast_02"]
        assert events[-2:] == ["photo", "history"]
        assert events.index("photo") < events.index("history")


def terminal_local_relaxation_forbids_exact_and_same_target_duplicates() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        def fail_provider(**_kwargs):
            raise RuntimeError("provider down")

        for reason, matched_date in (
            ("exact_duplicate", "2026-07-19"),
            ("near_duplicate", "2026-07-20"),
        ):
            case_root = root / (reason + matched_date)
            case_root.mkdir(parents=True, exist_ok=True)
            events: list[str] = []
            outcome = _run_delivery(
                case_root,
                generate=fail_provider,
                secondary_generate=fail_provider,
                evaluate=lambda *_args, _reason=reason, _date=matched_date, **_kwargs: _duplicate(
                    accepted=False,
                    reason=_reason,
                    distance=0,
                    matched_target_date=_date,
                ),
                cover_renderer=_bounded_cover_renderer(events),
                send_photo=lambda *args, **kwargs: events.append("photo") or 209,
                record=_record(events),
            )
            assert outcome["result"] == "skipped_duplicate"
            assert outcome["telegram_image_sent"] is False
            assert outcome["history_recorded"] is False
            assert "photo" not in events
            assert "history" not in events


def terminal_local_relaxation_send_failure_never_records_history() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def fail_provider(**_kwargs):
            raise RuntimeError("provider down")

        def fail_send(*_args, **_kwargs):
            events.append("photo")
            raise RuntimeError("fixture send failure")

        outcome = _run_delivery(
            root,
            generate=fail_provider,
            secondary_generate=fail_provider,
            evaluate=lambda *_args, **_kwargs: _duplicate(
                accepted=False,
                reason="near_duplicate",
                distance=0,
                matched_target_date="2026-07-19",
            ),
            cover_renderer=_bounded_cover_renderer(events),
            send_photo=fail_send,
            record=_record(events),
        )
        assert outcome["result"] == "failed_nonfatal"
        assert outcome["telegram_image_sent"] is False
        assert outcome["history_recorded"] is False
        assert "photo" in events
        assert "history" not in events

def accepted_first_local_cover_does_not_render_alternates() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def fail_provider(**_kwargs):
            raise RuntimeError("provider down")

        outcome = _run_delivery(
            root,
            generate=fail_provider,
            secondary_generate=fail_provider,
            evaluate=lambda *_args, **_kwargs: _duplicate(
                accepted=True,
                reason="accepted",
                distance=18,
            ),
            cover_renderer=_bounded_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 209,
            record=_record(events),
        )
        assert outcome["result"] == "fallback_sent"
        assert outcome["local_cover_published"] is True
        assert outcome["cover_metadata"]["curated_asset_id"] == "kld_overcast_01"
        assert events == ["cover:kld_overcast_01", "photo", "history"]
        local_checks = [
            item for item in outcome["dedup_results"]
            if item["backend"] == "local_informative_cover"
        ]
        assert len(local_checks) == 1


def recent_scene_and_composition_cooldown_is_applied() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = [
            {"scene_family": "wet_seaside_promenade", "composition": "wide diagonal shoreline composition"},
            {"scene_family": "elevated_baltic_overlook", "composition": "pine-framed side composition"},
            {"scene_family": "kaliningrad_urban_coastal_view", "composition": "open horizon with large sky"},
        ]
        (root / "history.json").write_text(json.dumps(history), encoding="utf-8")
        events: list[str] = []
        outcome = _run_delivery(
            root,
            generate=lambda **kwargs: _image(root / "ai.png"),
            evaluate=lambda *args, **kwargs: _duplicate(accepted=True, reason="accepted", distance=20),
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 108,
            record=_record(events),
        )
        selected = outcome["provider_attempts"][0]
        assert selected["scene_family"] not in outcome["scene_cooldown"]
        assert selected["composition"] not in outcome["composition_cooldown"]
        assert len(outcome["scene_cooldown"]) == 3
        assert len(outcome["composition_cooldown"]) == 3


def pollinations_exception_retains_all_http_attempts() -> None:
    original_http_get = imagegen._http_get
    original_sleep = imagegen.time.sleep
    calls = 0

    def fail_http(url: str, *, timeout: float):
        nonlocal calls
        calls += 1
        raise TimeoutError(f"offline timeout {calls}")

    try:
        imagegen._http_get = fail_http
        imagegen.time.sleep = lambda seconds: None
        with TemporaryDirectory() as tmp:
            try:
                imagegen.download_image_to_file(
                    "https://example.invalid/image",
                    Path(tmp) / "image.jpg",
                    retries=3,
                    backend="pollinations",
                )
            except imagegen.ImageGenerationError as exc:
                assert exc.backend == "pollinations"
                assert exc.reason == "provider_failure"
                assert len(exc.attempts) == 3
                assert all(item["exception_type"] == "TimeoutError" for item in exc.attempts)
            else:
                raise AssertionError("provider failure must retain structured attempts")
        diagnostics = imagegen.get_generation_diagnostics("pollinations")
        assert diagnostics["http_attempt_count"] == 3
        assert diagnostics["result"] == "failed"
    finally:
        imagegen._http_get = original_http_get
        imagegen.time.sleep = original_sleep


def invalid_provider_payload_is_classified_and_never_saved() -> None:
    original_http_get = imagegen._http_get
    original_sleep = imagegen.time.sleep
    try:
        imagegen._http_get = lambda url, timeout: (b"<html>not an image</html>" * 20, "text/html")
        imagegen.time.sleep = lambda seconds: None
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "bad.jpg"
            try:
                imagegen.download_image_to_file(
                    "https://example.invalid/image",
                    output,
                    retries=2,
                    backend="pollinations",
                )
            except imagegen.ImageGenerationError as exc:
                assert exc.reason == "invalid_image"
                assert len(exc.attempts) == 2
                assert all(item["exception_type"] == "ValueError" for item in exc.attempts)
            else:
                raise AssertionError("non-image provider payload must be rejected")
            assert not output.exists()
        diagnostics = imagegen.get_generation_diagnostics("pollinations")
        assert diagnostics["reason"] == "invalid_image"
    finally:
        imagegen._http_get = original_http_get
        imagegen.time.sleep = original_sleep


def stable_horde_backend_has_offline_success_and_url_safety() -> None:
    from PIL import Image

    payload_buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (70, 100, 150)).save(payload_buffer, format="PNG")
    encoded = base64.b64encode(payload_buffer.getvalue()).decode("ascii")
    original_json_request = imagegen._horde_json_request
    original_enabled = os.environ.get("KLD_STABLE_HORDE_ENABLED")
    submitted_prompt = ""

    def fake_json_request(url: str, *, attempts, stage: str, **kwargs):
        nonlocal submitted_prompt
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "stage": stage,
                "http_status": 200 if stage != "submit" else 202,
                "exception_type": "",
                "message": "",
            }
        )
        if stage == "submit":
            submitted_prompt = str((kwargs.get("payload") or {}).get("prompt") or "")
            return {"id": "offline-job"}
        if stage == "check":
            return {"done": True}
        return {"generations": [{"img": encoded}]}

    try:
        os.environ["KLD_STABLE_HORDE_ENABLED"] = "1"
        imagegen._horde_json_request = fake_json_request
        with TemporaryDirectory() as tmp:
            output = imagegen.generate_kld_stable_horde_image(
                prompt="offline Baltic coast",
                style_name="test",
                negative_prompt="dry yellow living grass, screenshot",
                seed=123,
                out_path=str(Path(tmp) / "horde.jpg"),
            )
            with Image.open(output) as rendered:
                assert rendered.size == (256, 256)
                assert rendered.format == "JPEG"
        diagnostics = imagegen.get_generation_diagnostics("stable_horde")
        assert diagnostics["result"] == "success"
        assert diagnostics["http_attempt_count"] == 3
        assert submitted_prompt == (
            "offline Baltic coast style:test ### dry yellow living grass, screenshot"
        )
        assert imagegen._public_horde_image_url("http://127.0.0.1/image.png") is False
        assert imagegen._public_horde_image_url("http://localhost/image.png") is False
    finally:
        imagegen._horde_json_request = original_json_request
        if original_enabled is None:
            os.environ.pop("KLD_STABLE_HORDE_ENABLED", None)
        else:
            os.environ["KLD_STABLE_HORDE_ENABLED"] = original_enabled



def scene_aware_caption_uses_final_selected_scene_without_changing_identity() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        args = _args(root)
        args.caption = ""
        args.caption_prefix = ""
        sent: list[str] = []

        def send_photo(_path, caption, *, chat_id_override=""):
            sent.append(caption)
            return 42

        def record(**_kwargs):
            return {"sha256": "c" * 64}

        def deliver(scene_family: str, cache_key: str) -> dict[str, object]:
            metadata = {
                "forecast_date": "2026-07-20",
                "target_date": "tomorrow",
                "scene_family": scene_family,
                "composition": "fixture composition",
                "prompt_version": "fixture",
            }
            return _send_and_record(
                args=args,
                outcome={},
                backend="pollinations",
                image_path="fixture.png",
                metadata=metadata,
                cache_key=cache_key,
                style_name="fixture-style",
                history_path=root / "history.json",
                send_photo=send_photo,
                record_publication=record,
            )

        open_result = deliver("yantarny_wide_beach", "cache-open")
        assert sent[-1] == "Визуальный вайб завтрашнего вечера над Балтикой 🌊"
        assert open_result["selected_scene_family"] == "yantarny_wide_beach"
        assert open_result["selected_cache_key"] == "cache-open"

        urban_result = deliver("zelenogradsk_promenade", "cache-urban")
        assert "над Балтикой" not in sent[-1]
        assert sent[-1] == "Визуальный вайб завтрашнего вечера у воды 🌆"
        assert urban_result["selected_scene_family"] == "zelenogradsk_promenade"
        assert urban_result["selected_cache_key"] == "cache-urban"


def morning_workflow_preserves_existing_user_caption() -> None:
    workflow = (ROOT / ".github/workflows/daily_post_klg.yml").read_text(encoding="utf-8")
    morning_start = workflow.index('"safe_test_post.py", "--mode", "morning", "--format-v2"')
    evening_start = workflow.index("\n  evening:\n", morning_start)
    morning = workflow[morning_start:evening_start]
    assert 'caption = "Визуальный вайб сегодняшнего утра над Балтикой 🌊"' in morning
    assert '"--caption", caption,' in morning
    assert '"--caption-prefix", "🧪"' not in morning




def accepted_provider_is_branded_after_raw_dedup_and_history_keeps_raw() -> None:
    from PIL import Image

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        raw_path = root / "accepted-raw.png"
        evaluated: list[str] = []
        sent: list[str] = []
        recorded: list[dict[str, object]] = []

        def generate(**_kwargs):
            return _image(raw_path, (61, 101, 141))

        def evaluate(path, **_kwargs):
            evaluated.append(str(path))
            return _duplicate(accepted=True, reason="accepted", distance=23)

        def send_photo(path, _caption, *, chat_id_override=""):
            sent.append(str(path))
            return 501

        def record(**kwargs):
            recorded.append(dict(kwargs))
            return {"sha256": "d" * 64, "scene_family": kwargs["scene_family"]}

        outcome = _run_delivery(
            root,
            generate=generate,
            evaluate=evaluate,
            cover_renderer=_cover_renderer([]),
            send_photo=send_photo,
            record=record,
        )

        assert evaluated == [str(raw_path)]
        assert len(sent) == 1
        assert sent[0] != str(raw_path)
        with Image.open(sent[0]) as rendered:
            assert rendered.size == (1080, 1350)
            assert rendered.info["presentation_version"] == "kld_ai_primary_branded_v1"
        assert recorded and recorded[0]["image_path"] == str(raw_path)
        assert outcome["source_image_path"] == str(raw_path)
        assert outcome["published_image_path"] == sent[0]
        assert outcome["source_sha256"]
        assert outcome["published_sha256"]
        assert outcome["source_sha256"] != outcome["published_sha256"]
        assert outcome["presentation_version"] == "kld_ai_primary_branded_v1"
        assert outcome["cover_attempted"] is False


def presentation_failure_uses_existing_validated_local_cover() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events: list[str] = []

        def fail_presentation(*_args, **_kwargs):
            raise RuntimeError("fixture presentation failure")

        outcome = _run_delivery(
            root,
            generate=lambda **_kwargs: _image(root / "raw.png", (62, 102, 142)),
            evaluate=lambda *_args, **_kwargs: _duplicate(accepted=True, reason="accepted", distance=24),
            cover_renderer=_cover_renderer(events),
            send_photo=lambda *args, **kwargs: events.append("photo") or 502,
            record=_record(events),
            presentation_renderer=fail_presentation,
        )

        assert outcome["backend"] == "local_informative_cover"
        assert outcome["cover_attempted"] is True
        assert outcome["local_cover_published"] is True
        assert outcome["fallback_reason"] == "presentation_failure"
        assert events == ["cover", "photo", "history"]


TESTS = [
    accepted_provider_is_branded_after_raw_dedup_and_history_keeps_raw,
    presentation_failure_uses_existing_validated_local_cover,
    production_delivery_first_repeat_and_partial_states_are_idempotent,
    production_delivery_keys_test_channel_and_trigger_are_isolated,
    scene_aware_caption_uses_final_selected_scene_without_changing_identity,
    morning_workflow_preserves_existing_user_caption,
    visual_target_date_propagates_through_provider_and_fallback,
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
    production_decorative_snow_headers_are_not_weather_evidence,
    local_cover_semantic_validation_blocks_tampering,
    local_cover_variants_rotate_across_adjacent_dates,
    curated_overlay_safe_zones_cover_all_assets,
    curated_slush_requires_mixed_near_freezing,
    invalid_local_cover_is_not_sent_and_text_remains_nonblocking,
    second_backend_runs_after_pollinations_exhaustion_with_diagnostics,
    semantic_rejection_rotates_to_next_candidate,
    provider_branding_rejections_exhaust_to_local_cover,
    near_duplicate_candidates_are_hard_rejected_and_rotate_scene,
    near_duplicate_local_cover_is_not_published,
    duplicate_first_local_cover_retries_bounded_alternate,
    all_local_cover_variants_near_duplicate_use_terminal_local_image,
    terminal_local_relaxation_forbids_exact_and_same_target_duplicates,
    terminal_local_relaxation_send_failure_never_records_history,
    accepted_first_local_cover_does_not_render_alternates,
    recent_scene_and_composition_cooldown_is_applied,
    pollinations_exception_retains_all_http_attempts,
    invalid_provider_payload_is_classified_and_never_saved,
    stable_horde_backend_has_offline_success_and_url_safety,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD image-first offline checks passed")


if __name__ == "__main__":
    main()
