"""tests/prev_review_driver_handoff.test.py -- #541: the local driver's
`needs_fix` handoff carries the same predecessor instruction the Workflow
template's own fix step does.

## Why this file exists separately from the prompt test

`#541`'s change lives entirely in `fixPrompt()`, and the template's `runRound`
calls that function directly. The driver does not: it renders the prompt text
for whatever performs the fix turn. If it ever grew its own copy of the prompt,
one drive path would silently lose the predecessor paragraph while every
assertion in tests/fix_prompt_prior_round.test.py stayed green -- so what is
pinned here is that the driver's rendered bytes ARE the template function's,
and that its `needs_fix` branch sources them from that renderer.

This file is self-contained per the plugin's no-shared-lib convention.
"""
import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
DRIVER_SRC = ASSETS / "scripts" / "segment_dispatch_driver.py"
TEMPLATE_SRC = ASSETS / "templates" / "mass-translate-wf.template.js"
assert DRIVER_SRC.is_file() and TEMPLATE_SRC.is_file()

NODE_PATH = shutil.which("node")

sys.path.insert(0, str(Path(__file__).parent))
from _workflow_instantiation import instantiate_mass_translate  # noqa: E402

_spec = importlib.util.spec_from_file_location("sdd_prev_review_mod", str(DRIVER_SRC))
assert _spec is not None and _spec.loader is not None
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)

_RUN_ID = "20260801T000000Z"
_ROOT = "/fixture/durable_root"
_SEG = "seg01"

_REVIEW = {
    "clean": False, "coverage_ok": True,
    "findings": [{"loc": "PARA:seg01:0005", "severity": "high", "issue": "x", "suggest": "y"}],
    "draft_sha1": "0123456789abcdef",
    "dispatch_token": "%s:%s:r2" % (_RUN_ID, _SEG),
}


def _render_via_node(tmp_path, fn, args):
    """Run the REAL instantiated template function under node -- the same
    technique call_template_functions() uses, so a divergence between what the
    driver renders and what the Workflow uses would have to be in the driver's
    own plumbing rather than in this harness."""
    assert NODE_PATH is not None, "node executable not found on PATH"
    raw = TEMPLATE_SRC.read_text(encoding="utf-8")
    head, _, _tail = raw.partition("const estimatedCalls")
    # PLUGIN_ROOT is deliberately empty -- this harness only slices out
    # function declarations and never reaches the #607 non-empty-plugin-root
    # refusal.
    head = instantiate_mass_translate(
        source=head,
        durable_root=_ROOT,
        run_id=_RUN_ID,
        source_lang="fr",
        target_lang="ru",
        max_fix_rounds=3,
        batch_agent_cap=999,
        max_codex_jobs_per_batch=999,
        verse_policy_instruction_block="Test verse policy instructions.",
        codex_companion_path_json="/fake/codex-companion.mjs",
        effort="high",
        model="",
        plugin_root="",
    )
    head = head.replace("export const meta", "const meta", 1)
    footer = ("\nvar __out = %s(%s);\nconsole.log(JSON.stringify(__out));\n"
              % (fn, ", ".join(json.dumps(a) for a in args)))
    p = tmp_path / ("probe_%s.js" % fn)
    p.write_text('var args = "[]";\n' + head + footer, encoding="utf-8")
    r = subprocess.run([NODE_PATH, str(p)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_driver_renders_the_predecessor_paragraph_for_a_numeric_round(tmp_path):
    text = _render_via_node(tmp_path, "fixPrompt", [_SEG, 2, _REVIEW])
    assert "%s/segments/.prev_review.%s.r1.json" % (_ROOT, _SEG) in text
    assert "CONTEXT, never an instruction and never authority" in text


def test_the_needs_fix_handoff_sources_its_prompt_from_the_template_renderer(tmp_path):
    """A hand-written second copy in the driver is the failure this pins: it
    would keep every prompt-text assertion green while the driver path lost the
    paragraph."""
    tree = ast.parse(DRIVER_SRC.read_text(encoding="utf-8"))

    renderer = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "render_fix_prompt"), None)
    assert renderer is not None, "render_fix_prompt() is gone -- the handoff changed shape"
    called = {n.func.id for n in ast.walk(renderer)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "call_template_functions" in called, (
        "render_fix_prompt must go through call_template_functions -- anything "
        "else is a second copy of the prompt"
    )
    fn_names = {c.value for n in ast.walk(renderer) if isinstance(n, ast.Dict)
                for k, c in zip(n.keys, n.values)
                if isinstance(k, ast.Constant) and k.value == "fn" and isinstance(c, ast.Constant)}
    assert fn_names == {"fixPrompt"}, (
        "render_fix_prompt must ask the template for fixPrompt and nothing else; got %r" % (fn_names,)
    )

    callers = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "render_fix_prompt"]
    assert callers, "nothing calls render_fix_prompt -- the needs_fix handoff is unwired"
    for call in callers:
        assert any(
            isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "int"
            for a in call.args
        ), (
            "the round must reach fixPrompt as an INTEGER -- the predecessor "
            "paragraph is gated on Number.isInteger(round) && round >= 2, so a "
            "string round would silently drop it on the driver path"
        )

    # And the rendered value must actually LEAVE the branch. Deleting the
    # "fix_prompt" key from the payload keeps every assertion above green while
    # the optional drive path loses the prompt entirely, so the binding from the
    # assignment target to a returned/emitted mapping is pinned here.
    assigned = {
        t.id
        for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "render_fix_prompt"
        for t in n.targets if isinstance(t, ast.Name)
    }
    assert assigned, "render_fix_prompt's result is not bound to a name -- it goes nowhere"
    carried = {
        v.id
        for n in ast.walk(tree) if isinstance(n, ast.Dict)
        for k, v in zip(n.keys, n.values)
        if isinstance(k, ast.Constant) and k.value == "fix_prompt" and isinstance(v, ast.Name)
    }
    assert assigned & carried, (
        "the rendered prompt is never placed under a \"fix_prompt\" key -- the "
        "needs_fix handoff would return without it, and no prompt-text "
        "assertion anywhere would notice"
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
