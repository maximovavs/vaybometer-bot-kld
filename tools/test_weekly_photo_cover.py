#!/usr/bin/env python3
"""Offline pure visual regression; use: python tools/test_weekly_photo_cover.py"""
from __future__ import annotations
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1] if Path(__file__).parent.name == 'tools' else Path(__file__).parent
sys.path.insert(0,str(ROOT))
import weekly_photo_cover as photo

def run_tests() -> None:
    region = 'cyprus' if (ROOT/'assets/weekly/cy').is_dir() else 'kld'
    if region == 'cyprus':
        base_text = (
            "🗓 Вайб недели: 12–18 октября\n\n✨ Главный фон недели\n"
            "Полный прогноз на все 7 дней пока не подтверждён; погодные ориентиры недели уточнятся ближе к датам.\n\n"
            "🌦 Погода\nПолный прогноз на все 7 дней пока не собран; температуру, осадки и ветер лучше уточнять ближе к датам.\n\n"
            "🌊 Море\nСредняя вода 25–26°C; лучше утром или ближе к закату."
        )
    else:
        base_text = (
            "🗓 Вайб недели: 12–18 октября\n\n✨ Главный фон недели\n"
            "влажная и ветреная неделя; у Балтики лучше выбирать защищённые прогулки.\n\n"
            "🌦 Погода\nТемпература держится в диапазоне 11–15°C; дождевые окна возможны во второй половине недели.\n\n"
            "🌊 Балтика\nВода 15–16°C; у открытой воды ветер ощущается сильнее."
        )
    assert photo._temperature('Температура держится в диапазоне 14–28°C', 'Полный прогноз не подтверждён') == 'Уточняется'
    assert photo._temperature('Температура держится в диапазоне 11–15°C', 'Влажно') == '11–15°C'
    assert photo._temperature('Без цифрового диапазона', 'Влажно') == 'Не указана'
    assert photo._sea('Вода 15–16°C; у открытой воды ветер сильнее') == '15–16°C'
    # No cross-section borrowing when a published source section is blank.
    empty_weather = base_text.replace("🌦 Погода\n", "🌦 Погода\n\n")
    empty_weather = empty_weather.replace(
        "🌦 Погода\n\n" + ("Полный прогноз на все 7 дней пока не собран; температуру, осадки и ветер лучше уточнять ближе к датам." if region == 'cyprus' else "Температура держится в диапазоне 11–15°C; дождевые окна возможны во второй половине недели."),
        "🌦 Погода\n\n",
    )
    assert photo._section(empty_weather, "🌦 Погода") == ""
    assert photo._section("🌊 Море\n\n🏄 Вода и спорт\nSUP: ...", "🌊 Море") == ""
    assert photo._section("🌊 Балтика\n\n🏄 Вода и спорт\nSUP: ...", "🌊 Балтика") == ""
    assert photo._week_label(date(2026,10,12)) == '12–18 октября'
    assert photo._week_label(date(2026,10,30)) == '30 октября – 5 ноября'
    assert photo._scenario(region,'', 'прогноз не подтверждён') == 'neutral'
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)/'preview.png'
        meta = photo.render_photo_cover(base_text, start=date(2026,10,12),output_path=out,region=region)
        assert meta['region']==region and meta['renderer_version']==photo.PHOTO_RENDERER_VERSION
        assert len(meta['asset_sha256'])==64
        with Image.open(out) as im:
            assert im.size == (1080,1350)
            assert im.format == 'PNG'
            assert im.info['source_text_sha256']==meta['source_text_sha256']
            im.verify()
        if region == 'cyprus':
            assert meta['temperature_label']=='Уточняется' and meta['sea_temperature_label']=='25–26°C'
        else:
            assert meta['temperature_label']=='11–15°C' and meta['sea_temperature_label']=='15–16°C'

        # The signed weekly source is used verbatim; image values must not exceed it.
        assert meta['main_fact'] in base_text
        assert meta['weather_fact'] in base_text
        assert meta['sea_fact'] in base_text
        os.environ['WEEKLY_PHOTO_COVER_ENABLED'] = '1'
        try:
            import weekly_cover as wrapper
        except ImportError:
            wrapper = None  # Standalone candidate package, not the repository.
        if wrapper is not None:
            enabled = wrapper.render_weekly_cover(base_text,start=date(2026,10,12),output_path=out)
            assert enabled['renderer_version']==photo.PHOTO_RENDERER_VERSION
            os.environ['WEEKLY_PHOTO_COVER_ENABLED'] = '0'
            disabled = wrapper.render_weekly_cover(base_text,start=date(2026,10,12),output_path=out)
            assert disabled['renderer_version'] != photo.PHOTO_RENDERER_VERSION
        # Missing source sections are fatal, never fabricated.
        try:
            photo.render_photo_cover('НЕТ ПРОГНОЗА',start=date(2026,10,12),output_path=out,region=region)
        except ValueError:
            pass
        else:
            raise AssertionError('Expected fail closed on missing source sections')
    if region == 'kld':
        from unittest.mock import patch
        import weekly_cover as wrapper
        # Only full October/November weeks may use the autumn foliage JPGs.
        # Boundary weeks crossing into/out of that range must fail closed.
        season_cases = (
            ('winter', date(2026, 1, 12), False),
            ('spring', date(2026, 4, 13), False),
            ('summer', date(2026, 7, 13), False),
            ('early_autumn', date(2026, 9, 14), False),
            ('september_october', date(2026, 9, 28), False),
            ('october', date(2026, 10, 12), True),
            ('october_november', date(2026, 10, 26), True),
            ('november', date(2026, 11, 23), True),
            ('november_december', date(2026, 11, 30), False),
            ('december', date(2026, 12, 14), False),
        )
        with tempfile.TemporaryDirectory() as season_dir:
            for name, week_start, supported in season_cases:
                target = Path(season_dir) / f'{name}.png'
                assert photo._kld_autumn_photo_week(week_start) is supported, name
                if not supported:
                    try:
                        photo.render_photo_cover(base_text, start=week_start,
                                                 output_path=target, region='kld')
                    except ValueError as exc:
                        assert 'autumn-only' in str(exc), (name, exc)
                    else:
                        raise AssertionError(f'{name}: non-autumn photo accepted')
                    assert not target.exists(), f'{name}: wrote unsupported photo'
                with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '1'}):
                    actual = wrapper.render_weekly_cover(base_text, start=week_start, output_path=target)
                assert (actual['renderer_version'] == photo.PHOTO_RENDERER_VERSION) is supported, name
                with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '0'}):
                    off = wrapper.render_weekly_cover(base_text, start=week_start, output_path=target)
                assert off['renderer_version'] != photo.PHOTO_RENDERER_VERSION, name
        print(f'PASS KLD seasonal weekly photo: {len(season_cases)} dates, ON/OFF, boundaries + fallback')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bounded.png"
            cases = (
                ("wet", "влажная неделя", "Температура 11–15°C; дождевые окна возможны во второй половине недели.", "Вода 15–16°C; ветер обычный."),
                ("coast", "ветреная штормовая неделя", "Температура 11–15°C; штормовые порывы до 24 м/с.", "Вода 15–16°C; берег прохладный."),
                ("golden", "ясная осенняя неделя", "Температура 11–15°C; ясно и сухо, ночами прохладно.", "Вода 15–16°C; температура подтверждена."),
            )
            for name, headline, weather, sea in cases:
                source = f"✨ Главный фон недели\n{headline}\n\n🌦 Погода\n{weather}\n\n🌊 Балтика\n{sea}"
                m = photo.render_photo_cover(source, start=date(2026, 10, 12), output_path=out, region='kld')
                assert m['variant'] == name, (name, m['variant'])
                assert m['weather_fact'] == weather and m['sea_fact'] == sea
                with Image.open(out) as im:
                    assert im.size == (1080, 1350)
                    assert im.info['source_text_sha256'] == m['source_text_sha256']
                    bottom = {'wet': 1282, 'coast': 1248, 'golden': 1245}[name]
                    asset = ROOT / 'assets' / 'weekly' / 'kld' / photo.ASSETS['kld'][name]
                    with Image.open(asset) as background:
                        unmarked = background.convert('RGB').resize((1080, 1350), Image.Resampling.LANCZOS)
                    outside = ImageChops.difference(
                        im.convert('RGB').crop((0, bottom, 1080, 1350)),
                        unmarked.crop((0, bottom, 1080, 1350)),
                    )
                    assert outside.getbbox() is None, f'{name}: painted below glass safe zone'
            # A long safety warning never gets shortened to an ellipsis or drawn below the glass.
            too_long = "Температура 11–15°C; штормовые порывы до 24 м/с, опасные волны, " + "сильный ветер и риск для судов; " * 9
            source = f"✨ Главный фон недели\nштормовая неделя\n\n🌦 Погода\n{too_long}\n\n🌊 Балтика\nВода 15–16°C."
            try:
                photo.render_photo_cover(source, start=date(2026, 10, 12), output_path=out, region='kld')
            except ValueError as exc:
                assert 'glass panel' in str(exc) or 'overwide' in str(exc)
            else:
                raise AssertionError('Long warning silently truncated')
            # Alerts confined to the marine section cannot be reduced to temperature only.
            alert_sea = source.replace("🌊 Балтика\nВода 15–16°C.", "🌊 Балтика\nВода 15–16°C; шторм, опасные волны.")
            try:
                photo.render_photo_cover(alert_sea, start=date(2026,10,12),output_path=out,region='kld')
            except ValueError as exc:
                assert 'marine alert' in str(exc)
            else:
                raise AssertionError('Marine safety warning silently omitted')
            # The configured exception fallback must still return the legacy renderer.
            import weekly_cover as wrapper
            with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '1'}):
                got = wrapper.render_weekly_cover(alert_sea, start=date(2026,10,12),output_path=out)
            assert got['renderer_version'] != photo.PHOTO_RENDERER_VERSION
            with patch.object(photo, 'render_photo_cover', side_effect=RuntimeError('synthetic renderer failure')):
                with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '1'}):
                    failed = wrapper.render_weekly_cover(base_text, start=date(2026,10,12),output_path=out)
                assert failed['renderer_version'] != photo.PHOTO_RENDERER_VERSION
                with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '0'}):
                    disabled = wrapper.render_weekly_cover(base_text,start=date(2026,10,12),output_path=out)
                assert disabled['renderer_version'] != photo.PHOTO_RENDERER_VERSION
    if region == 'kld':
        # A negated hazard must not force a stormy picture; real hazards win.
        checks = (
            ("штормовая неделя", "шторм подтверждён, порывы до 24 м/с", "coast"),
            ("ясная неделя без штормовых предупреждений", "ясно и сухо", "golden"),
            ("ясная неделя", "шторм не ожидается, солнечно", "golden"),
            ("ясная неделя", "не ожидается шторм, ясное небо", "golden"),
            ("ясная неделя", "штормов нет; без дождя", "golden"),
            ("ясная неделя", "нет штормовых предупреждений, дождей не ожидается", "golden"),
            ("ясная неделя без сильного ветра", "ясно, без сильных порывов", "golden"),
            ("ясная неделя", "шторм не ожидается, но штормовые порывы до 24 м/с", "coast"),
            ("ясная неделя без штормовых предупреждений", "ожидаются штормовые волны", "coast"),
            ("возможен шторм", "прогноз уточняется только по температуре", "neutral"),
            ("возможен шторм", "осадки возможны, штормовые волны", "coast"),
            ("штормов нет", "ветер умеренный", "neutral"),
            ("ясная неделя", "нет данных о ветре и волнах", "neutral"),
            ("ясная неделя", "опасные условия не подтверждены", "neutral"),
        )
        for headline, weather, expected in checks:
            got = photo._scenario('kld', weather, headline)
            assert got == expected, (headline, weather, got, expected)
        # Real end-to-end image: negated storm in the weather line is selected
        # as clear, with numeric temperatures only from the supplied forecast.
        clear_text = (
            "✨ Главный фон недели\nясная неделя\n\n"
            "🌦 Погода\nТемпература 11–15°C; ясно без штормовых предупреждений.\n\n"
            "🌊 Балтика\nВода 15–16°C; спокойный берег."
        )
        with tempfile.TemporaryDirectory() as td:
            preview = photo.render_photo_cover(clear_text, start=date(2026, 10, 12),
                                               output_path=Path(td) / 'negated.png', region='kld')
            assert preview['variant'] == 'golden'
            assert preview['temperature_label'] == '11–15°C'
            assert preview['sea_temperature_label'] == '15–16°C'
    if region == 'kld':
        # Bounded storm negation: absence of a storm alert is not a storm alert.
        clear_cues = (
            "ясная неделя без штормовых предупреждений",
            "нет штормовых предупреждений",
            "никаких штормовых предупреждений",
            "отсутствуют штормовые предупреждения",
            "шторм не ожидается",
            "не ожидается шторм",
            "штормов нет",
            "штормовые предупреждения отсутствуют",
        )
        for cue in clear_cues:
            assert not photo._critical_warning(cue), cue
        # Mixed and uncertain cues must stay fail-closed, not be erased together
        # with the negated storm phrase (including independent marine warnings).
        hazardous_cues = (
            "подтверждённый шторм с волнами",
            "без штормовых предупреждений, но штормовые волны у берега",
            "шторм не ожидается, однако порывы до 24 м/с",
            "нет штормовых предупреждений, опасные волны",
            "нет штормовых предупреждений; гроза у побережья",
            "штормов нет; гололёд на набережной",
            "без штормовых предупреждений; не выходить в море",
            "шторм не подтверждён",  # uncertain ≠ safely negated
        )
        for cue in hazardous_cues:
            assert photo._critical_warning(cue), cue
        # End to end: negated storm in the HEADLINE must not trigger legacy.
        clear_text = (
            "✨ Главный фон недели\nясная неделя без штормовых предупреждений\n\n"
            "🌦 Погода\nТемпература 11–15°C; ясно и сухо, ночами прохладно.\n\n"
            "🌊 Балтика\nВода 15–16°C; спокойный берег."
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'clear.png'
            with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '1'}):
                clear = wrapper.render_weekly_cover(clear_text, start=date(2026,10,12), output_path=p)
            assert clear['renderer_version'] == photo.PHOTO_RENDERER_VERSION, clear
            assert clear['variant'] == 'golden', clear
            assert clear['temperature_label'] == '11–15°C'
            assert clear['sea_temperature_label'] == '15–16°C'
            # Real warning in headline without a weather summary mention must
            # still reject the photo, retaining the full legacy content.
            for critical in (
                "нет штормовых предупреждений, но опасные волны у берега",
                "шторм не ожидается, однако порывы до 24 м/с",
                "шторм не подтверждён",
            ):
                source = clear_text.replace('ясная неделя без штормовых предупреждений', critical)
                with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '1'}):
                    legacy = wrapper.render_weekly_cover(source, start=date(2026,10,12), output_path=p)
                assert legacy['renderer_version'] != photo.PHOTO_RENDERER_VERSION, critical
            # A genuine warning in the marine section can never disappear into
            # the sea-temperature-only panel, even if headline storm is negated.
            unsafe_sea = clear_text.replace('Вода 15–16°C; спокойный берег.', 'Вода 15–16°C; опасные волны.')
            with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '1'}):
                sea_legacy = wrapper.render_weekly_cover(unsafe_sea,start=date(2026,10,12),output_path=p)
            assert sea_legacy['renderer_version'] != photo.PHOTO_RENDERER_VERSION
            # Explicitly disabled photo rendering must continue using legacy.
            with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED': '0'}):
                disabled = wrapper.render_weekly_cover(clear_text,start=date(2026,10,12),output_path=p)
            assert disabled['renderer_version'] != photo.PHOTO_RENDERER_VERSION
        print(f'PASS KLD critical-warning negation: {len(clear_cues)} negated, {len(hazardous_cues)} hazardous + render/fallback checks')
    print(f'PASS {region}: factual gating, source hashes, 1080x1350, real-example label, invalid-input refusal')
if __name__=='__main__':run_tests()
