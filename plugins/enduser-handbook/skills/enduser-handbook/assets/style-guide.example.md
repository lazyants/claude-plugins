# {{project_name}} handbook style guide

The `enduser-handbook` skill re-reads this file at the start of every session
([references/tone-consistency.md](../references/tone-consistency.md)) so tone, terminology,
and formatting stay consistent across chapters written months apart. `/scaffold-profile`
copies this stub to `.claude/handbook/style-guide.md` and pre-fills what the interview already
determined; fill in every remaining `TODO:` before the first real chapter ships.

## 1. Address form and register

- Register: {{language.register}} — TODO: confirm this reads naturally for
  `audience.persona`.
- Reader: {{audience.persona}} — {{audience.description}}

## 2. Sentence style

- TODO: e.g. "imperative second-person; one action per step."

## 3. UI-label rule

- TODO: e.g. "quote UI labels verbatim, in the language they appear in the running app —
  never invent and never translate a label." (This mirrors `SKILL.md` R1; restate any
  project-specific formatting convention, such as bold vs. quoted labels, here.)

## 4. Headings

- TODO: heading verb form (imperative vs. noun phrase), capitalization convention, whether a
  heading ever ends in punctuation.

## 5. Screenshots and alt text

- TODO: alt-text language and length convention; whether screenshots are cropped, annotated,
  or shown in full; masking convention for any incidental PII in frame.

## 6. Diátaxis usage

- Quadrants in use for this project: TODO (subset of tutorials, how-tos, reference,
  explanation — must match `diataxis.quadrants_in_use` in `profile.yml`).
- TODO: any project convention for mixing quadrants within one chapter (the default is: don't).

## 7. Terminology

- TODO: house terms that must always be used verbatim, and any term the project has
  deliberately retired (see [references/glossary-discipline.md](../references/glossary-discipline.md)
  for the one-canonical-term-per-concept rule this enforces).

## 8. Numbers, dates, currency

- Date format: {{language.date_format}}
- Decimal separator: {{language.decimal_separator}}
- Currency symbol: {{language.currency_symbol}}

## 9. Address-form examples

- TODO: one or two real sentences in {{language.code}} showing the register above applied to
  an actual instruction, so a future session can pattern-match tone without re-deriving it.

## 10. Do / don't

- TODO: short paired list of concrete do/don't examples (e.g. "Do: 'Select **Save**.' /
  Don't: 'You could click on the Save button if you want.'").
