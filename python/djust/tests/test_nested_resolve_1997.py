"""#1997 — `Context::resolve`'s lazy getattr walk must step into dict/list
intermediates (Django `Variable._resolve_lookup` parity).

Before the fix the sidecar walk did `getattr` only, so a dict/list intermediate
reached mid-path — the canonical case being a model's ``JSONField`` value
(`{{ block.content.text }}`) — resolved to empty with no error. The fix mirrors
Django's per-segment order: mapping item access → attribute → integer index.

These render through the REAL path (`LiveViewTestClient.render_with_patches()`
→ Rust `Context::resolve`), routing values through `get_context_data` from a
private attr so eager serialization stringifies/skips the custom object and the
dotted lookup exercises the sidecar getattr walk (reproduction fidelity).
"""

import pytest

from djust import LiveView
from djust.testing import LiveViewTestClient


class _Block:
    """Stands in for a model whose attribute is a JSONField (a dict) plus a
    list-of-dicts — both reached through the sidecar walk."""

    def __init__(self):
        self.content = {"text": "HELLO-NESTED", "cols": ["a", "b", "c"]}
        self.rows = [{"name": "row0"}, {"name": "row1"}]
        self.title = "plain-attr"


def _render(template):
    class _V(LiveView):
        def mount(self, request, **kwargs):
            self._block = _Block()

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["block"] = self._block
            return ctx

    _V.template = template
    client = LiveViewTestClient(_V)
    client.mount()
    html, _, _ = client.render_with_patches()
    return html


@pytest.mark.django_db
class TestNestedDictListResolve:
    def test_dict_value_mid_path(self):
        """`{{ block.content.text }}` — dict value reached through a model
        attribute (the JSONField case). Was empty pre-#1997."""
        assert "HELLO-NESTED" in _render("<div>[{{ block.content.text }}]</div>")

    def test_list_index_mid_path(self):
        """`{{ block.content.cols.1 }}` — integer list-index into a nested list."""
        assert "[b]" in _render("<div>[{{ block.content.cols.1 }}]</div>")

    def test_list_then_dict(self):
        """`{{ block.rows.0.name }}` — list index then dict key."""
        assert "row0" in _render("<div>[{{ block.rows.0.name }}]</div>")

    def test_plain_attribute_still_works(self):
        """Attribute access (segment 2 of the order) is unaffected."""
        assert "plain-attr" in _render("<div>[{{ block.title }}]</div>")

    def test_missing_key_renders_empty(self):
        """A missing dict key / attribute still renders empty (Django
        `string_if_invalid`), never crashes."""
        assert "[]" in _render("<div>[{{ block.content.nope }}]</div>")

    def test_dict_key_wins_over_attribute(self):
        """Django order is item-access FIRST: a dict whose key shadows a real
        attribute name (`items`) resolves the key, not the method. Routed
        through a custom object (`w.payload.items`) so the dict is reached as a
        mid-path intermediate on the SIDECAR walk (#1997) — a dict placed
        directly in context resolves via the already-dict-aware eager
        `Context::get` path and would not exercise this change."""

        class _Wrap:
            def __init__(self):
                self.payload = {"items": "DICT-ITEMS-VALUE"}

        class _D(LiveView):
            def mount(self, request, **kwargs):
                self._w = _Wrap()

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["w"] = self._w
                return ctx

        _D.template = "<div>[{{ w.payload.items }}]</div>"
        client = LiveViewTestClient(_D)
        client.mount()
        html, _, _ = client.render_with_patches()
        # Item access wins → the bare value `[DICT-ITEMS-VALUE]`. Under
        # getattr-first the `dict.items` method is auto-called and renders as
        # `dict_items([('items', 'DICT-ITEMS-VALUE')])` — whose repr also
        # contains the value substring, so assert on the EXACT bracketed value
        # and the absence of `dict_items` to stay gate-off-sensitive (#1468).
        assert "[DICT-ITEMS-VALUE]" in html
        assert "dict_items" not in html and "bound method" not in html


@pytest.mark.django_db
class TestFloorNotBypassedByItemAccess:
    """#1986 floor must still hold: item-access-first must not let a model's
    sensitive field leak (proxies implement no __getitem__, so item access
    falls through to the floored getattr)."""

    def test_password_still_refused_through_walk(self):
        from django.contrib.auth.models import User

        u = User.objects.create_user(username="v", password="s3cret")

        class _V(LiveView):
            template = "<div>[{{ member.password }}]</div>"

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["member"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html
        assert "[]" in html
