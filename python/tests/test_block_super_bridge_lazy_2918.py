"""A bridged Python tag under an armed ``block.super`` runs as often as Django's (#2918).

The divergence
--------------
#2710 made ``{{ block.super }}`` lazy on every channel the RENDERER owns, but
a bridged Python tag receives the context as a flat map, a map has nothing to
defer behind, and ``bridged_context_map`` therefore RENDERED THE PARENT FOR
EVERY BRIDGED CALL made while a ``block.super`` was armed, whether or not the
tag ever read it. Output was identical to Django's; the side effects of the
parent's own tags were not::

    base <- mid <- child, a ``{% probe %}`` per level in a loop

    django  12 probe calls
    djust   60 probe calls            <- before this fix

What the fix is
---------------
The ``block`` the handler receives is a lazy object (``LazyBlock``) whose
``super`` renders the parent when it is READ, and renders it again on every
read. Not memoized: Django's ``BlockNode.super()`` is a method call, so a tag
reading ``block.super`` twice renders the parent twice (``{% cycle 'a' 'b' %}``
in the parent answers ``a-b``, a memo would answer ``a-a``).

Django is CALLED in every case below, never transcribed. Each cell compares
the rendered output AND the number of times each probe ran.
"""

from __future__ import annotations

import copy
import gc
import pickle
import re
import sys
import tempfile
import types
import weakref
from pathlib import Path

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django import template as dj_template  # noqa: E402
from django.conf import settings  # noqa: E402
from django.utils.safestring import SafeString, mark_safe  # noqa: E402

if not settings.configured:
    settings.configure(
        SECRET_KEY="test-2918",
        INSTALLED_APPS=[],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402

from djust.template import DjustTemplateBackend  # noqa: E402

LIB_NAME = "probe2918_lib"
REPO = Path(__file__).resolve().parents[2]
RENDERER_RS = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"

#: ``label -> times the probe ran``, reset per render.
CALLS: dict[str, int] = {}


def _tick(label: str) -> None:
    CALLS[label] = CALLS.get(label, 0) + 1


register = dj_template.Library()


@register.simple_tag
def probe(label):
    """A bridged tag that touches nothing but its own counter."""
    _tick(label)
    return f"<{label}>"


@register.simple_tag(takes_context=True)
def probe_ctx(context, label):
    """A ``takes_context`` tag that never reads ``block`` — the shape that made
    "skip the parent when no operand names ``block.super``" unsafe, and the
    shape that must now cost nothing."""
    _tick(label)
    return f"<{label}>"


@register.simple_tag(takes_context=True)
def probe_super(context, label):
    """A ``takes_context`` tag that READS ``block.super`` without naming it in
    its arguments, through the same resolver Django's own nodes use."""
    _tick(label)
    return dj_template.Variable("block.super").resolve(context)


@register.simple_tag(takes_context=True)
def probe_super_call(context, label):
    """Django's own idiom: ``BlockNode.super()`` is a method."""
    _tick(label)
    return context["block"].super()


@register.simple_tag(takes_context=True)
def probe_super_twice(context, label):
    _tick(label)
    var = dj_template.Variable("block.super")
    return f"{var.resolve(context)}|{var.resolve(context)}"


@register.simple_tag(takes_context=True)
def probe_methods(context, label):
    """``get``, ``super()`` and membership — the shapes that exist."""
    _tick(label)
    block = context["block"]
    got = block.get("super")
    return mark_safe(
        f"{got}|{block.super()}|{'super' in block}|{'nope' in block}|{isinstance(got, SafeString)}"
    )


@register.simple_tag(takes_context=True)
def probe_subscript(context, label):
    """Django's ``BlockNode`` is not subscriptable, and neither is this."""
    _tick(label)
    try:
        context["block"]["super"]
    except TypeError:
        return "TypeError"
    return "subscriptable"


@register.simple_tag(takes_context=True)
def probe_membership(context, label):
    """Asks WHETHER ``super`` exists and lists the keys — neither is a read."""
    _tick(label)
    block = context["block"]
    return mark_safe(f"{'super' in block}|{'nope' in block}|{list(block.keys())}|{len(block)}")


@register.simple_tag
def probe_as(label):
    _tick(label)
    return "as-value"


@register.simple_block_tag(takes_context=True, name="wrap2918")
def _wrap(context, content):
    return mark_safe(f"[{content}]")


@register.simple_block_tag(takes_context=True, name="wrap_super2918")
def _wrap_super(context, content):
    """A block tag that reads ``block.super`` through its context."""
    return mark_safe(f"[{content}{dj_template.Variable('block.super').resolve(context)}]")


@register.simple_tag
def echo(value):
    return f"[{value}]"


@register.simple_tag
def boom(label):
    _tick(label)
    raise ValueError("parent exploded")


#: The five classes ``Variable._resolve_lookup`` catches around its mapping
#: access (``TypeError, AttributeError, KeyError, ValueError, IndexError``).
RAISED = {
    "ValueError": ValueError,
    "KeyError": KeyError,
    "AttributeError": AttributeError,
    "TypeError": TypeError,
    "IndexError": IndexError,
    "RuntimeError": RuntimeError,
}


@register.simple_tag
def boom_kind(label, kind):
    _tick(label)
    raise RAISED[kind]("parent exploded")


@register.simple_tag(takes_context=True)
def catch_super(context, label):
    """A tag with its own ``except``: which CLASS does the parent's error have?"""
    _tick(label)
    try:
        return dj_template.Variable("block.super").resolve(context)
    except Exception as exc:  # noqa: BLE001 - the class IS the answer
        return f"caught-{type(exc).__name__}"


@register.simple_tag(takes_context=True)
def show_block(context):
    """What ``block`` is, as a tag sees it."""
    return f"(block={context['block']})"


@register.simple_tag(takes_context=True)
def keep_context(context):
    """A tag that keeps the context it received — a cache, a debug panel."""
    context["holder"].ctx = context
    return ""


@register.simple_tag(takes_context=True)
def keep_block(context):
    context["holder"].block = context["block"]
    return ""


@register.simple_tag(takes_context=True)
def probe_copy(context):
    block = context["block"]
    try:
        pickle.dumps(block)
        pickled = "pickled"
    except TypeError:
        pickled = "TypeError"
    return f"{copy.copy(block) is block}|{copy.deepcopy(block) is block}|{pickled}"


_lib_module = types.ModuleType(LIB_NAME)
_lib_module.register = register
sys.modules[LIB_NAME] = _lib_module

LOAD = f"{{% load {LIB_NAME} %}}"
LOAD_I18N = "{% load i18n %}"

PARENTS = {
    # one probe per level, in a loop, so the count is sensitive to re-renders
    "base.html": LOAD
    + "{% block body %}{% for i in three %}{% probe 'base' %}{% endfor %}{% endblock %}",
    "mid.html": '{% extends "base.html" %}'
    + LOAD
    + "{% block body %}{% for i in three %}{% probe 'mid' %}{% endfor %}"
    "[{{ block.super }}]{% endblock %}",
    "cyc.html": "{% block body %}{% cycle 'a' 'b' %}{% endblock %}",
    "amp.html": "{% block body %}<b>&</b>{% endblock %}",
    "boom.html": LOAD + "{% block body %}{% boom 'parent' %}{% endblock %}",
    **{
        f"boom_{kind}.html": LOAD
        + f"{{% block body %}}{{% boom_kind 'parent' '{kind}' %}}{{% endblock %}}"
        for kind in RAISED
    },
    "inc.html": LOAD
    + "{% block body %}{% include \"frag.html\" %}{% probe 'parent' %}{% endblock %}",
    "frag.html": LOAD + "{% probe 'frag' %}F",
    "tick.html": LOAD + "{% block body %}{% probe 'parent' %}{% endblock %}",
    "raw.html": "{% block body %}{{ danger }}{% endblock %}",
    "ctx_parent.html": LOAD
    + "{% block body %}{% probe_ctx 'parent_ctx' %}{% probe 'parent' %}{% endblock %}",
}


#: A branch that is never taken but NAMES ``block.super``, which is what makes
#: the renderer arm the scope at all (``nodes_reference_block_super`` reads the
#: template's own text). A tag that reads ``block.super`` through its context
#: without the template naming it is a separate, older gap — see
#: ``TestAnImplicitReaderNeedsTheScopeArmed``.
ARM = "{% if show %}{{ block.super }}{% endif %}"


def _child(parent: str, body: str, extra_load: str = "") -> str:
    return f'{{% extends "{parent}" %}}{LOAD}{extra_load}{{% block body %}}{body}{{% endblock %}}'


CASES: dict[str, str] = {
    # ---- the reported shape (#2918): three levels, probes everywhere ------
    "issue_shape": _child(
        "mid.html",
        "{% for i in three %}{% probe 'child' %}{% endfor %}"
        "{{ block.super }}"
        "{% blocktranslate with s=block.super %}v={{ s }}{% endblocktranslate %}",
        LOAD_I18N,
    ),
    # ---- armed, but the bridged tag never reads it ------------------------
    "armed_unread_false_branch": _child(
        "tick.html", "{% if show %}{{ block.super }}{% endif %}{% probe 'child' %}"
    ),
    "armed_unread_ctx_tag": _child(
        "tick.html", "{% if show %}{{ block.super }}{% endif %}{% probe_ctx 'child' %}"
    ),
    "armed_unread_in_loop": _child(
        "tick.html",
        "{% if show %}{{ block.super }}{% endif %}{% for i in three %}{% probe 'child' %}{% endfor %}",
    ),
    "armed_read_once_probe_in_loop": _child(
        "tick.html",
        "{% for i in three %}{% probe 'child' %}{% endfor %}{{ block.super }}",
    ),
    "armed_read_per_iteration": _child(
        "tick.html", "{% for i in three %}{% probe 'child' %}{{ block.super }}{% endfor %}"
    ),
    # ---- a bridged tag that READS block.super without naming it -----------
    "ctx_tag_reads_super": _child("tick.html", ARM + "[{% probe_super 'child' %}]"),
    "ctx_tag_calls_super_method": _child(
        "tick.html", ARM + "{% probe_super_call 'child' %}{% probe_super_call 'child' %}"
    ),
    "ctx_tag_reads_super_twice": _child("tick.html", ARM + "{% probe_super_twice 'child' %}"),
    "ctx_tag_reads_super_per_iteration": _child(
        "tick.html", ARM + "{% for i in three %}{% probe_super 'child' %}{% endfor %}"
    ),
    "ctx_tag_reads_super_in_parent_with_ctx": _child(
        "ctx_parent.html", ARM + "{% probe_super 'child' %}"
    ),
    "ctx_tag_unarmed_branch": _child(
        "tick.html", ARM + "{% if show %}{% probe_super 'child' %}{% endif %}c"
    ),
    # ---- repeated reads: NOT memoized --------------------------------------
    "cycle_read_by_two_tags": _child(
        "cyc.html", ARM + "{% probe_super 'a' %}-{% probe_super 'b' %}"
    ),
    "cycle_read_twice_in_one_tag": _child("cyc.html", ARM + "{% probe_super_twice 'a' %}"),
    "cycle_read_by_tag_and_expression": _child(
        "cyc.html", "{% probe_super 'a' %}-{{ block.super }}"
    ),
    # ---- the other bridged call shapes (assignment, block tag, template object)
    "as_binding_unread": _child(
        "tick.html", ARM + "{% probe_as 'child' as v %}{{ v }}{% probe_as 'child' as w %}{{ w }}"
    ),
    "block_tag_unread": _child(
        "tick.html", ARM + "{% wrap2918 %}{% probe 'inner' %}{% endwrap2918 %}"
    ),
    "block_tag_unread_in_loop": _child(
        "tick.html",
        ARM + "{% for i in three %}{% wrap2918 %}{% probe 'inner' %}{% endwrap2918 %}{% endfor %}",
    ),
    "block_tag_reads_super": _child(
        "tick.html", ARM + "{% wrap_super2918 %}{% probe 'inner' %}{% endwrap_super2918 %}"
    ),
    "template_object_unread": _child("tick.html", ARM + "{% include tpl_plain %}"),
    # ---- the blocktranslate boundary ---------------------------------------
    "blocktranslate_unread": _child(
        "tick.html",
        "{% if show %}{{ block.super }}{% endif %}"
        "{% blocktranslate with s=x %}v={{ s }}{% endblocktranslate %}",
        LOAD_I18N,
    ),
    "blocktranslate_reads": _child(
        "tick.html",
        "{% blocktranslate with s=block.super %}v={{ s }}{% endblocktranslate %}",
        LOAD_I18N,
    ),
    "blocktranslate_in_loop": _child(
        "tick.html",
        "{% for i in three %}"
        "{% blocktranslate with s=block.super %}v={{ s }}{% endblocktranslate %}{% endfor %}",
        LOAD_I18N,
    ),
    "blocktranslate_in_loop_unread": _child(
        "tick.html",
        "{% if show %}{{ block.super }}{% endif %}{% for i in three %}"
        "{% blocktranslate with s=i %}v={{ s }}{% endblocktranslate %}{% endfor %}",
        LOAD_I18N,
    ),
    # ---- an operand channel the renderer owns (unchanged, kept as control) -
    "operand_names_super": _child("tick.html", "{% echo block.super %}"),
    "operand_names_super_in_loop": _child(
        "tick.html", "{% for i in three %}{% echo block.super %}{% endfor %}"
    ),
    "with_alias_then_bridged": _child(
        "tick.html", "{% with s=block.super %}{% probe 'child' %}{{ s }}{{ s }}{% endwith %}"
    ),
    # ---- multi-level --------------------------------------------------------
    "multilevel_ctx_reads": _child("mid.html", ARM + "{% probe_super 'child' %}"),
    "multilevel_unread": _child(
        "mid.html", "{% if show %}{{ block.super }}{% endif %}{% probe 'child' %}"
    ),
    "multilevel_loop_read": _child(
        "mid.html", ARM + "{% for i in three %}{% probe_super 'child' %}{% endfor %}"
    ),
    # ---- a parent that needs the loader / has side effects of its own -------
    "include_in_parent_read_by_tag": _child("inc.html", ARM + "[{% probe_super 'child' %}]"),
    "include_in_parent_unread": _child(
        "inc.html", "{% if show %}{{ block.super }}{% endif %}{% probe 'child' %}"
    ),
    # ---- a raising parent -----------------------------------------------------
    "boom_unread_by_bridged_tag": _child(
        "boom.html", "{% if show %}{{ block.super }}{% endif %}{% probe 'child' %}ok"
    ),
    "boom_read_by_bridged_tag": _child("boom.html", ARM + "{% probe_super 'child' %}"),
    # ---- the parent's OWN exception class must reach the tag unchanged -------
    # Django's resolver catches five classes around its mapping access; a
    # `block` that evaluated the parent there would have the error swallowed
    # (and the parent re-run), or would have to re-wrap it.
    **{
        f"{how}_over_{kind}": _child(
            f"boom_{kind}.html",
            ARM + body,
            LOAD_I18N,
        )
        for kind in RAISED
        for how, body in (
            ("tag_uncaught", "{% probe_super 'child' %}"),
            ("tag_catches", "{% catch_super 'child' %}"),
            (
                "blocktranslate",
                "{% blocktranslate with s=block.super %}v={{ s }}{% endblocktranslate %}",
            ),
        )
    },
    # ---- a user variable named `block` is not ours to replace ---------------
    "user_block_loop_var": _child(
        "tick.html", ARM + "{% for block in three %}{% show_block %}{% endfor %}"
    ),
    "user_block_with": _child(
        "tick.html", ARM + "{% with block='mine' %}{% show_block %}{% endwith %}"
    ),
    # ---- the safety grant ---------------------------------------------------
    "autoescape_markup": _child("amp.html", ARM + "[{% probe_super 'child' %}]"),
    "parent_escapes_user_data": _child("raw.html", ARM + "[{% probe_super 'child' %}]"),
    # ---- the control: no reference at all ----------------------------------------
    "not_referenced": _child("tick.html", "{% probe 'child' %}"),
}


@pytest.fixture(scope="module")
def template_dir():
    with tempfile.TemporaryDirectory() as directory:
        for name, source in PARENTS.items():
            Path(directory, name).write_text(source)
        yield directory


def _answer(engine: str, template_dir: str, source: str):
    """``(rendered-or-exception, {probe label: calls})`` for one cell."""
    CALLS.clear()
    data = {
        "show": False,
        "three": [1, 2, 3],
        "x": "X",
        "danger": "<script>x</script>",
    }
    try:
        engine_obj = Engine(
            dirs=[template_dir],
            libraries={LIB_NAME: LIB_NAME, "i18n": "django.templatetags.i18n"},
        )
        # A compiled Django Template in the context is how `{% include %}`
        # reaches `render_template_object` on the djust side.
        data["tpl_plain"] = engine_obj.from_string(LOAD + "{% probe 'tpl' %}T")
        if engine == "django":
            rendered = engine_obj.from_string(source).render(DjangoContext(data))
        else:
            backend = DjustTemplateBackend(
                {
                    "NAME": "t2918",
                    "DIRS": [template_dir],
                    "APP_DIRS": False,
                    "OPTIONS": {"libraries": {LIB_NAME: LIB_NAME}},
                }
            )
            rendered = backend.from_string(source).render(data)
    except Exception as exc:  # noqa: BLE001 - the refusal IS the answer
        rendered = "<<%s>>" % re.sub(r"\s+", " ", str(exc))[-40:]
    return rendered, dict(CALLS)


class TestDifferentialAgainstDjango:
    @pytest.mark.parametrize("case", sorted(CASES))
    def test_output_and_every_probe_count_match_django(self, case, template_dir):
        source = CASES[case]
        django_answer = _answer("django", template_dir, source)
        djust_answer = _answer("djust", template_dir, source)
        assert djust_answer == django_answer, case

    def test_the_corpus_is_not_silently_shrunk(self):
        assert len(CASES) == 57


class TestTheCountsAreNotVacuous:
    """Django's own numbers, stated outright, so a shared wrong answer cannot
    pass the differential (#1200)."""

    def test_the_issue_shape_runs_each_probe_the_django_number_of_times(self, template_dir):
        rendered, calls = _answer("djust", template_dir, CASES["issue_shape"])
        assert calls == {"child": 3, "mid": 6, "base": 6}, calls
        assert rendered == _answer("django", template_dir, CASES["issue_shape"])[0]

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            # Armed and never read: the parent does not run for a bridged tag.
            ("armed_unread_false_branch", {"child": 1}),
            ("armed_unread_ctx_tag", {"child": 1}),
            ("armed_unread_in_loop", {"child": 3}),
            ("armed_read_once_probe_in_loop", {"child": 3, "parent": 1}),
            ("armed_read_per_iteration", {"child": 3, "parent": 3}),
            ("multilevel_unread", {"child": 1}),
            ("blocktranslate_unread", {}),
            ("blocktranslate_in_loop_unread", {}),
            ("include_in_parent_unread", {"child": 1}),
            ("boom_unread_by_bridged_tag", {"child": 1}),
            # Read by the tag, once per read: NOT memoized.
            ("ctx_tag_reads_super", {"child": 1, "parent": 1}),
            ("ctx_tag_reads_super_twice", {"child": 1, "parent": 2}),
            ("ctx_tag_reads_super_per_iteration", {"child": 3, "parent": 3}),
            ("multilevel_loop_read", {"child": 3, "mid": 9, "base": 9}),
            ("blocktranslate_in_loop", {"parent": 3}),
            ("operand_names_super_in_loop", {"parent": 3}),
        ],
    )
    def test_exact_counts(self, case, expected, template_dir):
        _, calls = _answer("djust", template_dir, CASES[case])
        assert calls == expected, calls

    def test_a_side_effect_in_the_parent_runs_once_per_read_by_a_bridged_tag(self, template_dir):
        """``{% cycle %}`` lives on state a ``Context`` clone SHARES, so three
        reads answer ``a-b-a``; a memo would answer ``a-a-a``."""
        rendered, _ = _answer("djust", template_dir, CASES["cycle_read_by_two_tags"])
        assert rendered == "a-b"
        rendered, _ = _answer("djust", template_dir, CASES["cycle_read_by_tag_and_expression"])
        assert rendered == "a-b"
        rendered, _ = _answer("djust", template_dir, CASES["cycle_read_twice_in_one_tag"])
        assert rendered == "a|b"

    def test_a_raising_parent_is_harmless_to_a_tag_that_does_not_read_it(self, template_dir):
        rendered, calls = _answer("djust", template_dir, CASES["boom_unread_by_bridged_tag"])
        assert rendered == "&lt;child&gt;ok"
        assert "parent" not in calls

    def test_a_raising_parent_still_raises_through_the_tag_that_reads_it(self, template_dir):
        rendered, calls = _answer("djust", template_dir, CASES["boom_read_by_bridged_tag"])
        assert rendered.startswith("<<")
        assert "parent exploded" in rendered
        assert calls == {"child": 1, "parent": 1}

    def test_the_parents_own_escaping_is_neither_undone_nor_repeated(self, template_dir):
        markup, _ = _answer("djust", template_dir, CASES["autoescape_markup"])
        assert markup == "[<b>&</b>]"
        user_data, _ = _answer("djust", template_dir, CASES["parent_escapes_user_data"])
        assert user_data == "[&lt;script&gt;x&lt;/script&gt;]"


class TestTheMechanism:
    def test_no_bridged_site_renders_the_parent_eagerly(self):
        """The eager materialiser is gone: the only way a bridged tag gets the
        parent is by READING ``block.super`` off the lazy object."""
        source = RENDERER_RS.read_text()
        helper = source.split("fn bridged_context_map", 1)[1][:2500]
        assert "render_armed_block_super" not in helper
        assert "lazy_block_value" in helper
        assert "Value::String(parent_html)" not in helper


class TestTheShapeOfTheBlockAHandlerReceives:
    """Django's own ``BlockNode`` shape: ``super()`` is a method, the object is
    not subscriptable, and asking about it never evaluates it. (The ``dict``
    this replaced allowed ``block["super"]``; keeping that is what made a
    parent's ``ValueError`` arrive as a different class, so it is gone. Nothing
    in djust, djust.org, djustlive, djust-docs, docs.djust.org or sklful uses
    it.)"""

    def test_get_super_and_membership_answer_the_parent_once_per_read(self, template_dir):
        source = _child("amp.html", ARM + "{% probe_methods 'child' %}")
        rendered, calls = _answer("djust", template_dir, source)
        assert rendered == "<b>&</b>|<b>&</b>|True|False|True"
        assert calls == {"child": 1}

    def test_it_is_not_subscriptable_exactly_as_djangos_block_is_not(self, template_dir):
        source = _child("amp.html", ARM + "{% probe_subscript 'child' %}")
        assert _answer("djust", template_dir, source) == _answer("django", template_dir, source)
        assert _answer("djust", template_dir, source)[0] == "TypeError"

    def test_containment_and_keys_do_not_render_the_parent(self, template_dir):
        source = _child("tick.html", ARM + "{% probe_membership 'child' %}")
        rendered, calls = _answer("djust", template_dir, source)
        assert rendered == "True|False|['super']|1"
        assert calls == {"child": 1}, "asking whether `super` exists must not evaluate it"

    def test_copy_and_deepcopy_return_the_handle_and_pickle_refuses(self, template_dir):
        source = _child("tick.html", ARM + "{% probe_copy %}")
        assert _answer("djust", template_dir, source)[0] == "True|True|TypeError"


class TestTheParentsOwnExceptionReachesTheTag:
    """The class of the parent's error is what a tag's ``except`` and a
    ``{% blocktranslate %}`` see, on both engines."""

    @pytest.mark.parametrize("kind", sorted(RAISED))
    def test_a_tag_with_its_own_except_catches_the_parents_class(self, kind, template_dir):
        rendered, calls = _answer("djust", template_dir, CASES[f"tag_catches_over_{kind}"])
        assert rendered == f"caught-{kind}"
        assert calls == {"child": 1, "parent": 1}, "the parent runs once, not once per lookup step"

    @pytest.mark.parametrize("kind", sorted(RAISED))
    def test_blocktranslate_over_a_raising_parent_raises_the_parents_error(
        self, kind, template_dir
    ):
        rendered, calls = _answer("djust", template_dir, CASES[f"blocktranslate_over_{kind}"])
        assert rendered.startswith("<<") and "parent exploded" in rendered
        assert calls == {"parent": 1}


class TestTheLazyBlockDoesNotOutliveTheCall:
    """It holds strong references to the render context's Python objects and
    has no ``tp_traverse``, so a tag that keeps the context it received used to
    close a cycle the collector could not see (every block of every extending
    template is armed, so every bridged call there was exposed)."""

    @staticmethod
    def _render(template_dir, body):
        class Holder:
            ctx = None
            block = None

        holder = Holder()
        backend = DjustTemplateBackend(
            {
                "NAME": "t2918gc",
                "DIRS": [template_dir],
                "APP_DIRS": False,
                "OPTIONS": {"libraries": {LIB_NAME: LIB_NAME}},
            }
        )
        source = _child("tick.html", ARM + body)
        backend.from_string(source).render({"holder": holder, "show": False})
        return holder

    def test_a_holder_that_keeps_the_context_is_collectable(self, template_dir):
        holder = self._render(template_dir, "{% keep_context %}")
        assert holder.ctx is not None, "the tag kept the context"
        ref = weakref.ref(holder)
        del holder
        gc.collect()
        assert ref() is None, "the holder survived gc: an uncollectable cycle through LazyBlock"

    def test_a_block_kept_past_the_render_answers_empty_and_runs_nothing(self, template_dir):
        CALLS.clear()
        holder = self._render(template_dir, "{% keep_block %}")
        CALLS.clear()
        assert str(holder.block.super()) == ""
        assert holder.block.get("super") is not None and str(holder.block.get("super")) == ""
        assert CALLS == {}, "a retained handle re-ran the parent's tags after the render"

    def test_a_holder_that_keeps_the_block_is_collectable(self, template_dir):
        holder = self._render(template_dir, "{% keep_block %}")
        ref = weakref.ref(holder)
        del holder
        gc.collect()
        assert ref() is None


class TestAnImplicitReaderNeedsTheScopeArmed:
    """A KNOWN, OLDER gap, recorded rather than fixed here.

    The scope is armed only when the child body NAMES ``block.super``
    (``inheritance::nodes_reference_block_super``). A ``takes_context`` tag that
    reads it through the context, with nothing else in the body naming it, runs
    against an unarmed scope and answers nothing where Django answers the
    parent. Identical before and after #2918; arming for every block that
    holds a bridged tag would clone the parent nodes on every such entry, which
    is a cost decision for the owner."""

    @pytest.mark.xfail(
        strict=True, reason="scope is armed only when the template names block.super"
    )
    def test_a_tag_reading_super_with_no_other_mention(self, template_dir):
        source = _child("tick.html", "{% probe_super 'child' %}")
        assert _answer("djust", template_dir, source) == _answer("django", template_dir, source)
