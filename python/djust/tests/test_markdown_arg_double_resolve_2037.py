"""#2037 — custom-tag argument double-resolution corrupts loop/include-scoped
dict-field values.

Root cause (reproduced deterministically here — NO streaming / loop-cache /
WebSocket needed, contrary to the report's speculative hypothesis):

The Rust custom-tag dispatch (`crates/djust_templates/src/renderer.rs`
`Node::CustomTag`) resolves a bare-name argument (``block.text``) to its **value**
string and passes that value to the Python handler. ``TagHandler._resolve_arg``
then re-interprets the already-resolved value as if it were a template token:

  * a value containing ``=`` hits the ``key=value`` branch and is tuple-split →
    ``str((k, v))`` produces the observed ``('...', '...')`` Python-repr;
  * a dotted value whose first segment matches a context key is re-resolved.

Only per-``{% for %}``/``{% include ... with %}``-scoped **plain dict** fields
trigger it, because that is the case where Rust *succeeds* at resolving the arg
(a JSON-serializable dict is in the Rust context) and hands Python the value —
vs. a top-level model object Rust cannot resolve, where it passes the literal
token and Python resolves it correctly.

The fix token-guards ``_resolve_arg``: the kwarg-split and dotted-variable
heuristics fire only for genuine token shapes; a non-token (Rust-resolved) value
is returned verbatim.
"""

import pytest

from djust import LiveView
from djust.template_tags import TagHandler
from djust.template_tags.markdown import MarkdownTagHandler
from djust.testing import LiveViewTestClient


class _Probe(TagHandler):
    def render(self, args, context):  # pragma: no cover - not exercised
        return ""


# --- Unit level: _resolve_arg must not re-interpret a resolved value -----------


class TestResolveArgNoDoubleResolution:
    def setup_method(self):
        self.p = _Probe()

    def test_resolved_value_with_equals_is_not_tuple_split(self):
        """A Rust-resolved value that merely CONTAINS '=' must be returned
        verbatim, not split into a (key, value) tuple (the #2037 corruptor)."""
        val = "## Streaming demo\n\nHere is a reply where x = y matters."
        out = self.p._resolve_arg(val, {})
        assert not isinstance(out, tuple), f"value was tuple-split: {out!r}"
        assert out == val

    def test_resolved_prose_with_dot_is_returned_verbatim(self):
        val = "Here is a reply. More text follows."
        assert self.p._resolve_arg(val, {}) == val

    def test_genuine_kwarg_token_still_splits(self):
        """`tables=False` (a real template-authored kwarg) must still resolve
        to a (key, value) tuple — the fix must not break kwarg handling."""
        out = self.p._resolve_arg("tables=False", {})
        assert isinstance(out, tuple) and out[0] == "tables"

    def test_kwarg_with_spaced_resolved_value_splits_and_keeps_value(self):
        """Rust passes `title=My Post` for `title=post.title`; the key still
        splits and the value ("My Post") is returned verbatim, not re-resolved."""
        out = self.p._resolve_arg("title=My Post", {})
        assert out == ("title", "My Post")

    def test_genuine_dotted_variable_still_resolves(self):
        assert self.p._resolve_arg("block.text", {"block": {"text": "hi"}}) == "hi"

    def test_string_literal_and_int_unchanged(self):
        assert self.p._resolve_arg("'hello'", {}) == "hello"
        assert self.p._resolve_arg("123", {}) == 123

    def test_bare_word_in_context_still_resolves(self):
        assert self.p._resolve_arg("name", {"name": "Ada"}) == "Ada"


# --- Handler level: what Rust actually hands MarkdownTagHandler ----------------


class TestMarkdownHandlerResolvedSource:
    def test_render_resolved_value_with_equals_not_corrupted(self):
        """MarkdownTagHandler.render receives the Rust-resolved value string as
        args[0] (Rust already resolved the bare-name variable). A value with
        '=' must render as markdown, never as a Python-tuple-repr."""
        src = "## Streaming demo\n\nHere is a reply where x = y matters."
        out = MarkdownTagHandler().render([src], {})
        assert "', '" not in out, f"tuple-repr leaked into output: {out!r}"
        assert "Streaming demo" in out
        # Heading rendered as markdown, not literal '##' inside a tuple repr.
        assert "<h2" in out


# --- Integration level: the real Rust render path (reproduction fidelity) ------


def _render(template, ctx_blocks):
    class _V(LiveView):
        def mount(self, request, **kwargs):
            self.blocks = ctx_blocks

        def get_context_data(self, **kwargs):
            c = super().get_context_data(**kwargs)
            c["blocks"] = self.blocks
            return c

    _V.template = template
    client = LiveViewTestClient(_V)
    client.mount()
    html, _, _ = client.render_with_patches()
    return html


@pytest.mark.django_db
class TestRealPathLoopScopedMarkdown:
    TEMPLATE = (
        '<div class="prose">'
        "{% for block in blocks %}{% djust_markdown block.text %}{% endfor %}"
        "</div>"
    )

    def test_loop_scoped_dict_field_with_equals_not_corrupted(self):
        """The reporter's exact shape: a `{% for %}`-scoped plain-dict field
        (`block.text`) whose value contains '=' — must render as markdown, not
        a tuple-repr, through the real Rust engine + custom-tag bridge."""
        html = _render(
            self.TEMPLATE,
            [{"text": "## Streaming demo\n\nHere is a reply where x = y matters."}],
        )
        assert "', '" not in html, f"tuple-repr corruption in output: {html!r}"
        assert "Streaming demo" in html
        assert "<h2" in html

    def test_loop_scoped_plain_value_still_renders(self):
        html = _render(self.TEMPLATE, [{"text": "**bold** reply\n"}])
        # Real render stamps dj-id attrs: <strong dj-id="N">bold</strong>.
        assert "<strong" in html and ">bold</strong>" in html
