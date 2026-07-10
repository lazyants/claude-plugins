// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// control-inventory.d.mts — TypeScript declarations for control-inventory.mjs so a downstream
// project that DOES typecheck can resolve the .ts → .mjs import (surface-audit.playwright.ts
// imports from "./lib/control-inventory.mjs"). This repo does not compile any TypeScript; these
// declarations exist purely so the reference impl is copyable into a typechecked project.

/** Heuristic side-effect hint produced by classifyByShape. Never authoritative. */
export type ShapeHint = 'candidate-destructive' | 'unclassified';

/** Broad interactive-surface selector the surface audit enumerates with (imported by surface-audit). */
export const INTERACTIVE_SELECTOR: string;

/** One control's identity, as extracted from a single element handle. */
export interface ControlRecord {
  /** Uppercase tagName (e.g. "BUTTON", "A"), or null if unavailable. */
  tag: string | null;
  /**
   * Trimmed textContent; "" for icon-only controls (which still MUST be kept) AND for value-bearing
   * controls (textarea/select/non-button input/contenteditable) whose textContent is user data, not a
   * label — suppressed so PII never enters the console-logged inventory.
   */
  text: string;
  /** value attribute (a native <input type=submit/button> carries its label here), or null. */
  value: string | null;
  /**
   * Non-PII boolean: true when a <select>'s (suppressed) option list contained a destructive verb.
   * The raw option labels are never stored; this preserves the destructive signal for classifyByShape.
   */
  hasDestructiveText: boolean;
  /** Developer-set form-field name attribute (non-PII identity for unlabelled fields), or null. */
  name: string | null;
  /** title attribute, or null. */
  title: string | null;
  /** aria-label attribute, or null. */
  ariaLabel: string | null;
  /** href attribute, or null. */
  href: string | null;
  /** role attribute, or null. */
  role: string | null;
  /** data-testid attribute, or null. */
  testId: string | null;
  /**
   * class attribute, VERBATIM (never suppressed) — or null. Primarily the destructive heuristic's
   * signal for icon-only controls (glyphicon-trash / fa-trash / mdi-delete) and the only identity
   * such a control has beyond aria-label. A verbatim field under the documented PII boundary
   * (seeded data + human scrub), since a minority of apps encode record/user slugs into class names.
   */
  className: string | null;
}

/** A ControlRecord after normalization, carrying its enumeration index and the heuristic shape hint. */
export interface NormalizedControl extends ControlRecord {
  /** Positional index in the enumerated order (no records are dropped, so this is stable). */
  index: number;
  shape: ShapeHint;
}

/** Minimal element-handle surface extractRecord depends on (a Playwright ElementHandle satisfies it). */
export interface ControlHandle {
  getAttribute(name: string): Promise<string | null>;
  evaluate<R>(
    fn: (
      el: {
        tagName: string;
        textContent: string;
        isContentEditable: boolean;
        querySelector(selector: string): unknown;
      },
      arg: string,
    ) => R,
    arg?: string,
  ): Promise<R>;
}

/**
 * Read one control's identity from an element handle. ALWAYS resolves to a record — never null,
 * never filtered. Missing attributes are null.
 */
export function extractRecord(handle: ControlHandle): Promise<ControlRecord>;

/**
 * Attach a positional index + a shape hint to each record. Drops NOTHING and does NO content-based
 * dedup — collapsing by extracted attributes would drop genuinely distinct controls (two icon
 * buttons 'Edit'/'Delete'). MUST NOT filter by text presence — icon-only controls survive.
 */
export function normalizeControls(raw: ControlRecord[]): NormalizedControl[];

/** Heuristic destructive-shape hint for one record. A HINT only; the human classify pass rules. */
export function classifyByShape(record: ControlRecord): ShapeHint;

/**
 * Compose the surface-audit enumeration selector. No rowSelector → the interactive selector unchanged.
 * With a rowSelector, match interactive descendants of the scope AND the scope element itself when
 * interactive. The scope is wrapped as its own `:is()` group so a comma-separated rowSelector cannot
 * split the list and match a bare, non-interactive row container (whose textContent would leak PII).
 */
export function buildScopedSelector(
  rowSelector: string | null | undefined,
  interactiveSelector: string,
): string;

/**
 * Human-facing coverage-matrix label fallback chain. `value` sits directly below `ariaLabel`
 * (not below `text`) per HTML-AAM accessible-name precedence for `<input type="submit">`.
 */
export function matrixLabel(record: {
  text?: string | null;
  ariaLabel?: string | null;
  value?: string | null;
  title?: string | null;
  testId?: string | null;
  name?: string | null;
  href?: string | null;
  className?: string | null;
}): string;
