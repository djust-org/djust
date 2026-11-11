"""A Django ``Form`` / ``BoundField`` crossing to the Rust engine is not rendered until a template
reads it (#3769).

The conversion of a form object used to call ``str(form)`` (the ``as_div`` render of EVERY field)
and ``str(bf)`` for every field a loop produced, on every render, for every form in the context,
whether or not the template printed it. A template that never touched the form paid 4-5x the cost
of the flattened dict it replaced. The text of such a carrier (``Encoded.display``) is now a
``DisplayText`` that asks the object on first read (``crates/djust_core/src/lib.rs``).

What each class proves, and what removing its mechanism does:

* ``TestTheFormIsNotRenderedUntilRead``: the observable saving, as counts of ``__str__`` calls on a
  form and on its ``BoundField``s. With the text eager every row is >= 1.
* ``TestEachOccurrenceIsOneRead``: a form printed N times is rendered N times, as under Django.
* ``TestTheOutputIsDjangos``: the same templates under Django's engine, with the markup the form
  contributes asserted non-empty, so an equality between two empty strings proves nothing.
* ``TestTheSideEffectsOfStrAreNoLongerAccidental``: ``str(form)`` runs ``full_clean()``; the eager
  conversion therefore validated every form it was handed, and a template could read
  ``form.cleaned_data`` (or ``repr(form)``, ``form.instance`` on a ModelForm) off a form nothing had
  validated. Django's engine renders those empty / ``valid=Unknown`` / the unmodified instance;
  now so does this one. The old output was the accident, not a contract.
* ``TestAStrThatRaises``: a raising ``__str__`` is the object's own exception at ``{{ form }}``
  and nowhere else, as under Django.
* ``TestTheFloorStillHolds``: laziness changes WHEN ``str()`` is asked, never of WHICH object. The
  asked object is the walk-floored handle, so a floor field's typed value is still not printed.

* ``TestTheDeferralIsNarrow``: the other half of the deferral (#3832): an ordinary iterable's
  ``__str__`` is still read when it is converted, and a model formset that arrives as
  ``_SidecarFormSetProxy`` is deferred. Neither changes the output, only the call count.

Refs #3769, #3770, #3755, #3790, #3832, #1646, #1468.
"""

from __future__ import annotations

import collections
import re
from typing import Any

import pytest
from django import forms
from django.forms import formset_factory
from django.forms.boundfield import BoundField
from django.template import Context, Engine
from django.utils.safestring import mark_safe

pytestmark = [pytest.mark.pr_tier]


class CountingBoundField(BoundField):
    """Counts ``str()`` per FIELD NAME, so a row can say which fields were rendered."""

    calls: dict[str, int] = {}

    def __str__(self) -> str:
        type(self).calls[self.name] = type(self).calls.get(self.name, 0) + 1
        return super().__str__()


class CountingCharField(forms.CharField):
    def get_bound_field(self, form, field_name):
        return CountingBoundField(form, self, field_name)


class CountingForm(forms.Form):
    """Counts ``str(form)``; its fields are ``CountingBoundField``s."""

    form_calls = 0
    f0 = CountingCharField(max_length=3, help_text="h0")
    f1 = CountingCharField(max_length=3, help_text="h1")
    f2 = CountingCharField(max_length=3, help_text="h2")

    def __str__(self) -> str:
        type(self).form_calls += 1
        return super().__str__()


@pytest.fixture(autouse=True)
def _reset_counters():
    CountingForm.form_calls = 0
    CountingBoundField.calls = {}
    yield


@pytest.fixture(scope="module")
def rust():
    from djust.template.backend import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "lazy-3769", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


@pytest.fixture(scope="module")
def django_engine():
    return Engine()


def _form(bound: bool = True) -> CountingForm:
    return CountingForm({"f0": "toolong", "f1": "ok", "f2": "ok"}) if bound else CountingForm()


def _render_rust(rust, src: str, **context: Any) -> str:
    return rust.from_string(src).render(dict(context))


def _render_django(django_engine, src: str, **context: Any) -> str:
    return django_engine.from_string(src).render(Context(dict(context)))


def _ws(html: str) -> str:
    return re.sub(r"\s+", " ", html).strip()


# --------------------------------------------------------------------------- #
class TestTheFormIsNotRenderedUntilRead:
    #: (id, template, str(form) calls, {field: str(bf) calls}). A field absent from the dict is
    #: pinned at ZERO: the saving is that a field nothing printed is never rendered.
    ROWS = [
        ("untouched", "hello", 0, {}),
        ("one field", "{{ form.f0 }}", 0, {"f0": 1}),
        ("one field's errors", "{{ form.f0.errors }}", 0, {}),
        ("one field's attributes", "{{ form.f0.label }}{{ form.f0.help_text }}", 0, {}),
        ("is_bound / is_valid", "{{ form.is_bound }}{{ form.is_valid }}", 0, {}),
        ("truthiness", "{% if form %}y{% endif %}", 0, {}),
        ("length", "{{ form|length }}", 0, {}),
        ("a loop that reads labels", "{% for f in form %}{{ f.label }}{% endfor %}", 0, {}),
        ("a loop that reads errors", "{% for f in form %}{{ f.errors }}{% endfor %}", 0, {}),
        (
            "a loop that prints each field",
            "{% for f in form %}{{ f }}{% endfor %}",
            0,
            {"f0": 1, "f1": 1, "f2": 1},
        ),
        ("whole form", "{{ form }}", 1, None),
        ("whole form, filtered", "{{ form|upper }}", 1, None),
        ("whole form, twice", "{{ form }}{{ form }}", 2, None),
    ]

    @pytest.mark.parametrize("name,src,form_calls,field_calls", ROWS, ids=[r[0] for r in ROWS])
    def test_str_is_asked_only_for_what_the_template_prints(
        self, rust, name, src, form_calls, field_calls
    ):
        _render_rust(rust, src, form=_form())
        assert CountingForm.form_calls == form_calls, (
            f"{name}: str(form) was called {CountingForm.form_calls}x, wanted {form_calls}"
        )
        if field_calls is not None:
            assert CountingBoundField.calls == field_calls, (
                f"{name}: str(BoundField) calls {CountingBoundField.calls}, wanted {field_calls}"
            )

    def test_a_form_in_the_context_that_the_template_never_names_is_never_rendered(self, rust):
        _render_rust(rust, "{{ other }}", form=_form(), other="x")
        assert CountingForm.form_calls == 0
        assert CountingBoundField.calls == {}

    def test_a_formset_of_forms_renders_none_it_does_not_print(self, rust):
        FormSet = formset_factory(CountingForm, extra=2)
        formset = FormSet(
            {"form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-0-f0": "a", "form-1-f0": "b"}
        )
        out = _render_rust(rust, "{{ formset.total_form_count }}", formset=formset)
        assert out == "2"
        assert CountingForm.form_calls == 0
        assert CountingBoundField.calls == {}


class TestEachOccurrenceIsOneRead:
    """A template that prints the form N times renders it N times, as Django does. The eager
    conversion rendered it once before the template printed anything, plus once per occurrence
    (``hello`` 1 call, ``{{ form }}`` 2; now 0 and 1)."""

    def test_a_form_printed_twice_is_rendered_twice_and_no_more(self, rust):
        _render_rust(rust, "{{ form }}|{{ form }}", form=_form())
        assert CountingForm.form_calls == 2

    def test_a_field_printed_twice_is_rendered_twice_and_no_more(self, rust):
        _render_rust(rust, "{{ form.f0 }}{{ form.f0 }}", form=_form())
        assert CountingBoundField.calls == {"f0": 2}

    def test_a_form_bound_by_with_is_rendered_once_per_print(self, rust):
        _render_rust(rust, "{% with f=form %}{{ f }}{{ f }}{% endwith %}", form=_form())
        assert CountingForm.form_calls == 2


# --------------------------------------------------------------------------- #
class Plain(forms.Form):
    name = forms.CharField(max_length=5, help_text="nm")
    color = forms.ChoiceField(choices=[("r", "Red"), ("g", "Green")])
    tags = forms.MultipleChoiceField(
        choices=[("a", "A"), ("b", "B")], widget=forms.CheckboxSelectMultiple
    )
    agree = forms.BooleanField(required=False)


class SafeStr(forms.Form):
    a = forms.CharField()

    def __str__(self) -> str:
        return mark_safe("<b>SAFE-FORM</b>")


class PlainStr(forms.Form):
    a = forms.CharField()

    def __str__(self) -> str:
        return "<i>plain & form</i>"


def _unbound():
    return Plain()


def _invalid():
    return Plain({"name": "toolongname", "color": "x", "tags": ["q"]})


def _valid():
    return Plain({"name": "ok", "color": "r", "tags": ["a"], "agree": "on"})


FORMS = {"unbound": _unbound, "invalid": _invalid, "valid": _valid}

#: Spellings both engines render identically for a plain form. Each is a different path from the
#: carrier to the output: the text itself, a filter over it, a field, a loop, a subwidget loop,
#: a conditional, a ``with`` alias, and the attributes a field carries.
SPELLINGS = [
    "{{ form }}",
    "{{ form.as_p }}",
    "{{ form.as_table }}",
    "{{ form.as_ul }}",
    "{{ form.as_div }}",
    "{{ form.name }}",
    "{{ form.name.errors }}",
    "{{ form.name.label }}|{{ form.name.value }}|{{ form.name.help_text }}",
    "{{ form.non_field_errors }}{{ form.errors }}",
    "{{ form.is_bound }}|{{ form.is_valid }}",
    "{{ form|length }}",
    "{% if form %}T{% else %}F{% endif %}",
    "{% for f in form %}{{ f }}{% endfor %}",
    "{% for f in form %}[{{ f.label }}:{{ f.errors|length }}]{% endfor %}",
    "{% for f in form.visible_fields %}{{ f }}{% endfor %}",
    "{% for f in form %}{% for w in f %}{{ w.tag }}{% endfor %}{% endfor %}",
    "{% for f in form %}{{ f.label_tag }}{% endfor %}",
    "{% with f=form.name %}{{ f }}|{{ f.label }}{% endwith %}",
    "{% with f=form %}{{ f.name }}{% endwith %}",
    "{{ form|upper }}",
    "{{ form|safe }}",
    "{{ form|escape }}",
    "{{ form|force_escape }}",
    "{{ form|default:'dflt' }}",
    "{{ form|linebreaksbr|length }}",
    "{{ form|wordcount }}",
    "{{ form|truncatechars:30 }}",
    "{{ form.name|length }}{{ form.name|upper }}{{ form.name|safe }}",
    "{{ form.fields.name.label }}|{{ form.fields.color.choices }}",
    "{{ form.errors.as_json }}",
    "{{ form.tags.0 }}|{{ form.tags.1 }}",
    "{% for w in form.tags %}{{ w.choice_label }}{% endfor %}",
    "<div>{{ form }}</div><p>{{ form.name }}</p>",
    "{{ form }}{{ form }}",
]


class TestTheOutputIsDjangos:
    @pytest.mark.parametrize("kind", sorted(FORMS))
    @pytest.mark.parametrize("src", SPELLINGS)
    def test_every_spelling_matches_the_django_engine(self, rust, django_engine, kind, src):
        # a fresh form per engine: ``errors`` / ``cleaned_data`` are cached on the instance
        want = _render_django(django_engine, src, form=FORMS[kind]())
        got = _render_rust(rust, src, form=FORMS[kind]())
        assert _ws(got) == _ws(want), src

    def test_the_corpus_is_not_vacuous(self, rust):
        # the whole-form and per-field spellings really print markup that carries the form
        assert 'name="name"' in _render_rust(rust, "{{ form }}", form=_invalid())
        assert "errorlist" in _render_rust(rust, "{{ form.name.errors }}", form=_invalid())
        assert len(SPELLINGS) >= 30

    def test_a_safe_str_is_not_escaped_and_a_plain_str_is(self, rust, django_engine):
        for form_class, expected in ((SafeStr, "<b>SAFE-FORM</b>"), (PlainStr, "&lt;i&gt;")):
            got = _render_rust(rust, "{{ form }}", form=form_class())
            assert expected in got
            assert got == _render_django(django_engine, "{{ form }}", form=form_class())

    def test_safety_follows_str_through_a_string_filter(self, rust, django_engine):
        # `display_safe` is read next to the text and forces it: a safe `str(form)` stays safe
        # through `|upper` (Django's `stringfilter` keeps SafeData), a plain one is escaped.
        for form_class in (SafeStr, PlainStr):
            src = "{{ form|upper }}|{{ form|safe }}|{{ form|force_escape }}"
            assert _render_rust(rust, src, form=form_class()) == _render_django(
                django_engine, src, form=form_class()
            )

    def test_a_formset_matches(self, rust, django_engine):
        FormSet = formset_factory(Plain, extra=2)

        def make():
            return FormSet(
                {
                    "form-TOTAL_FORMS": "2",
                    "form-INITIAL_FORMS": "0",
                    "form-0-name": "a",
                    "form-1-name": "toolongname",
                }
            )

        for src in (
            "{{ formset }}",
            "{{ formset.management_form }}",
            "{% for f in formset %}{{ f.name }}{% endfor %}",
            "{% for f in formset %}{{ f }}{% endfor %}",
            "{% for f in formset %}{% for g in f %}{{ g.label }}{% endfor %}{% endfor %}",
            "{{ formset|length }}",
        ):
            assert _ws(_render_rust(rust, src, formset=make())) == _ws(
                _render_django(django_engine, src, formset=make())
            ), src

    def test_a_container_of_forms_matches(self, rust, django_engine):
        def make():
            return {"forms": {"a": _invalid(), "b": _valid()}, "rows": [_invalid(), _unbound()]}

        for src in (
            "{% for k, v in forms.items %}{{ k }}{{ v }}{% endfor %}",
            "{% for k, v in forms.items %}{{ v.name }}{% endfor %}",
            "{% for fm in rows %}{{ fm }}{% endfor %}",
            "{% for fm in rows %}{% for f in fm %}{{ f.label }}{% endfor %}{% endfor %}",
        ):
            assert _ws(_render_rust(rust, src, **make())) == _ws(
                _render_django(django_engine, src, **make())
            ), src


# --------------------------------------------------------------------------- #
class TestTheSideEffectsOfStrAreNoLongerAccidental:
    """``str(form)`` validates the form. Reading it eagerly meant every form the engine was handed
    was validated as a side effect, so these spellings printed what only validation produces."""

    def _both(self, rust, django_engine, src, make):
        return (
            _render_rust(rust, src, form=make()),
            _render_django(django_engine, src, form=make()),
        )

    def test_cleaned_data_of_a_form_nothing_validated_is_empty_as_in_django(
        self, rust, django_engine
    ):
        got, want = self._both(rust, django_engine, "[{{ form.cleaned_data.name }}]", _valid)
        assert want == "[]", "the control: Django has no cleaned_data on an unvalidated form"
        assert got == want

    def test_cleaned_data_after_the_view_validated_is_there(self, rust, django_engine):
        def make():
            form = _valid()
            assert form.is_valid()
            return form

        got, want = self._both(rust, django_engine, "[{{ form.cleaned_data.name }}]", make)
        assert want == "[ok]"
        assert got == want

    def test_cleaned_data_after_the_template_asked_for_validity_is_there(self, rust, django_engine):
        src = "{{ form.is_valid }}[{{ form.cleaned_data.name }}]"
        got, want = self._both(rust, django_engine, src, _valid)
        assert want == "True[ok]"
        assert got == want

    def test_repr_of_an_unvalidated_form_says_unknown_as_in_django(self, rust, django_engine):
        got, want = self._both(rust, django_engine, "{{ form|pprint }}", _invalid)
        assert "valid=Unknown" in want
        assert got == want

    @pytest.mark.django_db
    def test_a_modelform_instance_is_not_modified_by_rendering_it(self, rust, django_engine):
        from django.contrib.auth.forms import UserChangeForm
        from django.contrib.auth.models import User

        def make():
            user = User(username="before", email="before@example.com", pk=7)
            return UserChangeForm(
                {"username": "after", "email": "after@example.com", "date_joined": "2020-01-01"},
                instance=user,
            )

        got, want = self._both(rust, django_engine, "{{ form.instance.username }}", make)
        assert want == "before", "the control: construct_instance runs on validation, not on print"
        assert got == want


# --------------------------------------------------------------------------- #
class Boom(forms.Form):
    a = forms.CharField()

    def __str__(self) -> str:
        raise RuntimeError("boom-str")


class TestAStrThatRaises:
    """The form's ``__str__`` raising must be the object's own exception at ``{{ form }}``, and must
    not stop a template that never prints the form (it used to fail the conversion's carrier shape,
    ``handle_only_encoded``; it is now an ordinary carrier whose text is asked for on read)."""

    def test_printing_the_form_raises_the_objects_exception(self, rust, django_engine):
        with pytest.raises(RuntimeError, match="boom-str"):
            _render_django(django_engine, "{{ form }}", form=Boom())
        with pytest.raises(RuntimeError, match="boom-str"):
            _render_rust(rust, "{{ form }}", form=Boom())

    @pytest.mark.parametrize(
        "src",
        [
            "hello",
            "{{ form.a }}",
            "{% for f in form %}{{ f }}{% endfor %}",
            "{% for f in form %}{{ f.label }}{% endfor %}",
            "{% if form %}y{% endif %}",
            "{{ form.a.errors }}",
        ],
    )
    def test_a_template_that_does_not_print_the_form_is_unaffected(self, rust, django_engine, src):
        assert _ws(_render_rust(rust, src, form=Boom())) == _ws(
            _render_django(django_engine, src, form=Boom())
        )


# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestTheFloorStillHolds:
    """The text is asked of the WALK-FLOORED handle (``floored_form_handle``), as it was when it was
    read at conversion. A ModelForm over a floor column and a typed password are not printed."""

    def test_a_user_model_form_prints_no_password_hash_in_any_spelling(self, rust, django_engine):
        from django.contrib.auth.forms import UserChangeForm
        from django.contrib.auth.models import User

        def make():
            user = User(
                username="u",
                pk=1,
                password="pbkdf2_sha256$600000$fixedsalt$FLOORHASHFLOORHASHFLOORHASH=",
            )
            return UserChangeForm(instance=user)

        # whole-form spellings: the hash is not in `str(form)` on either engine (the widget is the
        # form author's, and masks it), so these hold the text, not the floor
        for src in ("{{ form }}", "{{ form|upper }}", "{{ form|pprint }}"):
            assert "FLOORHASH" not in _render_rust(rust, src, form=make()).upper(), src
        # reading routes: Django prints the hash for each (the control), this engine does not, and
        # each goes through the conversion of a `BoundField` the loop produced, whose handle is the
        # floored one (`floored_form_handle`)
        for src in (
            "{% for f in form %}[{{ f.value }}|{{ f.data }}|{{ f.initial }}]{% endfor %}",
            "{% for f in form.visible_fields %}[{{ f.value }}]{% endfor %}",
            "{{ form.password.value }}|{{ form.initial.password }}",
            "{% for f in form %}{% for w in f %}{{ w.data.value }}{% endfor %}{% endfor %}",
        ):
            control = _render_django(django_engine, src, form=make())
            assert "FLOORHASH" in control.upper(), f"the control must print the hash: {src}"
            assert "FLOORHASH" not in _render_rust(rust, src, form=make()).upper(), src

    def test_a_typed_password_is_not_printed(self, rust):
        class SignUp(forms.Form):
            name = forms.CharField()
            secret = forms.CharField(widget=forms.PasswordInput)

        form = SignUp({"name": "n", "secret": "Typed-Pw-3769-xyz!"})
        for src in ("{{ form }}", "{% for f in form %}{{ f }}{{ f.value }}{% endfor %}"):
            assert "Typed-Pw-3769" not in _render_rust(rust, src, form=form), src

    def test_a_form_nested_in_a_container_is_floored_before_its_text_is_asked(
        self, rust, django_engine
    ):
        """A raw form that arrives INSIDE a list / dict (nothing wraps it on the way in) is floored by
        the conversion (``floored_form_handle``): the handle the deferred text keeps, and every
        sink that reads it afterwards, is the floored object. Django prints the typed value; this
        engine does not."""

        class SignUp(forms.Form):
            name = forms.CharField()
            secret = forms.CharField(widget=forms.PasswordInput)

        secret = "Typed-Pw-3769-xyz!"

        def make():
            form = SignUp({"name": "n", "secret": secret})
            return {"rows": [form], "d": {"a": form}}

        for src in (
            "{% for fm in rows %}{% for f in fm %}[{{ f.value }}|{{ f.data }}]{% endfor %}{% endfor %}",
            "{% for fm in rows %}[{{ fm.secret.value }}|{{ fm.data }}|{{ fm.initial }}]{% endfor %}",
            "{% for k, fm in d.items %}{% for f in fm %}[{{ f.value }}]{% endfor %}{% endfor %}",
            "{% with fm=rows.0 %}{% for f in fm %}[{{ f.value }}]{% endfor %}{% endwith %}",
            "{% for f in rows.0 %}[{{ f.value }}]{% endfor %}",
        ):
            control = _render_django(django_engine, src, **make())
            assert secret in control, (
                f"the control must print the secret, or this proves nothing: {src}"
            )
            assert secret not in _render_rust(rust, src, **make()), src


# --------------------------------------------------------------------------- #
class TestATeardownReleasesTheObjectADeferredTextWouldAsk:
    """``RustLiveView.state`` outlives a render; ``clear_live_handles`` (ADR-027, #2539) drops the
    handle of every ``Encoded`` in it so a connection retains no Python object. A deferred text
    holds ITS OWN handle to the same object, so the clear must release that too (a weakref, not
    the absence of a field, because what matters is that nothing holds the object)."""

    def _state_with(self, make):
        from djust._rust import RustLiveView

        view = RustLiveView("{{ obj }}")
        obj = make()
        view.update_state({"obj": obj})
        return view, obj

    @pytest.mark.parametrize("what", ["form", "formset"])
    def test_the_object_is_collectable_after_the_clear(self, what):
        import gc
        import weakref

        def make():
            if what == "form":
                return Plain()
            return formset_factory(Plain, extra=1)()

        view, obj = self._state_with(make)
        ref = weakref.ref(obj)
        del obj
        gc.collect()
        assert ref() is not None, "premise: the Rust state keeps the object alive through its text"
        view.clear_live_handles()
        gc.collect()
        assert ref() is None, "the deferred text still holds the object after the teardown clear"

    def test_the_clear_does_not_run_the_objects_str(self):
        class Counting(forms.Form):
            a = forms.CharField()
            calls = 0

            def __str__(self):
                type(self).calls += 1
                return "rendered"

        view, _obj = self._state_with(Counting)
        assert Counting.calls == 0, "premise: updating state does not ask"
        view.clear_live_handles()
        assert Counting.calls == 0, "a teardown must not run the object's code"

    def test_a_render_after_the_clear_shows_the_stand_in_not_the_form(self):
        """PINNED, not endorsed: the teardown never runs the object's code, so a form whose text was
        never asked (the state's copy; a render asks its own re-read of the object) renders its
        ``repr`` if something renders the state AFTER the clear. The clear runs at disconnect, and a
        reconnect re-converts from Python first (``clear_live_handles`` docs, #2570), so no
        connected view renders through it; what the eager text kept there was ``str(form)`` computed
        at conversion. A text that was asked keeps its answer (the Rust unit test)."""
        import html

        from djust._rust import RustLiveView

        form = Plain()
        stand_in = html.escape(repr(form))
        view = RustLiveView("{{ obj }}")
        view.update_state({"obj": form})
        before = view.render()
        assert 'name="name"' in before, "premise: the render before the clear is the form"
        view.clear_live_handles()
        after = view.render()
        assert after == stand_in, after
        assert 'name="name"' not in after


class TestAnOrdinaryIterableIsNotAskedAboutFormsets:
    """``live_handle_is_formset`` is asked of every iterable that is not a form. It resolves the two
    classes once per process and answers with one C-level ``isinstance``: no import, no module
    lookup, no Python call per value (the first version imported ``django.forms.formsets`` for each
    ``set`` / ``range`` it converted: +4-6% CPU)."""

    def test_no_formset_class_lookup_per_value(self):
        import collections
        import sys
        import types

        import django.forms.formsets as real
        from djust._rust import RustLiveView

        view = RustLiveView("{{ s }}")
        # warm the once-per-process cache with a real formset (and prove the check still answers)
        view.update_state({"fs": formset_factory(Plain, extra=1)()})

        class Counting(types.ModuleType):
            hits = 0

            def __getattribute__(self, name):
                if name == "BaseFormSet":
                    type(self).hits += 1
                return getattr(real, name)

        original = sys.modules["django.forms.formsets"]
        sys.modules["django.forms.formsets"] = Counting("django.forms.formsets")
        try:
            for i in range(30):
                view.update_state(
                    {
                        "s": {i, i + 1},
                        "fz": frozenset({i}),
                        "r": range(i, i + 3),
                        "d": collections.deque([i]),
                    }
                )
        finally:
            sys.modules["django.forms.formsets"] = original
        assert Counting.hits == 0, (
            f"{Counting.hits} formset class lookups for 120 ordinary iterables: the class is "
            "resolved per value again"
        )


# --------------------------------------------------------------------------- #
def _counting(base: type) -> type:
    """A subclass of ``base`` that counts how many times ``str()`` is asked of an instance."""

    class Counting(base):  # type: ignore[valid-type, misc]
        calls = 0

        def __str__(self) -> str:
            type(self).calls += 1
            return super().__str__()

    return Counting


class TestTheDeferralIsNarrow:
    """Two properties of the lazy conversion that no output can show, only a call count (#3832).

    The text of a form or formset is deferred; the text of everything else is still read when the
    value is converted, because there a raising ``__str__`` must still decline the carrier and the
    objects whose ``str()`` is cheap have nothing to save. Both halves are PERFORMANCE and
    SIDE-EFFECT properties: the rendered output is the same either way, so a mutation of either is
    invisible to every test that compares output, and was found by gate-off, not by a failing test.

    Not pinned here: after ``importlib.reload(djust.serialization)`` the process-wide formset-class
    tuple (``live_handle_is_formset``, ``crates/djust_core/src/context.rs``) still names the
    PRE-reload ``_SidecarFormSetProxy``, so a post-reload proxy is read eagerly. That is the
    fail-safe direction (the text is read now, as it was before #3769) and a development-server
    effect only; a test would reload a module other tests hold references into, which costs more
    than the note is worth.
    """

    @pytest.mark.parametrize(
        "base", [set, frozenset, list, collections.deque], ids=lambda b: b.__name__
    )
    def test_an_ordinary_iterable_is_asked_for_its_text_when_converted(self, base):
        from djust._rust import RustLiveView

        Counting = _counting(base)
        view = RustLiveView("{{ obj }}")
        view.update_state({"obj": Counting([1, 2])})
        # Exactly once, at conversion: not deferred (0), which is what a formset gets.
        assert Counting.calls == 1, (
            f"str() of a {base.__name__} subclass was asked {Counting.calls}x by the conversion; "
            "an ordinary iterable keeps the eager read (0 means every iterable is deferred now)"
        )

    def test_the_form_is_the_contrast_and_is_deferred(self):
        # The same probe over a form reads 0: the counts above are not just "update_state never asks".
        from djust._rust import RustLiveView

        view = RustLiveView("{{ obj }}")
        view.update_state({"obj": _form()})
        assert CountingForm.form_calls == 0

    @pytest.mark.django_db
    def test_a_model_formset_reaching_conversion_as_the_sidecar_proxy_is_deferred(self):
        from django.contrib.auth.forms import UserChangeForm
        from django.contrib.auth.models import User
        from django.forms import BaseModelFormSet, modelformset_factory

        from djust import serialization
        from djust._rust import RustLiveView

        class CountingModelFormSet(BaseModelFormSet):
            calls = 0

            # counted on THIS class: `type(self)` is the subclass modelformset_factory makes
            def __str__(self) -> str:
                CountingModelFormSet.calls += 1
                return super().__str__()

            def __html__(self) -> str:
                # a printed value is read through `__html__` first (as under Django)
                CountingModelFormSet.calls += 1
                return super().__html__()

        FormSet = modelformset_factory(
            User, form=UserChangeForm, formset=CountingModelFormSet, extra=1
        )
        formset = FormSet(queryset=User.objects.none())
        # What a template walk hands the conversion for a formset over a model (#3790): the
        # PROXY, which is not a `BaseFormSet` and so is deferred only if the proxy class is named.
        proxy = serialization._protect_sidecar_walk(formset)
        assert isinstance(proxy, serialization._SidecarFormSetProxy), "premise: the floor wraps it"
        assert not isinstance(proxy, BaseModelFormSet)

        view = RustLiveView("{{ fs }}")
        view.update_state({"fs": proxy})
        assert CountingModelFormSet.calls == 0, (
            "str() of the formset was asked at conversion: the sidecar proxy is no longer deferred"
        )
        assert 'name="form-0-username"' in view.render(), "premise: it prints when read"
        assert CountingModelFormSet.calls >= 1
