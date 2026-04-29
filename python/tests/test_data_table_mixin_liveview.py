"""Regression tests for #1114: DataTableMixin compatibility with LiveView.

Three compounding root causes documented in #1114:
  1. ``get_context_data()`` runs before ``mount()`` in djust's WS lifecycle,
     so ``self.table_rows`` doesn't exist → silent ``AttributeError`` →
     empty initial VDOM → all subsequent patches diff against empty content.
  2. ``on_table_*`` event handlers aren't ``@event_handler()``-decorated,
     so they're rejected under default ``event_security="strict"``.
  3. (Documentation) Mixin authors using LiveView need to know the API
     boundary differs from the Component case.

This test module covers (1) and (2). (3) is verified by reading the
docstring (kept in sync as a regression).
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    )
    django.setup()

from django.test import TestCase

from djust.components.mixins.data_table import DataTableMixin, _PRE_MOUNT_TABLE_CONTEXT


class PreMountGuardTest(TestCase):
    """get_table_context() must be safe to call before init_table_state()."""

    def test_get_table_context_pre_mount_returns_default(self):
        """Calling ``get_table_context()`` before ``init_table_state()`` returns
        the empty-table default rather than raising ``AttributeError``."""

        view = DataTableMixin()
        # No init_table_state() called — table_rows not set
        ctx = view.get_table_context()

        # Should be the pre-mount default, not a raised exception
        self.assertEqual(ctx["rows"], [])
        self.assertEqual(ctx["columns"], [])
        self.assertEqual(ctx["page"], 1)
        self.assertEqual(ctx["sort_by"], "")

    def test_get_table_context_pre_mount_does_not_raise(self):
        """Pre-mount call must not raise any exception."""
        view = DataTableMixin()
        try:
            view.get_table_context()
        except Exception as exc:
            self.fail(f"get_table_context() raised {type(exc).__name__}: {exc}")

    def test_post_mount_returns_real_state(self):
        """After ``init_table_state()`` runs, real instance state is returned."""
        view = DataTableMixin()
        view.table_columns = [{"key": "name", "label": "Name"}]
        view.init_table_state()
        view.table_rows = [{"name": "Alice"}, {"name": "Bob"}]

        ctx = view.get_table_context()

        self.assertEqual(len(ctx["rows"]), 2)
        self.assertEqual(ctx["rows"][0]["name"], "Alice")
        self.assertEqual(ctx["columns"][0]["key"], "name")

    def test_pre_mount_default_has_required_template_keys(self):
        """The pre-mount default must contain every key the
        ``{% data_table %}`` template tag reads — otherwise the template
        raises VariableDoesNotExist at render time.

        The test asserts the pre-mount dict and the post-mount dict have the
        same keyset; that way adding a new key to the post-mount path will
        flag (via this test) that the pre-mount default needs updating too.
        """
        view = DataTableMixin()
        view.table_columns = [{"key": "name"}]
        view.init_table_state()
        view.table_rows = []
        post_mount_keys = set(view.get_table_context().keys())
        pre_mount_keys = set(_PRE_MOUNT_TABLE_CONTEXT.keys())

        missing = post_mount_keys - pre_mount_keys
        self.assertFalse(
            missing,
            f"Pre-mount default is missing keys present post-mount: {missing}",
        )

    def test_show_stats_present_post_mount(self):
        """Closes #1118 — ``show_stats`` was in ``_PRE_MOUNT_TABLE_CONTEXT``
        but missing from the post-mount return dict, so templates that
        reference ``{% if show_stats %}`` would get the falsy default
        pre-mount and ``VariableDoesNotExist`` post-mount.

        Surfaced by Stage 11 review of PR #1117. Fix: add
        ``"show_stats": self.table_show_stats`` to the post-mount return.
        """
        view = DataTableMixin()
        view.init_table_state()
        view.table_rows = []

        ctx = view.get_table_context()

        self.assertIn("show_stats", ctx)
        self.assertEqual(ctx["show_stats"], False)  # default

    def test_show_stats_class_override_flows_through(self):
        """A view setting ``table_show_stats = True`` sees it post-mount."""

        class _StatsTable(DataTableMixin):
            table_show_stats = True

        view = _StatsTable()
        view.init_table_state()
        view.table_rows = []

        self.assertEqual(view.get_table_context()["show_stats"], True)


class EventHandlerDecorationTest(TestCase):
    """All on_table_* methods must carry the @event_handler() decorator
    so they work under default event_security="strict" mode."""

    EXPECTED_HANDLERS = [
        "on_table_sort",
        "on_table_search",
        "on_table_filter",
        "on_table_select",
        "on_table_page",
        "on_table_cell_edit",
        "on_table_reorder",
        "on_table_visibility",
        "on_table_density",
        "on_table_row_edit",
        "on_table_row_save",
        "on_table_row_cancel",
        "on_table_expand",
        "on_table_bulk_action",
        "on_table_export",
        "on_table_group",
        "on_table_group_toggle",
        "on_table_row_drag",
        "on_table_copy",
        "on_table_import",
        "on_table_expression",
    ]

    def test_all_on_table_methods_decorated(self):
        """Every documented on_table_* method must have _djust_decorators
        metadata (the marker @event_handler() leaves)."""
        missing = []
        for name in self.EXPECTED_HANDLERS:
            method = getattr(DataTableMixin, name, None)
            if method is None:
                missing.append(f"{name} (not found on DataTableMixin)")
                continue
            metadata = getattr(method, "_djust_decorators", None)
            if not metadata or "event_handler" not in metadata:
                missing.append(f"{name} (no @event_handler() decoration)")

        self.assertFalse(
            missing,
            f"Methods missing @event_handler() decoration: {missing}",
        )

    def test_handler_count_matches_expected(self):
        """Sanity: 21 documented handlers; if a future PR adds more, this
        flags that EXPECTED_HANDLERS needs updating."""
        actual = [
            name
            for name in dir(DataTableMixin)
            if name.startswith("on_table_") and callable(getattr(DataTableMixin, name))
        ]
        self.assertEqual(
            sorted(actual),
            sorted(self.EXPECTED_HANDLERS),
            "EXPECTED_HANDLERS list is out of sync with DataTableMixin",
        )


class DocstringRegressionTest(TestCase):
    """The mixin docstring must explain the LiveView pre-mount lifecycle
    (root cause 3 of #1114). Catches doc-rot if a future refactor strips it."""

    def test_docstring_mentions_lifecycle(self):
        doc = DataTableMixin.__doc__ or ""
        self.assertIn("LiveView", doc)
        self.assertIn("mount", doc)
        self.assertIn("get_context_data", doc)

    def test_docstring_mentions_event_handler_decoration(self):
        doc = DataTableMixin.__doc__ or ""
        self.assertIn("event_handler", doc)
