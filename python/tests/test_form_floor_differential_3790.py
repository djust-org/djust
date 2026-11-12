"""A grammar-driven differential for the model-form floor (#3790).

The review of #3796 kept finding the same class one spelling over: a REAL ``BoundField`` of a floor
field (or the real form behind it) reaching a template by a route other than a path segment. A table
of hand-picked spellings samples the routes its author thought of. This file generates the cross
product instead:

    root form  x  how a field/form is PRODUCED (a path, a loop over the form, ``visible_fields``,
                  ``hidden_fields``, ``get_context().fields``, a ``|slice``, ``|first`` / ``|last``,
                  a ``{% with %}`` alias, a loop over a formset)  x  what is READ OFF it (``data``,
                  ``value``, ``initial``, ``form.data``, subwidgets by index and by loop and by slice,
                  the value-printing renderers, ``form.cleaned_data``, ``form.management_form``...)

and asserts the full secret AND its last twelve characters never appear in any render, for an
unbound and a bound ``UserChangeForm``, a bound model formset (its forms, ``empty_form`` and
``management_form``), with ``password`` (disabled) and ``email`` (``DJUST_SENSITIVE_FIELDS``, editable)
both on the floor. The Django engine renders every case too: ``TestTheControlLeaks`` asserts it prints
the secret for each group, so a group that renders nothing on BOTH engines cannot pass as protection.

The INVARIANT under test: a template walk never receives a real ``BoundField`` of a floor field, nor
the real form (or management form) behind one. The producers are decided at ONE place, the conversion
of a form object into the engine's value (``floored_form_handle`` in ``crates/djust_core/src/lib.rs``),
plus the path walk; this file is what says the list of producers is complete.

Refs #3790, #3796, #1646, #1468.
"""

from __future__ import annotations

import html
import re

import pytest
from django.contrib.auth import get_user_model
from django import forms
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.forms import formset_factory, modelformset_factory
from django.template import Context, Engine
from django.test import override_settings

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()
EMAIL = "secret-3790@example.com"
JOINED = "2020-01-01 00:00:00"
#: What a user TYPED into a ``PasswordInput`` (#3798). It is not a column of any model: the model
#: floor of #3796 cannot name it, so it is held by the WIDGET-TYPE floor.
TYPED = "Typed-Pw-3798-xyz!"
PAIR = "Pair-Pw-3798-abc!"  # typed into a MultiWidget of two password inputs
LATE = "Late-Pw-3798-def!"  # typed into a PasswordInput added in __init__


class _PairWidget(forms.MultiWidget):
    """A ``MultiWidget`` made of two password inputs: submitted under ``pair_0`` / ``pair_1``."""

    def __init__(self, attrs=None):
        super().__init__([forms.PasswordInput, forms.PasswordInput], attrs)

    def decompress(self, value):
        return value or [None, None]


class _PairField(forms.MultiValueField):
    widget = _PairWidget

    def __init__(self, **kwargs):
        super().__init__(fields=(forms.CharField(), forms.CharField()), **kwargs)

    def compress(self, data_list):
        return "|".join(data_list)


class PlainPasswordForm(forms.Form):
    """A plain ``Form`` (no model behind it, so no model floor, #3799) with a typed password: one
    declared ``PasswordInput``, one in a ``MultiWidget`` (held under ``pair_0`` / ``pair_1``) and one
    ADDED in ``__init__`` (so it is in no ``base_fields``)."""

    name = forms.CharField()
    secret = forms.CharField(widget=forms.PasswordInput, required=False)
    pair = _PairField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["late"] = forms.CharField(widget=forms.PasswordInput, required=False)


@pytest.fixture(scope="module")
def engine():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    return DjustTemplateBackend({"NAME": "diff-3790", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


@pytest.fixture
def world():
    """The user, the contexts and the secrets, under a floor that holds both columns."""
    user = User.objects.create_user("floor-user", EMAIL, "pw-3790", first_name="Ada", is_staff=True)
    data = {
        "username": user.username,
        "password": user.password,
        "email": EMAIL,
        "date_joined": JOINED,
    }
    bound = UserChangeForm(dict(data), instance=user)
    assert bound.is_valid(), bound.errors
    FormSet = modelformset_factory(User, form=UserChangeForm, extra=1)
    fs_data = {
        "form-TOTAL_FORMS": "1",
        "form-INITIAL_FORMS": "1",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-id": str(user.pk),
    }
    fs_data.update({"form-0-" + k: v for k, v in data.items()})
    fs = FormSet(fs_data, queryset=User.objects.filter(pk=user.pk))
    assert fs.is_valid(), fs.errors
    # (#3798) a bound ``UserCreationForm`` and a bound plain ``Form``, both holding a typed password,
    # each as a root form AND as the forms of a bound formset; and the plain form UNBOUND with the
    # password in ``initial`` (``BoundField.value()`` reads ``initial`` there).
    created = UserCreationForm(
        {"username": "newbie", "password1": TYPED, "password2": TYPED}, prefix=None
    )
    assert created.is_valid(), created.errors
    plain = PlainPasswordForm(
        {"name": "n", "secret": TYPED, "pair_0": PAIR, "pair_1": PAIR, "late": LATE}
    )
    assert plain.is_valid(), plain.errors
    unbound_plain = PlainPasswordForm(
        initial={"name": "n", "secret": TYPED, "pair": [PAIR, PAIR], "late": LATE}
    )
    created_fs_class = modelformset_factory(User, form=UserCreationForm, extra=1)
    created_fs = created_fs_class(
        {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-username": "newbie2",
            "form-0-password1": TYPED,
            "form-0-password2": TYPED,
        },
        queryset=User.objects.none(),
    )
    assert created_fs.is_valid(), created_fs.errors
    plain_fs = formset_factory(PlainPasswordForm, extra=0)(
        {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-name": "n",
            "form-0-secret": TYPED,
            "form-0-pair_0": PAIR,
            "form-0-pair_1": PAIR,
            "form-0-late": LATE,
        }
    )
    assert plain_fs.is_valid(), plain_fs.errors
    contexts = {
        "form": {"form": bound, "fs": fs},
        "uform": {"form": UserChangeForm(instance=user), "fs": fs},
        "ucform": {"form": created, "fs": created_fs},
        "pform": {"form": plain, "fs": plain_fs},
        "upform": {"form": unbound_plain, "fs": plain_fs},
    }
    secrets = [
        user.password,
        user.password[-12:],
        EMAIL,
        EMAIL[-12:],
        TYPED,
        TYPED[-12:],
        PAIR,
        LATE,
    ]
    with override_settings(DJUST_SENSITIVE_FIELDS=["email"]):
        yield contexts, secrets


# ---------------------------------------------------------------------------------------------
# The grammar. ``F`` is an expression for a form; ``v`` is bound to one of its fields; ``g`` to a form.

#: (root id, which context, expression for the form, a field name that exists on it, is it the
#: management form). The management form's own fields are counts; what it holds that matters is
#: ``.form.data``, the formset's whole submitted data.
ROOTS = [
    ("form", "form", "form", "password"),
    ("uform", "uform", "form", "password"),
    ("fs.forms.0", "form", "fs.forms.0", "password"),
    ("fs.0", "form", "fs.0", "email"),
    ("fs.empty_form", "form", "fs.empty_form", "password"),
    ("fs.management_form", "form", "fs.management_form", "TOTAL_FORMS"),
    # #3798: the typed password of a ``PasswordInput`` that is not a column of any model.
    ("ucform", "ucform", "form", "password1"),
    ("ucfs.forms.0", "ucform", "fs.forms.0", "password1"),
    ("ucfs.management_form", "ucform", "fs.management_form", "TOTAL_FORMS"),
    ("pform", "pform", "form", "secret"),
    ("upform", "upform", "form", "secret"),
    ("pfs.forms.0", "pform", "fs.forms.0", "secret"),
    ("pfs.management_form", "pform", "fs.management_form", "TOTAL_FORMS"),
]

#: how a field comes to be bound to ``v``. ``{F}`` and ``{N}`` (a field name) are filled per root.
FIELD_BINDERS = {
    "path": ("", "", "{F}.{N}"),
    "with": ("{{% with v={F}.{N} %}}", "{{% endwith %}}", "v"),
    "for-form": ("{{% for v in {F} %}}", "{{% endfor %}}", "v"),
    "for-visible": ("{{% for v in {F}.visible_fields %}}", "{{% endfor %}}", "v"),
    "for-hidden": ("{{% for v in {F}.hidden_fields %}}", "{{% endfor %}}", "v"),
    "for-slice": ("{{% for v in {F}.visible_fields|slice:':3' %}}", "{{% endfor %}}", "v"),
    "with-first": ("{{% with v={F}.visible_fields|first %}}", "{{% endwith %}}", "v"),
    "with-last": ("{{% with v={F}.visible_fields|last %}}", "{{% endwith %}}", "v"),
    "for-context": (
        "{{% for t in {F}.get_context.fields %}}{{% with v=t.0 %}}",
        "{{% endwith %}}{{% endfor %}}",
        "v",
    ),
}

#: what is read off a field ``X`` (the binder's spelling of it).
FIELD_SINKS = [
    "{{{{ {X}.data }}}}",
    "{{{{ {X}.value }}}}",
    "{{{{ {X}.initial }}}}",
    "{{{{ {X}.form.data }}}}",
    "{{{{ {X}.form.initial }}}}",
    "{{{{ {X}.form.cleaned_data }}}}",
    "{{{{ {X}.form.data|pprint }}}}",
    "{{{{ {X}.subwidgets.0.data.value }}}}",
    "{{{{ {X}.0.data.value }}}}",
    "{{% for w in {X} %}}{{{{ w.data }}}}{{{{ w.data.value }}}}{{% endfor %}}",
    "{{% for w in {X}|slice:':2' %}}{{{{ w.data }}}}{{% endfor %}}",
    "{{% for w in {X}.subwidgets %}}{{{{ w.data.value }}}}{{% endfor %}}",
    "{{{{ {X}.as_text }}}}{{{{ {X}.as_hidden }}}}{{{{ {X}.as_textarea }}}}",
    "{{% for k, x in {X}.form.data.items %}}{{{{ x }}}}{{% endfor %}}",
    "{{{{ {X}.form.password.value }}}}{{{{ {X}.form.email.value }}}}",
    "{{% for x in {X}.form %}}{{{{ x.value }}}}{{{{ x.data }}}}{{% endfor %}}",
    "{{{{ {X}.form.get_context.form.data }}}}",
    # Siblings of a field: its ``Field`` (declared or model-default, never the instance's value), the
    # widget's attrs, the form's ``fields`` mapping, ``cleaned_data`` by key, a repr.
    "{{{{ {X}.field.initial }}}}{{{{ {X}.field.widget.attrs }}}}{{{{ {X}.field|pprint }}}}",
    "{{{{ {X}.form.fields.password.initial }}}}{{{{ {X}.form.fields.email.initial }}}}",
    "{{{{ {X}.form.cleaned_data.password }}}}{{{{ {X}.form.cleaned_data.email }}}}",
    "{{{{ {X}|pprint }}}}{{{{ {X}.form|pprint }}}}",
    # #3798: the typed password by the name it has on a ``UserCreationForm`` / a plain form.
    "{{{{ {X}.form.password1.value }}}}{{{{ {X}.form.password2.value }}}}{{{{ {X}.form.secret.value }}}}",
    "{{{{ {X}.form.cleaned_data.password1 }}}}{{{{ {X}.form.cleaned_data.secret }}}}"
    "{{{{ {X}.form.data.password1 }}}}{{{{ {X}.form.initial.secret }}}}",
    "{{{{ {X}.form.password1.subwidgets.0.data.value }}}}{{{{ {X}.form.secret.0.data.value }}}}",
    "{{{{ {X}.form.pair.value }}}}{{{{ {X}.form.late.value }}}}{{{{ {X}.form.cleaned_data.pair }}}}{{{{ {X}.form.cleaned_data.late }}}}",
    "{{{{ {X}.form.data.pair_0 }}}}{{{{ {X}.form.data.late }}}}{{{{ {X}.form.initial.pair }}}}{{{{ {X}.form.initial.late }}}}",
]

#: how a FORM comes to be bound to ``g``.
FORM_BINDERS = {
    "path": ("", "", "{F}"),
    "with": ("{{% with g={F} %}}", "{{% endwith %}}", "g"),
    "via-field": ("{{% with g={F}.{N}.form %}}", "{{% endwith %}}", "g"),
    "via-context": ("{{% with g={F}.get_context.form %}}", "{{% endwith %}}", "g"),
    "via-fields-loop": (
        "{{% for t in {F}.get_context.fields %}}{{% with g=t.0.form %}}",
        "{{% endwith %}}{{% endfor %}}",
        "g",
    ),
    "via-loop-field": (
        "{{% for f in {F} %}}{{% with g=f.form %}}",
        "{{% endwith %}}{{% endfor %}}",
        "g",
    ),
}

FORM_SINKS = [
    "{{{{ {X}.data }}}}",
    "{{{{ {X}.initial }}}}",
    "{{{{ {X}.cleaned_data }}}}",
    "{{{{ {X}.data|pprint }}}}",
    "{{{{ {X}.password.value }}}}{{{{ {X}.email.value }}}}",
    "{{% for x in {X} %}}{{{{ x.value }}}}{{{{ x.data }}}}{{% endfor %}}",
    "{{% for k, x in {X}.data.items %}}{{{{ x }}}}{{% endfor %}}",
    "{{{{ {X}.get_context.form.data }}}}",
    "{{{{ {X}.fields.password.initial }}}}{{{{ {X}.fields.email.initial }}}}",
    "{{{{ {X}.fields.password.widget.attrs }}}}{{{{ {X}.fields.email.widget.attrs }}}}",
    "{{{{ {X}|pprint }}}}{{{{ {X}.errors }}}}{{{{ {X}.changed_data }}}}",
    "{{% for k, x in {X}.items %}}{{{{ x }}}}{{% endfor %}}{{% for k, x in {X}.initial.items %}}{{{{ x }}}}{{% endfor %}}",
    "{{{{ {X}.cleaned_data.password }}}}{{{{ {X}.cleaned_data.email }}}}{{{{ {X}.instance.password }}}}",
    # #3798
    "{{{{ {X}.password1.value }}}}{{{{ {X}.password2.value }}}}{{{{ {X}.secret.value }}}}",
    "{{{{ {X}.cleaned_data.password1 }}}}{{{{ {X}.cleaned_data.secret }}}}",
    "{{{{ {X}.data.password1 }}}}{{{{ {X}.initial.secret }}}}{{{{ {X}.data.secret }}}}",
    "{{{{ {X}.password1.data }}}}{{{{ {X}.secret.data }}}}{{{{ {X}.secret.initial }}}}",
    "{{{{ {X}.pair.value }}}}{{{{ {X}.late.value }}}}{{{{ {X}.cleaned_data.pair }}}}{{{{ {X}.cleaned_data.late }}}}",
    "{{{{ {X}.data.pair_0 }}}}{{{{ {X}.data.pair_1 }}}}{{{{ {X}.data.late }}}}{{{{ {X}.initial.pair }}}}{{{{ {X}.initial.late }}}}",
    "{{% for w in {X}.secret %}}{{{{ w.data.value }}}}{{% endfor %}}",
]

#: formset-level producers, rooted at ``fs``.
FORMSET_BINDERS = {
    "for-fs": ("{{% for g in fs %}}", "{{% endfor %}}", "g"),
    "for-forms": ("{{% for g in fs.forms %}}", "{{% endfor %}}", "g"),
    "for-initial": ("{{% for g in fs.initial_forms %}}", "{{% endfor %}}", "g"),
    "with-index": ("{{% with g=fs.0 %}}", "{{% endwith %}}", "g"),
    "with-management": ("{{% with g=fs.management_form %}}", "{{% endwith %}}", "g"),
    "for-slice": ("{{% for g in fs.forms|slice:':2' %}}", "{{% endfor %}}", "g"),
}
#: (group root id, context key, a field name): the model formset over ``UserChangeForm`` (#3790), the
#: model formset over ``UserCreationForm`` and the plain-form formset (#3798).
FORMSET_ROOTS = [
    ("fs", "form", "password"),
    ("ucfs", "ucform", "password1"),
    ("pfs", "pform", "secret"),
]

FORMSET_SINKS = FORM_SINKS + [
    "{{{{ fs.data }}}}{{{{ fs.cleaned_data }}}}{{{{ fs.management_form.data }}}}",
    "{{% for f in {X} %}}{{% for w in f %}}{{{{ w.data.value }}}}{{% endfor %}}{{% endfor %}}",
]


def _cases():
    """Every (group id, context key, [templates]) the grammar generates."""
    for root_id, ctx_key, expr, fname in ROOTS:
        for binder_id, binder in FIELD_BINDERS.items():
            yield (
                f"{root_id}|field|{binder_id}",
                ctx_key,
                _templates(binder, FIELD_SINKS, expr, fname),
            )
        for binder_id, binder in FORM_BINDERS.items():
            yield (
                f"{root_id}|form|{binder_id}",
                ctx_key,
                _templates(binder, FORM_SINKS, expr, fname),
            )
    for fs_id, ctx_key, fname in FORMSET_ROOTS:
        for binder_id, binder in FORMSET_BINDERS.items():
            yield (
                f"{fs_id}|formset|{binder_id}",
                ctx_key,
                _templates(binder, FORMSET_SINKS, "fs", fname),
            )


def _templates(binder, sinks, root_form, field_name):
    pre, post, expr = binder
    fill = {"F": root_form, "N": field_name}
    open_, close_ = pre.format(**fill), post.format(**fill)
    name = expr.format(**fill)
    out = []
    for sink in sinks:
        # `{{{{` / `}}}}` / `{{%` are str.format escapes for a literal `{{` / `}}` / `{%`.
        rendered = sink.format(X=name)
        out.append(open_ + rendered + close_)
    return out


GROUPS = list(_cases())
GROUP_IDS = [g[0] for g in GROUPS]


def _render(engine, which, src, context):
    if which == "rust":
        return str(engine.from_string(src).render(dict(context)))
    if which == "liveview":
        return _render_held_as_liveview_state(src, context)
    return str(Engine().from_string(src).render(Context(dict(context))))


def _render_held_as_liveview_state(src, context):
    """The same template over the same objects, each held as an attribute of a ``LiveView`` (#3767).

    A form held as view state used to reach the engine as ``{name: widget HTML}`` and never as the
    form, so none of the spellings below ran against it. It now rides the raw-Python sidecar, so every
    producer and sink in this grammar applies to it, and the floor has to hold for each."""
    from djust import LiveView
    from djust.testing import LiveViewTestClient

    class Held(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>" + src + "</div>"

        def mount(self, request, **kwargs):
            for name, value in context.items():
                setattr(self, name, value)

    client = LiveViewTestClient(Held)
    client.mount()
    html = client.render()
    match = re.search(r"<div[^>]*dj-root[^>]*>(.*)</div>", html, re.S)
    assert match, html[:200]
    return match.group(1)


def _leaks(out, secrets):
    plain = html.unescape(out)
    return [s for s in secrets if s in out or s in plain]


class TestTheGrammar:
    def test_the_grammar_generates_the_cross_product(self):
        n_field = len(ROOTS) * len(FIELD_BINDERS) * len(FIELD_SINKS)
        n_form = len(ROOTS) * len(FORM_BINDERS) * len(FORM_SINKS)
        n_fs = len(FORMSET_ROOTS) * len(FORMSET_BINDERS) * len(FORMSET_SINKS)
        assert sum(len(t) for _, _, t in GROUPS) == n_field + n_form + n_fs

    def test_every_template_compiles_on_both_engines(self, engine):
        for _, _, templates in GROUPS:
            for src in templates:
                engine.from_string(src)
                Engine().from_string(src)

    def test_a_template_is_what_the_grammar_says(self):
        template = _templates(FIELD_BINDERS["for-form"], FIELD_SINKS[:1], "form", "password")[0]
        assert template == "{% for v in form %}{{ v.data }}{% endfor %}"
        template = _templates(FORM_BINDERS["via-field"], FORM_SINKS[:1], "fs.0", "email")[0]
        assert template == "{% with g=fs.0.email.form %}{{ g.data }}{% endwith %}"


@pytest.mark.parametrize("group_id,ctx_key,templates", GROUPS, ids=GROUP_IDS)
class TestAFormHeldAsLiveViewStateIsHeldToTheSameFloor:
    """Render each template through the native backend and as LiveView state (#3767).

    Check both raw outputs for leaks before normalizing them for the parity check.
    """

    @staticmethod
    def _canon(text):
        text = re.sub(r"<!--dj-if-->", "", text)
        text = re.sub(r'\s*dj-[a-z-]+="[^"]*"', "", text)
        # ``empty_form`` is a NEW form on every access: its repr carries a fresh address.
        text = re.sub(r"0x[0-9a-f]+", "0x?", text)
        # ... and its ``date_joined`` initial is ``now()`` to the second, which two renders straddle.
        text = re.sub(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d+)?", "<now>", text)
        # ... and the same instant in Django's locale spelling ("Nov. 5, 2026, 6:48 a.m."), which
        # straddles a MINUTE rollover just as the ISO form straddles a second (main, 2026-11-05).
        text = re.sub(
            r"[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}, \d{1,2}(?::\d\d)? [ap]\.m\.", "<now>", text
        )
        return re.sub(r"\s+", " ", text).strip()

    def test_it_renders_what_the_plain_backend_renders(
        self, engine, world, group_id, ctx_key, templates
    ):
        contexts, secrets = world
        context = contexts[ctx_key]
        leaks = []
        mismatches = []
        for src in templates:
            native = _render(engine, "rust", src, context)
            found = _leaks(native, secrets)
            if found:
                leaks.append(("native", src, [s[-12:] for s in found]))

            try:
                held = _render(engine, "liveview", src, context)
            except Exception as exc:  # noqa: BLE001 - a LiveView render error is a mismatch
                mismatches.append((src, ("ok", native), ("err", type(exc).__name__)))
                continue

            found = _leaks(held, secrets)
            if found:
                leaks.append(("liveview", src, [s[-12:] for s in found]))
            if self._canon(native) != self._canon(held):
                mismatches.append((src, ("ok", native), ("ok", held)))

        assert not leaks and not mismatches, {"leaks": leaks[:5], "mismatches": mismatches[:3]}


def _control_is_empty_by_construction(group_id: str) -> bool:
    """Groups whose PRODUCER yields nothing to read, whatever the engine: ``empty_form`` is unbound
    and has no instance; a ``UserChangeForm`` has no hidden fields; the management form has only
    hidden fields, so the visible-field binders bind nothing."""
    root, _, binder = group_id.split("|")
    if root == "fs.empty_form":
        return True
    # The two #3798 formsets are built with no initial forms (INITIAL_FORMS = 0): every form in
    # them is an extra one, so ``initial_forms`` is empty on both engines.
    if root in {"ucfs", "pfs"} and binder == "for-initial":
        return True
    if binder == "for-hidden" and not root.endswith("management_form"):
        return True
    return root.endswith("management_form") and binder in {
        "for-visible",
        "for-slice",
        "with-first",
        "with-last",
        "for-context",
        "via-fields-loop",
    }


class TestTheCanonMasksTheClock:
    """Two renders of ``empty_form`` straddle the clock; ``_canon`` must not turn that into a failure
    (main's py3.14 run, 2026-11-05: the same instant rendered 6:48 a.m. then 6:49 a.m.)."""

    canon = staticmethod(TestAFormHeldAsLiveViewStateIsHeldToTheSameFloor._canon)

    def test_a_minute_rollover_is_masked(self):
        a = self.canon("True Nov. 5, 2026, 6:48 a.m. None")
        b = self.canon("True Nov. 5, 2026, 6:49 a.m. None")
        assert a == b

    def test_an_hour_only_and_a_long_month_are_masked(self):
        assert self.canon("Sept. 12, 2026, 11 p.m.") == self.canon("Sept. 12, 2026, 10 p.m.")

    def test_other_text_is_not_masked(self):
        assert self.canon("a 6:48 b") != self.canon("a 6:49 b")


@pytest.mark.parametrize("group_id,ctx_key,templates", GROUPS, ids=GROUP_IDS)
class TestTheControlLeaks:
    def test_django_prints_it_in_the_group(self, world, group_id, ctx_key, templates):
        """Not vacuous: the same templates, rendered by Django, print the secret somewhere in the
        group, so 'nothing leaked' above is not two empty strings."""
        contexts, secrets = world
        printed = 0
        for src in templates:
            try:
                out = _render(None, "django", src, contexts[ctx_key])
            except Exception:  # a producer Django itself cannot resolve renders nothing
                continue
            printed += bool(_leaks(out, secrets))
        if _control_is_empty_by_construction(group_id):
            return
        assert printed > 0, group_id


def test_the_total_number_of_renders_is_reported(engine, world):
    """Prints nothing; asserts the count the PR quotes is derived from the grammar, not typed."""
    total = sum(len(t) for _, _, t in GROUPS)
    assert total >= len(ROOTS) * len(FIELD_BINDERS) * len(FIELD_SINKS)
    assert re.fullmatch(r"\d+", str(total))
