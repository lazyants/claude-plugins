"""The glossary adjudicator must never mint an INERT canon entry (#383).

`bootstrap_names._capped_candidate_name()` bounds a candidate's `name` and
marks the cut, while `bootstrap_names.span_match_keys()` deliberately keys
each occurrence on the UNCAPPED masked slice, so a `source_form` copied
verbatim off a capped `name` freezes a spelling into `canon.json` that no
occurrence of it can ever match. What that costs, and why it fails as a green
run rather than a halt, is stated once in `references/canon-and-glossary.md`
("A machine-truncated candidate is never `accepted`") and is not restated
here.

Every MECHANICAL site that could refuse the spelling is inside a hashed cache
bundle (`bootstrap_names.py`/`segpack.py` in the derivation bundle;
`canon_validate.py` and both `.js` templates in the plugin bundle), so a
code-side refusal would re-stale every converged segment in every project for
a defect that needs a >200-character proper-noun run the adjudicator ACCEPTS.
`glossary_TASK.md` is the one adjudicator-facing surface in NO hashed tuple,
so the rule lives there and this file pins it.

WHY EXACT SENTENCES RATHER THAN PATTERNS, and why the bullet scoping is
separate from that. Both were bought with measurements, not taste:

* SCOPING. The template states the marker TWICE -- the `source_form` bullet
  says what it IS, the `disposition` bullet carries the operative refusal. A
  whole-file search is satisfied by whichever copy is still intact: changing
  ONLY the disposition bullet's digest width from 16 to 12 left an unscoped
  draft of every assertion green. Every assertion here is therefore scoped to
  ONE named bullet, via `_bullet()`.
* EXACTNESS. Three successive review rounds defeated pattern-based versions of
  these assertions, each time with prose that satisfied the pattern and meant
  the opposite -- most sharply a bullet reading "...carries the marker MAY be
  `"accepted"` ... Separately, a disputed transcription always gets
  `disposition: "review_queue"`, never `"accepted"`", which a bounded gap
  crosses happily. The gap was never the bug: a regex cannot decide what an
  English instruction MEANS, so each tightening only bought the next
  counterexample. Pinning the whole load-bearing sentence removes the class
  rather than detecting it -- there is no gap left to cross. The cost is that
  rewording either sentence turns this file RED and whoever rewords it must
  re-read the rule; for an instruction that is the ONLY thing standing between
  an over-cap candidate and an inert canon entry, that is the intended price.
  `tests/retired_wording_pins.test.py` is the house precedent, including the
  exactly-once rule.

The digest width in both pinned sentences is interpolated from a marker the
PRODUCER actually emitted rather than reassembled from its constants, so
mutating any of `_CAPPED_NAME_MARKER_PREFIX`, `_CAPPED_NAME_MARKER_SUFFIX` or
`_CAPPED_NAME_DIGEST_CHARS` turns these RED too -- a pin rebuilt from only
some of those pieces stays green when another one moves, while the prompt goes
on teaching the old shape.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
GLOSSARY_TASK_TEMPLATE = ASSETS_DIR / "templates" / "glossary_TASK.template.md"
TEMPLATE_TEXT = GLOSSARY_TASK_TEMPLATE.read_text(encoding="utf-8")


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Same loader as tests/capped_name_occurrence_lookup.test.py:
    SCRIPTS_DIR must be on sys.path around the in-process load so a standalone
    script's own top-level imports resolve exactly as under `python3 <script>`.
    """
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_truncated_name_rule_test",
                  SCRIPTS_DIR / "bootstrap_names.py", SCRIPTS_DIR)

# One token, so the ONLY reason the returned `name` can differ from the input
# is the cap -- no space-joining is involved.
_EXEMPLAR_INPUT = "A" * (bn._MAX_CANDIDATE_NAME_CHARS + 50)


def _producer_marker() -> str:
    """The marker tail of a name the PRODUCER actually capped, with the
    digest's own hex run left in place. Read off a real return value rather
    than rebuilt from constants -- see this module's docstring, last paragraph.
    """
    capped = bn._capped_candidate_name(_EXEMPLAR_INPUT)
    assert capped != _EXEMPLAR_INPUT, (
        "the exemplar input did not trip the cap -- this pin would be vacuous"
    )
    return capped[bn._MAX_CANDIDATE_NAME_CHARS:]


def test_producer_marker_has_the_expected_shape():
    """The one pin that does not read the template at all. The two sentence
    pins below INTERPOLATE the digest width, so a producer-side change to it
    that someone also carried into the prompt would pass them both; this
    freezes the shipped width instead. It also guards their arithmetic: they
    slice the marker at the literal prefix/suffix lengths, so a prefix drift
    would otherwise reach them as a silently mis-built `expected` string and
    fail as an unreadable diff rather than naming what moved.
    """
    marker = _producer_marker()
    assert marker.startswith(" [...truncated:"), marker
    assert marker.endswith("]"), marker
    digest = marker[len(" [...truncated:"):-len("]")]
    assert len(digest) == 16, f"expected a 16-char digest, got {len(digest)}: {digest!r}"
    assert re.fullmatch(r"[0-9a-f]{16}", digest), f"digest is not lowercase hex: {digest!r}"


_TOP_BULLET_RE = re.compile(r"^- \*\*`([a-z_]+)`\*\*", re.MULTILINE)


def _bullet(field: str) -> str:
    """The `- **`<field>`**` bullet of the shipped template, up to the next
    top-level bullet -- indented sub-bullets belong to the block; only a line
    starting at column 0 with `- **` ends it. Scoping every assertion to one
    bullet is load-bearing; see this module's docstring. A DUPLICATED field
    would silently return the FIRST block, so a rule moved into a second copy
    of the bullet would read as still present -- hence the count.
    """
    starts = [(m.start(), m.group(1)) for m in _TOP_BULLET_RE.finditer(TEMPLATE_TEXT)]
    names = [name for _, name in starts]
    hits = names.count(field)
    assert hits == 1, (
        f"{GLOSSARY_TASK_TEMPLATE} has {hits} `- **`{field}`**` bullets; "
        f"expected exactly one. Found: {names}"
    )
    index = names.index(field)
    end = starts[index + 1][0] if index + 1 < len(starts) else len(TEMPLATE_TEXT)
    return TEMPLATE_TEXT[starts[index][0]:end]


# --- The two pinned sentences -------------------------------------------
# EXACT sentences, exactly once, inside one named bullet. The rationale --
# and the measured counterexamples that killed the pattern-based drafts --
# is in this module's docstring; it is not restated here.

_SOURCE_FORM_SENTENCE = (
    "a `name` ENDING in `{prefix}` followed by {width} hexadecimal digits and "
    "`{suffix}` is a machine-truncated spelling, cut at the bound with a digest "
    "appended so two different over-long runs stay distinguishable"
)

_DISPOSITION_SENTENCE = (
    "A candidate whose `name` carries the `{prefix}<{width} hex digits>{suffix}` "
    "marker described under `source_form` always gets `disposition: "
    '"review_queue"`, never `"accepted"`'
)


def _assert_bullet_pins_sentence(field: str, sentence: str) -> None:
    """`field`'s bullet must contain `sentence` verbatim, exactly once, after
    whitespace normalisation (the template is hard-wrapped, so the sentence
    spans line breaks in the file but not in the normalised text). The marker's
    prefix, suffix and digest width come from the producer, never from a
    literal here.
    """
    marker = _producer_marker()
    prefix = marker[:len(" [...truncated:")]
    suffix = marker[-len("]"):]
    expected = sentence.format(
        prefix=prefix,
        suffix=suffix,
        width=len(marker) - len(prefix) - len(suffix),
    )
    flat = " ".join(_bullet(field).split())
    count = flat.count(expected)
    assert count == 1, (
        f"the `{field}` bullet of {GLOSSARY_TASK_TEMPLATE} must contain this "
        f"sentence EXACTLY ONCE, verbatim; found it {count} time(s).\n"
        f"--- expected ---\n{expected}\n--- bullet (normalised) ---\n{flat}"
    )


def test_source_form_bullet_pins_the_marker_explanation():
    """The `source_form` bullet is where the adjudicator learns what the
    marker IS -- the antecedent the disposition rule refers back to."""
    _assert_bullet_pins_sentence("source_form", _SOURCE_FORM_SENTENCE)


def test_disposition_bullet_pins_the_refusal():
    """The `disposition` bullet carries the OPERATIVE refusal: marker
    antecedent, `review_queue` consequent and never-`"accepted"` prohibition,
    in one indivisible sentence -- the one that decides whether an inert entry
    can be minted.
    """
    _assert_bullet_pins_sentence("disposition", _DISPOSITION_SENTENCE)
