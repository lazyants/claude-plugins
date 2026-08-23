"""tests/_resume_subst_fixture.py -- the `subst` payload two resume-digest test
files feed to `resume_setup.py`, kept once instead of twice (#413).

This is NOT the workflow-template token map. `tests/_workflow_instantiation.py`
owns that: what `{{TOKEN}}` a shipped `*-wf.template.js` takes, and how each one
is encoded into JavaScript. This file owns a different contract on the other
side of the same step -- `resume_setup.SUBST_FIELDS`, the lowercase, resolved
profile values that go into the run's INPUT DIGEST, so a resumed run can prove
it was set up under the same parameters as the run it resumes.

The two contracts overlap in what they describe and diverge in what they
require (`verse_policy` is a digest field with no template token of its own;
`durable_root` and `run_id` are template tokens deliberately outside the
digest), so they are deliberately not merged into one module. The only reason
this file exists is that `orchestration_hash_resume_gating.test.py` and
`resume_integrity.test.py` carried byte-identical copies of the dict below,
comment included -- and a second copy of a field list is exactly the shape
#413 exists to remove.
"""
from __future__ import annotations

BASE_SUBST = {
    "research_mode": "live",
    "verse_policy": "skip",
    "source_lang": "fr",
    "target_lang": "en",
    "max_fix_rounds": 3,
    "batch_agent_cap": 5,
    "max_codex_jobs_per_batch": 400,
    "effort": "high",
    # 1.16.1 (#347). Empty = fetch_citation.py's shipped default list. REQUIRED
    # even when empty: the value is what the template actually burned in, and a
    # digest that omitted it would let a resumed run reuse citation verdicts
    # taken under a DIFFERENT retrieval policy.
    "citation_content_types": "",
}
