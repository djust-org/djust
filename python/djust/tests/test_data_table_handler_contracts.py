"""
Regression tests for #1275, #1279, #1291.

The ``{% data_table %}`` tag emits event names that go straight into the
WS frame as the dispatcher's exact-match handler-name lookup
(``websocket_utils.py:173``: ``getattr(view, event_name, None)``).
DataTableMixin defines its handlers with an ``on_`` prefix, but the
tag-emit defaults previously used bare ``"table_*"`` strings — every
default WS interaction returned "no handler found".

This module locks in:

- #1275: every ``*_event`` default in the data_table tag and in
  DataTableMixin's class-level event-name attributes resolves to an
  existing ``on_table_*`` handler on the mixin.
- #1291: ``on_table_prev`` / ``on_table_next`` handlers exist and
  decrement/increment ``table_page`` (clamped to ``[1, table_total_pages]``).
- #1279: ``on_table_sort`` / ``on_table_search`` / ``on_table_filter`` /
  ``on_table_page`` / ``on_table_prev`` / ``on_table_next`` all call
  ``refresh_table()`` after mutating state.
"""

import inspect


from djust.components.mixins.data_table import (
    DataTableMixin,
    _PRE_MOUNT_TABLE_CONTEXT,
)
from djust.components.templatetags import djust_components


# ---------------------------------------------------------------------------
# 1. Symbol cross-reference: every emit-name default points at a real handler.
# ---------------------------------------------------------------------------


def _collect_data_table_emit_defaults():
    """Return {param_name: default} for every kwarg ending in ``_event`` on
    the ``data_table`` tag function.
    """
    sig = inspect.signature(djust_components.data_table)
    return {
        name: param.default
        for name, param in sig.parameters.items()
        if name.endswith("_event") and isinstance(param.default, str) and param.default
    }


def _collect_mixin_event_attrs():
    """Return {attr_name: value} for every class-level attr ending in
    ``_event`` on DataTableMixin.
    """
    return {
        name: getattr(DataTableMixin, name)
        for name in dir(DataTableMixin)
        if name.endswith("_event")
        and isinstance(getattr(DataTableMixin, name, None), str)
        and getattr(DataTableMixin, name)
    }


def _collect_mixin_handler_names():
    """Return set of ``on_table_*`` method names defined on DataTableMixin."""
    return {
        name
        for name in dir(DataTableMixin)
        if name.startswith("on_table_") and callable(getattr(DataTableMixin, name))
    }


class TestDataTableEmitToHandlerCrossReference:
    """Closes #1275: tag-emit defaults must match a DataTableMixin handler."""

    def test_every_data_table_emit_default_points_at_existing_handler(self):
        emits = _collect_data_table_emit_defaults()
        handlers = _collect_mixin_handler_names()
        unmatched = {param: default for param, default in emits.items() if default not in handlers}
        assert not unmatched, (
            "data_table tag emits these default event names that don't "
            f"match any DataTableMixin on_table_* handler: {unmatched}. "
            "See #1275."
        )

    def test_every_mixin_event_attr_points_at_existing_handler(self):
        attrs = _collect_mixin_event_attrs()
        handlers = _collect_mixin_handler_names()
        unmatched = {attr: val for attr, val in attrs.items() if val not in handlers}
        assert not unmatched, (
            "DataTableMixin class-level event attrs reference handlers "
            f"that don't exist: {unmatched}. See #1275."
        )

    def test_pre_mount_context_emit_defaults_match_handlers(self):
        handlers = _collect_mixin_handler_names()
        unmatched = {
            k: v
            for k, v in _PRE_MOUNT_TABLE_CONTEXT.items()
            if k.endswith("_event") and isinstance(v, str) and v and v not in handlers
        }
        assert not unmatched, (
            "_PRE_MOUNT_TABLE_CONTEXT references handlers that don't "
            f"exist: {unmatched}. See #1275."
        )


# ---------------------------------------------------------------------------
# 2. Pagination handlers exist and clamp correctly. Closes #1291.
# ---------------------------------------------------------------------------


class _MinimalTableView(DataTableMixin):
    """Minimal subclass for handler-only behavior tests.

    Bypasses init_table_state's heavy queryset machinery; we set just
    enough state to exercise the prev/next logic.
    """

    def __init__(self):
        self.table_page = 1
        self.table_total_pages = 1
        self.refresh_called_count = 0

    def refresh_table(self):
        self.refresh_called_count += 1


class TestPaginationHandlersExist:
    """Closes #1291: on_table_prev / on_table_next must exist and clamp."""

    def test_on_table_prev_handler_exists(self):
        assert callable(getattr(DataTableMixin, "on_table_prev", None)), (
            "DataTableMixin must define on_table_prev (paired with the "
            "data_table tag's prev_event default). See #1291."
        )

    def test_on_table_next_handler_exists(self):
        assert callable(getattr(DataTableMixin, "on_table_next", None)), (
            "DataTableMixin must define on_table_next (paired with the "
            "data_table tag's next_event default). See #1291."
        )

    def test_on_table_prev_decrements(self):
        v = _MinimalTableView()
        v.table_page = 3
        v.table_total_pages = 5
        v.on_table_prev()
        assert v.table_page == 2
        assert v.refresh_called_count == 1

    def test_on_table_prev_clamps_at_one(self):
        v = _MinimalTableView()
        v.table_page = 1
        v.table_total_pages = 5
        v.on_table_prev()
        assert v.table_page == 1, "prev at page 1 must NOT go below 1"
        assert v.refresh_called_count == 0, "prev at page 1 must NOT trigger a refresh"

    def test_on_table_next_increments(self):
        v = _MinimalTableView()
        v.table_page = 2
        v.table_total_pages = 5
        v.on_table_next()
        assert v.table_page == 3
        assert v.refresh_called_count == 1

    def test_on_table_next_clamps_at_total_pages(self):
        v = _MinimalTableView()
        v.table_page = 5
        v.table_total_pages = 5
        v.on_table_next()
        assert v.table_page == 5, "next at last page must NOT go past total_pages"
        assert v.refresh_called_count == 0, "next at last page must NOT trigger a refresh"


# ---------------------------------------------------------------------------
# 3. Handlers that mutate the visible row set call refresh_table. Closes #1279.
# ---------------------------------------------------------------------------


class _RowAffectingHandlerTestView(DataTableMixin):
    """Subclass with tracking refresh_table for row-affecting handlers."""

    def __init__(self):
        self.table_page = 1
        self.table_total_pages = 5
        self.table_sort_by = ""
        self.table_sort_desc = False
        self.table_search_query = ""
        self.table_filters = {}
        self.table_selected_rows = []
        self.table_row_key = "id"
        self.table_rows = []
        self.refresh_called_count = 0

    def refresh_table(self):
        self.refresh_called_count += 1


class TestRowAffectingHandlersCallRefresh:
    """Closes #1279: handlers that change the visible row set must refresh."""

    def test_on_table_sort_calls_refresh(self):
        v = _RowAffectingHandlerTestView()
        v.on_table_sort(value="name")
        assert v.refresh_called_count == 1

    def test_on_table_search_calls_refresh(self):
        v = _RowAffectingHandlerTestView()
        v.on_table_search(value="alice")
        assert v.refresh_called_count == 1

    def test_on_table_filter_calls_refresh(self):
        v = _RowAffectingHandlerTestView()
        v.on_table_filter(value="active", column="status")
        assert v.refresh_called_count == 1

    def test_on_table_page_calls_refresh(self):
        v = _RowAffectingHandlerTestView()
        v.on_table_page(value="2")
        assert v.refresh_called_count == 1

    def test_on_table_page_invalid_does_not_refresh(self):
        v = _RowAffectingHandlerTestView()
        v.on_table_page(value="not-a-number")
        assert v.refresh_called_count == 0, (
            "invalid page values must not trigger a refresh; user state "
            "unchanged should mean no work performed"
        )

    def test_on_table_select_does_NOT_call_refresh(self):
        """on_table_select changes UI selection state, not the visible
        rows — must NOT call refresh_table (would be wasteful).
        """
        v = _RowAffectingHandlerTestView()
        v.table_rows = [{"id": "1"}, {"id": "2"}]
        v.on_table_select(value="1")
        assert v.refresh_called_count == 0, (
            "on_table_select changes selection only; rows are unchanged "
            "so refresh_table should NOT be called"
        )
