"""Item content in ``{% dashboard_grid %}`` panels, and
``{% audit_log %}`` column names, are escaped the same way on every render path:
the component class, the Django template tag, and the Rust template engine itself
(``djust._rust.render_template``, which renders the tag's own node).

The class and the tag ``conditional_escape`` these values.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from djust.components import rust_handlers as rh
from djust.components.components.dashboard_grid import DashboardGrid

MARKUP_VALUES = [
    "<img src=x onerror=alert(1)>",
    '"><script>alert(1)</script>',
    "x' onmouseover='alert(1)",
    'x" onmouseover="alert(1)',
    "<b>bold</b> & <i>it</i>",
]

_ENGINE = Engine(
    dirs=[str(Path(rh.__file__).resolve().parent / "templates")],
    libraries={"djust_components": "djust.components.templatetags.djust_components"},
)


def _tag(source: str, **context: Any) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + source).render(Context(context))


_REGISTERED: list[bool] = []


def _engine(source: str, **context: Any) -> str:
    from djust._rust import render_template

    if not _REGISTERED:
        # The components app registers these at startup; the test settings
        # may not install it.
        rh.register_with_rust_engine()
        _REGISTERED.append(True)
    return str(render_template(source, context))


def _bodies(html: str, cls: str) -> list[str]:
    return re.findall(rf'<div class="{cls}">(.*?)</div>', html, re.S)


def _dashboard(value: Any) -> dict[str, str]:
    panels = [{"id": "p1", "title": "T", "content": value}]
    outputs = {
        "class": str(DashboardGrid(panels=panels).render()),
        "tag": _tag("{% dashboard_grid panels=panels %}{% enddashboard_grid %}", panels=panels),
    }
    engine = _engine("{% dashboard_grid panels=panels %}{% enddashboard_grid %}", panels=panels)
    # Some releases' engines pass no list arguments to block handlers; those
    # render no panels at all (nothing to escape on that path).
    if "dj-dashboard-grid__panel-body" in engine:
        outputs["engine"] = engine
    else:
        assert value not in engine
    return outputs


def _is_raw(html: str) -> bool:
    return any(
        marker in html
        for marker in ("<img src=x", "<script>", "' onmouseover='", '" onmouseover="', "<b>bold")
    )


@pytest.mark.parametrize("value", MARKUP_VALUES)
def test_dashboard_panel_content_is_escaped_on_every_path(value):
    outputs = _dashboard(value)
    bodies = {
        path: _bodies(html, "dj-dashboard-grid__panel-body") for path, html in outputs.items()
    }
    for path, html in outputs.items():
        assert not _is_raw(html), (path, html)
        assert bodies[path] == bodies["class"], (path, bodies)
    assert bodies["class"] == [str(_tag("{{ v }}", v=value))]


def test_marked_safe_content_is_kept_where_the_value_reaches_the_tag():
    """conditional_escape semantics: a SafeString passed to the tag as an
    object stays markup, on the class, the tag and the Rust engine."""
    safe = mark_safe("<b>card</b>")
    html = _engine(
        "{% dashboard_grid panels=panels %}{% enddashboard_grid %}",
        panels=[{"id": "p", "title": "T", "content": safe}],
    )
    assert '<div class="dj-dashboard-grid__panel-body"><b>card</b></div>' in html


def test_valid_plain_text_renders_unchanged():
    outputs = _dashboard("Panel text")
    for html in outputs.values():
        assert '<div class="dj-dashboard-grid__panel-body">Panel text</div>' in html


# --------------------------------------------------------------------------- #
# Three more components whose values must stay escaped (or cleaned) on every
# path: file_dropzone's name, meter values, rich_text_editor's value.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", MARKUP_VALUES)
def test_file_dropzone_name_is_escaped_on_every_path(value):
    from djust.components.components.file_dropzone import FileDropzone

    outputs = {
        "class": str(FileDropzone(name=value).render()),
        "tag": _tag("{% file_dropzone name=n %}", n=value),
        "engine": _engine("{% file_dropzone name=n %}", n=value),
    }
    for path, html in outputs.items():
        assert not _is_raw(html), (path, html)


@pytest.mark.parametrize("value", MARKUP_VALUES)
def test_meter_segment_values_are_escaped_on_every_path(value):
    from djust.components.components.meter import Meter

    segments = [{"value": value, "label": value, "color": "#3b82f6"}]
    renders = {
        "class": lambda: str(Meter(segments=segments).render()),
        "tag": lambda: _tag("{% meter segments=segments %}", segments=segments),
        "engine": lambda: _engine("{% meter segments=segments %}", segments=segments),
    }
    rendered = []
    for path, render in renders.items():
        try:
            html = render()
        except (TypeError, ValueError):
            continue  # this path refuses a non-numeric value outright
        rendered.append(path)
        assert not _is_raw(html), (path, html)
    assert "engine" in rendered


@pytest.mark.parametrize(
    "value",
    [
        "<img src=x onerror=alert(1)>",
        "<p>ok</p><script>alert(1)</script>",
        '<a href="javascript:alert(1)">x</a>',
        '<p onclick="alert(1)">x</p>',
    ],
)
def test_rich_text_editor_value_is_cleaned_on_every_path(value):
    from djust.components.components.rich_text_editor import RichTextEditor

    outputs = {
        "class": str(RichTextEditor(value=value).render()),
        "tag": _tag("{% rich_text_editor value=v %}", v=value),
        "engine": _engine("{% rich_text_editor value=v %}", v=value),
    }
    for path, html in outputs.items():
        lowered = html.lower()
        assert "onerror" not in lowered and "<script" not in lowered, (path, html)
        assert "javascript:" not in lowered and 'onclick="alert' not in lowered, (path, html)


def test_rich_text_editor_keeps_formatting():
    from djust.components.components.rich_text_editor import RichTextEditor

    value = "<p>Hello <strong>bold</strong> <em>it</em></p>"
    assert value in str(RichTextEditor(value=value).render())
    assert value in _tag("{% rich_text_editor value=v %}", v=value)


# --------------------------------------------------------------------------- #
# audit_log: column names go into each cell's class attribute.
# --------------------------------------------------------------------------- #


def _audit_cell_classes(html: str) -> list[str]:
    return re.findall(r'<td class="(dj-audit-log__td[^"]*)"', html)


@pytest.mark.parametrize("value", MARKUP_VALUES)
def test_audit_log_column_names_are_escaped_on_every_path(value):
    from djust.components.components.audit_log import AuditLog

    columns = ["user", value]
    entries = [{"user": "ada", value: "cell"}]
    source = "{% audit_log entries=entries columns=columns %}"
    outputs = {
        "class": str(AuditLog(entries=entries, columns=columns).render()),
        "tag": _tag(source, entries=entries, columns=columns),
        "engine": _engine(source, entries=entries, columns=columns),
    }
    expected = [
        "dj-audit-log__td dj-audit-log__td--user",
        f"dj-audit-log__td dj-audit-log__td--{_tag('{{ v }}', v=value)}",
    ]
    for path, html in outputs.items():
        assert not _is_raw(html), (path, html)
        assert _audit_cell_classes(html) == expected, (path, html)


def test_audit_log_plain_column_names_render_unchanged():
    from djust.components.components.audit_log import AuditLog

    columns = ["timestamp", "user"]
    entries = [{"timestamp": "2026-03-25 14:30", "user": "ada"}]
    source = "{% audit_log entries=entries columns=columns %}"
    outputs = [
        str(AuditLog(entries=entries, columns=columns).render()),
        _tag(source, entries=entries, columns=columns),
        _engine(source, entries=entries, columns=columns),
    ]
    for html in outputs:
        assert '<td class="dj-audit-log__td dj-audit-log__td--user">ada</td>' in html
