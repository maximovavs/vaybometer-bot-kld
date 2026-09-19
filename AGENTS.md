# AGENTS.md

## Scope
This repository is the production Kaliningrad VayboMeter automation. Its sibling regional repository is `maximovavs/vaybometer-bot`.

## Source of truth
- Live GitHub state is authoritative.
- Old chats, screenshots, copied SHAs, and archived Telegram captures are context only.
- Start with `PROJECT_STATE.md`; open implementation files only when the task requires them.

## Default operating mode
Read-only by default. Inspect repository state, workflow runs, logs, code, tests, and docs without mutating production.

Any commit, branch mutation, PR state change, merge, workflow dispatch, rerun/retry, secret/configuration change, provider call, Telegram send, or other publication action requires explicit authorization for that exact stage.

## Fresh guards before an authorized write
Re-read immediately before the write:
1. repository identity and live `main`;
2. target branch / PR / workflow;
3. allowed file scope;
4. any stage-specific expected SHA, CI, or production-state guard.

Stop on a material mismatch or ambiguity.

## Production safety
- Do not use a live publication as a diagnostic probe.
- Do not blindly retry ambiguous Telegram/provider/publication actions.
- Prefer deterministic/offline tests and safe fixtures before production actions when available.
- Never weaken existing safety, provenance, geographic, seasonal, deduplication, weather-consistency, or publication guards merely to make a test pass.

## Secrets and configuration
- Never print, copy, commit, or place credentials in agent-context files.
- Production credentials belong in the repository's configured secret mechanism, not tracked plaintext files.
- The removed `.github/workflows/.env` was not loaded by current workflows. Water-activity defaults and supported shore/spot profiles live in production code; do not recreate the deleted file as an implicit runtime dependency without a separately reviewed design.

## Context discipline
Read only the smallest source needed for the task.
- Text/editorial behavior: start with the relevant formatter / `post_common.py` section and targeted tests.
- Visual behavior: start with `docs/visual_weather_matrix.md`, then `kld_visual_policy.py`, `kld_visual_dedup.py`, and relevant image guards/tests.
- Publication behavior: inspect only the relevant workflow and publication module.
- Weather/AQI/sea/pollen/astro/FX/radiation/Schumann issues: open only the corresponding collector/module and targeted tests.
- Large generated data, old branches, old PRs, historical Telegram snapshots, and unrelated workflows are on-demand evidence, not startup context.

## AI execution routing

- Use ordinary ChatGPT with direct repository tools by default.
- Do not use ChatGPT Work for ordinary code review, CI diagnosis, PR handling, or repository writes when direct tools already support the required action.
- Use Work only for substantial external UI/browser execution that direct tools cannot perform.
- Use another model only for a defined independent review of a non-trivial safety, weather, publication, or architecture decision; do not duplicate routine tasks across models.

## Sibling-repository rule
Do not assume Kaliningrad and Cyprus implementations are identical. When a task spans both regions, read the sibling repo's own `AGENTS.md` and `PROJECT_STATE.md` and verify parity explicitly.
