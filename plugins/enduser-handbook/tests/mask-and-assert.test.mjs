// Executable coverage for maskAndAssert — the <canvas> refusal (#565), the frame refusal it mirrors
// (#472), and the order both sit in relative to the coverage assert.
//
// Why this exists as its own file. Before #565, maskAndAssert had NO executable test: every claim it
// makes was pinned only by greps in reference-assets.test.sh, and a grep proves text is PRESENT, not
// that it WORKS. #565's claim is specifically a two-sided mutation — a region containing a <canvas>
// must go RED, a legitimate capture must stay GREEN — and no grep can show that.
//
// The driver is TypeScript, so the actual driving happens in a sibling fixture run under
// --experimental-strip-types (Node >= 22.6); this file spawns it and asserts on its output. The
// fixture drives the REAL maskAndAssert against a DOM stub — a stub of the ENGINE, not a
// re-implementation of the helper. See the fixture header for what that stub does NOT prove.

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, 'mask-and-assert.fixture.mjs');

// Node strips TypeScript only from 22.6 onward. Probe rather than assume, so an older runtime reports
// a visible SKIP instead of a confusing failure — and so the skip can never be mistaken for a pass.
const probe = spawnSync(process.execPath, ['--experimental-strip-types', '-e', ''], { encoding: 'utf8' });
const STRIP_TYPES = probe.status === 0;
const skip = !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types';

// The fixture is deterministic and every test reads the same scenario table, so spawn it once and
// memoize. Memoizing the RESULT (never a failure) is what keeps a crashed fixture reported by EVERY
// test that asks, exactly as before: the status assert throws before `rows` is set, so the next
// caller re-spawns and fails too, rather than reading a cached success that never happened.
let rows = null;
const run = () => {
  if (rows !== null) return rows;
  const r = spawnSync(process.execPath, ['--experimental-strip-types', FIXTURE], { encoding: 'utf8' });
  assert.equal(r.status, 0, `fixture exited ${r.status}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  const byName = new Map();
  for (const line of r.stdout.split('\n').filter((l) => l.trim().length > 0)) {
    const row = JSON.parse(line);
    byName.set(row.name, row);
  }
  rows = byName;
  return rows;
};

// ── The two-sided <canvas> mutation (#565) ──────────────────────────────────────────────────────

test('a <canvas> in the captured region is refused by default', { skip }, () => {
  const threw = run().get('canvas-refused').threw;
  assert.notEqual(threw, null, 'a region containing a <canvas> must throw');
  assert.match(threw, /1 <canvas> element\(s\)/);
  // The message must name the remedies, and must NOT offer the framed-document one: a canvas hosts
  // no document, so "scan it yourself per frame" has no counterpart here.
  assert.match(threw, /allowUnscannedCanvas: true/);
  assert.match(threw, /bitmap/);
  assert.doesNotMatch(threw, /scan it yourself/);
});

test('a legitimate masked capture stays green after the refusal lands', { skip }, () => {
  // The GREEN half of the mutation. A refusal that also broke ordinary captures would be useless.
  assert.equal(run().get('clean-masked-region').threw, null);
});

test('the explicit opt-out is the way past the canvas refusal', { skip }, () => {
  assert.equal(run().get('canvas-opt-out').threw, null, 'allowUnscannedCanvas: true must admit it');
});

// ── Reach of the count ──────────────────────────────────────────────────────────────────────────

test('a <canvas> inside an OPEN shadow root is counted', { skip }, () => {
  // The count reuses queryDeep; dropping the shadow walk would leave a web-component canvas silently
  // uncounted, which is the exact silent-half shape #565 was filed for.
  assert.match(run().get('canvas-in-open-shadow-root').threw, /1 <canvas> element\(s\)/);
});

test('the region ITSELF being a <canvas> is counted', { skip }, () => {
  // querySelectorAll returns descendants only, so the .matches() term is what covers this; without
  // it, handing maskAndAssert a canvas-scoped locator would pass with the run green.
  assert.match(run().get('region-is-canvas').threw, /1 <canvas> element\(s\)/);
});

test('listing the <canvas> in selectors does not clear the refusal', { skip }, () => {
  // Setting textContent on a canvas paints nothing (its children are fallback content), so the mask
  // tag only removes it from the SCAN. Admitting it on that basis would be a false green.
  assert.match(run().get('canvas-listed-in-selectors-still-refused').threw, /1 <canvas> element\(s\)/);
});

// ── Ordering, and the #472 refusal it mirrors ───────────────────────────────────────────────────

test('the canvas refusal is checked before the coverage assert', { skip }, () => {
  // With both a canvas and a drifted selector count, the canvas must be named: reporting "the mask
  // missed a target" would send the author after selector drift that is not the cause.
  const threw = run().get('canvas-precedes-coverage-assert').threw;
  assert.match(threw, /<canvas> element\(s\)/);
  assert.doesNotMatch(threw, /expected 7/);
});

test('the frame refusal (#472) still fires, and still takes precedence', { skip }, () => {
  const rows = run();
  assert.match(rows.get('frame-still-refused').threw, /nested browsing context/);
  assert.equal(rows.get('frame-opt-out').threw, null, 'allowUnscannedFrames must still admit a frame');
  // Frames are checked first; a region with both reports the frame.
  const both = rows.get('frame-and-canvas-reports-frame').threw;
  assert.match(both, /nested browsing context/);
  assert.doesNotMatch(both, /<canvas> element\(s\)/);
});

// ── Shim self-checks: the mask and scan passes really ran ───────────────────────────────────────
// Without these, a stub that silently produced an empty scan corpus and a zero match count would
// make every assertion above pass for the wrong reason.

test('unmasked PII still trips the leak scan', { skip }, () => {
  assert.match(run().get('leak-scan-fires').threw, /a real identifier survived masking/);
});

test('a mask-count mismatch still trips the coverage assert', { skip }, () => {
  assert.match(run().get('coverage-assert-fires').threw, /masked 1 target\(s\) but expected 2/);
});

test('masked form controls are excluded from the scan by identity, not by silence', { skip }, () => {
  const rows = run();
  // A masked input value / select option labels must not leak…
  assert.equal(rows.get('form-controls-masked-and-excluded').threw, null);
  // …and an UNMASKED control's value must still be scanned, so the exclusion above is by identity
  // rather than the scan having stopped reading controls at all.
  assert.match(rows.get('unmasked-control-value-still-scanned').threw, /a real identifier survived masking/);
});
