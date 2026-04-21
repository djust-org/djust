"""Tests for the ``manage.py djust_typecheck`` command (v0.5.1 P2)."""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from djust.live_view import LiveView
from djust.management.commands.djust_typecheck import (
    _check_view,
    _extract_context_keys_from_ast,
    _extract_referenced_names,
    _extract_template_locals,
    _public_class_attrs,
)


def _template(src: str) -> Path:
    """Write ``src`` to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    f.write(src)
    f.close()
    return Path(f.name)


# ─────────────────────────────────────────────────────────────────────────────
# Helper-level tests
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_referenced_names_picks_up_variable_expressions():
    src = "<p>{{ user.name }}</p><p>{{ count|add:1 }}</p>"
    refs = _extract_referenced_names(src)
    names = {n for n, _ in refs}
    assert "user" in names
    assert "count" in names


def test_extract_referenced_names_picks_up_if_and_for_targets():
    src = "{% if is_admin %}{% for x in items %}{{ x }}{% endfor %}{% endif %}"
    refs = _extract_referenced_names(src)
    names = {n for n, _ in refs}
    assert "is_admin" in names
    assert "items" in names


def test_extract_referenced_names_ignores_if_keywords():
    src = "{% if a and not b or c %}{% endif %}"
    names = {n for n, _ in _extract_referenced_names(src)}
    # Keywords must not be reported as vars.
    assert "and" not in names and "or" not in names and "not" not in names
    assert {"a", "b", "c"} <= names


def test_extract_template_locals_binds_for_and_with():
    src = "{% for k, v in items %}{% with foo=1 %}{% endwith %}{% endfor %}"
    locals_ = _extract_template_locals(src)
    assert {"k", "v", "foo"} <= locals_


def test_extract_template_locals_binds_inputs_for_as_var():
    src = "{% inputs_for addresses as form %}{{ form.street }}{% endinputs_for %}"
    locals_ = _extract_template_locals(src)
    assert "form" in locals_


def test_public_class_attrs_returns_non_underscore_names():
    class _V(LiveView):
        foo = 1
        bar = 2
        _private = 3

    attrs = _public_class_attrs(_V)
    assert "foo" in attrs and "bar" in attrs
    assert "_private" not in attrs


def test_extract_context_keys_from_ast_finds_literal_dict_returns():
    class _V(LiveView):
        def get_context_data(self, **kwargs):
            return {"alpha": 1, "beta": 2}

    keys = _extract_context_keys_from_ast(_V)
    assert keys == {"alpha", "beta"}


def test_extract_context_keys_from_ast_finds_self_assignments():
    class _V(LiveView):
        def mount(self, request=None, **kwargs):
            self.count = 0
            self.items = []

        def tick(self):
            self.count += 1  # AugAssign

    keys = _extract_context_keys_from_ast(_V)
    assert {"count", "items"} <= keys


def test_extract_context_keys_from_ast_finds_annotated_assignments():
    """Typed `self.x: int = 0` declarations must be picked up (Stage 11 regression)."""

    class _V(LiveView):
        def mount(self, request=None, **kwargs):
            self.count: int = 0
            self.label: str = ""

    keys = _extract_context_keys_from_ast(_V)
    assert {"count", "label"} <= keys


def test_extract_context_keys_from_ast_finds_property_methods():
    class _V(LiveView):
        @property
        def display_name(self):
            return "x"

        @property
        def _private_prop(self):
            return "hidden"

    keys = _extract_context_keys_from_ast(_V)
    assert "display_name" in keys
    assert "_private_prop" not in keys  # private names skipped


# ─────────────────────────────────────────────────────────────────────────────
# _check_view integration tests
# ─────────────────────────────────────────────────────────────────────────────


def test_check_view_returns_none_when_all_names_resolve(monkeypatch):
    path = _template("<p>Hello {{ name }}</p>")

    class _V(LiveView):
        template_name = "ok.html"
        name = ""

    monkeypatch.setattr(
        "djust.management.commands.djust_typecheck._find_template_path",
        lambda _tn: path,
    )
    report = _check_view(_V)
    assert report is None


def test_check_view_reports_missing_names(monkeypatch):
    path = _template("<p>{{ missing_var }}</p>")

    class _V(LiveView):
        template_name = "bad.html"

    monkeypatch.setattr(
        "djust.management.commands.djust_typecheck._find_template_path",
        lambda _tn: path,
    )
    report = _check_view(_V)
    assert report is not None
    assert report["missing"][0]["name"] == "missing_var"


def test_check_view_respects_noqa_pragma(monkeypatch):
    path = _template("<p>{{ missing_var }}</p>\n{# djust_typecheck: noqa missing_var #}")

    class _V(LiveView):
        template_name = "pragma.html"

    monkeypatch.setattr(
        "djust.management.commands.djust_typecheck._find_template_path",
        lambda _tn: path,
    )
    assert _check_view(_V) is None


def test_check_view_strict_flag_flows_through(monkeypatch):
    path = _template("<p>{{ missing }}</p>")

    class _V(LiveView):
        template_name = "strict.html"
        strict_context = True

    monkeypatch.setattr(
        "djust.management.commands.djust_typecheck._find_template_path",
        lambda _tn: path,
    )
    report = _check_view(_V)
    assert report is not None
    assert report["strict"] is True


def test_check_view_honors_DJUST_TEMPLATE_GLOBALS(monkeypatch, settings):
    """Names in settings.DJUST_TEMPLATE_GLOBALS must resolve without being on the class."""
    path = _template("<p>{{ navbar }}</p>")

    class _V(LiveView):
        template_name = "globals.html"

    settings.DJUST_TEMPLATE_GLOBALS = ["navbar"]
    monkeypatch.setattr(
        "djust.management.commands.djust_typecheck._find_template_path",
        lambda _tn: path,
    )
    assert _check_view(_V) is None


def test_check_view_reports_when_template_not_found(monkeypatch):
    """Template loader failures must be surfaced, not silently swallowed."""

    class _V(LiveView):
        template_name = "does_not_exist.html"

    monkeypatch.setattr(
        "djust.management.commands.djust_typecheck._find_template_path",
        lambda _tn: None,
    )
    report = _check_view(_V)
    assert report is not None
    assert "template not found" in report["error"]


def test_check_view_skips_when_no_template_name():
    class _V(LiveView):
        pass

    report = _check_view(_V)
    assert report is None  # template_name=None short-circuits


# ─────────────────────────────────────────────────────────────────────────────
# Command-level integration — exercise via call_command
# ─────────────────────────────────────────────────────────────────────────────


def test_command_runs_cleanly_with_no_reports():
    """Default --view filter of a non-existent class means 0 reports."""
    out = StringIO()
    call_command("djust_typecheck", "--view", "__NoSuchView__", stdout=out)
    assert "no issues found" in out.getvalue().lower()


def test_command_json_output_is_parseable():
    out = StringIO()
    call_command("djust_typecheck", "--json", "--view", "__NoSuchView__", stdout=out)
    data = json.loads(out.getvalue())
    assert data == {"reports": []}
