"""#3867 -- the three mutations the review of #3863 found alive in the kept-rows handling of #3801.

``view._all_method_rows`` holds the evaluated rows of a top-level QuerySet whose template reads a
relation both as rows and through a QuerySet method; ``_sync_state_to_rust`` hands them to the engine's
sidecar so the method can be answered. Three properties of that hand-off had no test, and removing any
one of them left the suite green (gate-off, #3863 review):

* Y1 -- an explicit sidecar entry for the same name is not replaced by the kept rows;
* Y2 -- rows kept under a name that is not in the context are not handed over;
* Y6 -- rows are not kept at all for a QuerySet reached THROUGH a container (``ctx.users``): no
  top-level name can be answered from them.

Y2 and Y6 shadow each other in production (Y6 keeps nothing under a dotted name, so Y2 never sees
one), which is why neither mutation alone changed anything. Each is pinned here at its own seam: Y6
on what is kept, Y2 by putting a name the context lacks into the kept rows directly.
"""

# ruff: noqa: F811

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from djust.live_view import LiveView

from python.tests.test_queryset_all_residuals_3801 import MIXED, _users
from python.tests.test_queryset_all_traversal_3777 import world  # noqa: F401


class _Thing:
    """A value that rides the render sidecar on its own (it is not JSON-friendly)."""


_KEPT_ROWS = ["kept-rows-sentinel"]
_GHOST_ROWS = ["ghost-rows-sentinel"]


def _sidecar_values(monkeypatch, view_cls) -> list:
    """Every value the LiveView render hands to ``_protect_sidecar_tree`` (the last step before the
    sidecar reaches the engine) after mounting and rendering *view_cls* once."""
    from djust import serialization
    from djust.testing import LiveViewTestClient

    seen: list = []
    real = serialization._protect_sidecar_tree

    def recording(value, *args, **kwargs):
        seen.append(value)
        return real(value, *args, **kwargs)

    monkeypatch.setattr(serialization, "_protect_sidecar_tree", recording)
    client = LiveViewTestClient(view_cls)
    client.mount()
    client.render()
    return seen


@pytest.mark.django_db
class TestTheKeptRowsAreOnlyWhatTheSidecarCanUse3867:
    def test_an_explicit_sidecar_entry_is_not_replaced_by_the_kept_rows(
        self, world, monkeypatch
    ) -> None:
        """Y1: ``thing`` is BOTH a value that rides the sidecar and a name rows were kept for. The
        explicit entry is the one the template was written against, so it wins (``setdefault``, not
        assignment)."""
        thing = _Thing()

        class _V(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.thing = thing

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                self.__dict__["_all_method_rows"]["thing"] = _KEPT_ROWS
                return ctx

        _V.template = "<div dj-root>{{ thing }}</div>"
        seen = _sidecar_values(monkeypatch, _V)
        assert any(v is thing for v in seen), "control: the explicit entry reaches the sidecar"
        assert not any(v is _KEPT_ROWS for v in seen), "the kept rows replaced the explicit entry"

    def test_the_rows_of_a_name_that_is_not_in_the_context_reach_no_sidecar(
        self, world, monkeypatch
    ) -> None:
        """Y2: the sidecar answers a name the template can read, and only a name in the context can be
        read. Rows kept under any other name (``ghost``) are not handed to the engine."""

        class _V(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.users = _users()

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                self.__dict__["_all_method_rows"]["ghost"] = _GHOST_ROWS
                return ctx

        _V.template = f"<div dj-root>{MIXED['count-then-loop']}</div>"
        seen = _sidecar_values(monkeypatch, _V)
        assert not any(v is _GHOST_ROWS for v in seen), "rows for a name not in the context shipped"
        # Control: the kept name (``users``) is in the context and does reach the sidecar.
        assert any(isinstance(v, list) and v and isinstance(v[0], User) for v in seen), seen

    @pytest.mark.parametrize("name", ["count-then-loop", "loop-then-count"])
    def test_a_queryset_inside_a_container_keeps_no_rows(self, world, name) -> None:
        """Y6: ``ctx.users`` is not a name in the context (the context holds ``ctx``), so no sidecar
        entry can answer it and its rows are not kept (they hold evaluated models). The same mixed
        read on a top-level name keeps them."""
        from djust.testing import LiveViewTestClient

        def kept(source: str, attr: str, value) -> dict:
            class _V(LiveView):
                exposure_policy = "legacy"

                def mount(self, request, **kwargs):
                    setattr(self, attr, value())

            _V.template = f"<div dj-root>{source}</div>"
            client = LiveViewTestClient(_V)
            client.mount()
            client.render()
            return dict(client.view_instance.__dict__.get("_all_method_rows", {}))

        nested = MIXED[name].replace("in users", "in ctx.users")
        assert nested != MIXED[name]
        assert kept(nested, "ctx", lambda: {"users": _users()}) == {}
        assert "users" in kept(MIXED[name], "users", _users)  # control: a top-level name is kept
