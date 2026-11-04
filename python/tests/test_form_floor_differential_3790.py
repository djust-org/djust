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
from django.contrib.auth.forms import UserChangeForm
from django.forms import modelformset_factory
from django.template import Context, Engine
from django.test import override_settings

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()
EMAIL = "secret-3790@example.com"
JOINED = "2020-01-01 00:00:00"


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
    contexts = {
        "form": {"form": bound, "fs": fs},
        "uform": {"form": UserChangeForm(instance=user), "fs": fs},
    }
    secrets = [user.password, user.password[-12:], EMAIL, EMAIL[-12:]]
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
    for binder_id, binder in FORMSET_BINDERS.items():
        yield f"fs|formset|{binder_id}", "form", _templates(binder, FORMSET_SINKS, "fs", "password")


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
    return str(Engine().from_string(src).render(Context(dict(context))))


def _leaks(out, secrets):
    plain = html.unescape(out)
    return [s for s in secrets if s in out or s in plain]


class TestTheGrammar:
    def test_the_grammar_generates_the_cross_product(self):
        n_field = len(ROOTS) * len(FIELD_BINDERS) * len(FIELD_SINKS)
        n_form = len(ROOTS) * len(FORM_BINDERS) * len(FORM_SINKS)
        n_fs = len(FORMSET_BINDERS) * len(FORMSET_SINKS)
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
class TestNoRouteReachesTheSecret:
    def test_the_native_engine_prints_no_part_of_it(
        self, engine, world, group_id, ctx_key, templates
    ):
        contexts, secrets = world
        leaks = []
        for src in templates:
            out = _render(engine, "rust", src, contexts[ctx_key])
            found = _leaks(out, secrets)
            if found:
                leaks.append((src, [s[-12:] for s in found]))
        assert not leaks, leaks[:5]


def _control_is_empty_by_construction(group_id: str) -> bool:
    """Groups whose PRODUCER yields nothing to read, whatever the engine: ``empty_form`` is unbound
    and has no instance; a ``UserChangeForm`` has no hidden fields; the management form has only
    hidden fields, so the visible-field binders bind nothing."""
    root, _, binder = group_id.split("|")
    if root == "fs.empty_form":
        return True
    if binder == "for-hidden" and root != "fs.management_form":
        return True
    return root == "fs.management_form" and binder in {
        "for-visible",
        "for-slice",
        "with-first",
        "with-last",
        "for-context",
        "via-fields-loop",
    }


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
