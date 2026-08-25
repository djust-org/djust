"""``SimpleLiveView`` renders, and its context is its own state (#2219).

Before this, the module could not render **at all**. ``get_context_data``
walked ``dir(self)`` and ``getattr``-ed every name, which reaches Django's
``View.as_view`` — a ``classonlymethod`` whose ``__get__`` raises on an
instance:

    AttributeError: This method is available only on the class, not on
    instances.

True for any subclass, always. And ``render_template``'s ``except Exception``
turned the crash into ``<div>An error occurred rendering this view.</div>``, so
a user hitting it saw a generic message with no clue.

It survived because the class was named ``LiveView`` — the same name as
``djust.LiveView``, which it is not — so grepping for ``SimpleLiveView`` found
nothing and the module read as unused when it was only unfindable. It went two
PRs (#2209, #2223) without anyone noticing it was a live render path.
"""

import datetime as dt

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from djust.simple_live_view import SimpleLiveView


class _Greeting(SimpleLiveView):
    template = "<p>Hello {{ name }} — you have {{ count }}</p>"

    def mount(self, request, **kwargs):
        self.name = "Ada"
        self.count = 3


def _mounted(cls=_Greeting):
    view = cls()
    view.mount(RequestFactory().get("/"))
    return view


# ---------------------------------------------------------------------------
# The bug.
# ---------------------------------------------------------------------------


def test_get_context_data_does_not_raise():
    # The whole defect in one line: reading `as_view` off an instance raised,
    # so this call failed for every subclass before a template was involved.
    assert _mounted().get_context_data() is not None


def test_the_view_actually_renders():
    out = _mounted().render_template()
    assert "Hello Ada" in out
    assert "you have 3" in out
    assert "error occurred" not in out, (
        "the generic error message means render_template's except swallowed "
        "something — that is how this bug stayed invisible"
    )


def test_a_view_with_no_template_says_so_specifically():
    """Two different "cannot render" cases, two different messages.

    They used to share one: a view with no ``template`` was told the Rust
    backend was unavailable — false, and unactionable for the person reading
    it.

    (The first draft of this test asserted `... or True`, which passes
    unconditionally — a tautology in a test file about a module that was
    silently broken. Caught before commit; noting it because #1200 exists
    precisely because these read as fine.)
    """
    out = SimpleLiveView().render_template()
    assert "No template configured" in out
    assert "Rust backend" not in out


def test_a_bare_instance_has_an_empty_context():
    # Nothing but View plumbing on it, and none of that is offered.
    assert SimpleLiveView().get_context_data() == {}


def test_get_returns_the_rendered_html():
    response = _Greeting().get(RequestFactory().get("/"))
    assert isinstance(response, HttpResponse)
    assert b"Hello Ada" in response.content


# ---------------------------------------------------------------------------
# What belongs in the context, and what does not.
# ---------------------------------------------------------------------------


def test_view_machinery_is_not_offered_to_the_template():
    """Two exclusions, two different reasons — kept separable on purpose.

    ``http_method_names`` / ``template`` / ``view_is_async`` read perfectly
    well and are excluded because they are plumbing. ``as_view`` is excluded
    because it RAISES, by the guard below — it is deliberately not in
    ``_VIEW_INTERNALS``, since listing it in both places made the two
    mechanisms shadow each other: re-introducing the original #2219 bug left
    the suite green because the guard silently covered for it (#2129).
    """
    ctx = _mounted().get_context_data()
    for name in ("http_method_names", "template", "view_is_async"):
        assert name not in ctx, f"{name} is View plumbing, not view state"
    assert "as_view" not in ctx, "excluded by the AttributeError guard, not by name"


def test_private_and_callable_attributes_are_excluded():
    class _WithExtras(_Greeting):
        def helper(self):  # callable — not state
            return 1

    view = _mounted(_WithExtras)
    view._internal = "hidden"
    ctx = view.get_context_data()
    assert "helper" not in ctx
    assert "_internal" not in ctx
    assert ctx["name"] == "Ada"


def test_an_attribute_that_raises_on_read_is_skipped_not_fatal():
    """The general form of the bug, not just the one instance of it.

    ``as_view`` is now excluded by name, which fixes #2219 exactly. But a name
    in ``dir(self)`` is never a promise that ``getattr`` will succeed — a
    property whose getter raises looks identical until read, and this method's
    entire job is reading attributes it does not know about. Excluding one
    known offender would leave the next one fatal.
    """

    class _Landmine(_Greeting):
        @property
        def explodes(self):
            raise AttributeError("nope")

    ctx = _mounted(_Landmine).get_context_data()
    assert "explodes" not in ctx
    assert ctx["name"] == "Ada", "one bad attribute must not lose the rest"


def test_a_property_that_returns_a_value_is_included():
    # The guard must skip attributes that RAISE, not attributes that are
    # computed — a property is a perfectly good source of template state.
    class _Computed(_Greeting):
        @property
        def doubled(self):
            return self.count * 2

    assert _mounted(_Computed).get_context_data()["doubled"] == 6


# ---------------------------------------------------------------------------
# The name collision.
# ---------------------------------------------------------------------------


def test_the_old_name_still_imports_and_is_the_same_class():
    # The class was renamed because `LiveView` shadowed `djust.LiveView`. The
    # module is importable, so the old name stays as an alias.
    from djust.simple_live_view import LiveView as OldName

    assert OldName is SimpleLiveView


def test_it_is_not_the_websocket_live_view():
    # The confusion the rename exists to end: these are different classes with
    # different lifecycles, and one of them has no WebSocket at all.
    import djust

    assert SimpleLiveView is not djust.LiveView
    assert not issubclass(SimpleLiveView, djust.LiveView)


# ---------------------------------------------------------------------------
# Per-render settings reach this path too (#2209, #2221, #2223).
# ---------------------------------------------------------------------------


@override_settings(USE_TZ=True, TIME_ZONE="America/New_York", USE_THOUSAND_SEPARATOR=True)
def test_timezone_and_number_format_reach_this_render_path():
    class _Stamped(SimpleLiveView):
        template = '<p>{{ when|date:"H:i" }}|{{ n }}</p>'

        def mount(self, request, **kwargs):
            self.when = dt.datetime(2026, 8, 22, 23, 30, tzinfo=dt.timezone.utc)
            self.n = 1234567

    out = _mounted(_Stamped).render_template()
    assert "19:30" in out, "the active timezone must reach this path (#2209)"
    assert "1,234,567" in out, "the active locale must reach this path (#2221)"


@pytest.mark.parametrize("attr", ["name", "count"])
def test_mount_state_becomes_template_context(attr):
    assert attr in _mounted().get_context_data()
