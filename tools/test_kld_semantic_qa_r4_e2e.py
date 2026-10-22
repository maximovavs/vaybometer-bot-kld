"""R4 real image-delivery seam and receipt tests; offline injected only."""
from __future__ import annotations
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kld_semantic_qa_seam import (
    _deliver, _accept_all, _generate, _cover_renderer,
    existing_receipts_skip_image_step_and_semantic_qa,
    default_without_evaluator_is_unchanged,
)
from test_kld_image_first import (
    production_delivery_first_repeat_and_partial_states_are_idempotent,
    cover_failure_keeps_text_and_no_stale_image,
)
from kld_semantic_qa_wiring import select_semantic_injection


def r3_10_enabled_missing_evidence_uses_existing_curated_or_text_fallback():
    # Exercises the real execute_image_delivery, not a mocked outcome mapper.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        events = []
        injection = select_semantic_injection(enabled=True, source_snapshot=None,
            target_date="2026-07-20", post_type="evening",
            publication_target_date="2026-07-20", observer=None)
        assert injection.status == "fail_closed" and injection.evaluator is not None
        out = _deliver(root, generate=_generate(root), evaluate=_accept_all,
            cover_renderer=_cover_renderer(events), events=events,
            semantic_qa=injection.evaluator, semantic_qa_facts=injection.facts)
        assert out["result"] == "fallback_sent", out
        assert out["backend"] == "local_informative_cover", out
        assert [x["decision"] for x in out["semantic_qa_results"]] == ["UNKNOWN"] * 3
        assert not any(e.startswith("photo:ai-") for e in events), events
        assert events == ["cover", "photo:cover.png", "history"], events


def r3_10b_flag_off_keeps_original_image_delivery():
    with TemporaryDirectory() as tmp:
        root=Path(tmp); events=[]
        injection=select_semantic_injection(enabled=False,source_snapshot=None,
            target_date="2026-07-20",post_type="evening",
            publication_target_date="2026-07-20",observer=None)
        assert injection.status == "disabled" and injection.evaluator is None
        out=_deliver(root,generate=_generate(root),evaluate=_accept_all,
            cover_renderer=_cover_renderer(events),events=events,
            semantic_qa=injection.evaluator,semantic_qa_facts=injection.facts)
        assert out["result"] == "sent" and out["backend"] == "pollinations",out
        assert "semantic_qa_results" not in out
        assert events == ["photo:ai-1.branded.png", "history"],events


def main():
    tests = [
        r3_10_enabled_missing_evidence_uses_existing_curated_or_text_fallback,
        r3_10b_flag_off_keeps_original_image_delivery,
        existing_receipts_skip_image_step_and_semantic_qa,
        production_delivery_first_repeat_and_partial_states_are_idempotent,
        cover_failure_keeps_text_and_no_stale_image,
        default_without_evaluator_is_unchanged,
    ]
    for case in tests:
        case()
        print("PASS:", case.__name__)
    print("PASS: R4 full-checkout end-to-end delivery invariants 6/6 (offline fixtures)")
if __name__ == "__main__":main()
