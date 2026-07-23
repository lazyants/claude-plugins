#!/usr/bin/env python3
"""language_smoke_report.py -- the mandatory language-pair smoke test.

Generalizes the extraction algorithm proven in historiettes-t3's
`bootstrap_names.py` (French-only, hardcoded PARTICLES/STOPWORDS/ELISION_RE)
into a config-driven re-implementation used ONLY to build this report --
`scripts/bootstrap_names.py` (the production candidate-name extractor the
glossary pass actually consumes) is a SEPARATE script with its own copy of
the same generalized algorithm. Deliberate simplification versus the real
source: the source's extra `CONTRACTION_RE` heuristic (a hardcoded French
prefix list -- "J|C|N|S|Qu|Lorsqu|Puisqu|Jusqu|Quoiqu" -- for rejecting
elided contractions like "Qu'il") is NOT reproduced here, because
`particle_config`'s documented contract is four REQUIRED fields --
PARTICLES, STOPWORDS, ELISION_RE, has_elision -- plus one OPTIONAL fifth,
`name_inventory` (see references/language-pair-parameterization.md); a
project-specific, per-language REGEX field like the source's own
CONTRACTION_RE is still out of scope for this generalization.
STOPWORDS carries the equivalent burden for a project's own book (extend it
with any missed contraction spelling the smoke test surfaces).

Gate identity -- a TRIPLE, not "did the config change" (see
references/language-pair-parameterization.md, "The mandatory smoke test"):

  1. particle_config_sha1        -- sha1 of the resolved particle_config file
  2. source_sample_sha1          -- sha1 of THIS project's own extracted
                                     source-text sample (whitespace-collapsed)
  3. smoke_report_contract_hash  -- sha1 of this script's OWN bytes, so a
                                     stored pass:true from a since-fixed
                                     older version is never silently trusted

Sequencing: run at W3, strictly after W2 (extraction) has produced
manifest.json -- the sample is built from that file's segments/blocks.

Canonical paths (self-anchored; this script always lives at
${durable_root}/scripts/language_smoke_report.py):

  manifest       ${durable_root}/manifest.json
  particle_config  ${durable_root}/languages/<particle_config's literal value>
  schemas          ${durable_root}/schemas/language-smoke-report.schema.json
  default report   ${durable_root}/runs/language-smoke-report.json
                    (overridden by profile.yml's
                    source.language.smoke_test.report_path, a bare "runs/..."
                    relative path, or null for the default above)

profile.yml itself is NOT under durable_root (it lives at
.claude/literary-translator/profile.yml, relative to the project's cwd, per
SKILL.md Step 0) -- so it is read only via an explicit --profile flag,
exactly like profile_validate.py's own convention, and only lazily (a
--particle-config AND --report-path override on the command line means this
script never needs to read profile.yml, or import PyYAML, at all).

Exit codes:
  0 -- report written, pass: true
  1 -- report written, pass: false (a checked name/elision case/particle
       case failed) -- remediation guidance printed to stderr
  2 -- usage / precondition error -- NO report written at all (e.g. fewer
       than 10 checked names with no low-density confirmation, a missing
       required test-case file, a malformed particle_config)

CLI (see references/language-pair-parameterization.md, "CLI inputs"):
  --checked-names "Name1,Name2,..."     (>=10, or per the density branches)
  --elision-test-file <path>            (required iff has_elision)
  --particle-smoke-file <path>          (required iff particle_list_size > 0)
  --low-name-density-confirmed          (required iff candidate_names_total < 10)
  --no-names-confirmed                  (required iff candidate_names_total == 0)
  --no-particles-confirmed              (required iff particle_list_size == 0)
  --profile / --manifest / --particle-config / --report-path (path overrides)
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import NoReturn

try:
    import jsonschema
    import jsonschema.exceptions
    import jsonschema.validators
except ImportError:
    print(
        "language_smoke_report.py: FATAL: missing required dependency "
        "'jsonschema'. Install it with: pip install jsonschema (or: "
        "pip install -r requirements.txt from the literary-translator "
        "plugin root).",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at ${durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
LANGUAGES_DIR = DURABLE_ROOT / "languages"
RUNS_DIR = DURABLE_ROOT / "runs"
DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
DEFAULT_REPORT_PATH = RUNS_DIR / "language-smoke-report.json"
DEFAULT_PROFILE_PATH = Path(".claude/literary-translator/profile.yml")

THIS_SCRIPT_PATH = Path(__file__).resolve()

SAMPLE_WORD_CAP = 750
LOW_NAME_DENSITY_FLOOR = 10

# Generalized, offset-safe, MARK-inclusive tokenizer (issue #225): a token =
# one Unicode LETTER, its own trailing combining MARKs, then zero or more
# (optional internal apostrophe/right-single-quote/hyphen CONNECTOR + another
# LETTER + its MARKs) units -- so every token both STARTS and ENDS in a
# letter(+marks) run and a connector is only matched BETWEEN two letters. A
# trailing connector is deliberately left UNCONSUMED (a stray apostrophe after
# a name, e.g. "Fiona’ George", is not fused into the token -- see plugin issue
# #82). Makes no assumption about which alphabet the source language uses
# (Latin-accented, Cyrillic, Hebrew, Arabic, ...); whether a given alphabet
# actually WORKS is exactly what this smoke test exists to establish -- this
# regex only makes it plausible (see references/language-pair-parameterization.
# md). Combining marks (Hebrew niqqud/cantillation, Arabic harakat, Latin NFD
# accents) are absorbed INSIDE the token, preserving raw-codepoint offsets --
# this file emits no spans of its own, but its TOKEN_RE MUST stay byte-identical
# to bootstrap_names.py's (the offset-span consumer; see that file's OFFSET
# CONTRACT comment) or one extractor carves tokens differently from the other
# (tests/extractor_terminators_drift.test.py::
# test_token_re_identical_across_both_extractors enforces it) -- edit BOTH.
#
# MARK class: stdlib `re` has no \p{M}, so the ranges are a curated, commented
# list of sub-ranges over the four target scripts, category-filtered against
# `unicodedata` at import so an unassigned (Cn) codepoint inside a named range
# on an older interpreter's Unicode version is dropped automatically (keeps the
# class pure category-M on every interpreter). LETTER matches no category-M
# codepoint, so LETTER/MARK/CONNECTOR are disjoint -> linear, unambiguous parse.
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
# Hebrew ASCII-punctuation connector equivalence (#282/#283) -- see
# bootstrap_names.py's own comment block for the full derivation. Bound on
# stacked niqqud/cantillation marks the #282 lookbehind below proves a base
# letter through: real book prose is overwhelmingly unpointed; even heavily
# cantillated liturgical Hebrew rarely exceeds 2-3 marks on one letter (vowel
# point + dagesh + one trope mark). 4 is a generous, disclosed bound: a
# letter with MORE than 4 stacked marks before the quote falls back to NOT
# fusing (the pre-fix, safe behavior for that one rare case) rather than
# mis-fusing -- fail-safe direction, never a false positive. MUST stay
# identical across both files (drift guard enforces it) -- edit BOTH.
_HEBREW_LETTERS = "\u05D0-\u05EA"
_HEBREW_MARK = "\u0591-\u05BD\u05BF\u05C1-\u05C2\u05C4-\u05C5\u05C7"
_HEBREW_QUOTE_MAX_MARKS = 4
_HEBREW_QUOTE_LOOKBEHIND = "(?:" + "|".join(
    "(?<=[" + _HEBREW_LETTERS + "]" + ("[" + _HEBREW_MARK + "]") * _n + ")"
    for _n in range(_HEBREW_QUOTE_MAX_MARKS + 1)
) + ")"
# LETTER MARK* (CONNECTOR? LETTER MARK*)*  -- byte-identical to bootstrap_names.py.
TOKEN_RE = re.compile(
    r"[^\W\d_][" + _MARK_CLASS + r"]*(?:"
    + "(?:['’‑׳״־-]|" + _HEBREW_QUOTE_LOOKBEHIND + '"(?=[' + _HEBREW_LETTERS + r"]))?"
    + r"[^\W\d_][" + _MARK_CLASS + r"]*)*"
)
# Sentinels this plugin bakes into plain_text (footnote refs, verse
# placeholders -- see manifest.schema.json's FNREF_N / VERSE_{vid}_{shortsha}
# descriptions). Matched generically as any ⟦...⟧ bracketed token, mirroring
# bootstrap_names.py's own SENTINEL_RE -- unstripped, a sentinel fuses into an
# adjacent name token (e.g. "Bouchard⟦FNREF_5⟧" -> bogus candidate "Bouchard
# FNREF") and skews density_score()'s upper-initial ratio.
SENTINEL_RE = re.compile(r"⟦[^⟧]*⟧")
# — (U+2014 em-dash) and ― (U+2015 horizontal bar) are included because they
# are the dominant dialogue-line delimiter in French/Russian/Spanish literary
# prose -- this plugin's core domain -- not just a stylistic aside.
TERMINATORS = frozenset(".!?:;»\"”…—―")
_APOSTROPHES = "'’"
# Bracket/quote characters that WRAP text without ending a sentence -- the
# back-scan skips them so a real terminator masked behind a closing (or
# opening) quote/bracket is still found (e.g. "Fiona.' George", "(Fiona.)
# George", "Fiona. « George"). Every member is deliberately NOT in
# TERMINATORS; the closing quotes that DO end a sentence (" ” ») stay in
# TERMINATORS so they keep acting as boundaries.
_WRAPPERS = frozenset("()[]{}'’‘“«")

# ---------------------------------------------------------------------------
# #238/#241 MATCH KEY -- Hebrew niqqud/cantillation fold + connector fold,
# applied ONLY at trie-descent time (this script's own extract_candidate_
# names() emit site stays the raw surface reconstruction, mirroring
# bootstrap_names.py's Contract 5 -- see that script's own comment block on
# this same section for the full rationale). This is a SEPARATE, independent
# copy of bootstrap_names.py's match-key fold (A-C4 -- no shared import,
# same reason TOKEN_RE/TERMINATORS/_WRAPPERS above are independently copied
# here too). tests/extractor_terminators_drift.test.py's drift guard asserts
# this copy agrees with bootstrap_names.py's and with the pre-existing
# final_audit._fold_source_marks (final_audit.py:548).
# ---------------------------------------------------------------------------

# NAME_CONNECTORS -- byte-identical to bootstrap_names.py's own (maqaf
# U+05BE, geresh U+05F3, gershayim U+05F4 ONLY -- see that file's comment for
# why the apostrophes/hyphens TOKEN_RE also allows are deliberately excluded).
# MUST stay identical across both files (drift guard enforces it) -- edit BOTH.
NAME_CONNECTORS = "־׳״"
_NAME_CONNECTOR_SPLIT_RE = re.compile("[" + NAME_CONNECTORS + "]")


_HEBREW_ASCII_CONNECTOR_SPLIT_RE = re.compile(
    "(?<=[" + _HEBREW_LETTERS + "])[" + re.escape("-‑'’\"") + "](?=[" + _HEBREW_LETTERS + "])"
)


def _fold_match_marks(s):
    """Fold Hebrew niqqud/cantillation for the #238 MATCH KEY ONLY -- mirrors
    bootstrap_names.py's own ``_fold_match_marks`` (itself a mirror of
    ``final_audit._fold_source_marks``, ``final_audit.py:548``) byte-for-byte:
    NFD-decompose, drop every combining mark (Unicode category ``Mn``) in the
    Hebrew block range U+0591-U+05C7, then re-NFC. NEVER applied to an
    EMITTED name -- this script's own Contract-5-equivalent emit site in
    ``extract_candidate_names()`` stays raw regardless."""
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(
        c
        for c in decomposed
        if not (unicodedata.category(c) == "Mn" and 0x0591 <= ord(c) <= 0x05C7)
    )
    return unicodedata.normalize("NFC", stripped)


def _fold_token_to_units(token):
    """Uncached #238 mark-fold + #241/#283 connector-split for ONE raw token
    string -- mirrors bootstrap_names.py's own ``_fold_token_to_units``. The
    #283 Hebrew-scoped ASCII/Latin connector-twin split (hyphen/apostrophe/
    quote, only between two Hebrew letters) runs as a second pass so a
    Hebrew compound spelled with the ASCII hyphen or an ASCII-quoted acronym
    fused by the #282 TOKEN_RE fix converges with its maqaf/gershayim/space-
    joined equivalents. Wrapped by ``match_units()``'s per-string cache
    below."""
    folded = _fold_match_marks(token)
    return tuple(
        u2
        for u1 in _NAME_CONNECTOR_SPLIT_RE.split(folded)
        for u2 in _HEBREW_ASCII_CONNECTOR_SPLIT_RE.split(u1)
        if u2
    )


@lru_cache(maxsize=None)
def match_units(s):
    """The #238/#241 match units TOKEN_RE itself would carve out of ``s``,
    each mark-folded then connector-split -- mirrors bootstrap_names.py's own
    ``match_units()`` (see its docstring for the full rationale, including
    why this works identically for a single token or a whole multi-token
    string). Memoized per distinct ``s`` for the process lifetime."""
    return tuple(
        u
        for m in TOKEN_RE.finditer(s)
        for u in _fold_token_to_units(m.group(0))
    )


# The exact five keys a particle_config file may contain (four required +
# the optional name_inventory) -- mirrors bootstrap_names.py's own
# PARTICLE_CONFIG_ALLOWED_KEYS exactly. Any OTHER top-level key is rejected
# outright rather than silently ignored: a typo (e.g. "name_inventroy")
# would otherwise load with an EMPTY name_inventory and disable Phase 0's
# caseless bypass with no error at all (issue #204's follow-up finding 7).
PARTICLE_CONFIG_ALLOWED_KEYS = frozenset(
    {"PARTICLES", "STOPWORDS", "has_elision", "ELISION_RE", "name_inventory"}
)


def fatal(message) -> NoReturn:
    print(f"language_smoke_report.py: FATAL: {message}", file=sys.stderr)
    sys.exit(2)


def is_upper_initial(tok):
    # tok[0] is guaranteed to be a letter (TOKEN_RE starts with [^\W\d_]),
    # so str.isupper() on it is equivalent to unicodedata category "Lu".
    return bool(tok) and tok[0].isupper()


def collapse_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()


def cap_words(text, limit=SAMPLE_WORD_CAP):
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit])


def sha1_bytes(data):
    return hashlib.sha1(data).hexdigest()


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json_file(path, what):
    if not path.is_file():
        fatal(f"{what} not found at {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        fatal(f"{what} at {path} is not valid JSON: {exc}")


# ---------------------------------------------------------------------------
# profile.yml -- read lazily (only if a caller relies on it for a value it
# didn't override on the CLI), so a fully-overridden invocation never needs
# PyYAML installed at all.
# ---------------------------------------------------------------------------
_PROFILE_CACHE = {}


def load_profile(profile_path):
    cached = _PROFILE_CACHE.get(profile_path)
    if cached is not None:
        return cached
    try:
        import yaml
    except ImportError:
        fatal(
            "missing required dependency 'PyYAML' (needed to read "
            f"{profile_path} because --particle-config and/or --report-path "
            "were not both given explicitly). Install it with: pip install "
            "PyYAML (or: pip install -r requirements.txt from the "
            "literary-translator plugin root)."
        )
    if not profile_path.is_file():
        fatal(
            f"profile not found at {profile_path} -- run Step 0 "
            "(profile_validate.py) first, or pass --particle-config and "
            "--report-path explicitly to avoid needing profile.yml."
        )
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        fatal(f"profile at {profile_path} is not valid YAML: {exc}")
    if not isinstance(profile, dict):
        fatal(f"profile at {profile_path} must parse to a mapping/object")
    _PROFILE_CACHE[profile_path] = profile
    return profile


def profile_particle_config_value(profile_path):
    profile = load_profile(profile_path)
    try:
        value = profile["source"]["language"]["particle_config"]
    except (KeyError, TypeError):
        fatal(
            f"profile at {profile_path} has no "
            "source.language.particle_config field"
        )
    if not isinstance(value, str) or not value:
        fatal(
            f"profile at {profile_path}: source.language.particle_config "
            "must be a non-empty string"
        )
    return value


def profile_report_path_value(profile_path):
    profile = load_profile(profile_path)
    try:
        smoke_test = profile["source"]["language"]["smoke_test"]
    except (KeyError, TypeError):
        fatal(
            f"profile at {profile_path} has no "
            "source.language.smoke_test field"
        )
    if not isinstance(smoke_test, dict) or "report_path" not in smoke_test:
        fatal(
            f"profile at {profile_path}: source.language.smoke_test.report_path "
            "is missing"
        )
    return smoke_test["report_path"]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def is_bare_filename(value):
    return os.sep not in value and "/" not in value and ".." not in value


def resolve_particle_config_path(cli_value, profile_path):
    if cli_value is not None:
        value = cli_value
    else:
        value = profile_particle_config_value(profile_path)

    if is_bare_filename(value):
        resolved = LANGUAGES_DIR / value
    else:
        resolved = Path(value).resolve()

    if not resolved.is_file():
        fatal(
            f"particle_config file not found at {resolved} (resolved from "
            f"'{value}') -- was Step 0a run to copy assets/languages/ into "
            f"{LANGUAGES_DIR}, or is a project-local override missing?"
        )
    return resolved


def resolve_report_path(cli_value, profile_path):
    if cli_value is not None:
        return Path(cli_value).resolve()

    value = profile_report_path_value(profile_path)
    if value is None:
        return DEFAULT_REPORT_PATH
    if not isinstance(value, str):
        fatal(
            "profile source.language.smoke_test.report_path must be a "
            "string or null"
        )
    if not re.match(r"^runs/[A-Za-z0-9._/-]+$", value) or ".." in value:
        fatal(
            "profile source.language.smoke_test.report_path must be a bare "
            f"relative path matching ^runs/[A-Za-z0-9._/-]+$ with no '..' "
            f"segment, got: {value!r}"
        )
    return DURABLE_ROOT / value


def _warn_inventory_match_key_collisions(name_inventory, path):
    """A-C1 (#238/#241): warn -- NEVER fatal -- when two distinct
    name_inventory surface forms fold to the SAME #238/#241 match key.
    Mirrors bootstrap_names.py's own ``_warn_inventory_match_key_collisions``
    (see its docstring for the full rationale): structurally harmless for
    matching (the trie inserts the same flattened path for every colliding
    form), so nothing is dropped -- purely an operator-visibility warning.
    Deterministic message ordering (sorted keys, shortest-then-lexicographic
    "canonical" form) so it never depends on frozenset iteration order."""
    groups = {}
    for form in name_inventory:
        key = " ".join(match_units(form))
        groups.setdefault(key, []).append(form)
    for key in sorted(groups):
        forms = groups[key]
        if len(forms) > 1:
            forms_sorted = sorted(forms, key=lambda f: (len(f), f))
            print(
                f"WARN {path}: name_inventory forms {forms_sorted!r} all fold "
                f"to the same #238/#241 match key {key!r} -- every form still "
                f"matches identically (none is dropped); treating "
                f"{forms_sorted[0]!r} as canonical in any message that must "
                "name one.",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# particle_config loading + validation (the four required fields, plus the
# optional fifth name_inventory)
# ---------------------------------------------------------------------------
def load_particle_config(path):
    raw_bytes = path.read_bytes()
    try:
        config = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fatal(f"particle_config at {path} is not valid UTF-8 JSON: {exc}")
    if not isinstance(config, dict):
        fatal(f"particle_config at {path} must be a JSON object")

    unknown_keys = sorted(set(config.keys()) - PARTICLE_CONFIG_ALLOWED_KEYS)
    if unknown_keys:
        fatal(
            f"particle_config at {path}: unknown key(s) {unknown_keys} -- "
            f"allowed keys are {sorted(PARTICLE_CONFIG_ALLOWED_KEYS)}"
        )

    particles = config.get("PARTICLES")
    stopwords = config.get("STOPWORDS")
    elision_re_str = config.get("ELISION_RE")
    has_elision = config.get("has_elision")

    if not isinstance(particles, list) or not all(isinstance(p, str) for p in particles):
        fatal(f"particle_config at {path}: PARTICLES must be a JSON array of strings")
    if not isinstance(stopwords, list) or not all(isinstance(s, str) for s in stopwords):
        fatal(f"particle_config at {path}: STOPWORDS must be a JSON array of strings")
    if not isinstance(has_elision, bool):
        fatal(f"particle_config at {path}: has_elision must be a JSON boolean")

    elision_re = None
    if has_elision:
        if not isinstance(elision_re_str, str) or not elision_re_str:
            fatal(
                f"particle_config at {path}: has_elision is true but "
                "ELISION_RE is missing/empty"
            )
        try:
            elision_re = re.compile(elision_re_str)
        except re.error as exc:
            fatal(f"particle_config at {path}: ELISION_RE does not compile: {exc}")
        if elision_re.groups != 2:
            fatal(
                f"particle_config at {path}: ELISION_RE must have exactly "
                f"2 capture groups (particle prefix, remaining name), got "
                f"{elision_re.groups}"
            )
    elif elision_re_str is not None and not isinstance(elision_re_str, str):
        fatal(f"particle_config at {path}: ELISION_RE must be a string or null")

    name_inventory_raw = config.get("name_inventory")
    if name_inventory_raw is None:
        name_inventory = frozenset()
    elif not isinstance(name_inventory_raw, list) or not all(
        isinstance(f, str) and f.strip() for f in name_inventory_raw
    ):
        fatal(
            f"particle_config at {path}: name_inventory, when present, must "
            f"be a JSON array of non-empty strings (or null/absent for "
            f"none), got {name_inventory_raw!r}"
        )
    else:
        name_inventory = frozenset(name_inventory_raw)

    _warn_inventory_match_key_collisions(name_inventory, path)

    return {
        "raw_bytes": raw_bytes,
        "particles": particles,
        "particles_lower": {p.lower() for p in particles},
        "stopwords": set(stopwords),
        "has_elision": has_elision,
        "elision_re": elision_re,
        "name_inventory": name_inventory,
    }


# ---------------------------------------------------------------------------
# Extraction algorithm -- generalized re-implementation of the run-building
# core of historiettes-t3's bootstrap_names.py, parameterized entirely by
# the four required + optional name_inventory particle_config fields (no
# per-language literals here).
# ---------------------------------------------------------------------------
def _tokenize(text, has_elision, elision_re):
    """(token, preceding_char) pairs -- mirrors bootstrap_names.py's own
    tokenize() (this script is a deliberately SEPARATE re-implementation,
    see module docstring), but returns no spans since this script only
    reports name presence/counts, never occurrence offsets. Reused for BOTH
    the main text scan and inventory-form tokenization (0a) below, so a
    name_inventory entry with its own elidable article (e.g. French
    "d'Effiat") tokenizes IDENTICALLY to how the same text would tokenize in
    the scanned block.
    """
    tokens = []
    for m in TOKEN_RE.finditer(text):
        raw = m.group(0)
        start = m.start()
        j = start - 1
        while j >= 0 and (text[j].isspace() or text[j] in _WRAPPERS):
            j -= 1
        preceding = text[j] if j >= 0 else "."
        elided = elision_re.match(raw) if (has_elision and elision_re is not None) else None
        if elided:
            # Split an elided article fused to a capitalized name (e.g.
            # French/Italian d'Effiat, l'Autriche) into its own two tokens
            # BEFORE run-building -- otherwise the fused token's lowercase
            # first character defeats is_upper_initial() and the name is
            # silently, totally dropped (see gotchas.md /
            # french-elision-tokenizer-miss).
            tokens.append((elided.group(1), preceding))
            tokens.append((elided.group(2), "'"))
        else:
            tokens.append((raw, preceding))
    return tokens


def _build_inventory_trie(inventory_forms):
    """A nested-dict trie over ``inventory_forms`` (each a non-empty tuple of
    token strings). A node's ``None`` key marks that the path leading to it
    IS a complete inventory form -- tokens from ``_tokenize()`` are always
    non-empty strings, never ``None``, so it is a safe terminal sentinel that
    can never collide with a real token.

    Mirrors ``bootstrap_names.py``'s own ``_build_inventory_trie()`` exactly
    (parity is a hard requirement here too -- see
    ``tests/caseless_offset.test.py``'s parity assertions). Replaces a linear
    scan over every inventory form at every token position (O(n_tokens x
    n_forms), genuinely quadratic when both grow) with a single build
    (O(total inventory token count)) plus a walk per position that is O(L)
    in the worst case, where L is the longest inventory form's token count
    -- see ``_compiled_inventory_trie()``/``extract_candidate_names()``'s own
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
def _compiled_inventory_trie(name_inventory: frozenset, has_elision: bool, elision_re):
    """The inventory trie for one ``(name_inventory, has_elision, elision_re)``
    triple, built once and cached -- NOT rebuilt on every
    ``extract_candidate_names()`` call. Mirrors ``bootstrap_names.py``'s own
    ``_compiled_inventory_trie()`` exactly (parity is a hard requirement here
    too), including WHY: an uncached build re-tokenized every inventory form
    and rebuilt the whole trie once per manifest block scanned (issue #204
    follow-up finding 3). ``has_elision`` is part of this file's cache key
    (unlike bootstrap_names.py's, which only ever needs ``elision_re``)
    because this file's own ``_tokenize()`` gates elision splitting on BOTH
    ``has_elision`` and ``elision_re`` together, not on ``elision_re`` alone.
    The trie is read-only after construction, so sharing it across every
    block/call is safe.

    Keyed on #238/#241 MATCH UNITS (``match_units()``), not raw tokens --
    mirrors bootstrap_names.py's own ``_compiled_inventory_trie()`` exactly
    (parity is a hard requirement here too).
    """
    inventory_forms = (
        tuple(u for t, _p in _tokenize(form, has_elision, elision_re) for u in match_units(t))
        for form in name_inventory
    )
    return _build_inventory_trie(f for f in inventory_forms if f)


def extract_candidate_names(text, lang):
    particles_lower = lang["particles_lower"]
    stopwords = lang["stopwords"]
    has_elision = lang["has_elision"]
    elision_re = lang["elision_re"]
    name_inventory = lang["name_inventory"]

    tokens = _tokenize(text, has_elision, elision_re)
    n = len(tokens)
    out = []
    # De-dup key for pass 2 (see its own comment below): (name, start_token_
    # idx, end_token_idx). This file's public output carries no char offsets
    # (see the module docstring -- it reports name/count presence only), but
    # a pair of TOKEN indices serves the identical disambiguating role
    # bootstrap_names.py's (name, char_start, char_end) does -- two distinct
    # occurrences of the same name string always land at distinct token
    # positions, so this never conflates them.
    seen_spans = set()

    # Pass 1 -- capitalized-run algorithm (unchanged behavior).
    idx = 0
    while idx < n:
        tok, preceding = tokens[idx]
        if not is_upper_initial(tok) or tok in stopwords:
            idx += 1
            continue
        sentence_initial = preceding in TERMINATORS
        run_idx = [idx]
        k = idx + 1
        while k < n:
            t2, preceding2 = tokens[k]
            if preceding2 in TERMINATORS:
                # t2 is itself sentence-initial (a '.', '!', '?', ':', '»' or
                # a dialogue dash sits between the run so far and t2) -- the
                # run is stopped at any TERMINATORS boundary so two unrelated
                # proper nouns in adjacent sentences don't fuse into one
                # bogus multiword candidate (e.g. "Fiona. George arrived
                # quietly." must NOT become "Fiona George"). A boundary masked
                # by an intervening closing/opening quote or bracket (e.g.
                # "Fiona.' George", "(Fiona.) George", "Fiona. « George") is
                # caught too: the back-scan skips _WRAPPERS to reach the real
                # terminator behind them. t2 is re-examined as its own run
                # start by the outer loop.
                break
            low = t2.lower().rstrip(_APOSTROPHES)
            if is_upper_initial(t2) and t2 not in stopwords:
                run_idx.append(k)
                k += 1
            elif (
                low in particles_lower
                and k + 1 < n
                and is_upper_initial(tokens[k + 1][0])
                # Don't bridge a sentence boundary sitting between the
                # particle and the trailing name either (e.g. "Fiona du.
                # George" must not fuse into "Fiona du George").
                and tokens[k + 1][1] not in TERMINATORS
            ):
                run_idx.append(k)
                run_idx.append(k + 1)
                k += 2
            else:
                break
        name = " ".join(tokens[i][0] for i in run_idx)
        out.append((name, not sentence_initial))
        seen_spans.add((name, run_idx[0], run_idx[-1]))
        idx = k

    # Pass 2 -- inventory-driven caseless multiword bypass (0a; issue #204).
    # Mirrors bootstrap_names.py's extract_candidate_spans() pass 2 exactly
    # (parity is a hard requirement -- see extractor_terminators_drift.test.py
    # for the sibling drift guard on the shared constants): bypasses
    # is_upper_initial()/STOPWORDS/particles entirely -- an exact token-
    # sequence match against a name_inventory form is all that's required,
    # which is what lets a script with no case distinction at all (e.g.
    # Hebrew, Unicode category 'Lo') surface a candidate here. A match
    # bridging a TERMINATORS boundary between its own tokens is refused
    # (e.g. name_inventory entry "משה לייב" must NOT match text
    # "משה. לייב").
    #
    # INVARIANT (mirrors bootstrap_names.py's -- do not re-introduce a
    # per-token "claimed" bitmap; three rounds of adversarial review each
    # found a different case it breaks): pass 2 emits EVERY inventory-form
    # occurrence it finds, suppressing ONLY an exact duplicate -- the same
    # (name, span) already emitted by pass 1 or by pass 2 itself. A token
    # being part of some OTHER candidate's span never blocks a DIFFERENT
    # candidate from covering it too (e.g. "Cohen" inside mixed-script
    # "משה Cohen"; a solo "Cohen" inventory form inside pass 1's own larger
    # "Jean Cohen" run). Consequently pass 2 always advances by exactly one
    # token position, never by a matched form's length -- two inventory
    # forms sharing a boundary token (e.g. "משה לייב"/"לייב כהן" against
    # "משה לייב כהן") must BOTH get a chance, which advancing by match_len
    # would skip. At a given position, forms are tried longest-first and
    # the first one that token-matches AND is not an exact duplicate wins.
    if name_inventory:
        trie = _compiled_inventory_trie(name_inventory, has_elision, elision_re)
        idx = 0
        while idx < n:
            # Walk the trie as deep as the token run allows, collecting EVERY
            # depth at which a complete inventory form terminates (`None in
            # node`) -- not just the deepest. A single deepest-only
            # `last_match_len` cannot fall back: if the longest match at this
            # position turns out to be an exact duplicate (see INVARIANT),
            # any SHORTER-but-fresh terminal found earlier in the same walk
            # would otherwise be silently discarded (finding 2, RFC #215
            # Phase 0 review round 4 -- mirrors bootstrap_names.py's own fix
            # exactly). The `j >= 1` terminator check runs BEFORE descending
            # to depth j, so it stops the walk from ever reaching a
            # boundary-violating depth while preserving whatever shorter
            # match was already found -- equivalent to the old per-length
            # `any(... for j in range(1, m))` check (a violation at position
            # j invalidates every candidate length > j but never one of
            # length <= j). This walk is O(L) in the worst case (L = the
            # longest inventory form's token count), restarted at EVERY token
            # position, so the whole pass is O(n_tokens x L) -- not O(n_tokens)
            # (finding 9).
            node = trie
            match_lens = []
            j = 0
            while idx + j < n:
                if j >= 1 and tokens[idx + j][1] in TERMINATORS:
                    break
                # #238/#241: descend the CURRENT token's own match units one
                # at a time -- mirrors bootstrap_names.py's own pass-2 walk
                # exactly (parity is a hard requirement here too). `None in
                # node` is only checked once the WHOLE token's units are
                # consumed, so a terminal found mid-token is never recorded
                # (token-aligned only, never a sub-token match).
                units = match_units(tokens[idx + j][0])
                matched_token = True
                for u in units:
                    nxt = node.get(u)
                    if nxt is None:
                        matched_token = False
                        break
                    node = nxt
                if not matched_token:
                    break
                j += 1
                if None in node:
                    match_lens.append(j)
            # Longest-first: match_lens was appended in increasing depth
            # order, so the reversed iteration tries the longest terminal
            # first, falling back to shorter ones only when a longer
            # candidate turns out to be an exact duplicate. Emit AT MOST ONE
            # candidate per position -- stop at the first fresh one.
            for m in reversed(match_lens):
                name = " ".join(tokens[idx + k][0] for k in range(m))
                span_key = (name, idx, idx + m - 1)
                if span_key not in seen_spans:
                    preceding0 = tokens[idx][1]
                    sentence_initial = preceding0 in TERMINATORS
                    out.append((name, not sentence_initial))
                    seen_spans.add(span_key)
                    break
            idx += 1

    return out


def classify_is_particle(token, lang):
    normalized = token.lower().rstrip(_APOSTROPHES)
    return normalized in lang["particles_lower"]


def density_score(text, lang):
    words = text.split()
    wc = len(words)
    if wc == 0:
        return 0.0
    upper_count = sum(1 for m in TOKEN_RE.finditer(text) if is_upper_initial(m.group(0)))
    score = (upper_count / wc) * 100.0
    if lang["has_elision"] and lang["elision_re"] is not None:
        elision_count = sum(1 for m in TOKEN_RE.finditer(text) if lang["elision_re"].match(m.group(0)))
        score += (elision_count / wc) * 100.0
    return score


# ---------------------------------------------------------------------------
# Sample assembly from manifest.json (stratified, not front-loaded)
# ---------------------------------------------------------------------------
def segment_plain_text(seg_record, blocks):
    parts = []
    for bid in seg_record.get("block_ids", []):
        block = blocks.get(bid)
        if block is not None:
            parts.append(block.get("plain_text", ""))
    return "\n".join(parts)


def segment_clean_text(seg_record, blocks):
    """`segment_plain_text` with SENTINEL_RE stripped (see SENTINEL_RE's
    module-level comment for why every consumer in `build_source_sample`
    must see the sentinel-free text -- density scoring, the word cap, and
    candidate extraction all break in different ways if a raw sentinel
    reaches them)."""
    return SENTINEL_RE.sub(" ", segment_plain_text(seg_record, blocks))


def segment_order_index(seg_record, blocks):
    idxs = [
        blocks[bid]["order_index"]
        for bid in seg_record.get("block_ids", [])
        if bid in blocks and "order_index" in blocks[bid]
    ]
    return min(idxs) if idxs else 0


def build_source_sample(manifest, lang):
    blocks = manifest.get("blocks", {})
    if not isinstance(blocks, dict):
        fatal("manifest.json: 'blocks' must be an object")
    segments = manifest.get("segments", [])
    if not isinstance(segments, list):
        fatal("manifest.json: 'segments' must be an array")

    body_segs = [s for s in segments if s.get("kind") == "body"]
    body_segs.sort(key=lambda s: segment_order_index(s, blocks))
    n = len(body_segs)

    chosen = []  # list of (anchor, seg_record), de-duplicated by seg id, first-label-wins
    chosen_ids = set()

    def add(anchor, seg_record):
        sid = seg_record["seg"]
        if sid not in chosen_ids:
            chosen.append((anchor, seg_record))
            chosen_ids.add(sid)

    if n > 0:
        add("first", body_segs[0])
        add("middle", body_segs[n // 2])
        add("late", body_segs[-1])

        remaining = [s for s in body_segs if s["seg"] not in chosen_ids]
        if remaining:
            # Highest density; break ties by ascending seg id. Sentinels are
            # stripped (via segment_clean_text) before scoring so a
            # sentinel-heavy segment can't win selection purely by inflating
            # its upper-initial ratio.
            best = min(
                remaining,
                key=lambda s: (-density_score(segment_clean_text(s, blocks), lang), s["seg"]),
            )
            add("high_density", best)

    # order_index order for the body anchors, per
    # references/language-pair-parameterization.md
    chosen.sort(key=lambda pair: segment_order_index(pair[1], blocks))

    segments_used = []
    body_pieces = []
    for anchor, seg_record in chosen:
        segments_used.append({
            "segment_id": seg_record["seg"],
            "anchor": anchor,
            "kind": "body",
        })
        # Sentinels are stripped (via segment_clean_text) BEFORE cap_words()
        # -- a sentinel occupying a "word" slot in the raw text would
        # otherwise consume part of the SAMPLE_WORD_CAP budget before ever
        # being stripped, silently dropping a legitimate word/name sitting
        # just past the cap.
        body_pieces.append(cap_words(segment_clean_text(seg_record, blocks)))

    # Fifth anchor: every translate-decision FRONTBACK segment, concatenated
    # as ONE bucket, capped once as a whole (front matter is short/discrete,
    # not a narrative arc to sample a position from).
    frontback_entries = manifest.get("frontback", [])
    if not isinstance(frontback_entries, list):
        fatal("manifest.json: 'frontback' must be an array")
    translate_ids = {e["id"] for e in frontback_entries if e.get("decision") == "translate"}

    frontback_segs = [
        s for s in segments if s.get("kind") == "frontback" and s.get("seg") in translate_ids
    ]
    frontback_segs.sort(key=lambda s: segment_order_index(s, blocks))

    frontback_pieces = []
    for seg_record in frontback_segs:
        segments_used.append({
            "segment_id": seg_record["seg"],
            "anchor": "frontback",
            "kind": "frontback",
        })
        # Same ordering rule as body_pieces above: strip before this bucket
        # is later capped as a whole (line below, "cap_words(...)").
        frontback_pieces.append(segment_clean_text(seg_record, blocks))

    if not chosen and not frontback_segs:
        fatal(
            "manifest.json has no body segments and no translate-decision "
            "frontback segments -- nothing to build a smoke-test sample from"
        )

    # extraction_pieces keeps each anchor's text SEPARATE (never concatenated
    # across anchors) for candidate extraction -- the real historiettes-t3
    # bootstrap_names.py scans block-by-block, never across an arbitrary
    # join of non-adjacent excerpts, and the run-continuation loop below has
    # no sentence-boundary check of its own (faithfully ported from the
    # source), so concatenating unrelated excerpts before extraction would
    # fabricate spurious cross-excerpt candidate runs that could never occur
    # in the real per-block scan. The single concatenated+normalized blob
    # below is used ONLY for source_sample_sha1/word_count, never for
    # candidate extraction.
    extraction_pieces = list(body_pieces)
    if frontback_pieces:
        extraction_pieces.append(cap_words("\n\n".join(frontback_pieces)))

    normalized_sample = collapse_whitespace("\n\n".join(extraction_pieces))

    selection = {
        "method": "stratified",
        "segments_used": segments_used,
        "word_count": len(normalized_sample.split()),
    }
    return normalized_sample, selection, extraction_pieces


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def parse_checked_names(cli_value):
    if not cli_value:
        return []
    seen = set()
    out = []
    for n in cli_value.split(","):
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def load_elision_test_file(path):
    data = load_json_file(path, "elision-test-file")
    if not isinstance(data, list) or not data:
        fatal(f"elision-test-file at {path} must be a non-empty JSON array")
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("sentence"), str)
            or not isinstance(item.get("expected_names"), list)
            or not all(isinstance(x, str) for x in item.get("expected_names", []))
        ):
            fatal(
                f"elision-test-file at {path}: every entry must be "
                '{"sentence": string, "expected_names": [string, ...]}'
            )
    return data


def load_particle_smoke_file(path):
    data = load_json_file(path, "particle-smoke-file")
    if not isinstance(data, list) or not data:
        fatal(f"particle-smoke-file at {path} must be a non-empty JSON array")
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("token"), str)
            or not isinstance(item.get("is_particle"), bool)
        ):
            fatal(
                f"particle-smoke-file at {path}: every entry must be "
                '{"token": string, "is_particle": boolean}'
            )
    return data


def main():
    parser = argparse.ArgumentParser(
        prog="language_smoke_report.py",
        description=(
            "Run the mandatory TRIPLE-keyed language-pair smoke test "
            "(particle_config content + this project's own extracted-"
            "source-text sample + this script's own version) and write "
            "language-smoke-report.json. See "
            "references/language-pair-parameterization.md for the full "
            "procedure."
        ),
    )
    parser.add_argument(
        "--profile",
        default=str(DEFAULT_PROFILE_PATH),
        help=(
            "Path to profile.yml, read only if --particle-config and/or "
            "--report-path are not both given explicitly "
            f"(default: {DEFAULT_PROFILE_PATH}, relative to cwd)."
        ),
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST_PATH}).",
    )
    parser.add_argument(
        "--particle-config",
        default=None,
        help=(
            "Bare filename resolved under "
            f"{LANGUAGES_DIR}, or an explicit path. Default: read from "
            "profile.yml's source.language.particle_config."
        ),
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help=(
            "Where to write the report. Default: read from profile.yml's "
            "source.language.smoke_test.report_path (null -> "
            f"{DEFAULT_REPORT_PATH})."
        ),
    )
    parser.add_argument(
        "--checked-names",
        default="",
        help="Comma-separated hand-picked names (>=10, or per density branches).",
    )
    parser.add_argument(
        "--elision-test-file",
        default=None,
        help="JSON array of {sentence, expected_names[]}. Required iff has_elision.",
    )
    parser.add_argument(
        "--particle-smoke-file",
        default=None,
        help="JSON array of {token, is_particle}. Required iff particle_list_size > 0.",
    )
    parser.add_argument("--low-name-density-confirmed", action="store_true")
    parser.add_argument("--no-names-confirmed", action="store_true")
    parser.add_argument("--no-particles-confirmed", action="store_true")
    args = parser.parse_args()

    profile_path = Path(args.profile)

    manifest_path = Path(args.manifest).resolve() if args.manifest else DEFAULT_MANIFEST_PATH
    particle_config_path = resolve_particle_config_path(args.particle_config, profile_path)
    report_path = resolve_report_path(args.report_path, profile_path)

    lang = load_particle_config(particle_config_path)
    particle_config_sha1 = sha1_bytes(lang["raw_bytes"])

    manifest = load_json_file(manifest_path, "manifest.json")
    if not isinstance(manifest, dict):
        fatal(f"manifest.json at {manifest_path} must be a JSON object")

    sample_text, selection, extraction_pieces = build_source_sample(manifest, lang)
    source_sample_sha1 = sha1_bytes(sample_text.encode("utf-8"))
    smoke_report_contract_hash = sha1_file(THIS_SCRIPT_PATH)

    # Extracted per-piece (never across the concatenated blob) -- see the
    # comment in build_source_sample() for why. extraction_pieces are already
    # sentinel-free by construction (stripped upstream of cap_words() in
    # build_source_sample()) -- this second SENTINEL_RE.sub() is deliberate
    # defense-in-depth, not the primary fix.
    candidate_name_set = set()
    for piece in extraction_pieces:
        clean_piece = SENTINEL_RE.sub(" ", piece)
        for name, _ in extract_candidate_names(clean_piece, lang):
            candidate_name_set.add(name)
    candidate_names_total = len(candidate_name_set)

    checked_names = parse_checked_names(args.checked_names)

    # --- name-density branch -------------------------------------------------
    if candidate_names_total == 0:
        if not (args.low_name_density_confirmed and args.no_names_confirmed):
            fatal(
                "candidate_names_total is 0 (this sample contains no "
                "detectable proper-noun candidates at all) -- re-run with "
                "both --low-name-density-confirmed and --no-names-confirmed "
                "to acknowledge this explicitly, per the zero-candidate "
                "branch in references/language-pair-parameterization.md"
            )
        low_name_density_confirmed = True
        no_names_confirmed = True
    elif candidate_names_total < LOW_NAME_DENSITY_FLOOR:
        if args.no_names_confirmed:
            fatal(
                f"--no-names-confirmed was passed but candidate_names_total "
                f"is {candidate_names_total} (nonzero) -- that flag is "
                "reserved for the genuinely zero-candidate case"
            )
        if not args.low_name_density_confirmed:
            fatal(
                f"candidate_names_total is {candidate_names_total} (below "
                f"the {LOW_NAME_DENSITY_FLOOR}-name floor) -- re-run with "
                "--low-name-density-confirmed and supply --checked-names "
                "covering EVERY distinct candidate name -- this is a "
                "dedup-aware set-coverage requirement (duplicate entries do "
                "not count), not a bare entry count"
            )
        uncovered = sorted(candidate_name_set - set(checked_names))
        if uncovered:
            fatal(
                "low-name-density path requires EVERY distinct candidate name to be "
                f"hand-checked (set-coverage of the {candidate_names_total} distinct "
                "candidates, dedup-aware -- duplicates do not count). "
                f"{len(uncovered)} still uncovered: {uncovered}. Supply each distinct "
                "candidate in --checked-names."
            )
        low_name_density_confirmed = True
        no_names_confirmed = False
    else:
        if args.no_names_confirmed:
            fatal(
                f"--no-names-confirmed was passed but candidate_names_total "
                f"is {candidate_names_total} (>= {LOW_NAME_DENSITY_FLOOR}) "
                "-- that flag is reserved for the genuinely zero-candidate case"
            )
        if len(checked_names) < LOW_NAME_DENSITY_FLOOR:
            fatal(
                f"--checked-names must supply at least {LOW_NAME_DENSITY_FLOOR} "
                f"DISTINCT names (got {len(checked_names)} distinct) -- "
                "refusing to run a vacuous pass. If this source is genuinely "
                "name-sparse, re-run with --low-name-density-confirmed instead."
            )
        low_name_density_confirmed = False
        no_names_confirmed = False

    checked_names_out = [
        {"name": name, "found": name in candidate_name_set} for name in checked_names
    ]

    # --- elision branch --------------------------------------------------------
    has_elision = lang["has_elision"]
    if has_elision:
        if not args.elision_test_file:
            fatal(
                "has_elision is true in the resolved particle_config but "
                "--elision-test-file was not given (required, minItems 1)"
            )
        elision_cases_in = load_elision_test_file(Path(args.elision_test_file).resolve())
    else:
        if args.elision_test_file:
            fatal(
                "has_elision is false in the resolved particle_config but "
                "--elision-test-file was given -- this contradiction is "
                "refused rather than silently ignored"
            )
        elision_cases_in = []

    elision_test_cases = []
    for case in elision_cases_in:
        produced = {name for name, _ in extract_candidate_names(case["sentence"], lang)}
        expected = set(case["expected_names"])
        elision_test_cases.append({
            "sentence": case["sentence"],
            "expected_names": case["expected_names"],
            "passed": expected.issubset(produced),
        })

    # --- particle branch ---------------------------------------------------
    particle_list_size = len(lang["particles"])
    if particle_list_size > 0:
        if args.no_particles_confirmed:
            fatal(
                f"--no-particles-confirmed was passed but particle_list_size "
                f"is {particle_list_size} (> 0) -- that flag is reserved for "
                "a genuinely particle-free language"
            )
        if not args.particle_smoke_file:
            fatal(
                f"particle_list_size is {particle_list_size} (> 0) but "
                "--particle-smoke-file was not given (required, minItems 1) "
                "-- particle_smoke_cases is decoupled from name density and "
                "always required when the language has any particles"
            )
        particle_cases_in = load_particle_smoke_file(Path(args.particle_smoke_file).resolve())
        no_particles_confirmed = False
    else:
        if not args.no_particles_confirmed:
            fatal(
                "particle_list_size is 0 -- re-run with "
                "--no-particles-confirmed to acknowledge this is a "
                "genuinely particle-free language"
            )
        particle_cases_in = (
            load_particle_smoke_file(Path(args.particle_smoke_file).resolve())
            if args.particle_smoke_file
            else []
        )
        no_particles_confirmed = True

    particle_smoke_cases = []
    for case in particle_cases_in:
        computed = classify_is_particle(case["token"], lang)
        particle_smoke_cases.append({
            "token": case["token"],
            "is_particle": case["is_particle"],
            "passed": computed == case["is_particle"],
        })

    overall_pass = (
        all(c["found"] for c in checked_names_out)
        and all(c["passed"] for c in elision_test_cases)
        and all(c["passed"] for c in particle_smoke_cases)
    )

    report = {
        "particle_config_sha1": particle_config_sha1,
        "source_sample_sha1": source_sample_sha1,
        "smoke_report_contract_hash": smoke_report_contract_hash,
        "source_sample_selection": selection,
        "candidate_names_total": candidate_names_total,
        "checked_names": checked_names_out,
        "elision_test_cases": elision_test_cases,
        "particle_smoke_cases": particle_smoke_cases,
        "low_name_density_confirmed": low_name_density_confirmed,
        "no_names_confirmed": no_names_confirmed,
        "particle_list_size": particle_list_size,
        "no_particles_confirmed": no_particles_confirmed,
        "has_elision": has_elision,
        "pass": overall_pass,
    }

    schema_path = SCHEMAS_DIR / "language-smoke-report.schema.json"
    schema = load_json_file(schema_path, "language-smoke-report.schema.json")
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        errors = sorted(validator_cls(schema).iter_errors(report), key=str)
    except jsonschema.exceptions.SchemaError as exc:
        fatal(f"internal error: language-smoke-report.schema.json is invalid: {exc.message}")
    if errors:
        messages = "; ".join(e.message for e in errors)
        fatal(f"internal error: constructed report failed its own schema: {messages}")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.parent / f"{report_path.name}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, report_path)
    except OSError as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        fatal(f"failed writing report to {report_path}: {exc}")

    print(f"language_smoke_report.py: particle_config = {particle_config_path}")
    print(f"language_smoke_report.py: particle_config_sha1        = {particle_config_sha1}")
    print(f"language_smoke_report.py: source_sample_sha1          = {source_sample_sha1}")
    print(f"language_smoke_report.py: smoke_report_contract_hash  = {smoke_report_contract_hash}")
    print(f"language_smoke_report.py: candidate_names_total       = {candidate_names_total}")
    print(f"language_smoke_report.py: report written to           = {report_path}")

    if overall_pass:
        print("language_smoke_report.py: PASS")
        sys.exit(0)

    print("language_smoke_report.py: FAIL", file=sys.stderr)
    for c in checked_names_out:
        if not c["found"]:
            print(f"  - checked name not found among extracted candidates: {c['name']!r}", file=sys.stderr)
    for c in elision_test_cases:
        if not c["passed"]:
            print(f"  - elision test failed: {c['sentence']!r} (expected {c['expected_names']!r})", file=sys.stderr)
    for c in particle_smoke_cases:
        if not c["passed"]:
            print(
                f"  - particle-smoke case failed: token={c['token']!r} "
                f"expected is_particle={c['is_particle']!r}",
                file=sys.stderr,
            )
    print(
        "  Remediation: copy "
        f"{particle_config_path} to a project-local '<code>.local.json' "
        f"override inside {LANGUAGES_DIR}, fix the failure class there "
        "(extend PARTICLES/STOPWORDS, or fix ELISION_RE), repoint "
        f"profile.yml's source.language.particle_config at it (bare "
        "filename, e.g. 'fr.local.json'), and re-run this script against "
        "the SAME sample/checked-names/test-case files. See "
        "references/language-pair-parameterization.md, 'Failure and "
        "remediation'.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- last-resort safety net
        fatal(f"unexpected error: {exc}")
