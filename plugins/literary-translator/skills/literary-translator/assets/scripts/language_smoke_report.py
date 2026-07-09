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
`particle_config`'s documented contract is exactly four fields --
PARTICLES, STOPWORDS, ELISION_RE, has_elision (see
references/language-pair-parameterization.md) -- and a fifth,
language-specific regex field is out of scope for this generalization.
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

# Generalized tokenizer: first char = any Unicode letter (not digit/underscore),
# followed by letters, apostrophe/right-single-quote, or a hyphen -- makes no
# assumption about which alphabet the source language uses (Latin-accented,
# Cyrillic, Greek, ...). Whether a given alphabet actually WORKS is exactly
# what this smoke test exists to establish -- this regex only makes it
# plausible (see references/language-pair-parameterization.md).
TOKEN_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['’‑-])*")
# — (U+2014 em-dash) and ― (U+2015 horizontal bar) are included because they
# are the dominant dialogue-line delimiter in French/Russian/Spanish literary
# prose -- this plugin's core domain -- not just a stylistic aside.
TERMINATORS = frozenset(".!?:;»\"”…—―")
_APOSTROPHES = "'’"


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


# ---------------------------------------------------------------------------
# particle_config loading + validation (the four documented fields, exactly)
# ---------------------------------------------------------------------------
def load_particle_config(path):
    raw_bytes = path.read_bytes()
    try:
        config = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fatal(f"particle_config at {path} is not valid UTF-8 JSON: {exc}")
    if not isinstance(config, dict):
        fatal(f"particle_config at {path} must be a JSON object")

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

    return {
        "raw_bytes": raw_bytes,
        "particles": particles,
        "particles_lower": {p.lower() for p in particles},
        "stopwords": set(stopwords),
        "has_elision": has_elision,
        "elision_re": elision_re,
    }


# ---------------------------------------------------------------------------
# Extraction algorithm -- generalized re-implementation of the run-building
# core of historiettes-t3's bootstrap_names.py, parameterized entirely by
# the four particle_config fields (no per-language literals here).
# ---------------------------------------------------------------------------
def extract_candidate_names(text, lang):
    particles_lower = lang["particles_lower"]
    stopwords = lang["stopwords"]
    has_elision = lang["has_elision"]
    elision_re = lang["elision_re"]

    tokens = []
    for m in TOKEN_RE.finditer(text):
        raw = m.group(0)
        start = m.start()
        j = start - 1
        while j >= 0 and text[j] in " \t\n":
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

    n = len(tokens)
    idx = 0
    out = []
    while idx < n:
        tok, preceding = tokens[idx]
        if not is_upper_initial(tok) or tok in stopwords:
            idx += 1
            continue
        sentence_initial = preceding in TERMINATORS
        run = [tok]
        k = idx + 1
        while k < n:
            t2, preceding2 = tokens[k]
            if preceding2 in TERMINATORS:
                # t2 is itself sentence-initial (a '.', '!', '?', ':', '»' or
                # a dialogue dash sits between the run so far and t2) -- the
                # run is stopped at any TERMINATORS boundary so two unrelated
                # proper nouns in adjacent sentences don't fuse into one
                # bogus multiword candidate (e.g. "Fiona. George arrived
                # quietly." must NOT become "Fiona George"). KNOWN LIMITATION
                # (a follow-up): a boundary masked by an intervening closing
                # quote/bracket (e.g. "Fiona.' George") is missed, because
                # the back-scan stops at the quote/bracket rather than the
                # terminator behind it. t2 is re-examined as its own run
                # start by the outer loop.
                break
            low = t2.lower().rstrip(_APOSTROPHES)
            if is_upper_initial(t2) and t2 not in stopwords:
                run.append(t2)
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
                run.append(t2)
                run.append(tokens[k + 1][0])
                k += 2
            else:
                break
        name = " ".join(run)
        out.append((name, not sentence_initial))
        idx = k
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
            # Highest density; break ties by ascending seg id.
            best = min(
                remaining,
                key=lambda s: (-density_score(segment_plain_text(s, blocks), lang), s["seg"]),
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
        body_pieces.append(cap_words(segment_plain_text(seg_record, blocks)))

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
        frontback_pieces.append(segment_plain_text(seg_record, blocks))

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
    return [n.strip() for n in cli_value.split(",") if n.strip()]


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
    # comment in build_source_sample() for why.
    candidate_name_set = set()
    for piece in extraction_pieces:
        for name, _ in extract_candidate_names(piece, lang):
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
                "covering EVERY candidate the tool found"
            )
        if len(checked_names) != candidate_names_total:
            fatal(
                f"low-name-density path requires --checked-names to list "
                f"EXACTLY the {candidate_names_total} candidate(s) found "
                f"(got {len(checked_names)}) -- completeness is enforced by "
                "count, not just 'at least some'"
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
                f"names (got {len(checked_names)}) -- refusing to run a "
                "vacuous pass. If this source is genuinely name-sparse, "
                "re-run with --low-name-density-confirmed instead."
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
