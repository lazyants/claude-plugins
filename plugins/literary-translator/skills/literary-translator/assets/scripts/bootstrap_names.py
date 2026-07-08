#!/usr/bin/env python3
"""Deterministic proper-noun candidate extraction (language-agnostic).

No LLM. Scans a source-language text (either the whole project's
``manifest.json``, or an ad hoc text sample) and produces a frequency-ranked
list of proper-noun / title / toponym candidates. This is the INPUT to the
later codex glossary pass, which decides the canonical target-language form
of each candidate (established vs transliterated vs "not actually a name")
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

Everything source-language-specific -- ``PARTICLES``, ``STOPWORDS``,
``ELISION_RE``, ``has_elision`` -- is read from the resolved
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

# Sentinels this plugin bakes into plain_text (footnote refs, verse
# placeholders -- see manifest.schema.json's FNREF_N / VERSE_{vid}_{shortsha}
# descriptions). Matched generically as any ⟦...⟧ bracketed token rather than
# reconstructing the exact internal shape, since a per-sentinel-kind literal
# pattern would be a hidden adapt point this script is not supposed to have.
SENTINEL_RE = re.compile(r"⟦[^⟧]*⟧")

# A run "closes" at one of these trailing punctuation marks; the token right
# after one of these is sentence-initial.
TERMINATORS = frozenset(".!?:»")

# Fast-path ASCII uppercase set; unicodedata's 'Lu' category is the general,
# script-agnostic fallback (Cyrillic/Greek/accented-Latin capitals alike).
# Deliberately never a hardcoded per-language accented-letter range -- that is
# what keeps is_upper_initial() (and this whole script) fully generic.
_ASCII_UPPER = frozenset(chr(c) for c in range(ord("A"), ord("Z") + 1))

# A token = one Unicode letter, then zero or more further letters or an
# internal apostrophe/hyphen (covers "d'Artagnan", "Saint-Simon", accented
# names in any script). `[^\W\d_]` is the standard "any Unicode letter"
# idiom: Python's `\w` is Unicode-aware by default for `str` patterns, and
# subtracting `\d` and `_` from it leaves exactly the letter categories.
TOKEN_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['’‑-])*")

APOSTROPHES = "'’"  # ' and the Unicode right single quote


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

    Exactly the four fields ``references/language-pair-parameterization.md``
    documents a language config file as containing -- no fifth,
    project-specific field (e.g. the real historiettes-t3 project's own
    French-only ``CONTRACTION_RE`` is deliberately NOT part of this
    contract; a language file that needs to reject elided contraction
    openers like "C'est"/"J'ai" as false-positive candidates can simply list
    their exact surface forms in its own ``STOPWORDS`` array instead -- this
    keeps the script's inputs uniform across every language, and matches
    this whole tool's recall-oriented design: the codex glossary pass prunes
    remaining false positives).
    """

    path: Path
    particles: frozenset
    stopwords: frozenset
    elision_re: Optional["re.Pattern"]
    has_elision: bool
    raw_bytes: bytes


def load_language_config(particle_config_filename: str,
                          languages_dir: Path = LANGUAGES_DIR) -> LanguageConfig:
    """Resolve ``particle_config_filename`` under ``languages_dir`` and load
    ``PARTICLES``/``STOPWORDS``/``ELISION_RE``/``has_elision`` from it.

    ``particle_config_filename`` MUST be the profile's own
    ``source.language.particle_config`` LITERAL value (a bare filename) --
    never reconstructed from ``source.language.code``. That is what lets a
    project-local override such as ``fr.local.json`` actually take effect.

    Enforces the documented four-field contract exactly --
    ``PARTICLES``/``STOPWORDS`` must each be present as a JSON array of
    strings, ``has_elision`` must be present as a JSON boolean (no
    coercion), and ``ELISION_RE`` must be a non-empty, 2-capture-group
    string when ``has_elision`` is true (a plain string or ``null``
    otherwise). Any violation raises :class:`BootstrapNamesError` naming the
    exact malformed/missing field -- never a silently-coerced or
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

    return LanguageConfig(
        path=path,
        particles=particles,
        stopwords=stopwords,
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=raw_bytes,
    )


def tokenize(text: str, elision_re: Optional["re.Pattern"]):
    """Split ``text`` into ``(token, preceding_char)`` pairs.

    ``preceding_char`` is the last non-whitespace character before the
    token (or ``"."`` at the very start of the text, treated as a sentence
    boundary) -- used to decide whether a token is sentence-initial.

    When ``elision_re`` matches a raw token (e.g. French "d'Effiat"), it is
    split into two tokens: the elided article's own remnant (group 1, e.g.
    "d") and the name-initial remainder (group 2, e.g. "Effiat"). Without
    this split, the fused token's lowercase first character would defeat
    ``is_upper_initial()`` and the name behind the elision would be silently
    and totally dropped -- see references/gotchas.md's
    french-elision-tokenizer-miss lesson.
    """
    tokens = []
    for m in TOKEN_RE.finditer(text):
        start = m.start()
        j = start - 1
        while j >= 0 and text[j] in " \t\n":
            j -= 1
        preceding = text[j] if j >= 0 else "."
        raw = m.group(0)
        elided = elision_re.match(raw) if elision_re else None
        if elided:
            tokens.append((elided.group(1), preceding))
            tokens.append((elided.group(2), "'"))
        else:
            tokens.append((raw, preceding))
    return tokens


def extract_candidates(text: str, lang: LanguageConfig):
    """Yield ``(name, mid_sentence: bool)`` for each proper-noun run found
    in ``text``.

    ``text`` should already have this plugin's own ``⟦...⟧`` sentinels
    stripped (``SENTINEL_RE.sub(" ", text)``) -- callers that scan
    ``manifest.json`` blocks do this via ``collect_candidates()``; a caller
    handing in an already-clean text sample (e.g. the language smoke test's
    stratified sample) may skip that step if it has none to strip.
    """
    tokens = tokenize(text, lang.elision_re)
    n = len(tokens)
    out = []
    idx = 0
    while idx < n:
        tok, preceding = tokens[idx]
        if not is_upper_initial(tok) or tok in lang.stopwords:
            idx += 1
            continue
        sentence_initial = preceding in TERMINATORS
        run = [tok]
        k = idx + 1
        while k < n:
            t2, preceding2 = tokens[k]
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
                run.append(t2)
                k += 1
            elif is_particle(t2, lang) and k + 1 < n and is_upper_initial(tokens[k + 1][0]):
                run.append(t2)
                run.append(tokens[k + 1][0])
                k += 2
            else:
                break
        name = " ".join(run)
        out.append((name, not sentence_initial))
        idx = k
    return out


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
    ``multiword``/``abbrev``/``n_segments``/``likely_name``.
    """
    freq = defaultdict(int)
    mid = defaultdict(int)
    by_source = defaultdict(set)

    for source_id, text in sources:
        clean = SENTINEL_RE.sub(" ", text)
        for name, midsent in extract_candidates(clean, lang):
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
