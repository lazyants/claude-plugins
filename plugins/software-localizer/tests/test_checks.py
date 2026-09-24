"""Tests for `checks.py` (plan section 9): every check, pass and fail.

`checks.check_candidate` is pure, so these tests build small parse-result
fixtures directly instead of running a real adapter.
"""

from __future__ import annotations

from checks import check_candidate, forms_of

DEFAULT_CFG = {"allow_identical": []}
EMPTY_CANON = {"entries": []}


def make_message(msg_id, source, max_length=None):
    return {"id": msg_id, "source": source, "context": {"max_length": max_length}}


def make_plural_message(msg_id, source_forms, target_labels, general_index, count_arguments=(), max_length=None, locale="xx"):
    return {
        "id": msg_id,
        "source": {"forms": list(source_forms)},
        "plural": {
            "source_labels": [{"label": str(i), "exact": False} for i in range(len(source_forms))],
            "target_labels": {locale: target_labels},
            "count_arguments": list(count_arguments),
            "general_index": general_index,
        },
        "context": {"max_length": max_length},
    }


def ok(tokens=None):
    return {"ok": True, "tokens": list(tokens or [])}


def bad(error="parse error"):
    return {"ok": False, "error": error}


def arg(name, signature=None):
    return {"kind": "argument", "name": name, "signature": signature or "{" + name + "}"}


def struct(text):
    return {"kind": "structure", "text": text}


def by_check(problems, check):
    return [p for p in problems if p["check"] == check]


# --- forms_of -----------------------------------------------------------

def test_forms_of_string():
    assert forms_of("hi") == ["hi"]


def test_forms_of_forms_dict():
    assert forms_of({"forms": ["a", "b"]}) == ["a", "b"]


# --- parse ----------------------------------------------------------------

def test_parse_nonplural_pass():
    message = make_message("m1", "Hi")
    problems = check_candidate(message, "de", "Salut", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "parse") == []


def test_parse_nonplural_fail():
    message = make_message("m1", "Hi")
    problems = check_candidate(message, "de", "Salut???", ok(), bad("unexpected '?'"), EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "parse")) == 1


def test_parse_plural_fail_on_one_form():
    message = make_plural_message("m1", ["a", "b"], [{"label": "one", "exact": False}, {"label": "other", "exact": False}], 1)
    source_parse = [ok(), ok()]
    value_parse = [bad("bad"), ok()]
    problems = check_candidate(message, "xx", {"forms": ["x", "y"]}, source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "parse")) == 1


# --- forms ------------------------------------------------------------------

def test_forms_nonplural_wrong_type_is_refused():
    message = make_message("m1", "Hi")
    problems = check_candidate(message, "de", {"forms": ["a"]}, ok(), [ok()], EMPTY_CANON, DEFAULT_CFG)
    assert len(problems) == 1
    assert problems[0]["check"] == "forms"


def test_forms_plural_count_mismatch():
    message = make_plural_message("m1", ["a", "b"], [{"label": "one", "exact": False}, {"label": "other", "exact": False}], 1)
    source_parse = [ok(), ok()]
    value_parse = [ok()]
    problems = check_candidate(message, "xx", {"forms": ["x"]}, source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "forms")) == 1


def test_forms_plural_pass_when_count_matches():
    message = make_plural_message("m1", ["a", "b"], [{"label": "one", "exact": False}, {"label": "other", "exact": False}], 1)
    source_parse = [ok(), ok()]
    value_parse = [ok(), ok()]
    problems = check_candidate(message, "xx", {"forms": ["x", "y"]}, source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "forms") == []


# --- arguments (non-plural) --------------------------------------------------

def test_arguments_nonplural_pass():
    message = make_message("m1", "Pay {price}")
    source_parse = ok([arg("price", "{price, currency}")])
    value_parse = ok([arg("price", "{price, currency}")])
    problems = check_candidate(message, "de", "Zahl {price}", source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "arguments") == []


def test_arguments_nonplural_fail_missing():
    message = make_message("m1", "Pay {price}")
    source_parse = ok([arg("price")])
    value_parse = ok([])
    problems = check_candidate(message, "de", "Zahl jetzt", source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "arguments")) == 1


def test_arguments_nonplural_fail_extra():
    message = make_message("m1", "Pay now")
    source_parse = ok([])
    value_parse = ok([arg("price")])
    problems = check_candidate(message, "de", "Zahl {price} jetzt", source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "arguments")) == 1


def test_arguments_nonplural_fail_different_signature():
    message = make_message("m1", "Pay {price}")
    source_parse = ok([arg("price", "{price, number}")])
    value_parse = ok([arg("price", "{price, currency}")])
    problems = check_candidate(message, "de", "Zahl {price}", source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "arguments")) == 1


# --- arguments (plural): count_arguments as one group, exact/non-exact -----

# The inbox.count example from the plan (`adapter-contract.md` / plan section 4):
# source forms "No messages" / "{count} message" / "{count} messages", general_index 2.
INBOX_SOURCE_FORMS = ["No messages", "{count} message", "{count} messages"]
INBOX_SOURCE_PARSE = [
    ok([]),
    ok([arg("count")]),
    ok([arg("count")]),
]


def test_arguments_plural_exact_form_may_omit_count():
    # de: zero/one are exact, other is not; the exact "zero" form omits count
    # entirely (allowed) and still passes.
    message = make_plural_message(
        "m1", INBOX_SOURCE_FORMS,
        [{"label": "zero", "exact": True}, {"label": "one", "exact": True}, {"label": "other", "exact": False}],
        general_index=2, count_arguments=["count", "n"],
    )
    value = {"forms": ["Keine Nachrichten", "{count} Nachricht", "{count} Nachrichten"]}
    value_parse = [ok([]), ok([arg("count")]), ok([arg("count")])]
    problems = check_candidate(message, "xx", value, INBOX_SOURCE_PARSE, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "arguments") == []


def test_arguments_plural_nonexact_form_omitting_count_is_refused():
    # ru: "one" is NOT exact (21, 31, 101... also use it) and must carry the
    # count argument; omitting it is a defect.
    message = make_plural_message(
        "m1", INBOX_SOURCE_FORMS,
        [
            {"label": "zero", "exact": True}, {"label": "one", "exact": False},
            {"label": "few", "exact": False}, {"label": "many", "exact": False},
        ],
        general_index=2, count_arguments=["count", "n"],
    )
    value = {"forms": ["Нет сообщений", "сообщение", "{count} сообщения", "{count} сообщений"]}
    value_parse = [ok([]), ok([]), ok([arg("count")]), ok([arg("count")])]
    problems = check_candidate(message, "xx", value, INBOX_SOURCE_PARSE, value_parse, EMPTY_CANON, DEFAULT_CFG)
    arguments_problems = by_check(problems, "arguments")
    assert len(arguments_problems) == 1
    assert "one" in arguments_problems[0]["detail"]


def test_arguments_plural_count_and_n_are_one_group():
    # A form using "n" instead of "count" still satisfies the count-group
    # requirement, because count_arguments treats them as one name; the
    # source-forms/source-parse lists stay full length (3) since
    # general_index indexes into the SOURCE forms, not the target ones.
    message = make_plural_message(
        "m1", INBOX_SOURCE_FORMS,
        [{"label": "zero", "exact": True}, {"label": "other", "exact": False}],
        general_index=2, count_arguments=["count", "n"],
    )
    value = {"forms": ["Keine Nachrichten", "{n} Nachrichten"]}
    value_parse = [ok([]), ok([arg("n")])]
    problems = check_candidate(message, "xx", value, INBOX_SOURCE_PARSE, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "arguments") == []


def test_arguments_plural_unknown_argument_is_refused():
    message = make_plural_message(
        "m1", INBOX_SOURCE_FORMS,
        [{"label": "zero", "exact": True}, {"label": "other", "exact": False}],
        general_index=2, count_arguments=["count", "n"],
    )
    value = {"forms": ["Keine Nachrichten", "{count} Nachrichten von {sender}"]}
    value_parse = [ok([]), ok([arg("count"), arg("sender")])]
    problems = check_candidate(message, "xx", value, INBOX_SOURCE_PARSE, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "arguments")) == 1


def test_arguments_plural_missing_named_argument_is_refused():
    # Both labels exact, so the count group is never required here (isolating
    # the missing-non-count-argument case from the count-coverage case).
    source_forms = ["one item costing {price}", "{count} items costing {price}"]
    source_parse = [ok([arg("price")]), ok([arg("count"), arg("price")])]
    message = make_plural_message(
        "m1", source_forms,
        [{"label": "one", "exact": True}, {"label": "other", "exact": True}],
        general_index=1, count_arguments=["count"],
    )
    value = {"forms": ["ein Artikel kostet {price}", "{count} Artikel"]}
    value_parse = [ok([arg("price")]), ok([arg("count")])]
    problems = check_candidate(message, "xx", value, source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    problems = by_check(problems, "arguments")
    assert len(problems) == 1
    assert "other" in problems[0]["detail"] and "price" in problems[0]["detail"]


# --- structure ---------------------------------------------------------------

def test_structure_nonplural_pass():
    message = make_message("m1", "See @:common.save")
    source_parse = ok([struct("@:common.save")])
    value_parse = ok([struct("@:common.save")])
    problems = check_candidate(message, "de", "Siehe @:common.save", source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "structure") == []


def test_structure_nonplural_fail_order():
    message = make_message("m1", "@:a then @:b")
    source_parse = ok([struct("@:a"), struct("@:b")])
    value_parse = ok([struct("@:b"), struct("@:a")])
    problems = check_candidate(message, "de", "@:b dann @:a", source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "structure")) == 1


def test_structure_plural_pass():
    source_forms = ["no @:x items", "{count} @:x item"]
    source_parse = [ok([struct("@:x")]), ok([arg("count"), struct("@:x")])]
    message = make_plural_message(
        "m1", source_forms,
        [{"label": "zero", "exact": True}, {"label": "other", "exact": False}],
        general_index=1, count_arguments=["count"],
    )
    value = {"forms": ["kein @:x", "{count} @:x"]}
    value_parse = [ok([struct("@:x")]), ok([arg("count"), struct("@:x")])]
    problems = check_candidate(message, "xx", value, source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "structure") == []


def test_structure_plural_fail_changed_token():
    source_forms = ["no @:x items", "{count} @:x item"]
    source_parse = [ok([struct("@:x")]), ok([arg("count"), struct("@:x")])]
    message = make_plural_message(
        "m1", source_forms,
        [{"label": "zero", "exact": True}, {"label": "other", "exact": False}],
        general_index=1, count_arguments=["count"],
    )
    value = {"forms": ["kein @:x", "{count} @:y"]}
    value_parse = [ok([struct("@:x")]), ok([arg("count"), struct("@:y")])]
    problems = check_candidate(message, "xx", value, source_parse, value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "structure")) == 1


# --- whitespace ----------------------------------------------------------------

def test_whitespace_nonplural_pass():
    message = make_message("m1", "  Hello")
    problems = check_candidate(message, "de", "  Salut", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "whitespace") == []


def test_whitespace_nonplural_fail_dropped_leading_space():
    message = make_message("m1", "  Hello")
    problems = check_candidate(message, "de", "Salut", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "whitespace")) == 1


def test_whitespace_plural_pass():
    source_forms = ["no items", "  many items  "]
    message = make_plural_message(
        "m1", source_forms,
        [{"label": "zero", "exact": False}, {"label": "other", "exact": False}],
        general_index=1,
    )
    value = {"forms": ["  a  ", "  b  "]}
    value_parse = [ok(), ok()]
    problems = check_candidate(message, "xx", value, [ok(), ok()], value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "whitespace") == []


def test_whitespace_plural_fail_dropped_leading_space():
    source_forms = ["no items", "  many items  "]
    message = make_plural_message(
        "m1", source_forms,
        [{"label": "zero", "exact": False}, {"label": "other", "exact": False}],
        general_index=1,
    )
    value = {"forms": ["a  ", "  b  "]}
    value_parse = [ok(), ok()]
    problems = check_candidate(message, "xx", value, [ok(), ok()], value_parse, EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "whitespace")) == 1


# --- empty -----------------------------------------------------------------

def test_empty_pass():
    message = make_message("m1", "Hi")
    problems = check_candidate(message, "de", "Salut", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "empty") == []


def test_empty_fail_whitespace_only():
    message = make_message("m1", "Hi")
    problems = check_candidate(message, "de", "   ", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "empty")) == 1


# --- identical ----------------------------------------------------------------

def test_identical_pass_when_different():
    message = make_message("m1", "Cart")
    problems = check_candidate(message, "de", "Warenkorb", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "identical") == []


def test_identical_fail_when_equal():
    message = make_message("m1", "Cart")
    problems = check_candidate(message, "de", "Cart", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "identical")) == 1


def test_identical_exempt_via_allow_identical():
    message = make_message("m1", "OK")
    cfg = {"allow_identical": ["m1"]}
    problems = check_candidate(message, "de", "OK", ok(), ok(), EMPTY_CANON, cfg)
    assert by_check(problems, "identical") == []


def test_identical_exempt_via_dnt_source():
    message = make_message("m2", "iPhone")
    canon_lock = {"entries": [{"id": "t1", "kind": "dnt", "source": "iPhone", "occurrences": []}]}
    problems = check_candidate(message, "de", "iPhone", ok(), ok(), canon_lock, DEFAULT_CFG)
    assert by_check(problems, "identical") == []


# --- max_length ----------------------------------------------------------------

def test_max_length_pass():
    message = make_message("m1", "Hi", max_length=10)
    problems = check_candidate(message, "de", "Salut", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "max_length") == []


def test_max_length_fail():
    message = make_message("m1", "Hi", max_length=3)
    problems = check_candidate(message, "de", "Salutations", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert len(by_check(problems, "max_length")) == 1


def test_max_length_unset_never_checked():
    message = make_message("m1", "Hi")
    problems = check_candidate(message, "de", "A rather long translation for a short source", ok(), ok(), EMPTY_CANON, DEFAULT_CFG)
    assert by_check(problems, "max_length") == []


# --- dnt -------------------------------------------------------------------

def test_dnt_pass_term_present_verbatim():
    message = make_message("m1", "Buy Cart now")
    canon_lock = {"entries": [{"id": "t1", "kind": "dnt", "source": "Cart", "occurrences": ["m1"]}]}
    problems = check_candidate(message, "de", "Kaufe Cart jetzt", ok(), ok(), canon_lock, DEFAULT_CFG)
    assert by_check(problems, "dnt") == []


def test_dnt_fail_term_missing():
    message = make_message("m1", "Buy Cart now")
    canon_lock = {"entries": [{"id": "t1", "kind": "dnt", "source": "Cart", "occurrences": ["m1"]}]}
    problems = check_candidate(message, "de", "Kaufe jetzt", ok(), ok(), canon_lock, DEFAULT_CFG)
    assert len(by_check(problems, "dnt")) == 1


def test_dnt_ignores_entry_not_listing_this_message():
    message = make_message("m1", "Buy now")
    canon_lock = {"entries": [{"id": "t1", "kind": "dnt", "source": "Cart", "occurrences": ["other_id"]}]}
    problems = check_candidate(message, "de", "Kaufe jetzt", ok(), ok(), canon_lock, DEFAULT_CFG)
    assert by_check(problems, "dnt") == []


# --- full pass ---------------------------------------------------------------

def test_full_pass_returns_no_problems():
    source_forms = ["No messages", "{count} message", "{count} messages"]
    source_parse = [ok([]), ok([arg("count")]), ok([arg("count")])]
    message = make_plural_message(
        "app:inbox.count", source_forms,
        [{"label": "zero", "exact": True}, {"label": "one", "exact": True}, {"label": "other", "exact": False}],
        general_index=2, count_arguments=["count", "n"], max_length=None,
    )
    value = {"forms": ["Keine Nachrichten", "{count} Nachricht", "{count} Nachrichten"]}
    value_parse = [ok([]), ok([arg("count")]), ok([arg("count")])]
    canon_lock = {"entries": [{"id": "t1", "kind": "dnt", "source": "Unrelated", "occurrences": []}]}
    problems = check_candidate(message, "xx", value, source_parse, value_parse, canon_lock, DEFAULT_CFG)
    assert problems == []
