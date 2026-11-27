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

from djust._rust import rendered_dj_model_fields

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
class TestRenderedOutputOnlyNarrows:
    def test_output_cannot_add_a_field_the_template_never_binds(self):
        src = '<div dj-root><input dj-model="search">{{ comment|safe }}</div>'
        with _Templates(safe=src):
            v = _view("safe", comment='<input dj-model="staff_note">')
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search"})

    def test_text_that_looks_like_a_binding_does_not_count(self):
        src = (
            '<div dj-root><input dj-model="search"><p>{{ comment }}</p>'
            '{% if is_staff %}<input dj-model="staff_note">{% endif %}</div>'
        )
        with _Templates(text=src):
            v = _view("text", comment='<input dj-model="staff_note"> dj-model="staff_note"')
            v.render_with_diff(None)
            assert v._dj_model_fields == frozenset({"search"})


class TestRenderedFieldCollector:
    def test_only_element_attributes_count(self):
        html = (
            '<div dj-root><input dj-model="a"><p>dj-model="b"</p>'
            '<!-- <input dj-model="c"> --><textarea>&lt;input dj-model="d"&gt;</textarea></div>'
        )
        assert rendered_dj_model_fields(html) == ["a"]

    def test_embedded_child_views_are_skipped(self):
        html = (
            '<div dj-root><input dj-model="own">'
            '<div dj-view data-djust-embedded="c"><input dj-model="child"></div></div>'
        )
        assert rendered_dj_model_fields(html) == ["own"]

    def test_no_output_allows_nothing(self):
        from djust.mixins.model_binding import _narrow_to_rendered

        assert _narrow_to_rendered(["a"], "") == frozenset()
        assert _narrow_to_rendered(["a"], None) == frozenset()
        assert _narrow_to_rendered([], '<input dj-model="a">') == frozenset()
