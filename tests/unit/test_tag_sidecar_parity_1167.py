"""Sidecar bridge parity for block-tag and assign-tag handlers (#1167).

PR #1166 wired the raw-Python sidecar (``request`` / ``view`` / …)
into ``Node::CustomTag`` handlers so the ``live_render`` lazy-true
path could reach the parent view from the Rust template engine.
This file extends parity to the other two custom-tag node types:

* ``Node::BlockCustomTag`` — block tags such as ``{% modal %}…{% endmodal %}``.
* ``Node::AssignTag`` — context-mutating tags such as ``{% assign_slot %}``.

Each test exercises sidecar receipt by registering a handler that
inspects its ``context`` dict for the sidecar key, plus a back-compat
test confirming a handler that ignores the sidecar still works
unchanged.
"""

from __future__ import annotations

import pytest

from djust._rust import (
    RustLiveView,
    clear_assign_tag_handlers,
    clear_block_tag_handlers,
    register_assign_tag_handler,
    register_block_tag_handler,
)


# ---------------------------------------------------------------------------
# Sentinel objects we can identity-compare in handler render() calls
# ---------------------------------------------------------------------------


class _RequestSentinel:
    """Stand-in for a Django HttpRequest in the sidecar."""

    def __init__(self) -> None:
        self.method = "GET"


class _ViewSentinel:
    """Stand-in for a LiveView instance in the sidecar."""

    def __init__(self) -> None:
        self.flag = "view-sentinel"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registries():
    """Hard-reset block + assign registries around every test."""
    clear_block_tag_handlers()
    clear_assign_tag_handlers()
    yield
    clear_block_tag_handlers()
    clear_assign_tag_handlers()


def _make_view(template: str, *, request, view) -> RustLiveView:
    rv = RustLiveView(template)
    rv.set_raw_py_values({"request": request, "view": view})
    return rv


# ---------------------------------------------------------------------------
# Block-tag sidecar tests
# ---------------------------------------------------------------------------


class TestBlockTagSidecar:
    def test_block_handler_receives_request_from_sidecar(self):
        """A custom block tag can read ``request`` out of its context dict."""
        seen: dict[str, object] = {}

        class _CaptureBlock:
            def render(self, args, content, context):  # noqa: ARG002 — fixture stub
                seen["request"] = context.get("request")
                return f"<wrap>{content}</wrap>"

        register_block_tag_handler("capture_blk", "endcapture_blk", _CaptureBlock())

        request = _RequestSentinel()
        view = _ViewSentinel()
        rv = _make_view("{% capture_blk %}body{% endcapture_blk %}", request=request, view=view)
        html = rv.render()

        assert html == "<wrap>body</wrap>"
        assert seen["request"] is request, (
            "Block-tag handler must receive the raw `request` object from "
            "the sidecar, not a JSON projection or None."
        )

    def test_block_handler_receives_view_from_sidecar(self):
        """A custom block tag can read ``view`` (LiveView) from its context dict."""
        seen: dict[str, object] = {}

        class _CaptureViewBlock:
            def render(self, args, content, context):  # noqa: ARG002
                seen["view"] = context.get("view")
                return content

        register_block_tag_handler("capture_view_blk", "endcapture_view_blk", _CaptureViewBlock())

        request = _RequestSentinel()
        view = _ViewSentinel()
        rv = _make_view(
            "{% capture_view_blk %}x{% endcapture_view_blk %}",
            request=request,
            view=view,
        )
        rv.render()

        assert seen["view"] is view
        # And isinstance check — the actual Python object is exposed,
        # not a JSON snapshot.
        assert isinstance(seen["view"], _ViewSentinel)

    def test_block_handler_back_compat_without_sidecar(self):
        """Existing block handlers that ignore the sidecar are unaffected."""

        class _LegacyBlock:
            def render(self, args, content, context):  # noqa: ARG002
                # Legacy handler: only looks at content, never touches
                # request/view. This is the dominant case (modal, card,
                # slot, dj_suspense).
                return f"[legacy:{content}]"

        register_block_tag_handler("legacy_blk", "endlegacy_blk", _LegacyBlock())

        # No set_raw_py_values call — sidecar is None.
        rv = RustLiveView("{% legacy_blk %}hi{% endlegacy_blk %}")
        html = rv.render()
        assert html == "[legacy:hi]"


# ---------------------------------------------------------------------------
# Assign-tag sidecar tests
# ---------------------------------------------------------------------------


class TestAssignTagSidecar:
    def test_assign_handler_receives_view_from_sidecar(self):
        """A custom assign tag can read ``view`` from its context dict."""
        seen: dict[str, object] = {}

        class _CaptureViewAssign:
            def render(self, args, context):  # noqa: ARG002
                seen["view"] = context.get("view")
                # Emit a context update keyed off a sidecar attr so we
                # can confirm both arms (sidecar visible AND merge
                # still works) in a single render.
                v = context.get("view")
                return {"flag_seen": getattr(v, "flag", None)}

        register_assign_tag_handler("capture_view_assign", _CaptureViewAssign())

        request = _RequestSentinel()
        view = _ViewSentinel()
        rv = _make_view("{% capture_view_assign %}val={{ flag_seen }}", request=request, view=view)
        html = rv.render()

        assert seen["view"] is view
        assert isinstance(seen["view"], _ViewSentinel)
        assert html == "val=view-sentinel"

    def test_assign_handler_receives_request_from_sidecar(self):
        """A custom assign tag can read ``request`` from its context dict."""
        seen: dict[str, object] = {}

        class _CaptureReqAssign:
            def render(self, args, context):  # noqa: ARG002
                seen["request"] = context.get("request")
                return {}

        register_assign_tag_handler("capture_req_assign", _CaptureReqAssign())

        request = _RequestSentinel()
        view = _ViewSentinel()
        rv = _make_view("{% capture_req_assign %}", request=request, view=view)
        rv.render()

        assert seen["request"] is request

    def test_assign_handler_back_compat_without_sidecar(self):
        """Existing assign handlers that ignore the sidecar are unaffected."""

        class _LegacyAssign:
            def render(self, args, context):  # noqa: ARG002
                return {"legacy_key": "legacy_value"}

        register_assign_tag_handler("legacy_assign", _LegacyAssign())

        # No set_raw_py_values call — sidecar is None.
        rv = RustLiveView("{% legacy_assign %}{{ legacy_key }}")
        html = rv.render()
        assert html == "legacy_value"
