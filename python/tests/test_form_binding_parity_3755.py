"""A Django ``Form`` / ``BoundField`` bound by ``{% for %}`` or ``{% with %}`` reaches a tag, a
filter and an attribute read as the form object, as it does under Django (#3755).

The plain-backend path used to flatten a ``Form`` to ``{name: SafeString(widget HTML)}`` before the
engine saw it. That keeps ``{{ form.name }}`` working and loses everything else a form IS:
iterating it yields ``BoundField``s (the flattened dict iterated its KEYS), ``f.errors`` /
``f.label`` / ``f.field.required`` read attributes a string does not have, ``{{ form }}`` is its
``as_div`` markup, ``form|length`` is 0. ``{% field_error field=form.name %}`` outside a loop
worked only because the raw-Python sidecar still held the live form under its own name (#3753).

Three layers had to agree, and each has a test that fails without it:

* ``template.serialization.serialize_value(for_render=True)`` lets the object cross;
* ``value_into_handler_pyobject`` hands a bridged Python tag the live ``BoundField`` although a
  ``BoundField`` is iterable (it iterates its subwidgets), where it used to hand a wrapper with no
  ``errors`` (``context::live_handle_is_form_object``);
* a ``list`` SUBCLASS that spells itself (Django's ``ErrorList``) crosses as the carrier, so
  ``{{ f.errors }}`` is ``str(o)`` (``<ul class="errorlist">``) and not ``['...']``
  (``list_subclass_spells_itself``, the list half of #2899).

What each class proves:

* ``TestTheReportedShapes``: the shapes from the issue, byte-identical under both engines AND
  carrying the error text (an equality between two empty strings proves nothing).
* ``TestRandomisedDifferential``: seeded random templates over loops, ``with``, nested scopes and
  attribute reads, three forms; a curated table samples the axes its author thought of (v1.1.1-2).
* ``TestTheLayers``: each of the three fixes on its own, so a gate-off of one names it.
* ``TestNothingElseMoved``: what the flattening used to guarantee still holds.

Refs #3755, #3666, #3753, #2899, #2704, #1646, #1468.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

import pytest
from django import forms
from django.template import Context, Engine

from scripts.lib.template_library_parity import harness

COMPONENTS = "djust.components.templatetags.djust_components"
LIBRARIES = {
    "djust_components": COMPONENTS,
    "theme_tags": "djust.theming.templatetags.theme_tags",
}
LOAD = "{% load djust_components %}"
REQUIRED = "This field is required."


class SignupForm(forms.Form):
    name = forms.CharField(help_text="Your name")
    email = forms.EmailField(label="Mail")
    plan = forms.ChoiceField(choices=[("a", "Alpha"), ("b", "Beta")], widget=forms.RadioSelect)
    note = forms.CharField(required=False, widget=forms.Textarea)


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


FORMS = {"invalid": _invalid, "valid": _valid, "unbound": _unbound}


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    component_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {"NAME": "binding-3755", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


def _render(engines, which: str, src: str, context: dict[str, Any]) -> tuple[str, ...]:
    """``("ok", html)`` or ``("err", ExceptionType, message)``."""
    rust, django = engines
    try:
        if which == "rust":
            out = rust.from_string(src).render(dict(context))
        else:
            out = django.from_string(src).render(Context(dict(context)))
    except Exception as exc:  # noqa: BLE001 - the exception IS the observation
        return ("err", type(exc).__name__, str(exc))
    return ("ok", re.sub(r"\s+", " ", str(out)).strip())


def _both(engines, src: str, make_form=_invalid) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # A fresh form per engine: ``is_valid`` / ``errors`` are cached on the instance.
    return (
        _render(engines, "django", LOAD + src, {"form": make_form()}),
        _render(engines, "rust", LOAD + src, {"form": make_form()}),
    )


# --------------------------------------------------------------------------- #


class TestTheReportedShapes:
    #: (id, template body, text the FORM contributes, so the equality is not two empty strings)
    SHAPES = [
        (
            "field_error in a loop",
            "{% for f in form %}[{% field_error field=f %}]{% endfor %}",
            REQUIRED,
        ),
        (
            "field_error in with",
            "{% with f=form.name %}{% field_error field=f %}{% endwith %}",
            REQUIRED,
        ),
        ("errors in a loop", "{% for f in form %}[{{ f.errors }}]{% endfor %}", REQUIRED),
        ("errors in with", "{% with f=form.name %}{{ f.errors }}{% endwith %}", REQUIRED),
        ("errors, no loop", "{{ form.name.errors }}", REQUIRED),
        ("field_error, no loop", "{% field_error field=form.name %}", REQUIRED),
        (
            "field_error in a nested with",
            "{% for f in form %}{% with g=f %}{% field_error field=g %}{% endwith %}{% endfor %}",
            REQUIRED,
        ),
        (
            "field_error from visible_fields",
            "{% for f in form.visible_fields %}{% field_error field=f %}{% endfor %}",
            REQUIRED,
        ),
        ("widget in a loop", "{% for f in form %}[{{ f }}]{% endfor %}", 'name="name"'),
        (
            "attributes in a loop",
            "{% for f in form %}[{{ f.name }}|{{ f.label }}|{{ f.html_name }}"
            "|{{ f.field.required }}|{{ f.id_for_label }}|{{ f.help_text }}]{% endfor %}",
            "name|Name|name|True|id_name|Your name",
        ),
        (
            "forloop beside a field",
            "{% for f in form %}{{ forloop.counter }}:{{ f.label }}|{% endfor %}",
            "1:Name|2:Mail|3:Plan",
        ),
        (
            "conditional on errors",
            "{% for f in form %}{% if f.errors %}E{% else %}ok{% endif %}{% endfor %}",
            "E",
        ),
        (
            "subwidgets of a field",
            "{% for f in form %}{% if f.name == 'plan' %}"
            "{% for w in f %}{{ w.choice_label }},{% endfor %}{% endif %}{% endfor %}",
            "Alpha,Beta,",
        ),
        ("the form itself", "{{ form }}", 'name="email"'),
        ("length of a form", "{{ form|length }}", "0"),
        ("truthiness", "{% if form %}T{% else %}F{% endif %}", "T"),
        ("widget by name", "{{ form.email }}", 'name="email"'),
        ("non-field errors", "{{ form.non_field_errors }}", ""),
    ]

    @pytest.mark.parametrize("label,body,marker", SHAPES, ids=[s[0] for s in SHAPES])
    @pytest.mark.parametrize("which", list(FORMS))
    def test_identical_under_both_engines(self, engines, label, body, marker, which):
        django, rust = _both(engines, body, FORMS[which])
        assert django[0] == "ok", f"Django did not render {label}: {django}"
        assert rust == django, f"{label} [{which}]:\n  django {django}\n  rust   {rust}"
        if which == "invalid" and marker:
            assert marker in django[1], f"{label}: the form contributed nothing: {django[1][:200]}"

    def test_the_error_text_reaches_every_error_spelling(self, engines):
        """The reported cells, stated outright rather than only as 'the two agree'."""
        for body in (
            "{% for f in form %}{% field_error field=f %}{% endfor %}",
            "{% with f=form.name %}{% field_error field=f %}{% endwith %}",
            "{% for f in form %}{{ f.errors }}{% endfor %}",
        ):
            _django, rust = _both(engines, body)
            assert REQUIRED in rust[1], f"{body}: {rust}"

    def test_a_loop_binds_the_object_not_a_key(self, engines):
        """The flattened form iterated its KEYS: ``{{ f }}`` was ``name``, ``{{ f.label }}`` ''."""
        _django, rust = _both(engines, "{% for f in form %}{{ f.label }},{% endfor %}")
        assert rust == ("ok", "Name,Mail,Plan,Note,")


# --------------------------------------------------------------------------- #

#: Snippets valid inside ``{% for f in form %}`` (variable ``f``).
INNER = [
    "{{ f }}",
    "{{ f.errors }}",
    "{{ f.name }}",
    "{{ f.label }}",
    "{{ f.value }}",
    "{{ f.help_text }}",
    "{{ f.html_name|upper }}",
    "{{ f.field.required }}",
    "{{ f.field.label }}",
    "{{ f.id_for_label }}",
    "{{ f.errors|length }}",
    "{{ f.errors.0 }}",
    "{{ forloop.counter }}",
    "{{ forloop.first }}",
    "{% field_error field=f %}",
    "{% if f.errors %}E{% else %}ok{% endif %}",
    "{% if f.field.required %}req{% endif %}",
    "{% for e in f.errors %}<{{ e }}>{% endfor %}",
    "{% if f.name == 'plan' %}{% for w in f %}{{ w.choice_label }};{% endfor %}{% endif %}",
    "{% with g=f %}{{ g.label }}{% field_error field=g %}{% endwith %}",
    "{% with e=f.errors %}{{ e|length }}{% endwith %}",
    "{% with n=f.name %}{{ n }}{% endwith %}",
]
#: Snippets with no loop variable.
OUTER = [
    "{{ form }}",
    "{{ form.name }}",
    "{{ form.name.errors }}",
    "{{ form.email.label }}",
    "{{ form.errors }}",
    "{{ form|length }}",
    "{% field_error field=form.name %}",
    "{% if form %}T{% else %}F{% endif %}",
    "{% for f in form.visible_fields %}{{ f.label }}{% endfor %}",
    "{% for f in form.hidden_fields %}h{% endfor %}",
    "{% for name in form.fields %}{{ name }},{% endfor %}",
    "{{ form.non_field_errors }}",
    "{{ form.is_valid }}",
]
SOURCES = ["form", "form.visible_fields", "form.hidden_fields"]


def _random_template(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(1, 4)):
        if rng.random() < 0.55:
            source = rng.choice(SOURCES)
            body = "".join(
                rng.choice(INNER) + rng.choice(["", "|", ","]) for _ in range(rng.randint(1, 3))
            )
            parts.append("{% for f in " + source + " %}" + body + "{% endfor %}")
        elif rng.random() < 0.5:
            field = rng.choice(["name", "email", "plan", "note"])
            body = (
                rng.choice(INNER)
                .replace("forloop.counter", "f.label")
                .replace("forloop.first", "f.name")
            )
            body = re.sub(r"\{% if f.name == 'plan' %\}.*?\{% endif %\}", "{{ f.label }}", body)
            parts.append("{% with f=form." + field + " %}" + body + "{% endwith %}")
        else:
            parts.append(rng.choice(OUTER))
    return "".join(parts)


class TestRandomisedDifferential:
    def test_random_templates_render_identically(self, engines):
        """A seeded sample of templates built from the snippets above, against Django.

        The curated table covers the shapes its author named; this covers the combinations
        (a ``with`` over a field inside a loop over another, ``errors`` beside ``forloop``)
        nobody wrote down. The seed is fixed so a failure reproduces, and the count is
        asserted so a snippet table that stopped generating cannot pass vacuously."""
        rng = random.Random(3755)
        cases = [_random_template(rng) for _ in range(300)]
        assert len({c for c in cases}) > 200, "the generator stopped varying"
        mismatches = []
        for src in cases:
            for which, make_form in FORMS.items():
                django, rust = _both(engines, src, make_form)
                if rust != django:
                    mismatches.append(f"[{which}] {src}\n   django {django}\n   rust   {rust}")
        assert not mismatches, f"{len(mismatches)} mismatches:\n" + "\n".join(mismatches[:8])


# --------------------------------------------------------------------------- #


class TestTheLayers:
    """Each fix alone, so removing one names the layer that regressed."""

    def test_the_object_crosses_serialize_value(self):
        from djust.template.serialization import serialize_context

        form = _invalid()
        out = serialize_context({"form": form, "field": form["name"]}, for_render=True)
        assert out["form"] is form
        assert out["field"] is form["name"]

    def test_the_json_state_path_still_flattens(self):
        """``for_render`` is the native-render switch. The state converters need a plain value
        and keep ``render_form_value`` (``{name: SafeString}``), unchanged."""
        from djust.serialization import render_form_value
        from djust.template.serialization import serialize_value

        form = _invalid()
        flat = serialize_value(form)
        assert isinstance(flat, dict) and set(flat) == set(form.fields)
        assert flat["name"] == render_form_value(form)["name"]

    def test_a_bridged_tag_receives_the_live_boundfield(self, engines):
        """``field_error`` reads ``field.errors``; a ``BoundField`` is iterable, so the handler
        used to get a wrapper without it (``context::live_handle_is_form_object``)."""
        out = _render(
            engines,
            "rust",
            LOAD + "{% for f in form %}{% field_error field=f %}{% endfor %}",
            {"form": _invalid()},
        )
        assert out[0] == "ok" and out[1].count(REQUIRED) == 2, out

    def test_the_decision_is_the_form_family_and_nothing_else(self):
        """The iterable exception names Django's form types. A deque, a generator and a plain
        iterable class keep the wrapper, because the leaf floor cannot see inside them."""
        import collections

        from djust.serialization import _is_form_object

        form = _invalid()
        assert _is_form_object(form) and _is_form_object(form["name"])
        for other in (collections.deque([1]), (i for i in range(2)), [1], {"a": 1}, "s", 3):
            assert not _is_form_object(other)

    def test_an_errorlist_crosses_as_its_own_spelling(self, engines):
        out = _render(engines, "rust", "{{ errs }}", {"errs": _invalid()["name"].errors})
        assert out == (
            "ok",
            '<ul class="errorlist" id="id_name_error"><li>' + REQUIRED + "</li></ul>",
        )

    def test_an_errorlist_is_still_a_sequence(self, engines):
        """The carrier keeps the items: loops, ``length``, indexing and ``if`` read them."""
        errs = _invalid()["email"].errors
        for body in (
            "{% for e in errs %}<{{ e }}>{% endfor %}",
            "{{ errs|length }}",
            "{{ errs.0 }}",
            "{% if errs %}T{% else %}F{% endif %}",
            "{{ errs|first }}",
            "{{ errs|join:'/' }}",
        ):
            django = _render(engines, "django", body, {"errs": errs})
            rust = _render(engines, "rust", body, {"errs": errs})
            assert django[0] == "ok" and rust == django, (body, django, rust)

    def test_an_empty_errorlist_is_falsy_and_renders_empty(self, engines):
        errs = _valid()["name"].errors
        for body in ("{{ errs }}", "{% if errs %}T{% else %}F{% endif %}", "{{ errs|length }}"):
            django = _render(engines, "django", body, {"errs": errs})
            rust = _render(engines, "rust", body, {"errs": errs})
            assert rust == django, (body, django, rust)

    def test_an_errordict_keeps_its_own_spelling(self, engines):
        """The dict half, on the same Python path: ``serialize_value`` rebuilt any ``dict``
        subclass as a plain dict, so a top-level ``form.errors`` printed ``{...}``."""
        context = {"errs": _invalid().errors}
        for body in (
            "{{ errs }}",
            "{% for k in errs %}{{ k }},{% endfor %}",
            "{{ errs.email }}",
            "{{ errs|length }}",
            "{% if errs %}T{% else %}F{% endif %}",
        ):
            django = _render(engines, "django", body, context)
            rust = _render(engines, "rust", body, context)
            assert django[0] == "ok" and rust == django, (body, django, rust)

    def test_serialize_value_passes_only_markup_rendering_containers_through(self):
        """Narrower than the engine's rule on purpose (see ``_spells_itself``): a top-level
        ``OrderedDict`` or a ``str``-overriding list WITHOUT ``__html__`` is rebuilt as before,
        and its values are still converted (a ``FieldFile`` becomes its URL)."""
        import collections

        from djust.request_scoped import RequestScopedList
        from djust.template.serialization import serialize_value

        class Plain(dict):
            pass

        class Spelt(list):
            def __str__(self) -> str:
                return "custom"

        errs = _invalid()["name"].errors
        assert serialize_value(errs, for_render=True) is errs
        assert serialize_value(errs) is not errs  # the JSON helper still rebuilds
        errors = _invalid().errors
        assert serialize_value(errors, for_render=True) is errors
        assert type(serialize_value(Plain(a=1), for_render=True)) is dict
        assert type(serialize_value(RequestScopedList([1, 2]), for_render=True)) is list
        assert type(serialize_value(Spelt([1]), for_render=True)) is list
        assert type(serialize_value(collections.OrderedDict(a=1), for_render=True)) is dict

    def test_list_subclasses_that_do_not_respell_stay_lists(self):
        """The rule is the dict one (#2899): override ``__str__`` / ``__repr__`` or be a list.
        Compared by identity against ``list``'s own slots, so inheriting both is 'not respelt'."""
        from djust import _rust
        from djust.request_scoped import RequestScopedList

        class Plain(list):
            pass

        class WithStr(list):
            def __str__(self) -> str:
                return "custom"

        class WithRepr(list):
            def __repr__(self) -> str:
                return "custom"

        assert not _rust.crosses_as_encoded([1, 2])
        assert not _rust.crosses_as_encoded(Plain([1, 2]))
        assert not _rust.crosses_as_encoded(RequestScopedList([1, 2]))
        assert _rust.crosses_as_encoded(WithStr([1, 2]))
        assert _rust.crosses_as_encoded(WithRepr([1, 2]))
        assert _rust.crosses_as_encoded(_invalid()["name"].errors)


# --------------------------------------------------------------------------- #


class TestNothingElseMoved:
    def test_a_form_nested_in_containers_still_renders(self, engines):
        context = {"forms": [_invalid(), _valid()], "by_name": {"a": _invalid()}}
        for body in (
            "{% for f in forms %}{{ f.name }}|{% endfor %}",
            "{% for f in forms %}{% for b in f %}{{ b.label }},{% endfor %}{% endfor %}",
            "{{ by_name.a.name }}",
            "{% for f in by_name.a %}{{ f.errors }}{% endfor %}",
        ):
            django = _render(engines, "django", body, context)
            rust = _render(engines, "rust", body, context)
            assert django[0] == "ok" and rust == django, (body, django, rust)

    def test_widget_html_is_not_escaped(self, engines):
        _django, rust = _both(engines, "{{ form.name }}")
        assert "<input" in rust[1] and "&lt;" not in rust[1]

    def test_a_loop_variable_named_like_the_context_key_is_the_loop_value(self, engines):
        """``{% for form in forms %}`` rebinds ``form``; the sidecar's ``form`` must not win."""
        context = {"form": _unbound(), "forms": [_invalid()]}
        body = "{% for form in forms %}{{ form.name.errors }}{% endfor %}"
        django = _render(engines, "django", body, context)
        rust = _render(engines, "rust", body, context)
        assert django[0] == "ok" and REQUIRED in django[1] and rust == django, (django, rust)

    def test_the_parity_harness_form_works_in_a_loop_too(self, engines):
        """The parity ratchet's own form (``harness._bound_form``, with a form-level error)
        through ``{% form_errors %}`` and a loop, a shape the ratchet never gave it."""
        for body in (
            "{% form_errors form=form %}",
            "{% for f in form %}{% field_error field=f %}{% endfor %}",
        ):
            django = _render(engines, "django", LOAD + body, {"form": harness._bound_form()})
            rust = _render(engines, "rust", LOAD + body, {"form": harness._bound_form()})
            assert django[0] == "ok" and rust == django, (body, django, rust)
            assert "required" in django[1].lower() or "Form-level" in django[1], django


# --------------------------------------------------------------------------- #
# #3770 review: the carrier is `str(o)` for TEXT and the underlying container for JSON


class TestAnErrorContainerKeepsItsDataForJson:
    """``{{ form.errors|json_script:'e' }}`` is the errors as JSON on Django and was, after the
    first version of this PR, the errorlist HTML as a JSON string: the carrier's JSON form was
    ``str(o)``. A carrier made for a ``list`` / ``dict`` SUBCLASS must spell itself for text and
    stay the container for everything that reads the data."""

    BODIES = [
        "{{ form.errors|json_script:'e' }}",
        "{{ form.name.errors|json_script:'e' }}",
        "{{ form.email.errors|json_script:'e' }}",
        "{{ errs|json_script:'e' }}",
        "{{ errlist|json_script:'e' }}",
        "{{ nested|json_script:'e' }}",
        "{% for f in form %}{{ f.errors|json_script:'e' }}{% endfor %}",
        "{% with e=form.name.errors %}{{ e|json_script:'e' }}{% endwith %}",
        # neighbours that read the container or its spelling
        "{{ errlist|safe }}",
        "{{ errlist|escape }}",
        "{{ errlist|force_escape }}",
        "{{ errlist|length }}",
        "{{ errlist|first }}",
        "{{ errlist|join:', ' }}",
        "{{ errlist|default:'none' }}",
        "{{ errlist|slice:':1' }}",
        "{{ errs|safe }}",
        "{{ errs|escape }}",
        "{{ errs|length }}",
        "{{ errs.email|first }}",
        "{{ form.errors|safe }}",
        "{{ form.name.errors|safe }}",
        "{{ form.name.errors|escape }}",
    ]

    def _context(self):
        form = _invalid()
        return {
            "form": form,
            "errs": form.errors,
            "errlist": form["email"].errors,
            "nested": {"errors": form.errors, "email": form["email"].errors, "n": 1},
        }

    @pytest.mark.parametrize("body", BODIES)
    def test_identical_to_django(self, engines, body):
        # Fresh objects per engine: ``errors`` is cached on the form instance.
        django = _render(engines, "django", LOAD + body, self._context())
        rust = _render(engines, "rust", LOAD + body, self._context())
        assert django[0] == "ok", django
        assert rust == django, f"{body}\n  django {django}\n  rust   {rust}"

    def test_the_json_is_the_data_not_the_markup(self, engines):
        out = _render(engines, "rust", LOAD + "{{ form.errors|json_script:'e' }}", self._context())
        assert "errorlist" not in out[1], out
        assert "Enter a valid email address." in out[1]
        # an EMPTY list is `[]`, not `""`
        out2 = _render(
            engines,
            "rust",
            LOAD + "{{ form.name.errors|json_script:'e' }}",
            {"form": _valid()},
        )
        assert ">[]<" in out2[1], out2

    def test_a_list_subclass_with_its_own_str_keeps_json_and_text_apart(self, engines):
        class Tagged(list):
            def __str__(self) -> str:
                return "TEXT-FORM"

        class Holder:
            """Reached through an object, so it is the engine's rule (any ``__str__`` /
            ``__repr__`` override) that applies, not ``serialize_value``'s narrower one."""

            def __init__(self) -> None:
                self.t = Tagged(["a", "b"])

        ctx = {"h": Holder()}
        for body in (
            "{{ h.t }}",
            "{{ h.t|json_script:'e' }}",
            "{{ h.t|length }}",
            "{{ h.t|join:'-' }}",
            "{% for x in h.t %}{{ x }}{% endfor %}",
        ):
            django = _render(engines, "django", body, ctx)
            rust = _render(engines, "rust", body, ctx)
            assert django[0] == "ok" and rust == django, (body, django, rust)


# --------------------------------------------------------------------------- #
# #3770 review: a probe that raises must not hide the real exception


class _BoomRender(forms.TextInput):
    def get_context(self, name, value, attrs):
        raise RuntimeError("boom-render-3770")


class _BoomSubwidgets(forms.TextInput):
    def subwidgets(self, name, value, attrs=None):
        raise RuntimeError("boom-subwidgets-3770")


class _RenderForm(forms.Form):
    a = forms.CharField()
    b = forms.CharField(widget=_BoomRender)


class _SubwidgetsForm(forms.Form):
    a = forms.CharField()
    b = forms.CharField(widget=_BoomSubwidgets)


class TestARaisingProbeDoesNotHideTheRealException:
    """The conversion measures ``str(o)`` / ``bool(o)`` / ``len(o)`` / the items of an object it
    carries. When a probe raised, the object kept only its live handle and was marked NOT
    iterable, so ``{% for f in form %}`` failed with ``'_RenderForm' object is not iterable`` and
    the widget's own exception was never shown. Django is lazy: the loop over labels renders, and
    the exception appears only where the broken field is rendered."""

    def _outcome(self, engines, which, src, cls):
        return _render(engines, which, src, {"form": cls()})

    @pytest.mark.parametrize(
        "src",
        [
            "{% for f in form %}[{{ f.label }}]{% endfor %}",
            "{% for f in form %}[{{ f.name }}|{{ f.field.required }}]{% endfor %}",
            "{{ form.a.label }}",
            "{% for f in form %}{{ forloop.counter }}{% endfor %}",
        ],
    )
    def test_the_template_that_never_renders_the_broken_field_renders(self, engines, src):
        django = self._outcome(engines, "django", src, _RenderForm)
        rust = self._outcome(engines, "rust", src, _RenderForm)
        assert django[0] == "ok", django
        assert rust == django, (src, django, rust)

    @pytest.mark.parametrize(
        "src,cls,needle",
        [
            ("{{ form }}", _RenderForm, "boom-render-3770"),
            ("{% for f in form %}{{ f }}{% endfor %}", _RenderForm, "boom-render-3770"),
            (
                "{% for f in form %}{% for w in f %}{{ w }}{% endfor %}{% endfor %}",
                _SubwidgetsForm,
                "boom-subwidgets-3770",
            ),
        ],
    )
    def test_the_sink_that_needs_the_broken_part_raises_the_real_error(
        self, engines, src, cls, needle
    ):
        django = self._outcome(engines, "django", src, cls)
        rust = self._outcome(engines, "rust", src, cls)
        assert django[0] == "err" and needle in django[2], django
        assert rust[0] == "err", f"the Rust engine rendered where Django raised: {rust}"
        assert needle in rust[2], f"the real exception is hidden: {rust}"
        assert "not iterable" not in rust[2], rust
