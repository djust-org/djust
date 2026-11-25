"""djust.T027 counts only what reaches the returned context (#4224).

``_get_context_data_chain_keys`` (#4185) follows a view's
``get_context_data()`` chain through ``super()``. Its subscript arm counted any
``name["key"] = ...`` in an override as supplying ``key``, including an
unrelated local dict and an assignment inside a nested helper, so the check
stayed quiet about names nothing supplies. A key now counts only when it is
written into the dict the override returns: a returned literal, or a returned
local bound only to ``super().get_context_data(...)`` or to a literal.

Each class below is read from its source by the function under test; none has
a template, so the system check itself skips them.
"""

from __future__ import annotations

import re
from typing import Any

from djust import LiveView, state
from djust.admin_ext.views import ModelListView
from djust.checks.templates import _get_context_data_chain_keys

# --- the context dict --------------------------------------------------------


class UnrelatedLocalDict(LiveView):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        row: dict = {}
        row["leaked"] = 1
        ctx["kept"] = row
        return ctx


class NestedHelper(LiveView):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)

        def helper() -> dict:
            ctx["from_helper"] = 1
            return {"helper_literal": 2}

        helper()
        ctx["kept"] = 3
        return ctx


class LiteralLocal(LiveView):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = {"first": 1, **super().get_context_data(**kwargs)}
        ctx["second"] = 2
        return ctx


class SuperNotReturned(LiveView):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["dropped"] = 1
        return {"returned": 2, **super().get_context_data(**kwargs)}


class Rebound(LiveView):
    exposure_policy = "explicit"

    def _other(self) -> tuple:
        return {}, None

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["before"] = 1
        ctx, _ = self._other()
        return ctx


class SpreadIntoReturnedLiteral(LiveView):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["total"] = 1
        return {**ctx, "extra": 2}


class SpreadTotalPage(LiveView):
    """A user view whose spread context supplies the attribute it holds."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ total }}{{ extra }}</div>"
    total = 3

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["total"] = self.total
        return {**ctx, "extra": 1}


# --- the chain ----------------------------------------------------------------


class LiteralParent(LiveView):
    """A parent (like several shipped djust views) that returns a literal dict."""

    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        return {"parent_literal": 1}


class ChildOfLiteralParent(LiteralParent):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(child_kwarg=2, **kwargs)


class ChildWithoutSuper(LiteralParent):
    """An override that does not call super(): the parent's names stop here."""

    exposure_policy = "explicit"

    def get_context_data(self, **kwargs: Any) -> dict:
        return {"own": 1}


def test_an_unrelated_local_dict_supplies_nothing():
    assert _get_context_data_chain_keys(UnrelatedLocalDict) == {"kept"}


def test_a_nested_helper_supplies_nothing():
    assert _get_context_data_chain_keys(NestedHelper) == {"kept"}


def test_a_returned_literal_local_supplies_its_keys_and_writes():
    assert _get_context_data_chain_keys(LiteralLocal) == {"first", "second"}


def test_writes_to_a_dict_that_is_not_returned_supply_nothing():
    assert _get_context_data_chain_keys(SuperNotReturned) == {"returned"}


def test_a_local_spread_into_the_returned_literal_is_the_context():
    assert _get_context_data_chain_keys(SpreadIntoReturnedLiteral) == {"total", "extra"}


def test_the_system_check_is_quiet_for_a_name_written_into_a_spread_context():
    assert _t027_names(SpreadTotalPage) == set()


def test_a_name_rebound_to_something_else_is_not_the_context():
    assert _get_context_data_chain_keys(Rebound) == set()


def test_a_parent_that_returns_a_literal_dict_supplies_its_keys():
    assert _get_context_data_chain_keys(ChildOfLiteralParent) == {
        "parent_literal",
        "child_kwarg",
    }


def test_an_override_without_super_stops_the_chain():
    assert _get_context_data_chain_keys(ChildWithoutSuper) == {"own"}


def test_the_shipped_model_list_view_supplies_no_filter_choices():
    """``ModelListView`` writes ``filter_data["choices"]`` on a per-filter dict."""
    keys = _get_context_data_chain_keys(ModelListView)
    assert "choices" not in keys
    assert {"filters", "rows", "pagination"} <= keys


class ChoicesList(ModelListView):
    """A 1.x subclass whose template reads its own ``choices`` attribute."""

    template = "<div dj-root>{% for c in choices %}{{ c }}{% endfor %}{{ rows }}</div>"
    choices = ["a", "b"]
    extra = state("")


def _t027_names(view_class: type) -> set[str]:
    from djust.checks.templates import check_unsupplied_template_names

    label = "%s.%s --" % (__name__, view_class.__qualname__)
    return {
        re.search(r"reads '(\w+)'", str(message.msg)).group(1)
        for message in check_unsupplied_template_names(None)
        if str(message.msg).startswith(label) and message.id == "djust.T027"
    }


def test_the_system_check_names_a_subclass_attribute_the_parent_only_used_locally():
    assert _t027_names(ChoicesList) == {"choices"}
