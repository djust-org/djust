"""#4276: the queries a render makes after ``get_context_data()`` run inside the
``sync_turn_wrapper``'s ``"render"`` step.

Under explicit exposure a QuerySet reaches the template only through the
context, and the render evaluated it after the wrapped ``get_context_data()``
returned: in ``_sync_state_to_rust`` (the explicit JIT, the rows listed for the
template's sidecar, the normalize pass) and in the Rust render itself (a
relation the template walks on a model is a query). An app that scopes reads in
the wrapper (a per-request database role, a row-level security caller) got no
scope for them.

Each test records, for every SQL statement the render runs, whether the
wrapper's scope was open (``connection.execute_wrapper``).
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Callable, List
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import override_settings

from djust import LiveView
from djust.testing import LiveViewTestClient

pytestmark = pytest.mark.django_db

IN_SCOPE = contextvars.ContextVar("in_scope_4276", default=False)
ENTERED: List[str] = []


@contextlib.contextmanager
def scope(view: Any, step: str, turn: Any):
    ENTERED.append(step)
    token = IN_SCOPE.set(True)
    try:
        yield
    finally:
        IN_SCOPE.reset(token)


LOOP = "<div dj-root>{% for u in users %}<p>{{ u.pk }}</p>{% endfor %}</div>"


class ExplicitRows(LiveView):
    template = LOOP

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["users"] = get_user_model().objects.all()
        return context


class LegacyAddedAfterSuper(LiveView):
    exposure_policy = "legacy"  # legacy subject: #4276 row 3, a QuerySet added after super()
    template = LOOP

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["users"] = get_user_model().objects.all()
        return context


class LoopRelationWalk(LiveView):
    template = (
        "<div dj-root>{% for u in users %}<p>{{ u.username }}:{{ u.groups.count }}</p>"
        "{% endfor %}</div>"
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["users"] = get_user_model().objects.all()
        return context


class SidecarRelation(LiveView):
    template = "<div dj-root><p>{{ me.username }}:{{ me.groups.count }}</p></div>"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["me"] = get_user_model().objects.get(username="u1")
        return context


class BudgetedRows(ExplicitRows):
    query_budget = {"render": 0}


@pytest.fixture
def user():
    made = get_user_model().objects.create(username="u1")
    made.groups.add(Group.objects.create(name="g-4276"))
    return made


def _scopes_of(view_class: type, before_render: Callable[[Any], None] = lambda view: None):
    """Whether the wrapper's scope was open, per statement of one ``client.render()``."""
    seen: List[bool] = []

    def record(execute, sql, params, many, context):
        seen.append(IN_SCOPE.get())
        return execute(sql, params, many, context)

    with mock.patch("djust.sync_turn._wrapper", return_value=scope):
        client = LiveViewTestClient(view_class)
        client.mount()
        before_render(client.view_instance)
        del ENTERED[:]
        with connection.execute_wrapper(record):
            html = client.render()
    return seen, html


def test_an_explicit_context_queryset_is_read_inside_the_wrapper(user):
    seen, html = _scopes_of(ExplicitRows)
    assert "<p>%s</p>" % user.pk in html
    assert seen == [True]


def test_a_legacy_queryset_added_after_super_is_read_inside_the_wrapper(user):
    seen, html = _scopes_of(LegacyAddedAfterSuper)
    assert "<p>%s</p>" % user.pk in html
    assert seen and all(seen), seen


def test_a_loop_row_relation_walk_is_read_inside_the_wrapper(user):
    seen, html = _scopes_of(LoopRelationWalk)
    assert "u1:1" in html
    assert len(seen) >= 2 and all(seen), seen


def test_a_sidecar_model_relation_is_read_inside_the_wrapper(user):
    """The relation query runs in the Rust render's walk of the sidecar model, not
    in ``_sync_state_to_rust``: only the render methods' own step covers it."""
    seen, html = _scopes_of(SidecarRelation)
    assert "u1:1" in html
    assert len(seen) >= 2 and all(seen), seen


def test_the_wrapper_is_entered_once_per_render(user):
    """The render method's step, ``_sync_state_to_rust``'s and ``get_context_data()``'s
    nest: one wrapper entry for one render."""
    _scopes_of(ExplicitRows)
    assert ENTERED == ["render"]


def test_a_render_budget_counts_the_jit_queries(user):
    from djust._query_budget import QueryBudgetExceeded

    with override_settings(LIVEVIEW_CONFIG={"query_budget": "error"}):
        client = LiveViewTestClient(BudgetedRows)
        with pytest.raises(QueryBudgetExceeded, match="render"):
            client.mount()
            client.render()


def test_a_marked_view_is_refreshed_once_and_unmarked_after_the_render(user):
    """A view written to since it last rendered is refreshed once, by the outermost
    render step (the nested ones just run), unmarked when the render returns, and
    its read set completed after that (``recorded_render`` stays outermost, #4112)."""
    from djust import _held_rows, sync_turn

    refreshed: List[Any] = []
    completed: List[Any] = []
    real_refresh = sync_turn.refresh_for_render
    real_completed = _held_rows.render_completed

    def refresh(view: Any) -> None:
        refreshed.append(view)
        real_refresh(view)

    def complete(view: Any) -> None:
        completed.append((view, _held_rows.is_marked(view)))
        real_completed(view)

    with (
        mock.patch.object(sync_turn, "refresh_for_render", refresh),
        mock.patch.object(_held_rows, "render_completed", complete),
    ):
        seen, _html = _scopes_of(ExplicitRows, before_render=_held_rows.mark_tree)
    assert seen == [True]
    assert len(refreshed) == 1
    assert len(completed) == 1
    view, still_marked = completed[0]
    assert not still_marked
    assert not _held_rows.is_marked(view)


def test_a_direct_state_sync_reads_inside_the_wrapper(user):
    """The runtime calls ``_sync_state_to_rust()`` on its own before a render (an
    async result, a url_change, the socket mount): it is a render step itself."""
    seen: List[bool] = []

    def record(execute, sql, params, many, context):
        seen.append(IN_SCOPE.get())
        return execute(sql, params, many, context)

    with mock.patch("djust.sync_turn._wrapper", return_value=scope):
        client = LiveViewTestClient(ExplicitRows)
        client.mount()
        client.render()
        del ENTERED[:]
        with connection.execute_wrapper(record):
            client.view_instance._sync_state_to_rust()
    assert seen == [True]
    assert ENTERED == ["render"]
