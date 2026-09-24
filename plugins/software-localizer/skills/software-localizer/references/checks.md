# What scripts check, and what the review judges

## Script checks (`checks.py`) — exact, on every candidate value

They work on the adapter's `parse` tokens, so they know nothing about any one message syntax.

| check | a value fails when |
| --- | --- |
| `parse` | any form does not parse in the project's syntax |
| `forms` | a plural value has a different number of forms than the target locale's labels, or a non-plural value is not a string |
| `arguments` | non-plural: the multiset of argument signatures differs from the source's. Plural: a form uses an argument the source never uses (or with a signature the source never uses), or lacks an argument of the source's general form — the count arguments count as one, and only a form whose label is `exact` may omit the count |
| `structure` | non-plural: the ordered structure tokens differ from the source's. Plural: a form's structure tokens differ from the source's general form |
| `whitespace` | a form's leading or trailing whitespace differs from the source's (suffix-style messages depend on it) |
| `empty` | a form is empty or only whitespace |
| `identical` | the value equals the source — unless the id is in `allow_identical`, or the source is a do-not-translate canon entry |
| `max_length` | a form is longer than the message's `max_length` |
| `dnt` | a do-not-translate canon entry listed for this message does not appear verbatim in every form |

Why the singular form is not the model for plural parity: in Russian the `one` form also covers
21, 31, 101…, so "один файл" without the number would be wrong there. The project's plural
rule, through the adapter's `exact` flag, decides which forms may drop the count.

## The review turn (Claude) — what scripts cannot see

Meaning in context; whether approved canon terms and UI labels are used (in any correct
inflection — an exact-match script would both miss violations and flag correct text); formality
and house style; fit for the UI element; consistency with sibling messages; correctness of each
plural form for the counts its label covers.

A review verdict is bound to the sha256 of the exact value reviewed. A verdict for another
hash is ignored; a value changed by a fix is reviewed again.

## The audit turn

The same judgement over the translation the project ships today. Findings carry a proposed
replacement; proposals are checked by script at once but applied only when a person accepts
them (`packets.py accept-audit`), and each accepted proposal still gets its own review verdict
before export.
