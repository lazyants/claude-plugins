# What scripts check, and what the review judges

## Script checks (`checks.py`) — exact, on every candidate value

They work on the adapter's `parse` tokens, so they know nothing about any one message syntax.

| check | a value fails when |
| --- | --- |
| `parse` | any form does not parse in the project's syntax |
| `forms` | a plural value has a different number of forms than the target locale's labels, or a non-plural value is not a string |
| `arguments` | non-plural: the multiset of argument signatures differs from the source's. Plural: a form uses an argument the source never uses (or with a signature the source never uses), or lacks an argument of the source's general form — an `exact` target form may mirror omissions in the parsed source form with the same label; without a match, general-form requirements remain. The count arguments count as one, and only a form whose label is `exact` may omit the count |
| `structure` | non-plural: the ordered structure tokens differ from the source's. Plural: a form's structure tokens differ from the source's general form |
| `whitespace` | a form's leading or trailing whitespace differs from the source's (suffix-style messages depend on it) |
| `empty` | a form is empty or only whitespace |
| `identical` | the value equals the source — unless the id is in `allow_identical`, or the source is a do-not-translate canon entry |
| `max_length` | a form is longer than the message's `max_length` |
| `dnt` | a do-not-translate canon entry listed for this message does not appear verbatim in every form |

Why the singular form is not the model for plural parity: in Russian the `one` form also covers
21, 31, 101…, so "один файл" without the number would be wrong there. The project's plural
rule, through the adapter's `exact` flag, decides which forms may drop the count.

Boundary whitespace is an exact source contract, including for an accepted adapter. If a
locale needs a different boundary to compose a sentence correctly, follow the
[fragment-boundary decision route](fragment-boundaries.md): preserve the rejection and escalate
to a coordinated caller/catalog change. The current translation path has no whitespace override.

## When a locale needs to repeat a named argument

Non-plural messages require the same **number of occurrences of each signature** as the
source. Reusing one named runtime value can be valid in the project's renderer and still fail
this check. The current contract has no per-message or per-locale multiplicity waiver;
adapter acceptance, an operator's audit acceptance and a model review do not grant one.

For example, suppose the caller supplies one numeric `n >= 2`, the number of great
generations for both reciprocal relatives:

```text
source:   {n}× Great-grandparent or Great-grandchild
current:  Ur×{n}-Großelternteil oder Urenkelkind
proposed: Ur×{n}-Großelternteil oder Ur×{n}-Enkelkind
```

The current target applies the multiplier only to the ancestor. Repeating it for the
descendant corrects that depth mismatch, but has two `{n}` signatures against the source's
one, so the proposal remains blocked with `arguments: extra ['{n}']`. This example does not
approve the German wording as a whole. Successful parsing or rendering proves neither the
intended relationship depth nor linguistic suitability.

**Escalate to the project owner for a reviewed caller/source-catalog rewrite:**

1. Record the message id, locale, exact source/current/proposed values and parsed signatures,
   the failed core check, the caller's actual argument meaning and supported values, and the
   reason repetition is needed. Preserve the failed proposal and its check output as evidence;
   a stored audit finding is not an accepted candidate.
2. Have the project owner and a reviewer competent in the locale judge a concrete rewrite
   against the caller and UI. One option, if both roles use the same depth, is to make both
   occurrences explicit in the source:
   `{n}× Great-grandparent or {n}× Great-grandchild`. A named-value renderer may still need only
   one supplied `n`; verify that in the actual project with representative supported values
   (here, at least `n=2` and `n=3`) and check the meaning of both roles. Review every affected
   locale against the rewritten source: a target with only one `{n}` now fails multiplicity.
   If the source cannot express the intended use safely, redesign the caller/messages with
   the owner. Record who approved which exact change and why through normal project review.
3. After that project change is approved, run `collect.py` and `ledger.py sync` again. Build
   fresh audit/translation and review packets against the new source; old proposals and
   verdicts do not authorize it. Existing and human-locked translations still need explicit
   adoption or audit acceptance. Follow the [project checks](project-checks.md) before audit
   acceptance and live export, as well as all core checks and review of the exact value.
   Any adapter change also needs `adapter_check.py run` + `accept` again.
4. If the owner declines the rewrite, a reviewer rejects it, or caller/runtime evidence is
   unavailable, leave the proposal blocked and report the unresolved message. Do not promote
   or export it. A pending translation that exhausts `max_rounds` follows the normal
   `escalated` path; existing project text stays in place pending an approved project fix.

Do not deduplicate parser tokens, hide or rename an extra argument, change its formatter,
misclassify a non-plural message as plural, or edit the ledger/check results to make this pass.
`allow_identical` only covers the `identical` check. A future adapter/per-message exception
would require an explicitly reviewed core contract change; none is enabled by this procedure.

## Canon coherence before freeze and packet construction

For a non-plural collected message whose complete source equals an approved UI-label
source, the approved label value must satisfy every approved DNT entry explicitly naming
that occurrence. `canon.py freeze` refuses before replacing the lock if it does not;
loading an existing contradictory lock also refuses. The diagnostic identifies the
locale, UI-label entry, DNT entry, occurrence, approved value and failed DNT constraint.
Approved UI labels and nonempty active DNT scopes require a collected `messages.json` snapshot.
Recollect after source changes; occurrences absent from that snapshot are no longer
current messages.

The owner reconciles wording with `canon.py change`, or reviews an exact `change-scope`
delta bound to the entry hash returned by `scope`. Pending DNT occurrences are not active
constraints. A proposed wording or scope delta that introduces a conflict is refused
without changing canon, history or lock. Valid repairs can be recorded sequentially:
if other conflicts remain,
`change` records the approved wording and audit request but returns `frozen: false` with
the remaining conflicts and leaves the lock unchanged. The final coherent repair
refreshes the lock. A conflicting lock still blocks packet builds, ledger sync and
export until reconciled. Scope repairs follow the same rule: a valid partial repair is
recorded with its audit requests, remaining conflicts keep the old lock, and the final
coherent repair refreezes. Scripts do not append a brand or waive DNT.

Shared-note corrections require a coherent canon and refuse before changing notes,
history or evidence when a whole-label/DNT conflict remains.

A label mentioned inside a longer message is not compared as if
it were the complete translation: the surrounding translation may supply the brand.
Plural messages remain subject to the per-candidate checks above, without this
whole-label inference. Meaning, inflection and other canon usage remain review work.

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

The existing value also receives every applicable script check independently of
that judgement. Packets and durable audit records keep `current_checks` separate
from the model verdict and `proposal_checks`. Reports preserve the original
observations even if the model passes or offers a mechanically clean replacement.
Parser failures mean unavailable syntax coverage; parser-independent checks still
run. Identity, fragment composition and legacy DNT scope conflicts remain owner
holds for contextual adjudication, with no automatic rewrite or waiver.
