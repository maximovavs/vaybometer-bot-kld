# KLD R5-G7 — preliminary offline Golden Set

This PR stores only the corrected R5-G6 preliminary label/observation records (20 existing images, 36 synthetic weather scenarios, eight provisional label changes). It does **not** store duplicated pictures and does **not** establish human expert gold labels or real vision-model accuracy. Semantic QA stays OFF and PR #96 remains Draft.

The JSON fixture is compact but preserves all original synthetic scenario fields, image-only G5 observations, original G2 label, provisional G6 policy-aware label and G5 visual-only label. The manifest anchors the original R5-G6 SHA-256, exact G2 contexts digest and Git asset blobs. 17 images are reproduced via the existing curated fallback atlas loader; 3 weekly JPGs are read from existing assets.

Offline check from repository root: python tools/test_kld_semantic_qa_r5g7_min.py

Expected: four passing test groups including 20/20 asset SHA-256, 36/36 scenario replays, Moon safeguards, rain/snow/storm/fog cases, fail-closed, dedup and fallback. Every actual G6 manual-observer verdict is UNKNOWN (36/36), a known utility GAP. Four July snowfall scenarios are artificial safety stress cases, not measured regional weather. No API/provider/Telegram calls, production code or workflow modifications.
