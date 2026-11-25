"""The attribute walk and its exporters are gone (#4202).

With the legacy exposure policy refused (#4199), ``get_context_data()`` builds
only the explicit context, ``get_state()`` returns only ``client=True`` fields,
and the debug panel, the observability ``view_assigns`` endpoint and the
``full_html_update`` signal show the bounded explicit projection.
"""

import pathlib

import pytest
from django.db import connection

import djust
from djust import LiveView, state
from djust.observability import sql as sql_capture

PACKAGE = pathlib.Path(djust.__file__).resolve().parent

WALK_FILES = (
    "mixins/context.py",
    "mixins/post_processing.py",
    "observability/views.py",
    "observability/sql.py",
    "mixins/rust_bridge.py",
)


@pytest.mark.parametrize("relative", WALK_FILES)
def test_walk_files_have_no_legacy_branch(relative):
    assert "uses_legacy_exposure" not in (PACKAGE / relative).read_text()


def test_removed_walk_names_are_gone():
    from djust.mixins import context
    from djust.observability import views

    assert not hasattr(context, "legacy_unsaved_keys")
    assert not hasattr(context, "legacy_render_only_keys")
    assert not hasattr(LiveView, "_is_serializable")
    assert not hasattr(views, "_lenient_assigns")
    assert "add_counts" not in (PACKAGE / "mixins/context.py").read_text()


class _Walked(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0)

    def mount(self, request=None, **kwargs):
        self.secret = "S"


def test_get_context_data_never_walks_undeclared_attributes():
    view = _Walked()
    view.mount(None)
    context = view.get_context_data()
    assert "secret" not in context
    assert "count" in context


class _Panel(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"
    a = state(1, client=True)
    b = state("x")


def test_debug_panel_on_explicit_view():
    view = _Panel()
    info = view.get_debug_info()
    assert info["handlers"] == {}
    assert info["variables"]["a"]["value"] == "1"
    assert info["variables"]["a"]["type"] == "int"
    assert info["variables"]["b"]["type"] == "redacted"
    assert set(info["variables"]) == {"a", "b"}
    update = view.get_debug_update()
    assert update["variables"] == info["variables"]
    assert view._debug_state_sizes() == info["state_sizes"]
    assert view.get_state() == {"a": 1}


class _Owner:
    pass


@pytest.mark.django_db
def test_sql_params_redacted_for_any_owner():
    sql_capture._clear_queries()
    owner = _Owner()
    with sql_capture.capture_for_event(session_id="s", owner=owner):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s AS walk4202_owned", ["PARAM_SENTINEL"])
    with sql_capture.capture_for_event(session_id="s"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s AS walk4202_unowned", ["PARAM_SENTINEL"])
    entries = {e["sql"]: e for e in sql_capture.get_queries_since()}
    owned = next(e for sql, e in entries.items() if "walk4202_owned" in sql)
    unowned = next(e for sql, e in entries.items() if "walk4202_unowned" in sql)
    assert owned["params"] == ["[redacted]"]
    assert unowned["params"] == ["PARAM_SENTINEL"]
