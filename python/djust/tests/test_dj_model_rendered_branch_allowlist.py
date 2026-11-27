"""dj-model accepts only fields bound in the branch the template rendered.

The template's static ``dj-model`` bindings span every ``{% if %}`` branch.
The writable set of a view instance is that static set narrowed to the
bindings present in the output of its last render: a field bound only inside a
branch that was not rendered for this instance is refused, becomes writable
when a re-render emits it, and is refused again when a re-render drops it.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

_GATED = (
    '<div dj-root><input dj-model="search">'
    '{% if is_staff %}<input dj-model="staff_note">{% endif %}'
    "</div>"
)


class _Templates:
    """Write templates to a tmp dir wired into Django + Rust search paths."""

    def __init__(self, **files: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        for name, src in files.items():
            (self._tmpdir / f"{name}.html").write_text(src)
        self._override = None

    def __enter__(self):
        from django.conf import settings
        from django.test import override_settings

        from djust.utils import clear_template_dirs_cache

        templates = [dict(t) for t in settings.TEMPLATES]
        templates[0] = dict(templates[0])
        templates[0]["DIRS"] = [str(self._tmpdir), *templates[0].get("DIRS", [])]
        self._override = override_settings(TEMPLATES=templates)
        self._override.enable()
        clear_template_dirs_cache()
        return self

    def __exit__(self, *exc):
        from djust.utils import clear_template_dirs_cache

        if self._override is not None:
            self._override.disable()
        clear_template_dirs_cache()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


def _view(template_name: str, **attrs):
    from djust import LiveView

    names = ["search", "staff_note", "is_staff", "rows", "comment", *attrs]
    defaults = {
        "search": "",
        "staff_note": "",
        "is_staff": False,
        "rows": [],
        "comment": "",
        **attrs,
    }
    body = {
        "template_name": f"{template_name}.html",
        **defaults,
        "mount": lambda self, request, **kwargs: None,
        "get_context_data": lambda self, **kwargs: {n: getattr(self, n) for n in names},
    }
    view = type("BranchView", (LiveView,), {"exposure_policy": "explicit", **body})()
    view.mount(None)
    return view


def _inline_view(src: str, **attrs):
    """A view whose template is a string (Rust-only tags never reach Django)."""
    view = _view("unused", **attrs)
    type(view).template_name = None
    type(view).template = src
    return view


@pytest.mark.django_db
class TestConditionalBinding:
    def test_untaken_branch_field_is_refused(self):
        with _Templates(gated=_GATED):
            v = _view("gated", is_staff=False)
            html, _, _ = v.render_with_diff(None)
            assert "staff_note" not in html
            assert v._dj_model_fields == frozenset({"search"})

            v.update_model(field="staff_note", value="written")
            assert v.staff_note == ""
            # The rendered binding still works.
            v.update_model(field="search", value="q")
            assert v.search == "q"

    def test_taken_branch_field_is_allowed(self):
        with _Templates(gated=_GATED):
            v = _view("gated", is_staff=True)
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search", "staff_note"})

            v.update_model(field="staff_note", value="written")
            assert v.staff_note == "written"

    def test_allowlist_follows_the_condition_across_renders(self):
        with _Templates(gated=_GATED):
            v = _view("gated", is_staff=False)
            v.render_with_diff(None)
            v.update_model(field="staff_note", value="a")
            assert v.staff_note == ""

            v.is_staff = True
            v.render_with_diff(None)
            v.update_model(field="staff_note", value="b")
            assert v.staff_note == "b"

            v.is_staff = False
            v.render_with_diff(None)
            v.update_model(field="staff_note", value="c")
            assert v.staff_note == "b"

    def test_full_template_render_is_narrowed_too(self):
        with _Templates(gated=_GATED):
            v = _view("gated", is_staff=False)
            v.render_full_template(None)
            assert v._dj_model_fields == frozenset({"search"})

    def test_else_branch_grants_only_its_own_binding(self):
        src = (
            '<div dj-root>{% if is_staff %}<input dj-model="staff_note">'
            '{% else %}<input dj-model="search">{% endif %}</div>'
        )
        with _Templates(other=src):
            v = _view("other", is_staff=False)
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search"})
            v.is_staff = True
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"staff_note"})


@pytest.mark.django_db
class TestLegitimateShapesStillBind:
    def test_binding_inside_a_loop(self):
        src = (
            '<div dj-root>{% for r in rows %}<input dj-model="search" value="{{ r }}">'
            "{% endfor %}</div>"
        )
        with _Templates(loop=src):
            v = _view("loop", rows=["a", "b"])
            v.render_with_diff(None)
            v.update_model(field="search", value="x")
            assert v.search == "x"

    def test_binding_in_an_included_partial(self):
        with _Templates(
            outer='<div dj-root>{% if is_staff %}{% include "part.html" %}{% endif %}</div>',
            part='<textarea dj-model="staff_note"></textarea>',
        ):
            v = _view("outer", is_staff=True)
            v.render_with_diff(None)
            assert "staff_note" in v._dj_model_fields
            v.is_staff = False
            v.render_with_diff(None)
            assert "staff_note" not in v._dj_model_fields

    def test_explicit_allowed_model_fields_still_applies(self):
        with _Templates(gated=_GATED):
            v = _view("gated", is_staff=False, allowed_model_fields=["staff_note"])
            v.render_with_diff(None)
            v.update_model(field="staff_note", value="explicit")
            assert v.staff_note == "explicit"


@pytest.mark.django_db
class TestRenderedDataNeverAddsABinding:
    """The field is bound only in an untaken branch; the data tries to
    re-create the binding in the output. None of it counts: only template
    text that rendered grants a field."""

    def _assert_refused(self, src, **attrs):
        with _Templates(inj=src):
            v = _view("inj", is_staff=False, **attrs)
            html, _, _ = v.render_with_diff(None)
            assert "staff_note" not in v._dj_model_fields, html
            v.update_model(field="staff_note", value="written")
            assert v.staff_note == ""
            return html

    def test_unquoted_interpolated_attribute(self):
        html = self._assert_refused(
            '<div dj-root><input dj-model="search"><span title={{ comment }}>x</span>'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>',
            comment="x dj-model=staff_note",
        )
        # The payload really is an attribute of the rendered element.
        assert "dj-model=staff_note" in html or 'dj-model="staff_note"' in html

    def test_dynamic_binding_name(self):
        self._assert_refused(
            '<div dj-root><input dj-model="{{ comment }}">'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>',
            comment="staff_note",
        )

    def test_safe_html(self):
        self._assert_refused(
            '<div dj-root><input dj-model="search">{{ comment|safe }}'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>',
            comment='<input dj-model="staff_note">',
        )

    def test_escaped_text(self):
        self._assert_refused(
            '<div dj-root><input dj-model="search"><p>{{ comment }}</p>'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>',
            comment='<input dj-model="staff_note"> dj-model="staff_note"',
        )

    def test_output_cannot_add_a_field_the_template_never_binds(self):
        src = '<div dj-root><input dj-model="search">{{ comment|safe }}</div>'
        with _Templates(safe=src):
            v = _view("safe", comment='<input dj-model="staff_note">')
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search"})


@pytest.mark.django_db
class TestEmbeddedChildRender:
    """A ``{% live_render %}`` child renders through Django's engine; its
    allowlist follows the TextNode runs that render executed."""

    def _child(self, src, **attrs):
        from djust import LiveView

        names = ["search", "staff_note", "is_staff", "comment"]
        body = {
            "template": src,
            "search": "",
            "staff_note": "",
            "is_staff": False,
            "comment": "",
            **attrs,
            "get_context_data": lambda self, **kw: {n: getattr(self, n) for n in names},
        }
        return type("Child", (LiveView,), {"exposure_policy": "explicit", **body})()

    def test_child_branch_and_injection(self):
        from djust.websocket import render_embedded_child_html

        src = (
            '<div dj-root><input dj-model="search"><span title={{ comment }}>x</span>'
            "{{ comment|safe }}"
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>'
        )
        child = self._child(src, comment="x dj-model=staff_note")
        render_embedded_child_html(child)
        assert child._dj_model_fields == frozenset({"search"})

        child = self._child(src, is_staff=True)
        render_embedded_child_html(child)
        assert child._dj_model_fields == frozenset({"search", "staff_note"})


class TestTextNodeRecorder:
    def test_scan_matches_the_template_walker(self):
        from djust._dj_model_bindings import scan_dj_model_bindings

        assert scan_dj_model_bindings('<input dj-model="a"><i data-dj-model="b">') == ["a"]
        assert scan_dj_model_bindings("<input dj-model='a' x dj-model=\"\">") == ["a"]
        assert scan_dj_model_bindings("<input dj-model=a>") == []

    def test_records_only_the_text_that_rendered(self):
        from django.template import Context, Template

        from djust._dj_model_bindings import record_bindings

        tpl = Template('<input dj-model="a">{% if f %}<input dj-model="b">{% endif %}{{ v|safe }}')
        with record_bindings() as fields:
            tpl.render(Context({"f": False, "v": '<input dj-model="c">'}))
        assert fields == {"a"}
        with record_bindings() as outer:
            with record_bindings() as inner:
                tpl.render(Context({"f": True}))
        assert inner == {"a", "b"} and outer == set()


_CHILD_SRC = (
    '<div dj-root><input dj-model="search"><span title={{ comment }}>x</span>'
    "{{ comment|safe }}"
    '{% if is_staff %}{% include "part.html" %}{% endif %}</div>'
)


def _file_child(**attrs):
    from djust import LiveView

    names = ["search", "staff_note", "is_staff", "comment"]
    body = {
        "template_name": "child.html",
        "search": "",
        "staff_note": "",
        "is_staff": False,
        "comment": "",
        **attrs,
        "get_context_data": lambda self, **kw: {n: getattr(self, n) for n in names},
    }
    return type("FileChild", (LiveView,), {"exposure_policy": "explicit", **body})()


def test_child_allowlist_under_each_template_engine(rel_files) -> None:
    """Django's engine and djust's own backend (which renders in Rust) agree."""
    from djust.websocket import render_embedded_child_html

    rel_files("child.html", _CHILD_SRC)
    rel_files("part.html", '<textarea dj-model="staff_note"></textarea>')

    child = _file_child(comment="x dj-model=staff_note")
    render_embedded_child_html(child)
    assert child._dj_model_fields == frozenset({"search"})

    child = _file_child(comment='<input dj-model="staff_note">')
    render_embedded_child_html(child)
    assert child._dj_model_fields == frozenset({"search"})

    child = _file_child(is_staff=True)
    render_embedded_child_html(child)
    assert child._dj_model_fields == frozenset({"search", "staff_note"})


# --- Bindings are recorded during the real render ---------------------------

from django import template as _django_template  # noqa: E402

register = _django_template.Library()


class _DiscardBodyNode(_django_template.Node):
    """Renders its body, then decides not to emit it (a permission gate)."""

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        self.nodelist.render(context)
        return ""


@register.tag("gate_discard")
def _gate_discard(parser, token):
    nodelist = parser.parse(("endgate_discard",))
    parser.delete_first_token()
    return _DiscardBodyNode(nodelist)


@register.inclusion_tag(_django_template.Template('<input dj-model="staff_note">'))
def gate_inclusion():
    return {}


class _Counter:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result

    def render(self, *args):
        self.calls += 1
        if self.result is not None:
            return self.result
        return {"assigned": "x"} if len(args) == 2 else args[1]


@pytest.fixture
def rust_tags():
    """Register Rust tag handlers for one test, unregistering them after."""
    from djust import _rust

    registered = []

    def add(kind, name, handler, end=None):
        if kind == "assign":
            _rust.register_assign_tag_handler(name, handler)
        elif kind == "block":
            _rust.register_block_tag_handler(name, end or "end" + name, handler)
        else:
            _rust.register_tag_handler(name, handler)
        registered.append((kind, name))
        return handler

    yield add
    for kind, name in registered:
        {
            "assign": _rust.unregister_assign_tag_handler,
            "block": _rust.unregister_block_tag_handler,
            "tag": _rust.unregister_tag_handler,
        }[kind](name)


@pytest.mark.django_db
class TestRecordedDuringTheRender:
    def test_template_code_runs_once_per_render(self, rust_tags):
        assign = rust_tags("assign", "dm_rec_assign", _Counter())
        inline = rust_tags("tag", "dm_rec_inline", _Counter(result="i"))
        src = (
            "<div dj-root>{% dm_rec_assign %}{% dm_rec_inline %}{{ assigned }}"
            '<input dj-model="search">'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>'
        )
        v = _inline_view(src, is_staff=True)
        v.render_with_diff(None)
        assert v._dj_model_fields == frozenset({"search", "staff_note"})
        assert (assign.calls, inline.calls) == (1, 1)
        v.render_full_template(None)
        assert (assign.calls, inline.calls) == (2, 2)

    def test_partial_render_keeps_bindings_of_cached_nodes(self):
        src = (
            '<div dj-root><input dj-model="search">'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}'
            "<p>{{ comment }}</p></div>"
        )
        with _Templates(partial=src):
            v = _view("partial", is_staff=True)
            v.render_with_diff(None)
            assert "staff_note" in v._dj_model_fields
            # Only ``comment`` changes: the first node is served from the node
            # cache, and still contributes the bindings it rendered.
            v.comment = "changed"
            html, _, _ = v.render_with_diff(None)
            assert "changed" in html
            assert v._dj_model_fields == frozenset({"search", "staff_note"})
            v.is_staff = False
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search"})

    def test_loop_bindings_survive_the_loop_render_caches(self):
        src = (
            "<div dj-root>{% for r in rows %}"
            '<label>{{ r }}<input dj-model="search" value="{{ r }}"></label>{% endfor %}'
            '{% for r in rows %}{% if is_staff %}<input dj-model="staff_note">{% endif %}'
            "{% endfor %}<p>{{ comment }}</p></div>"
        )
        with _Templates(cached_loop=src):
            v = _view("cached_loop", rows=["a", "b"], is_staff=True)
            for n in range(3):
                v.comment = str(n)
                v.render_with_diff(None)
                assert v._rust_view.loop_render_cache_enabled()
                assert v._dj_model_fields == frozenset({"search", "staff_note"})
            v.rows = ["a", "b", "c"]
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search", "staff_note"})
            v.is_staff = False
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search"})


@pytest.mark.django_db
class TestCustomBlockTagBodiesAreNotGranted:
    def test_native_block_tag_that_drops_its_body(self, rust_tags):
        rust_tags("block", "dm_ifperm", _Counter(result=""))
        src = (
            '<div dj-root><input dj-model="search">'
            '{% dm_ifperm %}<input dj-model="staff_note">{% enddm_ifperm %}</div>'
        )
        v = _inline_view(src, is_staff=False)
        v.render_with_diff(None)
        assert v._dj_model_fields == frozenset({"search"})
        v.update_model(field="staff_note", value="written")
        assert v.staff_note == ""

    def test_native_block_tag_that_emits_its_body_still_fails_closed(self, rust_tags):
        class Wrap:
            def render(self, args, content, context):
                return content

        rust_tags("block", "dm_wrap", Wrap())
        src = '<div dj-root>{% dm_wrap %}<input dj-model="staff_note">{% enddm_wrap %}</div>'
        v = _inline_view(src)
        html, _, _ = v.render_with_diff(None)
        assert 'dj-model="staff_note"' in html
        assert v._dj_model_fields == frozenset()
        v2 = _inline_view(src, allowed_model_fields=["staff_note"])
        v2.render_with_diff(None)
        v2.update_model(field="staff_note", value="listed")
        assert v2.staff_note == "listed"

    def test_handler_declaring_emits_body_is_granted(self, rust_tags):
        class Layout:
            EMITS_BODY = True

            def render(self, args, content, context):
                return "<section>%s</section>" % content

        from djust import _rust

        rust_tags("block", "dm_layout", Layout())
        assert _rust.block_tag_handler_emits_body("dm_layout")
        assert not _rust.block_tag_handler_emits_body("dm_no_such_tag")
        src = (
            "<div dj-root>{% dm_layout %}"
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}'
            "{% enddm_layout %}</div>"
        )
        v = _inline_view(src, is_staff=True)
        v.render_with_diff(None)
        assert v._dj_model_fields == frozenset({"staff_note"})
        v.is_staff = False
        v.render_with_diff(None)
        assert v._dj_model_fields == frozenset()

    def test_ifchanged_body_is_not_granted(self):
        src = (
            "<div dj-root>{% for r in rows %}{% ifchanged %}"
            '<input dj-model="staff_note">{% endifchanged %}{% endfor %}</div>'
        )
        with _Templates(changed=src):
            v = _view("changed", rows=["a"])
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset()


class TestDjangoEngineCustomNodes:
    def _engine(self):
        from django.template import Engine

        return Engine(libraries={"gate": __name__})

    def test_custom_node_that_discards_its_body(self):
        from django.template import Context

        from djust._dj_model_bindings import record_bindings

        tpl = self._engine().from_string(
            '{% load gate %}<input dj-model="search">'
            '{% gate_discard %}<input dj-model="staff_note">{% endgate_discard %}'
            '{% if f %}{% with x=1 %}<input dj-model="b">{% endwith %}{% endif %}'
        )
        with record_bindings() as fields:
            tpl.render(Context({"f": True}))
        assert fields == {"search", "b"}

    def test_inclusion_tag_template_is_not_recorded(self):
        from django.template import Context

        from djust._dj_model_bindings import record_bindings

        tpl = self._engine().from_string(
            '{% load gate %}<input dj-model="search">{% gate_inclusion %}'
        )
        with record_bindings() as fields:
            html = tpl.render(Context({}))
        assert 'dj-model="staff_note"' in html
        assert fields == {"search"}


class TestCheckT030:
    def _run(self, src):
        from djust.checks.templates import _check_dj_model_in_custom_block_tag

        errors: list = []
        _check_dj_model_in_custom_block_tag(src, "t.html", "/t.html", errors)
        return [(e.id, e.line_number) for e in errors]

    def test_warns_inside_a_custom_block_tag(self):
        src = (
            '<input dj-model="a">\n'
            '{% if x %}<input dj-model="b">{% endif %}\n'
            "{% ifperm %}\n"
            '  {% if y %}<input dj-model="c">{% endif %}\n'
            "{% endifperm %}\n"
            '{% ifchanged %}<input dj-model="d">{% endifchanged %}\n'
            '{% verbatim %}{% ifperm %}<input dj-model="e">{% endverbatim %}\n'
        )
        assert self._run(src) == [("djust.T030", 4), ("djust.T030", 6)]

    def test_inline_tags_and_dynamic_names_are_ignored(self):
        src = (
            '{% url "x" %}{% ifperm %}<input dj-model="{{ name }}">{% endifperm %}'
            '{% csrf_token %}<input dj-model="a">'
        )
        assert self._run(src) == []
