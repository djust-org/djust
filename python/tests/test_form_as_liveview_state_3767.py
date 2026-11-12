"""A Django ``Form`` held as LiveView state reaches the template as the form (#3767).

``LiveView`` state is JSON, so the bridge used to flatten a ``BaseForm`` held on the view to
``{field_name: SafeString(widget HTML)}`` before the Rust engine saw it
(``serialization.render_form_value``), and left it out of the raw-Python sidecar. That keeps
``{{ form.name }}`` working and loses everything else a form IS: ``{{ form.name.errors }}`` and
``{% field_error field=form.name %}`` were empty, ``{% for f in form %}`` iterated the KEYS and
``{{ f.label }}`` was empty. ``DjustTemplateBackend`` has carried the form as itself since #3755.

**The decision.** The issue's three options all assume the form must also be IN the Rust state (as
a flattened dict, or as a handle-bearing value). The engine already has a channel for a live
object that is not state: the raw-Python sidecar, resolved lazily, per segment, under the
serialization floor. So a form (or a dict / list / tuple holding one) is kept OUT of the state and
rides the sidecar only, and the value stack has nothing to answer first
(``serialization.holds_form``). No Rust change.

Every test renders the SAME template through Django's engine (the control) and through a real
``LiveView`` and asserts they are byte-identical AND carry the text (an equality between two empty
strings proves nothing). The entries are the ones that exist: ``LiveViewTestClient`` (mount and an
event re-render), an HTTP GET (the dj-root and the page shell), a real WebSocket mount plus events,
and a ``template_name`` component.

What each class proves:

* ``TestTheReportedShapes``: the spellings of the issue and their neighbours, three forms, mount and
  event.
* ``TestTheStateChangesAreRendered``: a form that CHANGES between renders (unbound, invalid, valid,
  rebound, mutated in place) renders what Django renders for it each time. The partial-render path
  is only exercised when something changed, so an identical rebind proves nothing.
* ``TestEveryShapeThatHoldsAForm``: a top-level form, a ``BoundField``, a formset, a list / tuple /
  dict of forms, a form two containers deep, an object holding a form.
* ``TestAContainerOfFormsSurvivesAnEventThatDoesNotChangeIt``: the verdict for a name the sync did
  not send (``_form_held_keys``).
* ``TestAFormIsNotState``: the Rust state, the persisted (session) state and ``get_state`` carry no
  more of the form than they did.
* ``TestTheRealEntries`` / ``TestThePageShellAndComponents``: HTTP GET, WebSocket, the shell, a
  component.
* ``TestEveryEntryThatSyncsAContextHonoursTheDecision``: a structural pin on the set of
  ``update_state`` call sites.

Refs #3767, #3755, #3769, #3790, #3796, #3813, #1646, #1468.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import pytest
from django import forms
from django.forms import formset_factory
from django.template import Context, Engine
from django.test import override_settings
from django.utils.safestring import mark_safe

from djust import LiveView
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient

pytestmark = [pytest.mark.pr_tier]

COMPONENTS = "djust.components.templatetags.djust_components"
LOAD = "{% load djust_components %}"
REQUIRED = "This field is required."
MARKER = "MARKER-3767-help"


class SignupForm(forms.Form):
    name = forms.CharField(help_text="Your name")
    email = forms.EmailField(label="Mail")
    plan = forms.ChoiceField(choices=[("a", "Alpha"), ("b", "Beta")], widget=forms.RadioSelect)
    note = forms.CharField(required=False, widget=forms.Textarea)


class MarkedForm(forms.Form):
    name = forms.CharField(help_text=MARKER)


def _invalid() -> SignupForm:
    form = SignupForm(data={"email": "not-an-email"})
    form.is_valid()
    return form


def _valid() -> SignupForm:
    form = SignupForm(data={"name": "Al", "email": "a@b.co", "plan": "a"})
    form.is_valid()
    return form


def _unbound() -> SignupForm:
    return SignupForm(initial={"name": "Initial"})


FORMS: dict[str, Callable[[], SignupForm]] = {
    "invalid": _invalid,
    "valid": _valid,
    "unbound": _unbound,
}

#: The six spellings of the issue, plus the ones that read the same state from another angle.
SPELLINGS = {
    "widget": "{{ form.name }}",
    "errors": "{{ form.name.errors }}",
    "field_error": "{% field_error field=form.name %}",
    "loop_widget": "{% for f in form %}[{{ f }}]{% endfor %}",
    "loop_label": "{% for f in form %}[{{ f.label }}]{% endfor %}",
    "loop_field_error": "{% for f in form %}{% field_error field=f %}{% endfor %}",
    "loop_errors": "{% for f in form %}[{{ f.errors }}]{% endfor %}",
    "with_field_error": "{% with f=form.name %}{% field_error field=f %}{% endwith %}",
    "form_whole": "{{ form }}",
    "form_errors": "{{ form.errors }}",
    "required": "{% for f in form %}{% if f.field.required %}[{{ f.name }}]{% endif %}{% endfor %}",
    "bound": "{% if form.is_bound %}bound{% else %}unbound{% endif %}",
    "length": "{{ form|length }}",
    "help": "{% for f in form %}[{{ f.help_text }}]{% endfor %}",
    "value": "{{ form.name.value }}",
    "nonfield": "{{ form.non_field_errors }}",
}

#: Every spelling at once, so one real WebSocket / HTTP round trip checks them all.
COMPOSITE = "|".join(f"<i>{src}</i>" for src in SPELLINGS.values())


def _norm(text: str) -> str:
    text = re.sub(r"<!--dj-if-->", "", text)
    return re.sub(r"\s+", " ", re.sub(r'\s*dj-[a-z-]+="[^"]*"', "", text)).strip()


class _Canon(HTMLParser):
    """Tags with their attributes sorted and ``required`` == ``required=""``: the WebSocket frame is
    re-serialised by the VDOM, which orders attributes, where Django writes them in source order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        rendered = "".join(f' {k}="{v or ""}"' for k, v in sorted(attrs) if not k.startswith("dj-"))
        self.out.append(f"<{tag}{rendered}>")

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(data)


def _canon(text: str) -> str:
    parser = _Canon()
    parser.feed(text)
    # The VDOM drops whitespace between tags and a textarea's leading newline; Django keeps both.
    return re.sub(r"\s*(</?[a-z][^>]*>)\s*", r"\1", _norm("".join(parser.out)))


def _django_engine() -> Engine:
    from djust.components import rust_handlers as component_handlers

    return Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries={"djust_components": COMPONENTS},
    )


def _django(src: str, context: dict[str, Any]) -> str:
    return _norm(_django_engine().from_string(LOAD + src).render(Context(dict(context))))


def _inner(html: str) -> str:
    match = re.search(r"<div[^>]*dj-root[^>]*>(.*)</div>", html, re.S)
    assert match, html[:300]
    return _norm(match.group(1))


def _page(src: str) -> str:
    return "<div dj-root>" + LOAD + src + "</div>"


def _view_class(src: str, make: Callable[[], dict[str, Any]]) -> type[LiveView]:
    class HeldView(LiveView):
        exposure_policy = "legacy"
        template = _page(src)

        def mount(self, request, **kwargs):
            for name, value in make().items():
                setattr(self, name, value)

        @event_handler()
        def rebind(self, **kwargs):
            for name, value in make().items():
                setattr(self, name, value)

    return HeldView


@pytest.fixture(scope="module", autouse=True)
def _tags():
    from djust.components import rust_handlers as component_handlers

    component_handlers.register_with_rust_engine()


def _mounted(src: str, make: Callable[[], dict[str, Any]]) -> LiveViewTestClient:
    client = LiveViewTestClient(_view_class(src, make))
    client.mount()
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("which", sorted(FORMS))
@pytest.mark.parametrize("name", sorted(SPELLINGS))
class TestTheReportedShapes:
    def test_mount_matches_django(self, name, which):
        src = SPELLINGS[name]
        client = _mounted(src, lambda: {"form": FORMS[which]()})
        assert _inner(client.render()) == _django(src, {"form": FORMS[which]()})

    def test_event_rerender_matches_django(self, name, which):
        src = SPELLINGS[name]
        client = _mounted(src, lambda: {"form": FORMS[which]()})
        client.send_event("rebind")
        assert _inner(client.render()) == _django(src, {"form": FORMS[which]()})


@pytest.mark.django_db
class TestTheIssuesExactClaims:
    """The text the issue says is missing is present (not only equal to Django's)."""

    def test_the_errors_carry_the_message(self):
        client = _mounted(SPELLINGS["errors"], lambda: {"form": _invalid()})
        assert REQUIRED in _inner(client.render())

    def test_field_error_carries_the_message(self):
        client = _mounted(SPELLINGS["field_error"], lambda: {"form": _invalid()})
        out = _inner(client.render())
        assert REQUIRED in out and "dj-field-error" in out

    def test_the_loop_yields_bound_fields(self):
        client = _mounted(SPELLINGS["loop_label"], lambda: {"form": _invalid()})
        assert _inner(client.render()) == "[Name][Mail][Plan][Note]"

    def test_the_loop_field_error_reaches_every_field(self):
        client = _mounted(SPELLINGS["loop_field_error"], lambda: {"form": _invalid()})
        assert _inner(client.render()).count(REQUIRED) == 2  # name, plan; email is malformed


# --------------------------------------------------------------------------- #


class SteppingView(LiveView):
    """A form that CHANGES: the partial-render path only runs for a changed name."""

    exposure_policy = "legacy"
    template = _page("")

    def mount(self, request, **kwargs):
        self.form = _unbound()
        self.tick = 0

    @event_handler()
    def to_invalid(self, **kwargs):
        self.form = _invalid()

    @event_handler()
    def to_valid(self, **kwargs):
        self.form = _valid()

    @event_handler()
    def to_unbound(self, **kwargs):
        self.form = _unbound()

    @event_handler()
    def add_error_in_place(self, **kwargs):
        self.form.add_error("name", "Added in place.")
        self.set_changed_keys("form")

    @event_handler()
    def unrelated(self, **kwargs):
        self.tick += 1


def _stepping(src: str) -> LiveViewTestClient:
    view = type("Stepping", (SteppingView,), {"template": _page(src + "{{ tick }}")})
    client = LiveViewTestClient(view)
    client.mount()
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["errors", "field_error", "loop_label", "loop_errors", "bound"])
class TestTheStateChangesAreRendered:
    def test_each_step_renders_what_django_renders_for_it(self, name):
        src = SPELLINGS[name]
        client = _stepping(src)
        seen = []
        for event, make in (
            (None, _unbound),
            ("to_invalid", _invalid),
            ("to_valid", _valid),
            ("to_invalid", _invalid),
            ("to_unbound", _unbound),
        ):
            if event:
                client.send_event(event)
            got = _inner(client.render())
            want = _django(src, {"form": make()}) + "0"
            assert got == want, (event, got, want)
            seen.append(got)
        # not vacuous: the steps differ from each other for the spellings that read errors
        if name in {"errors", "field_error", "loop_errors", "bound"}:
            assert len(set(seen)) > 1, seen

    def test_an_unrelated_event_keeps_the_form(self, name):
        """A partial render that names only ``tick``: ``form`` is neither re-sent nor lost."""
        src = SPELLINGS[name]
        client = _stepping(src)
        client.send_event("to_invalid")
        before = _inner(client.render())
        assert before.endswith("0")
        client.send_event("unrelated")
        assert _inner(client.render()) == before[:-1] + "1"


@pytest.mark.django_db
class TestAFormMutatedInPlace:
    def test_set_changed_keys_rerenders_it(self):
        client = _stepping(SPELLINGS["errors"])
        client.send_event("to_unbound")
        assert "Added in place." not in _inner(client.render())
        client.view_instance.form = _valid()  # bound, valid: no errors yet
        client.send_event("add_error_in_place")
        assert "Added in place." in _inner(client.render())


# --------------------------------------------------------------------------- #


class Holder:
    def __init__(self, form):
        self.form = form


FS = formset_factory(SignupForm, extra=0)


def _formset():
    fs = FS({"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-email": "x"})
    fs.is_valid()
    return fs


#: name -> (what the view holds, a template over it). The context handed to Django is the same dict.
SHAPES: dict[str, tuple[Callable[[], dict[str, Any]], str]] = {
    "top": (
        lambda: {"form": _invalid()},
        "{{ form.name.errors }}{% for f in form %}[{{ f.label }}]{% endfor %}",
    ),
    "boundfield": (
        lambda: {"field": _invalid()["plan"]},
        "{{ field.errors }}[{{ field.label }}]{{ field.field.required }}"
        "{% for w in field %}[{{ w.choice_label }}]{% endfor %}{{ field|length }}",
    ),
    "formset": (
        lambda: {"fs": _formset()},
        "{% for f in fs %}{{ f.name.errors }}[{{ f.name.label }}]{% endfor %}",
    ),
    "formset_forms": (
        lambda: {"fs": _formset()},
        "{% for f in fs.forms %}{{ f.name.errors }}{% endfor %}{{ fs.management_form.TOTAL_FORMS }}",
    ),
    "formset_index": (
        lambda: {"fs": _formset()},
        "{{ fs.0.name.errors }}[{{ fs.0.name.label }}]{{ fs.0.is_bound }}{{ fs|length }}",
    ),
    "formset_form_whole": (lambda: {"fs": _formset()}, "{{ fs.0 }}"),
    "list": (
        lambda: {"forms": [_invalid(), _valid()]},
        "{% for f in forms %}{{ f.name.errors }}[{{ f.name.label }}]{% endfor %}",
    ),
    "list_index": (lambda: {"forms": [_invalid(), _valid()]}, "{{ forms.0.name.errors }}"),
    "tuple": (lambda: {"t": (_invalid(),)}, "{{ t.0.name.errors }}"),
    "dict": (
        lambda: {"d": {"a": _invalid(), "b": _valid()}},
        "{{ d.a.name.errors }}{% for f in d.b %}[{{ f.label }}]{% endfor %}",
    ),
    "dict_with_data": (
        lambda: {"d": {"title": "T", "n": 3, "form": _invalid()}},
        "{{ d.title }}{{ d.n }}{{ d.form.name.errors }}",
    ),
    "mixed_container": (
        # The rest of the container renders as it does under Django (and as it did): a
        # SafeString stays safe, an unsafe string stays escaped, a date, a Decimal, a set.
        lambda: {
            "d": {
                "form": _invalid(),
                "safe": mark_safe("<b>x</b>"),
                "raw": "<i>y</i>",
                "when": datetime.date(2024, 1, 2),
                "price": Decimal("1.50"),
                "n": 3,
                "none": None,
                "items": [1, 2, {"k": "v"}],
                "tags": {"a", "b"},
            }
        },
        "{{ d.safe }}|{{ d.raw }}|{{ d.when|date:'Y' }}|{{ d.price }}|{{ d.n|add:1 }}|"
        "{{ d.none|default:'dflt' }}|{% for i in d.items %}{{ i }},{% endfor %}|{{ d.items.2.k }}|"
        "{{ d.tags|length }}|{% if d.form.name.errors %}E{% endif %}|{{ d.raw|safe }}",
    ),
    "two_deep": (
        lambda: {"steps": [{"title": "One", "form": _invalid()}]},
        "{% for s in steps %}{{ s.title }}{{ s.form.name.errors }}{% endfor %}",
    ),
    "holder": (
        lambda: {"h": Holder(_invalid())},
        "{{ h.form.name.errors }}{% for f in h.form %}[{{ f.label }}]{% endfor %}",
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize("shape", sorted(SHAPES))
class TestEveryShapeThatHoldsAForm:
    def test_mount_and_event_match_django(self, shape):
        make, src = SHAPES[shape]
        client = _mounted(src, make)
        want = _django(src, make())
        # not two empty strings: Django's own answer carries something a form drew
        assert re.sub(r"[\s,|\[\]]", "", want) != "", want
        assert _inner(client.render()) == want
        client.send_event("rebind")
        assert _inner(client.render()) == want


@pytest.mark.django_db
class TestTheLimit:
    @staticmethod
    def _wrapped(depth: int) -> Any:
        value: Any = _invalid()
        for _ in range(depth):
            value = [value]
        return value

    def test_the_boundary_is_four_containers_not_the_constant(self):
        """The documented limit is a LITERAL four: a test written in terms of
        ``_FORM_SCAN_DEPTH`` follows the constant wherever it is moved and pins nothing."""
        from djust.serialization import holds_form

        for depth in range(0, 5):
            assert holds_form(self._wrapped(depth)) is True, depth
        for depth in (5, 6, 9):
            assert holds_form(self._wrapped(depth)) is False, depth

    @pytest.mark.django_db
    def test_four_deep_renders_and_five_deep_stays_flattened(self):
        """Rendered, not only classified: the 4-deep form answers ``.errors`` from the sidecar,
        the 5-deep one is the flattened dict of widget HTML it always was (``.errors`` empty)."""

        wrapped = self._wrapped

        class V(LiveView):
            exposure_policy = "legacy"
            template = _page("[{{ deep4.0.0.0.0.name.errors }}][{{ deep5.0.0.0.0.0.name.errors }}]")

            def mount(self, request, **kwargs):
                self.deep4 = wrapped(4)
                self.deep5 = wrapped(5)

        client = LiveViewTestClient(V)
        client.mount()
        out = _inner(client.render())
        assert out.startswith("[<ul") and REQUIRED in out.split("][")[0], out
        assert out.endswith("[]"), out  # five deep: flattened, nothing to read

    def test_plain_data_is_not_claimed(self):
        from djust.serialization import holds_form

        for value in ("s", 3, None, [1, {"a": [2]}], {"a": (1, 2)}, {"a": object()}, set()):
            assert holds_form(value) is False


@pytest.mark.django_db
class TestAManagementFormHeldDirectly:
    """Pins TODAY's behaviour (#3799, docs/SECURE_DEFAULTS.md): a view that keeps
    ``formset.management_form`` as an attribute used to flatten it (no data readable); it now
    reaches the template as the plain ``Form`` it is, with no pinned floor, so ``{{ mf.data }}``
    prints the formset's whole submitted data, exactly as the plain backend does. The formset's OWN
    ``management_form`` (reached through the formset) stays floored. When #3799 is resolved this
    test flips: update it and the doc together."""

    def _world(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.forms import UserChangeForm
        from django.forms import modelformset_factory

        User = get_user_model()
        user = User.objects.create_user("mf-user", "mf@example.com", "pw-3767")
        data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(user.pk),
            "form-0-username": user.username,
            "form-0-password": user.password,
            "form-0-email": "mf@example.com",
            "form-0-date_joined": "2020-01-01 00:00:00",
        }
        FormSet = modelformset_factory(User, form=UserChangeForm, extra=0)
        fs = FormSet(data, queryset=User.objects.filter(pk=user.pk))
        assert fs.is_valid(), fs.errors
        return user, fs

    def _live(self, src, attrs):
        class V(LiveView):
            exposure_policy = "legacy"
            template = _page(src)

            def mount(self, request, **kwargs):
                for name, value in attrs().items():
                    setattr(self, name, value)

        client = LiveViewTestClient(V)
        client.mount()
        return _inner(client.render())

    def test_held_directly_it_is_readable_as_on_the_plain_backend(self):
        from djust.template.backend import DjustTemplateBackend

        user, fs = self._world()
        tail = user.password[-12:]
        held = self._live("{{ mf.data }}", lambda: {"mf": fs.management_form})
        plain = DjustTemplateBackend(
            {"NAME": "mf-3767", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
        ).from_string("{{ mf.data }}")
        assert tail in held, "pinned: a directly held management form is a plain Form (#3799)"
        assert tail in plain.render({"mf": fs.management_form})

    def test_through_the_formset_it_stays_floored(self):
        user, fs = self._world()
        out = self._live(
            "{{ fs.management_form.data }}{{ fs.0.initial }}{{ fs.0.data }}", lambda: {"fs": fs}
        )
        assert user.password[-12:] not in out and "mf-user" in out


# --------------------------------------------------------------------------- #


class KeepView(LiveView):
    exposure_policy = "legacy"
    template = _page(
        "{% for f in forms %}{{ f.name.errors }}{% endfor %}{{ d.form.name.errors }}[{{ tick }}]"
    )

    def mount(self, request, **kwargs):
        self.forms = [_invalid()]
        self.d = {"form": _invalid()}
        self.tick = 0

    @event_handler()
    def unrelated(self, **kwargs):
        self.tick += 1

    @event_handler()
    def swap_for_plain(self, **kwargs):
        self.forms = ["plain"]
        self.d = {"form": "gone"}

    @event_handler()
    def swap_back(self, **kwargs):
        self.forms = [_invalid()]
        self.d = {"form": _invalid()}


@pytest.mark.django_db
class TestAContainerOfFormsSurvivesAnEventThatDoesNotChangeIt:
    def test_the_sidecar_keeps_a_name_the_sync_did_not_send(self):
        client = LiveViewTestClient(KeepView)
        client.mount()
        first = _inner(client.render())
        assert first.count(REQUIRED) == 4 or first.count(REQUIRED) >= 2, first
        for expected in (1, 2, 3):
            client.send_event("unrelated")
            out = _inner(client.render())
            assert out.endswith(f"[{expected}]")
            assert out.count(REQUIRED) == first.count(REQUIRED), out

    def test_swapping_a_container_for_plain_data_and_back(self):
        client = LiveViewTestClient(KeepView)
        client.mount()
        client.render()
        client.send_event("swap_for_plain")
        out = _inner(client.render())
        assert REQUIRED not in out
        client.send_event("swap_back")
        out = _inner(client.render())
        assert out.count(REQUIRED) >= 2, out
        client.send_event("unrelated")
        assert _inner(client.render()).count(REQUIRED) >= 2


# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestAFormIsNotState:
    def _view(self):
        class V(LiveView):
            exposure_policy = "legacy"
            template = _page("{{ form.name }}{{ plain }}")

            def mount(self, request, **kwargs):
                self.form = MarkedForm(data={})
                self.plain = MARKER + "-plain"

        client = LiveViewTestClient(V)
        client.mount()
        client.render()
        return client

    def test_the_rust_state_does_not_carry_the_form(self):
        client = self._view()
        snapshot = bytes(client.view_instance._rust_view.serialize_msgpack())
        assert (MARKER + "-plain").encode() in snapshot, (
            "control: a plain attribute is in the state"
        )
        assert b"id_name" not in snapshot, "the form's widget markup is not in the state"

    def test_the_instrument_sees_a_flattened_form(self):
        """Control for the assertion above: past the scan depth a form is still flattened into
        the state, and the snapshot then holds its markup."""

        class V(LiveView):
            exposure_policy = "legacy"
            template = _page("{{ plain }}")

            def mount(self, request, **kwargs):
                self.deep = [[[[[MarkedForm(data={})]]]]]
                self.plain = "x"

        client = LiveViewTestClient(V)
        client.mount()
        client.render()
        snapshot = bytes(client.view_instance._rust_view.serialize_msgpack())
        assert b"id_name" in snapshot

    def test_the_session_state_is_what_it_was(self):
        from djust.serialization import normalize_django_value

        flat = normalize_django_value({"form": MarkedForm(data={})}, state_roundtrip=True)
        assert set(flat["form"]) == {"name"} and "<input" in flat["form"]["name"]

    def test_get_state_is_what_it_was(self):
        """``get_state()`` (the persistence reader) has always refused a form attribute: it is
        not JSON. Nothing here changes that, and the form stays on the view untouched."""
        client = self._view()
        with override_settings(DEBUG=False):
            state = client.view_instance.get_state()
        assert "form" not in state and state["plain"] == MARKER + "-plain"
        assert isinstance(client.view_instance.form, forms.BaseForm)

    def test_a_stale_flattened_copy_is_dropped_even_for_a_form_that_did_not_change(self):
        """The rolling-deploy case: a Rust state written by a build that flattened the form still
        holds ``{name: html}`` under ``form``. The value stack answers first, so it must go, on an
        event that does not re-send the form too."""

        class V(LiveView):
            exposure_policy = "legacy"
            template = _page("{{ form.name }}[{{ tick }}]")

            def mount(self, request, **kwargs):
                self.form = MarkedForm(data={})
                self.tick = 0

            @event_handler()
            def unrelated(self, **kwargs):
                self.tick += 1

        client = LiveViewTestClient(V)
        client.mount()
        client.render()
        client.view_instance._rust_view.update_state({"form": {"name": "STALE-3767"}})
        client.send_event("unrelated")
        out = _inner(client.render())
        assert "STALE-3767" not in out and 'id="id_name"' in out and out.endswith("[1]"), out

    def test_form_held_keys_is_framework_bookkeeping(self):
        from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

        assert "_form_held_keys" in _FRAMEWORK_INTERNAL_ATTRS
        client = self._view()
        assert client.view_instance._form_held_keys == frozenset({"form"})


# --------------------------------------------------------------------------- #


def _http_get(view_cls: type[LiveView]) -> str:
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    return view_cls.as_view()(request).content.decode()


class WsFormView(LiveView):
    exposure_policy = "legacy"
    template = _page(COMPOSITE)

    def mount(self, request, **kwargs):
        self.form = _unbound()

    @event_handler()
    def to_invalid(self, **kwargs):
        self.form = _invalid()

    @event_handler()
    def to_valid(self, **kwargs):
        self.form = _valid()


class HttpFormView(LiveView):
    exposure_policy = "legacy"
    template = _page(COMPOSITE)

    def mount(self, request, **kwargs):
        self.form = _invalid()


async def _ws_run(view_path: str, events: list[str]) -> list[str]:
    """The html after mount, and (via ``request_html``) after each event."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Session:
        session_key = "ws-3767"

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _Session()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": "/"})
    out: list[str] = []
    frame: dict[str, Any] = {}
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=30)
        if frame.get("type") in ("mount", "error"):
            break
    assert frame.get("type") == "mount", frame
    out.append(str(frame.get("html", "")))
    for ref, event in enumerate(events, 1):
        await communicator.send_json_to({"type": "event", "event": event, "params": {}, "ref": ref})
        for _ in range(8):
            frame = await communicator.receive_json_from(timeout=30)
            if frame.get("type") in ("patch", "html_update", "error", "noop"):
                break
        assert frame.get("type") != "error", frame
        await communicator.send_json_to({"type": "request_html"})
        for _ in range(8):
            frame = await communicator.receive_json_from(timeout=30)
            if frame.get("type") in ("html_recovery", "error"):
                break
        assert frame.get("type") == "html_recovery", frame
        out.append(str(frame.get("html", "")))
    await communicator.disconnect()
    return out


@pytest.mark.django_db(transaction=True)
class TestTheRealEntries:
    def test_http_get_renders_the_form(self):
        pytest.importorskip("channels")
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            html = _http_get(HttpFormView)
        assert _inner(html) == _django(COMPOSITE, {"form": _invalid()})
        assert REQUIRED in html

    def test_websocket_mount_and_events(self):
        pytest.importorskip("channels")
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            frames = asyncio.run(
                _ws_run(f"{__name__}.WsFormView", ["to_invalid", "to_valid", "to_invalid"])
            )
        expected = [
            _canon(_django(COMPOSITE, {"form": make()}))
            for make in (_unbound, _invalid, _valid, _invalid)
        ]
        got = [_canon(f) for f in frames]
        assert got == expected
        assert len(set(got)) == 3, "the steps differ from each other (not vacuous)"
        assert REQUIRED in got[1] and REQUIRED not in got[2]


# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestIncludesCarryTheForm:
    """``{% include %}`` is how a form page is usually split: the form, a field and a part of it."""

    FILES = {
        "field_3767.html": "<i>{{ field.label }}{{ field.errors }}{{ field.field.required }}</i>",
        "whole_3767.html": "<b>{{ form.name.errors }}{% for f in form %}{{ f.label }}{% endfor %}</b>",
        "form_3767.html": (
            "{% for f in form %}{% include 'field_3767.html' with field=f %}{% endfor %}|"
            "{% include 'field_3767.html' with field=form.name %}|"
            "{% include 'whole_3767.html' %}|"
            "{% include 'whole_3767.html' with form=form %}|"
            "{% include 'field_3767.html' with field=d.form.name only %}"
        ),
    }

    @pytest.mark.parametrize("make", [_invalid, _valid, _unbound])
    def test_includes_match_django(self, tmp_path, make):
        for name, body in self.FILES.items():
            (tmp_path / name).write_text(body)
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ]
        src = "{% include 'form_3767.html' %}"

        class V(LiveView):
            exposure_policy = "legacy"
            template = _page(src)

            def mount(self, request, **kwargs):
                self.form = make()
                self.d = {"form": make()}

        with override_settings(TEMPLATES=templates):
            client = LiveViewTestClient(V)
            client.mount()
            got = _inner(client.render())
            from django.template.loader import get_template

            want = _norm(
                get_template("form_3767.html").render({"form": make(), "d": {"form": make()}})
            )
        assert got == want
        assert len(want) > 40 and "Name" in want, want


@pytest.mark.django_db(transaction=True)
class TestTheSseTransport:
    """SSE mounts and dispatches through the same ``ViewRuntime`` and the same
    ``_sync_state_to_rust`` as the WebSocket; this is the real mount + event over it."""

    @pytest.mark.asyncio
    async def test_sse_mount_and_event_carry_the_form(self):
        pytest.importorskip("channels")
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from djust.runtime import SSESessionTransport, ViewRuntime
        from djust.sse import SSESession, _sse_sessions

        session = SSESession("sse-3767")
        _sse_sessions["sse-3767"] = session
        session.runtime = ViewRuntime(SSESessionTransport(session))
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = SessionStore()
        session._request = request

        def frames() -> list[dict[str, Any]]:
            out = []
            while not session.queue.empty():
                item = session.queue.get_nowait()
                if isinstance(item, dict):
                    out.append(item)
            return out

        try:
            with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
                await session.runtime.dispatch_mount(
                    {"type": "mount", "view": f"{__name__}.WsFormView", "url": "/", "params": {}}
                )
                mounted = [f for f in frames() if f.get("type") == "mount"]
                assert mounted, "no mount frame"
                assert _canon(mounted[0]["html"]) == _canon(
                    _django(COMPOSITE, {"form": _unbound()})
                )
                await session.runtime.dispatch_event(
                    {"type": "event", "event": "to_invalid", "params": {}, "ref": 1}
                )
                after = [f for f in frames() if f.get("type") in {"patch", "html_update"}]
                assert after, "no frame after the event"
                assert REQUIRED in "".join(str(f) for f in after)
        finally:
            _sse_sessions.pop("sse-3767", None)


class ShellView(LiveView):
    """The form is read OUTSIDE the dj-root too: the page shell renders that part, on its own
    ``RustLiveView`` (``render_full_template``), which only a ``template_name`` view has."""

    exposure_policy = "legacy"
    template_name = "page_3767.html"

    def mount(self, request, **kwargs):
        self.form = _invalid()
        self.d = {"form": _invalid()}


SHELL_BASE = (
    "<html><body><p id='shell'>{{ form.name.errors }}"
    "[{% for f in form %}{{ f.label }}{% endfor %}]|{{ d.form.name.errors }}</p>"
    "{% block content %}{% endblock %}</body></html>"
)
SHELL_PAGE = (
    '{% extends "base_3767.html" %}{% block content %}'
    "<div dj-root>{{ form.name.errors }}</div>{% endblock %}"
)


@pytest.mark.django_db(transaction=True)
class TestThePageShellAndComponents:
    def test_the_shell_reads_the_form(self, tmp_path):
        pytest.importorskip("channels")
        (tmp_path / "base_3767.html").write_text(SHELL_BASE)
        (tmp_path / "page_3767.html").write_text(SHELL_PAGE)
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ]
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], TEMPLATES=templates):
            html = _http_get(ShellView)
        shell = re.search(r"<p id=.shell.>(.*?)</p>", html, re.S)
        assert shell, html[:400]
        want = _django(
            "{{ form.name.errors }}[{% for f in form %}{{ f.label }}{% endfor %}]|"
            "{{ d.form.name.errors }}",
            {"form": _invalid(), "d": {"form": _invalid()}},
        )
        assert _norm(shell.group(1)) == want
        assert _norm(shell.group(1)).count(REQUIRED) == 2
        assert "[NameMailPlanNote]" in _norm(shell.group(1))

    def test_the_shell_reads_the_form_when_the_caller_passes_no_context(self, tmp_path):
        """``render_full_template(request)`` with no ``serialized_context`` (the entry a child or a
        fallback uses) builds its own state from the view's context: the second branch."""
        (tmp_path / "base_3767.html").write_text(SHELL_BASE)
        (tmp_path / "page_3767.html").write_text(SHELL_PAGE)
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ]
        with override_settings(TEMPLATES=templates):
            client = LiveViewTestClient(ShellView)
            client.mount()
            view = client.view_instance
            view.get_template()  # sets _full_template, as the GET path does
            html = view.render_full_template(view.request)
        shell = re.search(r"<p id=.shell.>(.*?)</p>", html, re.S)
        assert shell, html[:400]
        text = _norm(shell.group(1))
        assert text.count(REQUIRED) == 2 and "[NameMailPlanNote]" in text, text

    def test_a_template_name_component_reads_the_form(self):
        from djust.components.base import _render_template_name_with_markers

        loaders = [
            (
                "django.template.loaders.locmem.Loader",
                {
                    "form_component_3767.html": (
                        "<div>{{ form.name.errors }}[{% for f in form %}{{ f.label }}{% endfor %}]"
                        "{{ d.form.name.errors }}</div>"
                    )
                },
            )
        ]
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": loaders},
            }
        ]
        with override_settings(TEMPLATES=templates):
            out = _render_template_name_with_markers(
                "form_component_3767.html",
                {"form": _invalid(), "d": {"form": _invalid()}},
            )
        assert out is not None
        text = _norm(out)
        assert text.count(REQUIRED) == 2 and "[NameMailPlanNote]" in text, text


# --------------------------------------------------------------------------- #


class TestEveryEntryThatSyncsAContextHonoursTheDecision:
    """The decision is read at each place that hands the engine a state dict and the raw objects
    separately. The set is DERIVED from the source (#2727), so a new entry that calls
    ``update_state`` without it fails here instead of reintroducing the flattened copy (#1646)."""

    ROOT = Path(__file__).resolve().parents[1] / "djust"

    def _sites(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for path in self.ROOT.rglob("*.py"):
            if "/tests/" in str(path):
                continue
            body = path.read_text()
            count = len(re.findall(r"\.update_state\(", body))
            if count:
                found[str(path.relative_to(self.ROOT))] = count
        return found

    def test_the_update_state_call_sites_are_the_ones_that_decide(self):
        sites = self._sites()
        assert sites == {
            "components/base.py": 1,
            "mixins/rust_bridge.py": 1,
            "mixins/template.py": 1,
        }, sites
        for relative in sites:
            body = (self.ROOT / relative).read_text()
            assert "holds_form" in body or "without_form_values" in body, relative

    def test_nothing_else_flattens_a_form_for_the_engine(self):
        """``render_form_value`` is the JSON-state converter. Its callers are the ones that
        produce JSON (the session / snapshot ``normalize_django_value``), not an engine entry."""
        callers = set()
        for path in self.ROOT.rglob("*.py"):
            if "/tests/" in str(path):
                continue
            if re.search(r"render_form_value\(", path.read_text()):
                callers.add(str(path.relative_to(self.ROOT)))
        assert callers == {"serialization.py", "template/serialization.py"}, callers
