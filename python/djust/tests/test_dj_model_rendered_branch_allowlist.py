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
    view = type("BranchView", (LiveView,), body)()
    view.mount(None)
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


_ENGINES = {
    "django": "django.template.backends.django.DjangoTemplates",
    "djust": "djust.template.backend.DjustTemplateBackend",
}


@pytest.fixture(params=sorted(_ENGINES))
def rel_files(tmp_path, request):
    """Template files for each engine (``write(name, source)``)."""
    from django.test import override_settings

    from djust.utils import clear_template_dirs_cache

    root = tmp_path / "templates"
    root.mkdir()

    def write(name: str, source: str) -> None:
        (root / name).write_text(source)

    templates = [
        {"BACKEND": _ENGINES[request.param], "DIRS": [str(root)], "APP_DIRS": False, "OPTIONS": {}}
    ]
    with override_settings(TEMPLATES=templates):
        clear_template_dirs_cache()
        try:
            yield write
        finally:
            clear_template_dirs_cache()


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
