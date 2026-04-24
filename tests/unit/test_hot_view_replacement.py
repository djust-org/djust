"""
Unit tests for Hot View Replacement (HVR, v0.6.1).

Covers:
    1-5  — ``_is_state_compatible`` heuristic decisions
    6    — module reload discovers multiple LiveView subclasses
    7    — ``apply_class_swap`` preserves instance ``__dict__``
    8    — old instance dispatch resolves to new class body
    9-11 — channel-layer roundtrip / dedup / incompat fallback
    12   — ``importlib.reload`` error falls through cleanly
    13   — ``C401`` check fires when watchdog is missing
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.hot_view_replacement import (
    ReloadResult,
    _find_module_for_path,
    _is_state_compatible,
    _module_has_liveview,
    _resolve_class_pairs,
    apply_class_swap,
    broadcast_hvr_event,
    reload_module_if_liveview,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_temp_module(source: str, module_name: str) -> tuple[str, str]:
    """Write ``source`` as ``<tmpdir>/<module_name>.py`` on sys.path.

    Uses a per-call tmp directory so ``importlib.reload`` can later
    re-discover the file via the standard finder (which needs the
    containing directory on ``sys.path``).

    Returns ``(file_path, tmp_dir)``. Caller is responsible for removing
    both + popping the dir from ``sys.path``.
    """
    tmpdir = tempfile.mkdtemp(prefix="hvr_test_")
    path = os.path.join(tmpdir, f"{module_name}.py")
    with open(path, "w") as f:
        f.write(textwrap.dedent(source))
    sys.path.insert(0, tmpdir)
    return os.path.abspath(path), tmpdir


def _cleanup_temp_module(path: str, tmpdir: str, module_name: str) -> None:
    sys.modules.pop(module_name, None)
    if tmpdir in sys.path:
        sys.path.remove(tmpdir)
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Compat heuristic (cases 1-5)
# ---------------------------------------------------------------------------


class _BaseHandlerView(LiveView):
    """Shared fixture class for compat-heuristic tests."""

    template = "<div>{{ value }}</div>"

    @event_handler
    def set_value(self, value: str = "", **kwargs):
        self.value = value


class TestStateCompatHeuristic:
    def test_same_class_passes(self):
        """Case 1: same-class pair is trivially compatible."""
        ok, reason = _is_state_compatible(_BaseHandlerView, _BaseHandlerView)
        assert ok is True
        assert reason == ""

    def test_added_handler_passes(self):
        """Case 2: new class with an added handler is compatible.

        Both classes define ``set_value`` locally (not via inheritance) so
        the compat check sees matching old/new handler sets plus one
        additional handler on the new side.
        """

        class WithExtraHandler(LiveView):
            template = "<div>{{ value }}</div>"

            @event_handler
            def set_value(self, value: str = "", **kwargs):
                self.value = value

            @event_handler
            def reset(self, **kwargs):
                self.value = ""

        ok, reason = _is_state_compatible(_BaseHandlerView, WithExtraHandler)
        assert ok is True, f"expected compatible, got reason={reason!r}"

    def test_removed_handler_fails(self):
        """Case 3: removing an old handler breaks compat with named reason."""

        class NoHandlers(LiveView):
            template = "<div>{{ value }}</div>"

        ok, reason = _is_state_compatible(_BaseHandlerView, NoHandlers)
        assert ok is False
        assert reason == "handler_removed:set_value"

    def test_renamed_handler_param_fails(self):
        """Case 4: renamed positional param ⇒ incompatible."""

        class RenamedParam(LiveView):
            template = "<div>{{ value }}</div>"

            @event_handler
            def set_value(self, new_val: str = "", **kwargs):  # renamed `value` -> `new_val`
                self.value = new_val

        ok, reason = _is_state_compatible(_BaseHandlerView, RenamedParam)
        assert ok is False
        assert reason == "handler_sig_changed:set_value"

    def test_different_slots_fails(self):
        """Case 5: ``__slots__`` layout drift ⇒ incompatible."""

        class OldSlots(LiveView):
            __slots__ = ("count",)
            template = "<div></div>"

        class NewSlots(LiveView):
            __slots__ = ("count", "total")
            template = "<div></div>"

        ok, reason = _is_state_compatible(OldSlots, NewSlots)
        assert ok is False
        assert reason == "slots_changed"

    def test_state_compat_removed_kwargs_fails(self):
        """Regression (Fix #1): dropping ``**kwargs`` from a handler is a
        breaking change.

        The live WebSocket dispatch path unconditionally passes extra
        kwargs from the event payload (e.g. ``value=`` for input events).
        Old ``def inc(self, **kwargs)`` → new ``def inc(self)`` has
        identical positional lists (``["self"]``) but calling
        ``inc(value="x")`` against the new handler raises ``TypeError``.
        """

        class OldAcceptsKwargs(LiveView):
            template = "<div></div>"

            @event_handler
            def inc(self, **kwargs):
                self.count = getattr(self, "count", 0) + 1

        class NewRejectsKwargs(LiveView):
            template = "<div></div>"

            @event_handler
            def inc(self):  # no **kwargs
                self.count = getattr(self, "count", 0) + 1

        ok, reason = _is_state_compatible(OldAcceptsKwargs, NewRejectsKwargs)
        assert ok is False
        assert reason == "handler_sig_changed:inc"

    def test_state_compat_mro_change_fails(self):
        """Regression (Fix #4): MRO change (mixin drop) is incompatible.

        ``_is_state_compatible`` walks only ``cls.__dict__`` for handler
        discovery, so inherited-from-mixin handler changes are invisible
        to the per-class heuristic. Catching them requires an MRO
        comparison.
        """

        class MixinA:
            @event_handler
            def mixin_handler(self, **kwargs):
                self.touched = True

        class OldWithMixin(MixinA, LiveView):
            template = "<div></div>"

        class NewNoMixin(LiveView):  # MixinA dropped
            template = "<div></div>"

        ok, reason = _is_state_compatible(OldWithMixin, NewNoMixin)
        assert ok is False
        assert reason == "mro_changed"


# ---------------------------------------------------------------------------
# Module reload (case 6, 12)
# ---------------------------------------------------------------------------


class TestModuleReload:
    def test_module_reload_finds_all_liveview_subclasses(self):
        """Case 6: multi-class module yields one pair per class."""
        source = """
            from djust import LiveView

            class Alpha(LiveView):
                template = "<div>a</div>"

            class Beta(LiveView):
                template = "<div>b</div>"

            class NotAView:
                pass
        """
        module_name = "hvr_fixture_case6"
        path, tmpdir = _write_temp_module(source, module_name)
        try:
            module = importlib.import_module(module_name)
            assert _module_has_liveview(module) is True
            assert _find_module_for_path(path) is module

            result = reload_module_if_liveview(path)
            assert result is not None
            assert result.module_name == module_name
            assert len(result.class_pairs) == 2
            names = {new.__name__ for _, new in result.class_pairs}
            assert names == {"Alpha", "Beta"}
            assert result.reload_id  # non-empty uuid hex
        finally:
            _cleanup_temp_module(path, tmpdir, module_name)

    def test_module_reload_returns_none_for_non_liveview_module(self):
        """Non-LiveView modules return ``None`` so the caller falls through."""
        source = """
            DATA = 42

            def helper():
                return DATA
        """
        module_name = "hvr_fixture_plain"
        path, tmpdir = _write_temp_module(source, module_name)
        try:
            importlib.import_module(module_name)
            assert reload_module_if_liveview(path) is None
        finally:
            _cleanup_temp_module(path, tmpdir, module_name)

    def test_reload_returns_none_on_syntax_error(self):
        """Case 12: ``SyntaxError`` during reload is swallowed + logged."""
        good_source = """
            from djust import LiveView

            class Gamma(LiveView):
                template = "<div>g</div>"
        """
        module_name = "hvr_fixture_case12"
        path, tmpdir = _write_temp_module(good_source, module_name)
        try:
            importlib.import_module(module_name)
            # Overwrite with a broken version BEFORE calling reload.
            with open(path, "w") as f:
                f.write("this is not valid python :::\n")
            result = reload_module_if_liveview(path)
            assert result is None, "syntax error should return None"
            # Live instance still references the pre-save class (safety).
            assert sys.modules[module_name].Gamma.template == "<div>g</div>"
        finally:
            _cleanup_temp_module(path, tmpdir, module_name)


# ---------------------------------------------------------------------------
# Class swap (cases 7, 8)
# ---------------------------------------------------------------------------


class TestClassSwap:
    def test_class_swap_preserves_instance_dict(self):
        """Case 7: ``__class__`` reassignment keeps instance state."""

        class OldCounter(LiveView):
            template = "<div>{{ count }}</div>"

            @event_handler
            def increment(self, **kwargs):
                self.count += 1

        class NewCounter(LiveView):
            template = "<div>{{ count }}</div>"

            @event_handler
            def increment(self, **kwargs):
                self.count += 1

        # Spoof same name + module so apply_class_swap matches.
        NewCounter.__name__ = OldCounter.__name__
        NewCounter.__module__ = OldCounter.__module__

        inst = OldCounter()
        inst.count = 5
        ok, reason = apply_class_swap(inst, [(OldCounter, NewCounter)])
        assert ok is True, reason
        assert inst.__class__ is NewCounter
        assert inst.count == 5

    def test_class_swap_old_instance_method_resolves_to_new_class(self):
        """Case 8: post-swap calls dispatch to the new class body."""

        class OldView(LiveView):
            template = "<div>{{ count }}</div>"

            @event_handler
            def increment(self, **kwargs):
                self.count += 1

        class NewView(LiveView):
            template = "<div>{{ count }}</div>"

            @event_handler
            def increment(self, **kwargs):
                self.count += 2  # NEW BEHAVIOR

        NewView.__name__ = OldView.__name__
        NewView.__module__ = OldView.__module__

        inst = OldView()
        inst.count = 10
        ok, _ = apply_class_swap(inst, [(OldView, NewView)])
        assert ok is True

        inst.increment()
        assert inst.count == 12, "expected new class body (+= 2), got old"

    def test_class_swap_handles_cyclic_child_views(self):
        """Regression (Fix #2): cyclic ``_child_views`` must not recurse infinitely.

        Construct two instances ``a`` and ``b`` where
        ``a._child_views = {"b": b}`` and ``b._child_views = {"a": a}``.
        ``apply_class_swap`` must complete without ``RecursionError`` and
        should apply the swap to both participants (both are of the
        changed class).
        """

        class OldCyclic(LiveView):
            template = "<div></div>"

            @event_handler
            def touch(self, **kwargs):
                self.touched = True

        class NewCyclic(LiveView):
            template = "<div></div>"

            @event_handler
            def touch(self, **kwargs):
                self.touched = True

        NewCyclic.__name__ = OldCyclic.__name__
        NewCyclic.__module__ = OldCyclic.__module__

        a = OldCyclic()
        b = OldCyclic()

        # Stub _get_all_child_views to return the cyclic peer.
        a._get_all_child_views = lambda: {"b": b}
        b._get_all_child_views = lambda: {"a": a}

        # Must not RecursionError.
        ok, reason = apply_class_swap(a, [(OldCyclic, NewCyclic)])
        assert ok is True, reason
        # Both participants got the swap.
        assert a.__class__ is NewCyclic
        assert b.__class__ is NewCyclic


# ---------------------------------------------------------------------------
# Channel-layer roundtrip (cases 9-11)
# ---------------------------------------------------------------------------


class _HvrRoundtripView(LiveView):
    template = "<div>{{ count }}</div>"

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class _HvrRoundtripViewV2(LiveView):
    template = "<div>{{ count }}</div>"

    @event_handler
    def increment(self, **kwargs):
        self.count += 2  # new behavior


# Spoof same identity for the swap-match check.
_HvrRoundtripViewV2.__name__ = _HvrRoundtripView.__name__
_HvrRoundtripViewV2.__module__ = _HvrRoundtripView.__module__


class TestHvrBroadcast:
    @pytest.mark.asyncio
    async def test_hvr_broadcast_to_consumer_triggers_swap_and_patch(self):
        """Case 9: broadcast ⇒ consumer applies swap ⇒ hvr-applied frame sent."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = _HvrRoundtripView()
        consumer.view_instance.count = 7

        sent: list = []
        consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
        # Stub out the template-refresh tail so the handler returns early after
        # sending hvr-applied — we only want to assert the HVR pre-step here.
        consumer._clear_template_caches = MagicMock(return_value=0)
        consumer._send_update = AsyncMock()

        # Shim sys.modules for _resolve_class_pairs.
        module = sys.modules[_HvrRoundtripView.__module__]
        original = module.__dict__.get(_HvrRoundtripView.__name__)
        module.__dict__[_HvrRoundtripView.__name__] = _HvrRoundtripViewV2
        try:
            event = {
                "type": "hotreload",
                "file": "/tmp/fake.py",
                "hvr_meta": {
                    "module": _HvrRoundtripView.__module__,
                    "class_names": [_HvrRoundtripView.__name__],
                    "reload_id": "reload-9",
                },
            }
            await consumer.hotreload(event)

            # Assert: swap applied (class changed to V2).
            assert consumer.view_instance.__class__ is _HvrRoundtripViewV2
            # Assert: hvr-applied frame sent.
            hvr_frames = [m for m in sent if m.get("type") == "hvr-applied"]
            assert len(hvr_frames) == 1
            assert hvr_frames[0]["version"] == 1
            # State preserved.
            assert consumer.view_instance.count == 7
        finally:
            if original is not None:
                module.__dict__[_HvrRoundtripView.__name__] = original

    @pytest.mark.asyncio
    async def test_hvr_broadcast_drops_stale_versions(self):
        """Case 10: same reload_id arriving twice ⇒ second is a no-op."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = _HvrRoundtripView()
        consumer.view_instance.count = 1
        consumer._hvr_last_reload_id = "dup-id"

        sent: list = []
        consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
        consumer._clear_template_caches = MagicMock(return_value=0)
        consumer._send_update = AsyncMock()

        event = {
            "type": "hotreload",
            "file": "/tmp/x.py",
            "hvr_meta": {
                "module": _HvrRoundtripView.__module__,
                "class_names": [_HvrRoundtripView.__name__],
                "reload_id": "dup-id",
            },
        }
        await consumer.hotreload(event)
        assert sent == [], "duplicate reload_id should be dropped silently"

    @pytest.mark.asyncio
    async def test_hvr_falls_back_to_full_reload_on_incompat(self):
        """Case 11: incompatible pair ⇒ client receives ``{type: 'reload'}``."""
        from djust.websocket import LiveViewConsumer

        # A V2 with the handler REMOVED — guaranteed incompat.
        class _IncompV1(LiveView):
            template = "<div></div>"

            @event_handler
            def will_be_removed(self, **kwargs):
                pass

        class _IncompV2(LiveView):
            template = "<div></div>"

        _IncompV2.__name__ = _IncompV1.__name__
        _IncompV2.__module__ = _IncompV1.__module__

        consumer = LiveViewConsumer()
        consumer.view_instance = _IncompV1()

        sent: list = []
        consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
        consumer._clear_template_caches = MagicMock(return_value=0)
        consumer._send_update = AsyncMock()

        # Shim sys.modules so the resolver returns the V2 class.
        module = sys.modules[_IncompV1.__module__]
        module.__dict__[_IncompV1.__name__] = _IncompV2
        try:
            event = {
                "type": "hotreload",
                "file": "/tmp/incomp.py",
                "hvr_meta": {
                    "module": _IncompV1.__module__,
                    "class_names": [_IncompV1.__name__],
                    "reload_id": "incomp-1",
                },
            }
            await consumer.hotreload(event)

            reloads = [m for m in sent if m.get("type") == "reload"]
            assert len(reloads) >= 1, f"expected a full-reload frame, got {sent!r}"
        finally:
            module.__dict__.pop(_IncompV1.__name__, None)


# ---------------------------------------------------------------------------
# C401 check (case 13)
# ---------------------------------------------------------------------------


class TestC401Check:
    def test_c401_fires_when_watchdog_missing(self, settings):
        """Case 13: DEBUG=True, hvr_enabled, no watchdog ⇒ C401 warning."""
        from djust.checks import check_hot_view_replacement

        settings.DEBUG = True
        with patch("djust.dev_server.WATCHDOG_AVAILABLE", False):
            warnings = check_hot_view_replacement(app_configs=None)
        ids = [w.id for w in warnings]
        assert "djust.C401" in ids

    def test_c401_silent_in_production(self, settings):
        """DEBUG=False suppresses C401 entirely."""
        from djust.checks import check_hot_view_replacement

        settings.DEBUG = False
        with patch("djust.dev_server.WATCHDOG_AVAILABLE", False):
            warnings = check_hot_view_replacement(app_configs=None)
        assert warnings == []


# ---------------------------------------------------------------------------
# Extra wiring coverage
# ---------------------------------------------------------------------------


class TestResolveClassPairs:
    def test_returns_none_for_unknown_module(self):
        assert _resolve_class_pairs("nonexistent.module.name", ["X"]) is None

    def test_returns_pairs_for_known_module(self):
        module = sys.modules[_HvrRoundtripView.__module__]
        pairs = _resolve_class_pairs(module.__name__, [_HvrRoundtripView.__name__])
        assert pairs is not None
        assert len(pairs) == 1


class TestBroadcastHvrEvent:
    @pytest.mark.asyncio
    async def test_broadcast_calls_group_send_with_hvr_meta(self):
        result = ReloadResult(
            module_name="fake.module",
            class_pairs=[(_HvrRoundtripView, _HvrRoundtripViewV2)],
            reload_id="abc123",
        )

        fake_layer = AsyncMock()
        fake_layer.group_send = AsyncMock()
        with patch("channels.layers.get_channel_layer", return_value=fake_layer):
            await broadcast_hvr_event(result, "/tmp/fake.py")

        fake_layer.group_send.assert_awaited_once()
        call_args = fake_layer.group_send.call_args
        assert call_args.args[0] == "djust_hotreload"
        payload = call_args.args[1]
        assert payload["type"] == "hotreload"
        assert payload["file"] == "/tmp/fake.py"
        assert payload["hvr_meta"]["module"] == "fake.module"
        assert payload["hvr_meta"]["reload_id"] == "abc123"
