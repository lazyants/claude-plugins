#!/usr/bin/env python3
"""Deterministic proper-noun candidate extraction (language-agnostic).

No LLM. Scans a source-language text (either the whole project's
``manifest.json``, or an ad hoc text sample) and produces a frequency-ranked
list of proper-noun / title / toponym candidates. This is the INPUT to the
later codex glossary pass, which decides the canonical target-language form
of each candidate -- established, transliterated, a title requiring
unpacking, a sense-translated speaking name, or not actually a name at all
-- this script only surfaces candidates; it never decides a translation.

Heuristics (recall-oriented; the codex glossary pass prunes precision
later):
  * A candidate = a maximal run of Capitalized tokens optionally joined by
    this source language's own name particles (e.g. French
    de/du/des/la/le/l'/von/van/saint/sainte -- see
    ``references/language-pair-parameterization.md``).
  * "mid_sentence" count = occurrences whose first token is NOT
    sentence-initial; a high mid-sentence share is strong evidence it is a
    real proper noun and not merely a sentence-opening capital.

Everything source-language-specific -- the four required fields
``PARTICLES``/``STOPWORDS``/``ELISION_RE``/``has_elision``, plus the
optional fifth ``name_inventory`` -- is read from the resolved
``${durable_root}/languages/<particle_config's LITERAL value>`` file, never
reconstructed from ``source.language.code``: a project-local override such
as ``fr.local.json`` must be respected exactly, never silently ignored.
This script itself has **no per-project adapt point** -- it is fully
generic; everything language-specific lives in that one config file.

Two invocations use the exact same extractor:

1. The mandatory language smoke test (``SKILL.md`` W3, see
   ``references/language-pair-parameterization.md``) runs this extractor
   against a real text sample of *this* book BEFORE anyone is allowed to
   trust its output.
2. Only once ``scripts/language_smoke_report.py`` has produced a report
   whose particle-config/source-sample/script-version TRIPLE all match and
   whose own ``pass`` field is ``true`` does the SAME extractor get run
   again, this time against the whole ``manifest.json``, to produce
   ``name_candidates.json`` for the codex glossary pass to consume.

That TRIPLE-keyed gate is enforced by the *workflow* (``SKILL.md``'s W3
step / ``language_smoke_report.py``), not by this script -- this script's
own first-ever invocation on a new project literally *is* the smoke test,
so it cannot gate on a report that does not exist yet. This script prints a
one-line reminder of that invariant when run in manifest mode, but never
hard-blocks on it itself.
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Self-anchored: this file lives at ${durable_root}/scripts/bootstrap_names.py.
# Never takes a --durable-root flag, never assumes cwd.
DURABLE_ROOT = Path(__file__).resolve().parents[1]
LANGUAGES_DIR = DURABLE_ROOT / "languages"

DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
DEFAULT_OUT_PATH = DURABLE_ROOT / "name_candidates.json"

# Bare-filename contract for particle_config -- must match profile_validate.py's
# own schema pattern for source.language.particle_config exactly (see
# references/language-pair-parameterization.md). Enforced again here, in
# defense-in-depth, because this script is also invoked directly by a human
# during the manual smoke-test procedure, bypassing profile_validate.py
# entirely.
PARTICLE_CONFIG_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")

# The exact five keys a particle_config file may contain (four required +
# the optional name_inventory) -- see LanguageConfig's own docstring. Any
# OTHER top-level key is rejected outright rather than silently ignored: a
# typo (e.g. "name_inventroy") would otherwise load with an EMPTY
# name_inventory and disable Phase 0's caseless bypass with no error at all
# (issue #204's follow-up finding 7).
PARTICLE_CONFIG_ALLOWED_KEYS = frozenset(
    {"PARTICLES", "STOPWORDS", "has_elision", "ELISION_RE", "name_inventory"}
)

# Sentinels this plugin bakes into plain_text (footnote refs, verse
# placeholders -- see manifest.schema.json's FNREF_N / VERSE_{vid}_{shortsha}
# descriptions). Matched generically as any ⟦...⟧ bracketed token rather than
# reconstructing the exact internal shape, since a per-sentinel-kind literal
# pattern would be a hidden adapt point this script is not supposed to have.
SENTINEL_RE = re.compile(r"⟦[^⟧]*⟧")


def mask_sentinels(text: str) -> str:
    """Replace each ``⟦...⟧`` sentinel with a SAME-LENGTH run of spaces --
    never a single collapsing space -- so every remaining character's
    Unicode-**codepoint** offset in the result is IDENTICAL to its offset in
    ``text``. A length-COLLAPSING substitution (a single space regardless of
    the sentinel's own length) would shift every subsequent character's
    offset by the sentinel's ``len() - 1``, silently corrupting any span
    computed against the masked copy the moment it is re-applied to the
    original raw ``text``/block bytes -- exactly the failure
    ``occ_index.py``'s offset-preserving evidence spans (RFC #215 Phase 0c)
    must not have. Safe with ``TOKEN_RE`` (see its own comment above): a run
    of spaces never fuses two tokens across the masked region, same as the
    single-space form it replaces.
    """
    return SENTINEL_RE.sub(lambda m: " " * len(m.group(0)), text)

# A run "closes" at one of these trailing punctuation marks; the token right
# after one of these is sentence-initial. — (U+2014) / ― (U+2015) are the
# dominant dialogue-line delimiter in French/Russian/Spanish literary prose.
TERMINATORS = frozenset(".!?:;»\"”…—―")

# Fast-path ASCII uppercase set; unicodedata's 'Lu' category is the general,
# script-agnostic fallback (Cyrillic/Greek/accented-Latin capitals alike).
# Deliberately never a hardcoded per-language accented-letter range -- that is
# what keeps is_upper_initial() (and this whole script) fully generic.
_ASCII_UPPER = frozenset(chr(c) for c in range(ord("A"), ord("Z") + 1))

# A token = one Unicode LETTER, its own trailing combining MARKs, then zero or
# more (optional internal apostrophe/hyphen CONNECTOR + another LETTER + its
# MARKs) units -- so every token both STARTS and ENDS in a letter(+marks) run
# and a connector is only ever matched BETWEEN two letters (covers
# "d'Artagnan", "Saint-Simon", accented names in any script). A trailing
# connector is deliberately left UNCONSUMED: a stray apostrophe after a name
# (e.g. "Fiona’ George") is not fused into the token -- see plugin issue #82.
# `[^\W\d_]` is the standard "any Unicode letter" idiom: Python's `\w` is
# Unicode-aware by default for `str` patterns, and subtracting `\d` and `_`
# from it leaves exactly the letter categories (it matches NO combining mark).
#
# OFFSET CONTRACT (issue #225; CROSS-SESSION -- Session 1's #206 occ_index gate
# is the consumer): combining marks -- Hebrew niqqud/cantillation, Arabic
# harakat, Latin NFD accents -- are absorbed INSIDE the token (a MARK* after
# every letter), never split off. The raw Unicode-codepoint offsets into the
# scanned plain_text are preserved verbatim: tokenize()/extract_candidate_
# spans() emit (start, end) with text[start:end] reconstructing the pointed
# surface form character-for-character, exactly as occ_index.py's
# production_occurrences() / evidence_verify.py bind stored evidence spans
# (RFC #215 Phase 0c). An NFD-drop-before-tokenize normalization is therefore
# FORBIDDEN here: dropping marks before scanning would shift every later
# codepoint offset and silently corrupt those spans.
#
# MARK class: stdlib `re` has no \p{M}, so the combining-mark ranges are a
# curated, commented list of sub-ranges over LT's four target scripts
# (Latin/general, Cyrillic, Hebrew, Arabic), category-filtered against
# `unicodedata` at import -- any unassigned (Cn) codepoint inside a named range
# on an older interpreter's Unicode version (e.g. the U+1AB0-1ACE tail is only
# fully assigned from Unicode 14) is dropped automatically, so the class stays
# pure category-M on every interpreter. tests/bootstrap_names.test.py's
# completeness test is the empirical backstop. bootstrap_names.py and
# language_smoke_report.py MUST keep TOKEN_RE byte-identical
# (tests/extractor_terminators_drift.test.py::
# test_token_re_identical_across_both_extractors enforces it) -- edit BOTH.
_MARK_SUBRANGES = (
    # Latin / general combining diacritics
    (0x0300, 0x036F), (0x1AB0, 0x1ACE), (0x1DC0, 0x1DFF), (0xFE20, 0xFE2F),
    # Cyrillic combining
    (0x0483, 0x0489),
    # Hebrew points + cantillation. The punctuation in this span
    # (05BE maqaf/Pd, 05C0 paseq, 05C3 sof pasuq, 05C6 nun hafukha/Po) is NOT
    # category M, so the sub-ranges skip it -- the category filter drops it too.
    (0x0591, 0x05BD), (0x05BF, 0x05BF), (0x05C1, 0x05C2), (0x05C4, 0x05C5),
    (0x05C7, 0x05C7),
    # Arabic harakat + Quranic annotation marks
    (0x0610, 0x061A), (0x064B, 0x065F), (0x0670, 0x0670), (0x06D6, 0x06DC),
    (0x06DF, 0x06E4), (0x06E7, 0x06E8), (0x06EA, 0x06ED),
    # Arabic Extended-A/B (Quranic annotation + historic-manuscript marks);
    # the whole block is Arabic-script, so the category filter alone keeps
    # only its marks (e.g. 08F0 ARABIC OPEN FATHATAN) and drops its letters.
    (0x0870, 0x08FF),
)


def _build_mark_class():
    """Return the character-class body (escaped, compressed to runs) of every
    category-M codepoint inside ``_MARK_SUBRANGES`` on the running interpreter.

    Category-filtered against ``unicodedata`` so the class can never carry an
    unassigned (Cn) codepoint regardless of the interpreter's Unicode version;
    identical build code + one interpreter per process means both extractors
    compute a byte-identical ``TOKEN_RE`` (the drift guard enforces it).
    """
    kept = [
        cp
        for lo, hi in _MARK_SUBRANGES
        for cp in range(lo, hi + 1)
        if unicodedata.category(chr(cp)).startswith("M")
    ]
    parts = []
    i = 0
    while i < len(kept):
        j = i
        while j + 1 < len(kept) and kept[j + 1] == kept[j] + 1:
            j += 1
        parts.append(
            "\\u%04x" % kept[i]
            if kept[i] == kept[j]
            else "\\u%04x-\\u%04x" % (kept[i], kept[j])
        )
        i = j + 1
    return "".join(parts)


_MARK_CLASS = _build_mark_class()
# LETTER MARK* (CONNECTOR? LETTER MARK*)*  -- see the OFFSET CONTRACT comment.
TOKEN_RE = re.compile(
    r"[^\W\d_][" + _MARK_CLASS + r"]*(?:['’‑׳״־-]?[^\W\d_][" + _MARK_CLASS + r"]*)*"
)

APOSTROPHES = "'’"  # ' and the Unicode right single quote

# Bracket/quote characters that WRAP text without ending a sentence -- the
# back-scan skips them so a real terminator masked behind a closing (or
# opening) quote/bracket is still found (e.g. "Fiona.' George", "(Fiona.)
# George", "Fiona. « George"). Every member is deliberately NOT in
# TERMINATORS; the closing quotes that DO end a sentence (" ” ») stay in
# TERMINATORS so they keep acting as boundaries.
WRAPPERS = frozenset("()[]{}'’‘“«")


class BootstrapNamesError(Exception):
    """Raised for a config/IO problem this script cannot recover from."""


def is_upper_initial(tok: str) -> bool:
    """True if ``tok``'s first character is an uppercase letter, any script.

    ASCII A-Z is a fast path; ``unicodedata.category(ch) == 'Lu'`` is the
    general fallback. This is the ONE piece of the character-class logic
    that stays script-agnostic without any per-language config at all.
    """
    if not tok:
        return False
    ch = tok[0]
    return ch in _ASCII_UPPER or unicodedata.category(ch) == "Lu"


def is_particle(token: str, lang: "LanguageConfig") -> bool:
    """True if ``token`` (case/trailing-apostrophe-folded) is one of this
    language's own name particles -- the exact membership test
    ``extract_candidates()`` uses to decide whether a lowercase connector
    token (e.g. French "de", "von") continues a proper-noun run. Exposed
    separately so ``language_smoke_report.py`` can build
    ``particle_smoke_cases[]`` against the SAME classification, not a
    reimplementation of it.
    """
    return token.lower().rstrip(APOSTROPHES) in lang.particles


@dataclass(frozen=True)
class LanguageConfig:
    """The resolved contents of one ``${durable_root}/languages/<file>.json``.

    The four REQUIRED fields ``references/language-pair-parameterization.md``
    documents a language config file as containing, plus one OPTIONAL fifth,
    ``name_inventory`` -- a project-local, exact-form allowlist (see below).
    Beyond these five, no field is added on a whim (e.g. the real
    historiettes-t3 project's own French-only ``CONTRACTION_RE`` is
    deliberately NOT part of this contract; a language file that needs to
    reject elided contraction openers like "C'est"/"J'ai" as false-positive
    candidates can simply list their exact surface forms in its own
    ``STOPWORDS`` array instead -- this keeps the script's inputs uniform
    across every language, and matches this whole tool's recall-oriented
    design: the codex glossary pass prunes remaining false positives).
    """

    path: Path
    particles: frozenset
    stopwords: frozenset
    elision_re: Optional["re.Pattern"]
    has_elision: bool
    raw_bytes: bytes
    # OPTIONAL fifth field (issue #204): a project-local list of full
    # native-script name forms (multi-token allowed) matched as COMPLETE
    # forms by extract_candidate_spans()'s caseless inventory route --
    # never assembled from a token run the way the particle algorithm
    # builds candidates. Absent from every shipped Latin preset; empty
    # frozenset() when the config file omits it (or sets it null). Exists
    # for scripts is_upper_initial() cannot see at all (Hebrew letters are
    # Unicode category 'Lo', not 'Lu').
    name_inventory: frozenset = frozenset()


def load_language_config(particle_config_filename: str,
                          languages_dir: Path = LANGUAGES_DIR) -> LanguageConfig:
    """Resolve ``particle_config_filename`` under ``languages_dir`` and load
    ``PARTICLES``/``STOPWORDS``/``ELISION_RE``/``has_elision`` (plus the
    optional ``name_inventory``) from it.

    ``particle_config_filename`` MUST be the profile's own
    ``source.language.particle_config`` LITERAL value (a bare filename) --
    never reconstructed from ``source.language.code``. That is what lets a
    project-local override such as ``fr.local.json`` actually take effect.

    Enforces the documented four required + optional name_inventory field
    contract exactly -- ``PARTICLES``/``STOPWORDS`` must each be present as
    a JSON array of strings, ``has_elision`` must be present as a JSON
    boolean (no coercion), ``ELISION_RE`` must be a non-empty,
    2-capture-group string when ``has_elision`` is true (a plain string or
    ``null`` otherwise), and ``name_inventory``, when present and non-null,
    must be a JSON array of non-empty strings (absent or ``null`` -> empty).
    Any violation raises :class:`BootstrapNamesError` naming the exact
    malformed/missing field -- never a silently-coerced or
    silently-defaulted value -- so a malformed particle_config can never
    diverge from what ``scripts/language_smoke_report.py``'s own
    ``load_particle_config`` accepts.
    """
    if not PARTICLE_CONFIG_FILENAME_RE.match(particle_config_filename):
        raise BootstrapNamesError(
            f"--particle-config {particle_config_filename!r} is not a bare "
            f"filename matching {PARTICLE_CONFIG_FILENAME_RE.pattern!r} -- "
            "pass the literal source.language.particle_config value from "
            "profile.yml (e.g. 'fr.json' or a project-local 'fr.local.json'), "
            "never a path containing '/' or '..'."
        )

    languages_dir = languages_dir.resolve()
    path = (languages_dir / particle_config_filename).resolve()
    try:
        path.relative_to(languages_dir)
    except ValueError as exc:
        # Belt-and-suspenders: the filename regex already forbids '/' and a
        # bare '..' component, so this should be unreachable, but never trust
        # a single layer of defense for a path that ends up open()'d.
        raise BootstrapNamesError(
            f"resolved particle_config path {path} escapes {languages_dir} "
            "-- refusing to read it."
        ) from exc

    if not path.is_file():
        raise BootstrapNamesError(
            f"particle_config file not found: {path}\n"
            f"  (expected a shipped preset or project-local override copied "
            f"under {languages_dir} by Step 0a)"
        )

    raw_bytes = path.read_bytes()
    try:
        # json.loads() accepts bytes and raises UnicodeDecodeError itself on
        # invalid UTF-8, so no separate decode step is needed.
        data = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapNamesError(f"{path} is not valid UTF-8 JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise BootstrapNamesError(f"{path} must contain a JSON object, got {type(data).__name__}")

    unknown_keys = sorted(set(data.keys()) - PARTICLE_CONFIG_ALLOWED_KEYS)
    if unknown_keys:
        raise BootstrapNamesError(
            f"{path}: unknown key(s) {unknown_keys} -- allowed keys are "
            f"{sorted(PARTICLE_CONFIG_ALLOWED_KEYS)}"
        )

    particles_raw = data.get("PARTICLES")
    if not isinstance(particles_raw, list) or not all(isinstance(p, str) for p in particles_raw):
        raise BootstrapNamesError(
            f"{path}: PARTICLES must be present as a JSON array of strings, "
            f"got {particles_raw!r}"
        )
    particles = frozenset(p.strip().lower() for p in particles_raw if p.strip())

    stopwords_raw = data.get("STOPWORDS")
    if not isinstance(stopwords_raw, list) or not all(isinstance(w, str) for w in stopwords_raw):
        raise BootstrapNamesError(
            f"{path}: STOPWORDS must be present as a JSON array of strings, "
            f"got {stopwords_raw!r}"
        )
    stopwords = frozenset(stopwords_raw)

    has_elision = data.get("has_elision")
    if not isinstance(has_elision, bool):
        raise BootstrapNamesError(
            f"{path}: has_elision must be present as a JSON boolean, got "
            f"{type(has_elision).__name__} ({has_elision!r})"
        )

    elision_pattern = data.get("ELISION_RE")

    if has_elision:
        if not isinstance(elision_pattern, str) or not elision_pattern:
            raise BootstrapNamesError(
                f"{path} sets has_elision: true but ELISION_RE is missing/empty"
            )
    elif elision_pattern is not None and not isinstance(elision_pattern, str):
        raise BootstrapNamesError(
            f"{path}: ELISION_RE must be a string or null when has_elision is "
            f"false, got {type(elision_pattern).__name__}"
        )

    elision_re = None
    if elision_pattern:
        try:
            elision_re = re.compile(elision_pattern)
        except re.error as exc:
            raise BootstrapNamesError(f"{path}'s ELISION_RE does not compile: {exc}") from exc
        if elision_re.groups != 2:
            raise BootstrapNamesError(
                f"{path}'s ELISION_RE must have exactly 2 capture groups "
                f"(1: the elided article's own remnant, 2: the name-initial "
                f"remainder), got {elision_re.groups}"
            )

    name_inventory_raw = data.get("name_inventory")
    if name_inventory_raw is None:
        name_inventory = frozenset()
    elif not isinstance(name_inventory_raw, list) or not all(
        isinstance(f, str) and f.strip() for f in name_inventory_raw
    ):
        raise BootstrapNamesError(
            f"{path}: name_inventory, when present, must be a JSON array of "
            f"non-empty strings (or null/absent for none), got "
            f"{name_inventory_raw!r}"
        )
    else:
        name_inventory = frozenset(name_inventory_raw)

    return LanguageConfig(
        path=path,
        particles=particles,
        stopwords=stopwords,
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=raw_bytes,
        name_inventory=name_inventory,
    )


def tokenize(text: str, elision_re: Optional["re.Pattern"]):
    """Split ``text`` into ``(token, preceding_char, start, end)`` tuples.

    ``start``/``end`` are half-open Unicode-**codepoint** offsets into
    ``text`` -- ``text[start:end]`` reconstructs the token's own raw
    substring. This is the single, shared span-emitting tokenizer every
    occurrence-span consumer (``extract_candidate_spans()`` below,
    ``occ_index.py``'s ``production_occurrences()``) relies on -- never a
    second implementation.

    ``preceding_char`` is the last non-whitespace, non-``WRAPPERS`` character
    before the token (or ``"."`` at the very start of the text, treated as a
    sentence boundary) -- used to decide whether a token is sentence-initial.
    Skipping ``WRAPPERS`` too means a real terminator masked behind a
    closing/opening quote or bracket (e.g. "Fiona.' George") is still found.

    When ``elision_re`` matches a raw token (e.g. French "d'Effiat"), it is
    split into two tokens: the elided article's own remnant (group 1, e.g.
    "d") and the name-initial remainder (group 2, e.g. "Effiat") -- each
    carrying its OWN span, computed as ``m.start() + elided.start(group)``
    .. ``m.start() + elided.end(group)`` (``elided`` matched against ``raw``
    == ``m.group(0)``, not the full ``text``, so its own group offsets are
    relative to ``raw`` and must be shifted by ``m.start()``). E.g.
    ``tokenize("d'Effiat", elision_re)`` emits ``("Effiat", "'", 2, 8)`` --
    ``"d'Effiat"[2:8] == "Effiat"``. Without this split, the fused token's
    lowercase first character would defeat ``is_upper_initial()`` and the
    name behind the elision would be silently and totally dropped -- see
    references/gotchas.md's french-elision-tokenizer-miss lesson.
    """
    tokens = []
    for m in TOKEN_RE.finditer(text):
        start = m.start()
        j = start - 1
        while j >= 0 and (text[j].isspace() or text[j] in WRAPPERS):
            j -= 1
        preceding = text[j] if j >= 0 else "."
        raw = m.group(0)
        elided = elision_re.match(raw) if elision_re else None
        if elided:
            tokens.append((elided.group(1), preceding, start + elided.start(1), start + elided.end(1)))
            tokens.append((elided.group(2), "'", start + elided.start(2), start + elided.end(2)))
        else:
            tokens.append((raw, preceding, start, m.end()))
    return tokens


def _build_inventory_trie(inventory_forms):
    """A nested-dict trie over ``inventory_forms`` (each a non-empty tuple of
    token strings). A node's ``None`` key marks that the path leading to it
    IS a complete inventory form -- tokens from ``tokenize()`` are always
    non-empty strings, never ``None``, so it is a safe terminal sentinel that
    can never collide with a real token.

    Replaces a linear scan over every inventory form at every token position
    (O(n_tokens x n_forms), genuinely quadratic when both grow -- see issue
    #204's follow-up perf finding) with a single build (O(total inventory
    token count)) plus a walk per position that is O(L) in the worst case,
    where L is the longest inventory form's token count -- see
    ``_compiled_inventory_trie()``/``extract_candidate_spans()``'s own
    docstring for the walk's real bound, since a per-position descent is NOT
    O(1).
    """
    root = {}
    for form_tokens in inventory_forms:
        node = root
        for t in form_tokens:
            node = node.setdefault(t, {})
        node[None] = True
    return root


@lru_cache(maxsize=32)
def _compiled_inventory_trie(name_inventory: frozenset, elision_re: Optional["re.Pattern"]):
    """The inventory trie for one ``(name_inventory, elision_re)`` pair,
    built once and cached -- NOT rebuilt on every ``extract_candidate_spans()``
    call. ``collect_candidates()`` calls the extractor once per manifest
    block, so an uncached build re-tokenized every inventory form and
    rebuilt the whole trie on EVERY block (issue #204 follow-up finding 3):
    O(blocks x inventory_tokens) work before a single block's text was even
    scanned. Both ``name_inventory`` (a ``frozenset``) and ``elision_re`` (a
    compiled ``re.Pattern``, hashable by identity) are already hashable, and
    every call within one script invocation passes the SAME resolved
    ``LanguageConfig``'s fields, so this cache hits on every block after the
    first. The trie is read-only after construction -- the pass-2 walk below
    only ever reads ``node.get(...)``/``None in node`` -- so sharing the same
    dict object across every block/call is safe.
    """
    inventory_forms = (
        tuple(t for t, _p, _s, _e in tokenize(form, elision_re))
        for form in name_inventory
    )
    return _build_inventory_trie(f for f in inventory_forms if f)


def extract_candidate_spans(text: str, lang: LanguageConfig):
    """Yield ``(name, mid_sentence: bool, start: int, end: int)`` for each
    proper-noun run found in ``text`` -- the single, richer implementation
    of the run-building algorithm; ``extract_candidates()`` below is a thin
    wrapper over this that drops the span for callers with no need of one.
    ``start``/``end`` are half-open Unicode-codepoint offsets into ``text``
    (first token's start to last token's end), reconstructing exactly the
    substring the production tokenizer/matcher consumed for that run --
    this is the ONE function ``occ_index.py``'s ``production_occurrences()``
    calls into to re-derive evidence spans (RFC #215 Phase 0c); it never
    reimplements any part of this decision logic.

    ``text`` is the RAW block/sample text (sentinels included) --
    ``⟦...⟧`` sentinels are masked internally via ``mask_sentinels()``
    (a same-length substitution, so every span below is valid directly
    against the ``text`` argument as given, with no separate remapping
    step needed by the caller).

    Two passes over the SAME token stream (``tokenize()``, so every span
    traces back to ``text`` itself); UNLIKE the two passes' original
    description, they are not mutually exclusive -- their outputs may
    legitimately overlap, nest, or interleave (see the INVARIANT below):

    1. The original capitalized-run algorithm (unchanged behavior) --
       gated on ``is_upper_initial()``/``STOPWORDS``/particle continuation.
    2. An inventory-driven CASELESS route (issue #204): for each of
       ``lang.name_inventory``'s literal multiword forms, scan EVERY token
       position for an EXACT token-sequence match -- bypassing
       ``is_upper_initial()`` entirely, which is what lets a script with no
       case distinction at all (e.g. Hebrew, Unicode category ``Lo``)
       surface a candidate at all. At a given start position, forms are
       tried longest-first and the first one that both token-matches AND
       is not an exact duplicate (see INVARIANT) wins -- so the longest
       form wins whenever it is fresh; only when the longest match at a
       position turns out to be a duplicate does a shorter, still-fresh
       match at that SAME position get a chance. A match is refused if it
       would bridge a ``TERMINATORS`` boundary between any two of its own
       tokens -- the same rule pass 1 enforces -- e.g. a ``name_inventory``
       entry "משה לייב" must NOT match text "משה. לייב".

    INVARIANT (do not re-introduce a per-token "claimed" bitmap -- three
    rounds of adversarial review each found a different case it breaks):
    pass 2 emits EVERY inventory-form token-run occurrence it finds,
    suppressing ONLY an EXACT duplicate -- an identical ``(name, start,
    end)`` triple already emitted (by pass 1 or by pass 2 itself). A token
    being part of some OTHER candidate's span -- whether from pass 1 or an
    earlier pass-2 match -- never blocks a DIFFERENT candidate from also
    covering it; only re-emitting the literal same span under the literal
    same name is refused. Consequently pass 2 always advances by exactly
    one token position, never by a matched form's length, so a form
    starting anywhere -- including a position "inside" an already-emitted
    run -- still gets its chance. This is what a bitmap-based "claimed"
    approach cannot express, and got caught breaking three different ways:
    (i) any-token-claimed-rejects (a Latin-script token inside a mostly-
    Hebrew inventory form, e.g. "Cohen" inside "משה Cohen", poisoned the
    whole form out of existence); (ii) a solo inventory form fully inside a
    LARGER pass-1 run (e.g. inventory ``["Cohen"]`` against pass-1's own
    "Jean Cohen") was suppressed even though pass 1 never emits "Cohen" as
    its own candidate at all; (iii) advancing by match_len after a match
    skips positions entirely, so two inventory forms sharing a boundary
    token (e.g. ``["משה לייב", "לייב כהן"]`` against "משה לייב כהן") only
    ever surfaced the first -- the second's start position was never
    visited. Overlapping/nested candidates for DIFFERENT names are
    expected output, not a bug: downstream consumers
    (``occ_index.py``'s ``production_occurrences()``) match spans by exact
    ``source_form`` string, so an overlapping candidate for a different
    name is never confused with the one being queried.
    """
    tokens = tokenize(mask_sentinels(text), lang.elision_re)
    n = len(tokens)
    out = []

    # Pass 1 -- capitalized-run algorithm (unchanged).
    idx = 0
    while idx < n:
        tok, preceding, _start, _end = tokens[idx]
        if not is_upper_initial(tok) or tok in lang.stopwords:
            idx += 1
            continue
        sentence_initial = preceding in TERMINATORS
        run = [idx]
        k = idx + 1
        while k < n:
            t2, preceding2, _s2, _e2 = tokens[k]
            if preceding2 in TERMINATORS:
                # t2 is itself sentence-initial (a '.', '!', '?', ':' or '»'
                # sits between the run so far and t2) -- never let a
                # capitalized-token run bridge a sentence boundary, or two
                # unrelated proper nouns in adjacent sentences fuse into one
                # bogus multiword candidate (e.g. "Effiat. Ensuite Effiat."
                # must NOT become "Effiat Ensuite Effiat"). Stop the run here;
                # t2 is re-examined as its own run start by the outer loop.
                break
            if is_upper_initial(t2) and t2 not in lang.stopwords:
                run.append(k)
                k += 1
            elif (
                is_particle(t2, lang)
                and k + 1 < n
                and is_upper_initial(tokens[k + 1][0])
                and tokens[k + 1][1] not in TERMINATORS
            ):
                run.append(k)
                run.append(k + 1)
                k += 2
            else:
                break
        name = " ".join(tokens[i][0] for i in run)
        run_start = tokens[run[0]][2]
        run_end = tokens[run[-1]][3]
        out.append((name, not sentence_initial, run_start, run_end))
        idx = k

    # Pass 2 -- inventory-driven caseless multiword bypass (0a). Each
    # inventory form is tokenized with the SAME tokenize()/elision_re as the
    # scanned text, so an inventory entry containing an elidable article
    # tokenizes identically to how the same text would in the real block.
    # See the INVARIANT in the docstring above: `seen_spans` (not a
    # per-token bitmap) is the ONLY suppression state, and every token
    # position is tried regardless of what any other pass/match covers.
    if lang.name_inventory:
        trie = _compiled_inventory_trie(lang.name_inventory, lang.elision_re)
        seen_spans = {(name, start, end) for name, _mid, start, end in out}
        idx = 0
        while idx < n:
            # Walk the trie as deep as the token run allows, collecting EVERY
            # depth at which a complete inventory form terminates (`None in
            # node`) -- not just the deepest. A single deepest-only
            # `last_match_len` cannot fall back: if the longest match at this
            # position turns out to be an exact duplicate (see INVARIANT),
            # any SHORTER-but-fresh terminal found earlier in the same walk
            # would otherwise be silently discarded (finding 2, RFC #215
            # Phase 0 review round 4 -- e.g. inventory ["Jean Cohen", "Jean"]
            # against "Jean Cohen arrived.": pass 1 already emits "Jean
            # Cohen", so pass 2 must fall back to emit "Jean" instead of
            # nothing). The `j >= 1` terminator check runs BEFORE descending
            # to depth j, so it stops the walk from ever reaching a
            # boundary-violating depth while preserving whatever shorter
            # match was already found -- equivalent to the old per-length
            # `any(... for j in range(1, m))` check, since a violation at
            # position j invalidates every candidate length > j but never one
            # of length <= j. This walk is O(L) in the worst case (L = the
            # longest inventory form's token count), restarted at EVERY token
            # position, so the whole pass is O(n_tokens x L) -- not O(n_tokens)
            # (finding 9; see
            # test_bootstrap_inventory_scan_shared_prefix_stays_within_generous_bound
            # in tests/caseless_offset.test.py for the case the earlier
            # no-shared-prefix perf fixture hid).
            node = trie
            match_lens = []
            j = 0
            while idx + j < n:
                if j >= 1 and tokens[idx + j][1] in TERMINATORS:
                    break
                nxt = node.get(tokens[idx + j][0])
                if nxt is None:
                    break
                node = nxt
                j += 1
                if None in node:
                    match_lens.append(j)
            # Longest-first: match_lens was appended in increasing depth
            # order, so the reversed iteration tries the longest terminal
            # first, falling back to shorter ones only when a longer
            # candidate turns out to be an exact duplicate. Emit AT MOST ONE
            # candidate per position -- stop at the first fresh one.
            for m in reversed(match_lens):
                run_start = tokens[idx][2]
                run_end = tokens[idx + m - 1][3]
                name = " ".join(tokens[idx + k][0] for k in range(m))
                if (name, run_start, run_end) not in seen_spans:
                    preceding0 = tokens[idx][1]
                    sentence_initial = preceding0 in TERMINATORS
                    out.append((name, not sentence_initial, run_start, run_end))
                    seen_spans.add((name, run_start, run_end))
                    break
            idx += 1

    out.sort(key=lambda r: r[2])
    return out


def extract_candidates(text: str, lang: LanguageConfig):
    """Yield ``(name, mid_sentence: bool)`` for each proper-noun run found
    in ``text`` -- unchanged public contract used by ``collect_candidates()``'s
    frequency/ranking pipeline, which has no need for per-occurrence spans.
    Thin wrapper over ``extract_candidate_spans()`` (the single
    implementation of the run-building + inventory-bypass algorithm --
    see its docstring for the full behavior, including sentinel-masking,
    which now happens internally: a caller no longer needs to pre-strip
    ``⟦...⟧`` sentinels itself).
    """
    return [(name, mid) for name, mid, _start, _end in extract_candidate_spans(text, lang)]


def iter_manifest_texts(manifest_path: Path):
    """Yield ``(seg_or_None, plain_text)`` for every non-empty block in
    ``manifest.json``.

    Deliberately does NOT filter by a block's own ``type`` -- per
    ``manifest.schema.json``, ``type`` is an adapter-defined free-text tag
    (``HEAD``/``PARA``/``QUOTE``/``FN``/``FRONTBACK`` for
    ``gutenberg_epub``, something else entirely for ``plain_text``/
    ``custom``); hardcoding a fixed type set here would be exactly the kind
    of per-project adapt point this script is not supposed to have. Any
    block whose ``plain_text`` is genuinely empty/whitespace contributes
    nothing and is skipped; everything else is scanned -- recall-oriented,
    like the rest of this script.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    blocks = manifest.get("blocks", {})
    for block in blocks.values():
        text = block.get("plain_text", "")
        if text and text.strip():
            yield block.get("seg"), text


def collect_candidates(sources, lang: LanguageConfig):
    """Run ``extract_candidates`` over every ``(source_id, text)`` pair in
    ``sources``, aggregate, and score.

    ``source_id`` may be ``None`` (e.g. a single ad hoc text sample with no
    per-segment tracking); when non-``None`` it feeds ``n_segments`` exactly
    like the original per-project script's ``by_seg`` tracking.

    Returns the same shape the original script wrote to
    ``name_candidates.json``: ``{n_candidates, n_strong, candidates: [...]}``,
    each candidate row carrying ``name``/``freq``/``mid_sentence``/
    ``multiword``/``abbrev``/``n_segments``/``likely_name``. A single-token
    capitalized row whose lowercased first character would match this
    language's own ``ELISION_RE`` and whose stripped name-initial remainder is
    itself another candidate row ADDITIONALLY carries ``elision_ambiguous:
    true`` and ``elision_stripped_form`` (the re-capitalized remainder) -- a
    DETECTION-ONLY flag for the glossary adjudicator (see plugin issue #91);
    both fields are simply absent on every other row.
    """
    freq = defaultdict(int)
    mid = defaultdict(int)
    by_source = defaultdict(set)

    for source_id, text in sources:
        for name, midsent in extract_candidates(text, lang):
            freq[name] += 1
            if midsent:
                mid[name] += 1
            if source_id is not None:
                by_source[name].add(source_id)

    # A single-word candidate that NEVER appears mid-sentence is likely a
    # sentence-initial false positive; a bare single capital (e.g. an editorial
    # initial) is an abbreviation, not a name candidate worth ranking as
    # "strong".
    rows = []
    for name, f in freq.items():
        words = name.split()
        multiword = len(words) > 1
        abbrev = len(words) == 1 and len(words[0]) == 1
        mid_count = mid[name]
        rows.append({
            "name": name,
            "freq": f,
            "mid_sentence": mid_count,
            "multiword": multiword,
            "abbrev": abbrev,
            "n_segments": len(by_source[name]),
            "likely_name": not abbrev and (mid_count > 0 or multiword or f >= 4),
        })

    # #91 -- capitalized-elision ambiguity DETECTION (detection only; obeys the
    # plugin-wide IRON RULE -- this NEVER auto-splits or auto-merges a name, it
    # only surfaces the ambiguity for the glossary adjudicator).
    #
    # For has_elision languages, a capitalized single-token candidate whose
    # first character, lowercased, would match this language's own ELISION_RE
    # (e.g. French "L'Enclos" -> article "l'" + name-initial "Enclos") is
    # genuinely ambiguous ONLY when the stripped name-initial remainder also
    # appears as its own candidate row: the surface could be a fixed
    # proper-noun compound (D'Artagnan, L'Aquila) OR an elided article + an
    # already-known name. ELISION_RE itself is untouched -- the tokenizer still
    # keeps such capitalized forms fused (its article group is lowercase-only),
    # so this is purely an extra flag layered on the finished rows. We reuse
    # each language's ELISION_RE verbatim (no hardcoded [dDlL]), so it
    # generalizes to fr.json AND it.json alike. Matching is GLOBAL across all
    # rows, never per-source: freq is aggregated by name only, and by_source is
    # empty whenever source_id is None (an explicitly supported text-mode
    # input), so a same-source-only rule would silently never fire there.
    if lang.has_elision and lang.elision_re is not None:
        all_names = {r["name"] for r in rows}
        for row in rows:
            name = row["name"]
            if row["multiword"] or not is_upper_initial(name):
                continue
            elided = lang.elision_re.match(name[0].lower() + name[1:])
            if not elided:
                continue
            stripped = elided.group(2)
            stripped_cap = stripped[:1].upper() + stripped[1:]
            if stripped_cap != name and stripped_cap in all_names:
                row["elision_ambiguous"] = True
                row["elision_stripped_form"] = stripped_cap

    rows.sort(key=lambda r: (-r["freq"], -r["mid_sentence"], r["name"]))

    return {
        "n_candidates": len(rows),
        "n_strong": sum(1 for r in rows if r["likely_name"]),
        "candidates": rows,
    }


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    tmp_path.replace(path)  # atomic on the same filesystem


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Deterministic, language-agnostic proper-noun candidate "
            "extraction. See references/language-pair-parameterization.md "
            "and SKILL.md's W3 for the mandatory language smoke test this "
            "script's output must pass before a mass run's candidates are "
            "trusted for canon-building."
        ),
    )
    p.add_argument(
        "--particle-config", required=True, metavar="FILENAME",
        help=(
            "Bare filename under ${durable_root}/languages/ -- the profile's "
            "own source.language.particle_config LITERAL value (e.g. "
            "'fr.json', or a project-local 'fr.local.json'). Never a path "
            "containing '/' or '..', and never reconstructed from "
            "source.language.code."
        ),
    )
    p.add_argument(
        "--manifest", metavar="PATH", default=None,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST_PATH}). "
             "Ignored when --text is given.",
    )
    p.add_argument(
        "--text", metavar="PATH", default=None,
        help=(
            "Scan a single plain-text file instead of manifest.json -- used "
            "for the mandatory language smoke test's stratified text sample, "
            "or for manual remediation runs against a saved excerpt. When "
            "given, --out must also be given explicitly (there is no "
            "default), so a smoke-test sample run never silently overwrites "
            "the real name_candidates.json."
        ),
    )
    p.add_argument(
        "--out", metavar="PATH", default=None,
        help=(
            f"Where to write the ranked-candidates JSON. Default when "
            f"scanning manifest.json: {DEFAULT_OUT_PATH}. No default when "
            "--text is given -- pass --out explicitly, or omit it to print "
            "the JSON to stdout instead."
        ),
    )
    p.add_argument(
        "--top", type=int, default=60, metavar="N",
        help="How many strong candidates to print in the human-readable "
             "preview (default: 60). Does not affect what is written to --out.",
    )
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        lang = load_language_config(args.particle_config)
    except BootstrapNamesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.text:
        text_path = Path(args.text)
        if not text_path.is_file():
            print(f"error: --text file not found: {text_path}", file=sys.stderr)
            return 1
        try:
            sample_text = text_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"error: --text file is not valid UTF-8: {exc}", file=sys.stderr)
            return 1
        sources = [(None, sample_text)]
        out_path = Path(args.out) if args.out else None
    else:
        manifest_path = Path(args.manifest) if args.manifest else DEFAULT_MANIFEST_PATH
        if not manifest_path.is_file():
            print(
                f"error: manifest not found: {manifest_path}\n"
                "  (pass --manifest <path>, or --text <path> to scan a text "
                "sample instead)",
                file=sys.stderr,
            )
            return 1
        try:
            sources = list(iter_manifest_texts(manifest_path))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"error: {manifest_path} is not valid UTF-8 JSON: {exc}", file=sys.stderr)
            return 1
        out_path = Path(args.out) if args.out else DEFAULT_OUT_PATH
        print(
            "reminder: do not trust these candidates for canon-building "
            "until scripts/language_smoke_report.py has produced a passing "
            "report for this exact particle_config/source-sample/"
            "script-version triple (see SKILL.md's W3).",
            file=sys.stderr,
        )

    result = collect_candidates(sources, lang)

    if out_path is not None:
        _write_json_atomic(out_path, result)
        dest_desc = str(out_path)
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
        dest_desc = "(stdout)"

    print(
        f"candidates: {result['n_candidates']}  (strong={result['n_strong']})  -> {dest_desc}",
        file=sys.stderr,
    )
    print("-" * 64, file=sys.stderr)
    print(f"{'freq':>4} {'mid':>4} {'segs':>4}  name", file=sys.stderr)
    strong_rows = [r for r in result["candidates"] if r["likely_name"]]
    for r in strong_rows[: args.top]:
        print(f"{r['freq']:>4} {r['mid_sentence']:>4} {r['n_segments']:>4}  {r['name']}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
