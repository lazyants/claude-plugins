"""tests/authoring_hygiene_drift.test.py

Targets a specific authoring-hygiene defect class (issue #77): a shipped
script's docstring telling an installed reader to consult a NON-shipped
file for ground truth. This plugin's scripts legitimately attribute
provenance to the private `historiettes-t3` origin project ("Generalized
from historiettes-t3's ..."), and that attribution is fine to ship -- but
a handful of docstrings went one step further and told the reader to
"read it directly before changing this one", pointing at a
`historiettes-t3/...` path that does not exist in an installed copy of
this plugin. An installed user has no way to follow that instruction.

`canon_adjudication_audit.py` and `final_audit.py` both carried this
banned directive (fixed for #77, redirected to in-repo authorities:
`references/canon-and-glossary.md` and SKILL.md's "W7 Final audit" section
/ `assets/schemas/final-audit-summary.schema.json`, respectively).

This guard scans EVERY shipped Python script under `assets/scripts/` and
asserts none of them reintroduces the banned "read it directly before
changing this one" directive -- an "unreachable ground truth" pointer at
a non-shipped origin. It does NOT ban `historiettes-t3`
mentions outright (plain provenance attribution, like
`profile_validate.py`'s own pointer at the SHIPPED
`assets/profile.example.yml`, is legitimate and must keep working).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

SHIPPED_SCRIPTS = sorted(SCRIPTS_DIR.glob("*.py"))

# The banned phrase itself: an instruction to read something "directly"
# before changing "this one" (case-insensitive -- the exact shipped wording
# was "read it directly before changing this one", but a rewording that
# still tells the reader to go read "this file" or "that file" directly is
# just as unreachable for an installed user). The inter-word separator is
# `[\s#]+`, not just `\s+`, so the match survives a hard-wrapped `#` comment
# where the phrase continues on a following comment line (the `\n# ` between
# words is otherwise non-whitespace at the `#` and would break a plain
# `\s+` join).
READ_DIRECTLY_PHRASE = re.compile(
    r"read[\s#]+it[\s#]+directly[\s#]+before[\s#]+changing[\s#]+this[\s#]+one", re.IGNORECASE
)


@pytest.mark.parametrize("script_path", SHIPPED_SCRIPTS, ids=lambda p: p.name)
def test_script_does_not_direct_readers_to_non_shipped_origin_for_ground_truth(script_path):
    source = script_path.read_text(encoding="utf-8")

    assert not READ_DIRECTLY_PHRASE.search(source), (
        f"{script_path.name} still tells readers to \"read it directly before "
        "changing this one\" -- that instruction is unreachable for an "
        "installed user if it points at a non-shipped historiettes-t3 path. "
        "Redirect to an in-repo authority instead (a references/*.md doc, "
        "SKILL.md section, or a shipped schema)."
    )


def test_shipped_scripts_directory_is_non_empty():
    """Guards against a silently-empty glob (e.g. a path typo) making every
    parametrized test above vacuously pass."""
    assert len(SHIPPED_SCRIPTS) > 10, (
        f"expected many shipped scripts under {SCRIPTS_DIR}, found "
        f"{len(SHIPPED_SCRIPTS)} -- SCRIPTS_DIR may be wrong"
    )


def test_banned_phrase_regex_actually_matches_the_original_pre_fix_wording():
    """Proves READ_DIRECTLY_PHRASE isn't accidentally too narrow to have ever
    caught the real pre-fix docstring text (both #77 originals used this
    exact phrasing)."""
    assert READ_DIRECTLY_PHRASE.search(
        "ground truth for the methodology below; read it directly before "
        "changing this one"
    )
    assert READ_DIRECTLY_PHRASE.search(
        "that file is ground truth for the checks below; read it directly "
        "before changing this one"
    )


def test_banned_phrase_regex_catches_a_hard_wrapped_comment_reintroduction():
    """Proves the `[\\s#]+` join isn't just cosmetic: a two-line `#` comment
    that wraps mid-phrase (the `before`/`changing` boundary lands right at
    the line break) must still be caught. Before this fix, `\\s+` could not
    cross the `\\n# ` comment-continuation marker (`#` is non-whitespace),
    so a reintroduction split across comment lines would pass this guard
    silently."""
    assert READ_DIRECTLY_PHRASE.search(
        "# See historiettes-t3/foo.py -- read it directly before\n"
        "# changing this one."
    )


def test_historiettes_t3_provenance_attribution_remains_legitimate_and_unbanned():
    """The fix must not have overcorrected into banning `historiettes-t3`
    mentions outright -- plain provenance attribution ("Generalized from
    historiettes-t3's ...") is legitimate and several shipped scripts
    still carry it. Confirm at least one such attribution survives."""
    mentions = [
        path for path in SHIPPED_SCRIPTS if "historiettes-t3" in path.read_text(encoding="utf-8")
    ]
    assert mentions, (
        "expected at least one shipped script to still attribute provenance "
        "to historiettes-t3 -- if none do, the fix may have stripped "
        "legitimate attribution along with the banned directive"
    )
