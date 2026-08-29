"""The `_rust.pyi` tag-handler example must actually run (#2417).

`scripts/check-doc-snippets.py` does not read `.pyi`, which is why
`register_tag_handler`'s example shipped documenting a bare function --
a shape the runtime rejects with `TypeError: Handler must have a 'render'
method`. Of the twelve `Example::` blocks in the stub it was the only one
that supplied every name it used and still failed; the rest are ordinary
placeholders.

These tests EXECUTE the block rather than inspecting it, because the
failure mode was a plausible-looking example that raises on copy-paste.
"""

from pathlib import Path

import pytest
from django.utils.html import escape  # noqa: F401 - the example imports it
from django.utils.safestring import mark_safe  # noqa: F401

from djust import _rust

PYI = Path(__file__).resolve().parents[2] / "python" / "djust" / "_rust.pyi"


def _example_of(func_name: str) -> str:
    """Return the dedented body of `func_name`'s ``Example::`` block."""
    src = PYI.read_text()
    start = src.index(f"def {func_name}")
    doc_open = src.index('"""', start)
    doc = src[doc_open + 3 : src.index('"""', doc_open + 3)]
    assert "Example::" in doc, f"{func_name} has no Example:: block"
    body = doc[doc.index("Example::") + len("Example::") :]
    return "\n".join(
        line[8:] if line.startswith(" " * 8) else line for line in body.split("\n")
    ).strip()


class TestTheStubExampleRuns:
    def test_the_example_executes_without_raising(self):
        code = _example_of("register_tag_handler")
        # Non-vacuity: if the block were empty this test would pass trivially.
        assert "register_tag_handler(" in code, "extracted block is not the example"
        ns = {"register_tag_handler": _rust.register_tag_handler}
        exec(compile(code, "<_rust.pyi example>", "exec"), ns)  # noqa: S102
        assert "CustomTagHandler" in ns

    def test_the_example_renders_its_markup_and_escapes_the_argument(self):
        code = _example_of("register_tag_handler")
        ns = {"register_tag_handler": _rust.register_tag_handler}
        exec(compile(code, "<_rust.pyi example>", "exec"), ns)  # noqa: S102
        out = _rust.render_template("{% custom p %}", {"p": "<img src=x onerror=alert(1)>"})
        # The handler's own markup survives (it used mark_safe)...
        assert "<custom>" in out
        # ...and the untrusted argument does not.
        assert "&lt;img src=x onerror=alert(1)&gt;" in out
        assert "<img src=x onerror=alert(1)>" not in out

    def test_a_bare_function_is_still_rejected(self):
        """The shape the old example documented. Pins WHY it was wrong."""
        with pytest.raises(TypeError, match="render"):
            _rust.register_tag_handler("bare_fn_2417", lambda args, ctx: "x")

    def test_args_are_resolved_values_not_names(self):
        """The stub's second inaccuracy: `args` arrive already resolved."""
        seen = {}

        class Probe:
            def render(self, args, context):
                seen["args"] = list(args)
                return "ok"

        _rust.register_tag_handler("probe_2417", Probe())
        _rust.render_template("{% probe_2417 p %}", {"p": "<b>hi</b>"})
        assert seen["args"] == ["<b>hi</b>"], (
            "args must be the RESOLVED value, not the variable name -- the stub "
            "documented context.get(args[0]) which would look up '<b>hi</b>'"
        )
