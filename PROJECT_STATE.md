# PROJECT_STATE.md

Repository: `maximovavs/vaybometer-bot-kld`

Purpose: production automation for Kaliningrad VayboMeter regional posts and supporting weather/environmental collectors.

## Operating contract
Permission, safety, fresh-guard, secret-handling, and context-loading rules are defined in `AGENTS.md`. This file grants no write or production authorization.

## Last verified baseline
Agent-ready cleanup was prepared from live `main` at:
`3240b8478cfed5cd9d2efccae0fd53accd833498`

This SHA is provenance only. Scheduled collector commits can move `main`; always fetch live state before acting.

## Current production state at verification
- Scheduled Kaliningrad publishing is active.
- Recent Daily VayboMeter, Schumann, radiation, and SafeCast runs were completing successfully.
- No open PR or open issue was present at the audit.
- No single engineering blocker was encoded in GitHub at the time of this snapshot.
- Tracked `.github/workflows/.env` was removed in this cleanup because current workflows did not load it.
- Current kite/SUP/surf thresholds used by production have inline defaults in `post_common.py`; Kaliningrad shore/spot profiles are also present there.

## Durable source pointers
- Shared content / water-activity logic: `post_common.py`
- Kaliningrad publisher: `post_kld.py`
- Kaliningrad visual policy: `kld_visual_policy.py`
- Visual deduplication: `kld_visual_dedup.py`
- Image content guard: `kld_image_content_guard.py`
- Cross-region visual specification: `docs/visual_weather_matrix.md`
- Main Kaliningrad publisher workflow: `.github/workflows/daily_post_klg.yml`
- Safe test workflow: `.github/workflows/safe_test_post.yml`
- Visual smoke tests: `.github/workflows/visual_kld_smoke_tests.yml`

## Current-state rule
Do not turn this file into a chronological log. Replace stale current-state facts when they materially matter; keep detailed history in GitHub commits, PRs, workflow runs, and existing docs.

## Next step
Establish the next concrete task from fresh GitHub state plus the user's current handoff. Default to read-only until a specific write or production stage is authorized.
