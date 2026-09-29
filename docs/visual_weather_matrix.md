# VayboMeter Visual Weather Matrix

Version: 1.0
Scope: Cyprus and Kaliningrad weather image generation
Status: source-of-truth specification before implementation

This file preserves the agreed visual decision matrix for weather images. Any future image prompt logic should be implemented from these rules first, then wired into `safe_test_post.py`, `image_prompt_*`, and production workflows.

## 1. Goal

The image should show the overall vibe of the day, not duplicate the full text post.

Each generated image must communicate:

1. main weather state;
2. wind/sea state when relevant;
3. one activity cue when relevant: SUP, kite, wing, windsurf;
4. moon phase when relevant and only when correct;
5. regional identity: Cyprus or Kaliningrad/Baltic.

Hard rule: image must not contradict the forecast text.

## 2. Input model

```python
VisualContext(
    region="cyprus" | "kaliningrad",
    post_type="morning" | "evening" | "forecast_tomorrow" | "fx_post",
    weather_main="clear" | "partly_cloudy" | "cloudy" | "drizzle" | "rain" | "fog" | "snow" | "storm",
    temp_max=float | None,
    temp_min=float | None,
    wind_avg=float | None,       # m/s
    wind_gust=float | None,      # m/s
    sea_temp=float | None,       # C
    wave_height=float | None,    # m
    sport="none" | "sup" | "kite" | "wing" | "windsurf",
    sport_level="none" | "excellent" | "good" | "experienced_only" | "not_recommended",
    moon_phase="new" | "waxing_crescent" | "first_quarter" | "waxing_gibbous" | "full" | "waning_gibbous" | "last_quarter" | "waning_crescent" | None,
    time_hint="day" | "sunrise" | "sunset" | "night",
    uv_index=float | None,
    score=float | None,
)
```

## 3. Thresholds

```python
WIND_LIGHT = 4
WIND_MODERATE = 7
WIND_STRONG = 10
WIND_VERY_STRONG = 13

GUST_MODERATE = 10
GUST_STRONG = 13
GUST_VERY_STRONG = 16

WAVE_LOW = 0.4
WAVE_MEDIUM = 0.8
WAVE_HIGH = 1.0

HOT_CYPRUS = 31
WARM_CYPRUS = 27

WARM_KLD = 22
MILD_KLD = 17
COOL_KLD = 13

SEA_COLD = 16
SEA_VERY_COLD = 13
```

## 4. Region base rules

### Cyprus

```python
if region == "cyprus":
    base_palette = "warm mediterranean"
    base_scene = "rocky coast, soft seaside promenade, Mediterranean bay or Troodos contrast"
    vegetation = "dry grasses, cypress, mediterranean shrubs"
    water_style = "clear luminous mediterranean water"
    light_style = "golden, bright, airy"
```

```python
if region == "cyprus" and temp_max is not None and temp_max > HOT_CYPRUS:
    add("heat haze")
    add("strong sunlight")
    add("dry bright atmosphere")
    add("deep shade as visual relief")
```

```python
if region == "cyprus" and weather_main in ["cloudy", "drizzle"]:
    keep("southern lightness")
    avoid("heavy northern grey")
```

### Kaliningrad

```python
if region == "kaliningrad":
    base_palette = "cool baltic"
    base_scene = "Baltic coast, dunes, pines, promenade, sea horizon"
    vegetation = "dune grass, pine silhouettes"
    water_style = "cool Baltic sea"
    light_style = "soft northern restrained light"
```

```python
if region == "kaliningrad" and weather_main in ["drizzle", "rain", "cloudy"]:
    add("wet Baltic air")
    add("cool grey-blue tones")
    add("damp promenade or shoreline")
```

```python
if region == "kaliningrad" and temp_max is not None and temp_max < MILD_KLD:
    add("fresh Baltic feeling")
    add("cooler tones")
    avoid("tropical beach relaxation")
```

## 5. Weather rules

```python
if weather_main == "clear":
    show("mostly clear sky")
    show("good visibility and readable horizon")
    avoid("rain, heavy clouds, storm mood")
```

```python
if weather_main == "partly_cloudy":
    show("clouds as visible sky element")
    show("sun gaps or balanced soft light")
```

```python
if weather_main == "cloudy":
    show("cloud-dominant sky")
    show("diffused light")
    reduce("hard sunlight")
```

```python
if weather_main == "drizzle":
    show("soft grey sky")
    show("humid air")
    show("subtle drizzle texture")
    show("wet promenade or damp foreground")
    avoid("bright harsh sun")
    avoid("cheerful beach postcard look")
```

```python
if weather_main == "rain":
    show("visible rain streaks")
    show("wet surfaces")
    show("darker rainy clouds")
    optionally_show("puddles on promenade")
    avoid("leisurely dry beach mood")
```

```python
if weather_main in ["fog", "mist"]:
    show("low horizon visibility")
    show("soft haze")
    reduce("contrast")
    soften("distant buildings and shoreline")
```

```python
if weather_main == "snow":
    show("snow in air or on ground")
    show("cold winter sky")
    avoid("summer greenery")
    if region == "cyprus":
        scene = "Troodos mountain winter, not seaside snow"
```

```python
if weather_main == "storm":
    show("dramatic clouds")
    show("rough sea")
    show("spray and wave energy")
    avoid("relaxed casual sport foreground")
```

## 6. Wind and sea rules

```python
if wind_avg is not None and wind_gust is not None:
    if wind_avg <= WIND_LIGHT and wind_gust <= GUST_MODERATE:
        show("calm or lightly textured water")
    elif wind_avg <= WIND_MODERATE:
        show("light chop on water")
        show("mild vegetation movement")
    elif wind_avg <= WIND_STRONG:
        show("active textured water")
        show("visible movement in vegetation and sky")
    else:
        show("windy sea")
        show("dynamic sky motion")
```

```python
if wind_gust is not None and wind_gust >= GUST_STRONG:
    add("dynamic sea texture")
    add("windy atmosphere")
    avoid("mirror-flat sea")
    avoid("full calm visual language")
```

```python
if wind_gust is not None and wind_gust >= GUST_VERY_STRONG:
    atmosphere_priority = "conditions over relaxation"
    if sport == "sup":
        no_hero_sup = True
```

```python
if wave_height is not None:
    if wave_height >= WAVE_HIGH:
        show("visibly wavy sea")
        avoid("perfect calm activity mood")
    elif wave_height >= WAVE_MEDIUM:
        show("moderately active sea")
    elif wave_height < WAVE_LOW:
        show("quite calm sea")
```

```python
if sea_temp is not None:
    if sea_temp <= SEA_VERY_COLD:
        water_feeling = "very cold"
        shift_palette("cool blue-grey")
        if sport != "none":
            imply("wetsuit")
    elif sea_temp <= SEA_COLD:
        water_feeling = "fresh/cold"
    else:
        water_feeling = "comfortable or moderate"
```

## 7. Sport rules

### SUP

```python
if sport == "sup" and sport_level == "excellent":
    show("one visible paddleboarder")
    place("midground or calm foreground water")
    require("relatively calm water")
```

```python
if sport == "sup" and sport_level == "good":
    show("one paddleboarder")
    place("midground")
    keep("athlete secondary, not dominant")
```

```python
if sport == "sup" and sport_level == "experienced_only":
    show("small distant paddleboarder or side element")
    prioritize("conditions over athlete")
    avoid("relaxed beginner scene")
```

```python
if sport == "sup" and sport_level == "not_recommended":
    avoid("clear prominent SUP rider")
    optionally_show("tiny distant board hint only")
```

```python
if sport == "sup" and wind_gust is not None and wind_gust > 12:
    no_hero_sup = True
```

```python
if sport == "sup" and wind_gust is not None and wind_gust > 14:
    sport_level_visual = "experienced_only_or_remove"
```

```python
if sport == "sup" and wave_height is not None and wave_height >= 0.8:
    avoid("calm beginner SUP look")
```

### Kite / Wing / Windsurf

```python
if sport in ["kite", "wing", "windsurf"] and sport_level == "excellent":
    show("1-3 small kites or riders on horizon")
    show("dynamic sea texture")
    mood = "wind-active day"
```

```python
if sport in ["kite", "wing", "windsurf"] and sport_level == "good":
    show("1-2 kites or one rider")
    keep("athlete smaller than landscape")
```

```python
if sport in ["kite", "wing", "windsurf"] and sport_level == "experienced_only":
    show("small distant rider")
    emphasize("wind and water conditions")
    avoid("easy beginner recreational mood")
```

```python
if sport in ["kite", "wing", "windsurf"] and sport_level == "not_recommended":
    avoid("hero kite surfer")
    optionally_show("no sport at all")
```

```python
if sport in ["kite", "wing", "windsurf"] and wind_gust is not None and wind_gust >= 10:
    allow("small kites on horizon")
```

```python
if sport in ["kite", "wing", "windsurf"] and wind_gust is not None and wind_gust < 8:
    avoid("kite as key object")
```

```python
if sport in ["kite", "wing", "windsurf"] and weather_main == "storm":
    if sport_level != "excellent":
        no_hero_rider = True
```

## 8. Moon rules

```python
if moon_phase == "new":
    do_not_draw_moon = True
    show("moonless dark sky if night/evening")
    avoid("visible crescent, full moon, lunar disc")
```

```python
if moon_phase == "waxing_crescent":
    show("thin waxing crescent")
```

```python
if moon_phase == "first_quarter":
    show("half moon")
```

```python
if moon_phase == "waxing_gibbous":
    show("almost full moon, not full")
```

```python
if moon_phase == "full":
    show("bright full moon")
    if time_hint in ["sunset", "night"]:
        optionally_show("moon reflection on water")
```

```python
if moon_phase == "waning_gibbous":
    show("bright waning gibbous moon")
```

```python
if moon_phase == "last_quarter":
    show("half moon")
```

```python
if moon_phase == "waning_crescent":
    show("thin waning crescent")
```

```python
if post_type == "morning":
    moon_priority = "low"
    avoid("moon as dominant subject")
```

```python
if post_type in ["evening", "forecast_tomorrow"]:
    moon_priority = "medium_to_high"
```

## 9. Temperature / UV rules

```python
if region == "cyprus" and temp_max is not None and temp_max > HOT_CYPRUS:
    show("heat haze")
    show("bright hard sun")
    show("dry Mediterranean atmosphere")
    show("shade importance")
```

```python
if region == "cyprus" and uv_index is not None and uv_index >= 8:
    show("strong sun feeling")
    show("high contrast light")
    show("shade importance")
```

```python
if region == "cyprus" and uv_index is not None and uv_index >= 10:
    show("very intense daylight")
    show("bright hot atmosphere")
```

```python
if region == "kaliningrad" and temp_max is not None and temp_max < 18:
    show("fresh Baltic feeling")
    show("cooler tones")
    reduce("beach-relax mood")
```

## 10. Score mood rules

```python
if score is not None and score >= 8.5:
    overall_mood = "pleasant, attractive, calm-confidence"
```

```python
if score is not None and 7.0 <= score < 8.5:
    overall_mood = "good with caveats"
```

```python
if score is not None and 6.0 <= score < 7.0:
    overall_mood = "mixed, cautious comfort"
```

```python
if score is not None and score < 6.0:
    overall_mood = "visibly cautious day"
    avoid("idyllic holiday postcard look")
```

## 11. Anti-conflict validator rules

These rules must be enforced after prompt assembly.

```python
if weather_main in ["drizzle", "rain"] and prompt_contains("bright clear sunny beach"):
    reject_prompt()
```

```python
if sport == "sup" and wind_gust is not None and wind_gust > 12 and prompt_contains("hero paddleboarder"):
    reject_prompt()
```

```python
if moon_phase == "new" and prompt_contains_any(["full moon", "crescent moon", "visible moon", "lunar disc"]):
    reject_prompt()
```

```python
if region == "cyprus" and temp_max is not None and temp_max > 31 and prompt_contains("cold northern scene"):
    reject_prompt()
```

```python
if region == "kaliningrad" and prompt_contains_any(["tropical", "palm trees", "Caribbean", "turquoise resort lagoon"]):
    reject_prompt()
```

```python
if sport == "none" and prompt_contains_any(["main athlete", "prominent surfer", "hero paddleboarder"]):
    reject_prompt()
```

```python
if weather_main == "snow" and region == "cyprus" and prompt_contains("snowy seaside"):
    reject_prompt()
```

```python
if sport in ["kite", "wing", "windsurf"] and wind_gust is not None and wind_gust < 8 and prompt_contains("large kite"):
    reject_prompt()
```

## 12. Implementation order

1. Preserve this file in both repos.
2. Add `VisualContext` parser for final FORMAT_V2 message.
3. Add `visual_rules.py` that converts `VisualContext` into `SceneCues`.
4. Add prompt-only logging in `safe_test_post.py`.
5. Fix moon conflicts in existing `image_prompt_*` modules.
6. Add sport cues to prompts.
7. Add KLD morning image prompt module.
8. Add synthetic visual prompt tests.
9. Only after prompt logs are stable, enable actual image generation/sending.

## 13. Minimal prompt assembly flow

```python
ctx = build_visual_context(final_message)
cues = apply_visual_rules(ctx)
prompt = build_visual_prompt(ctx, cues)
validate_visual_prompt(ctx, prompt)
```

Only after this passes:

```python
image = generate_image(prompt)
send_image(image)
```
