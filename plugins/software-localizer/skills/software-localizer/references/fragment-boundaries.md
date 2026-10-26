# Locale-specific sentence-fragment boundaries

The current contract requires each target form's leading and trailing whitespace to match
the source exactly. There is no accepted adapter option or per-message locale override for
this rule. Adapter acceptance proves exact value preservation, not permission to change a
boundary. Context comments, a model verdict and passing project tests cannot override a core
rejection.

## Decide before accepting a replacement

1. Inspect the actual caller and the exact source and target values. Establish what separates
   the fragment from its neighbours using the project's renderer/compiler or a focused rendering
   test; source-code indentation alone does not establish a separator. Record the message id,
   locale, values, caller location, check output and rendering evidence with the audit results.
2. If a correct value can preserve the source boundary, check and review that exact value
   through the normal workflow. A matching boundary alone does not establish correct composition.
3. If the independently checked composition needs a different boundary, save the exact failed
   value and check output alongside the caller evidence in `R/project-checks/` before building
   another packet: reused run directories and candidates can be overwritten by a later attempt.
   Pause the affected message instead of sending it through another automatic fix round.
   Mark it as requiring a coordinated
   caller/catalog change in the operator report. Do not accept or export that proposal through
   the present translation path. Do not trim it, normalize adapter output, edit the ledger to
   approve it, or add an unsupported config/context field to suppress the failure.
4. Agree the project change with the operator and review it through the project's normal
   process. Prefer a complete localized sentence with the link represented by the project's
   supported rich-text/slot mechanism, so each locale controls word order and internal spacing.
   Another composition is possible if the caller and all affected catalog values are reviewed
   together. If no change is authorized or the rendering evidence is inconclusive, leave the
   proposal rejected and report the unresolved dependency; do not keep retrying a known mismatch.
5. After the coordinated change, run adapter acceptance again if adapter code or options changed,
   then collect and sync the changed project. Generate new candidates as needed, run all core
   checks and a fresh review of their exact values. Repeat the scratch project checks before
   audit approval and the final export rehearsal
   ([procedure](project-checks.md)). Earlier approval, review and project-check results do not
   cover the changed composition.

## Golden example: punctuation suffix versus word suffix

Suppose the actual caller renders a link immediately followed by a suffix, with no independent
separator:

```html
<a>{{ privacyPolicyLabel }}</a>{{ consentSuffix }}
```

| Locale | Link text | Suffix |
| --- | --- | --- |
| en | `Privacy Policy` | `. The service processes these data.` |
| de | `Datenschutzerklärung` | ` beschrieben zu. Der Dienst verarbeitet diese Daten.` |

The German suffix needs its initial space. Core checks correctly reject it because the source
has none. Removing that space passes the boundary check but renders
`Datenschutzerklärungbeschrieben`; that is not a valid correction.

A coordinated change can replace the fragment assembly with one whole message containing a
link slot. In the neutral unit fixture, the argument is written as `{privacyPolicy}`:

```text
en: I consent to processing as described in {privacyPolicy}. The service processes these data.
de: Ich stimme der Verarbeitung wie in der {privacyPolicy} beschrieben zu. Der Dienst verarbeitet diese Daten.
```

Both messages have no boundary whitespace, preserve the argument and put the required German
space inside the message. Extra leading or trailing whitespace still fails. The fixture in
`tests/fixtures/fragment_boundaries.json`, exercised by `tests/test_checks.py`, uses synthetic
parse results and text substitution; it proves the core behaviour and illustrates the project
change. It does not compile a Vue template or approve this wording for a real project. A real
caller must use its framework's supported link-slot syntax and pass its own rendering tests
and language review before the changed composition is approved.
