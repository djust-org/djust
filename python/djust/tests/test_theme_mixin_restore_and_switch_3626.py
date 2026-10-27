"""``ThemeMixin`` after the #3626 fix: a restored view, derived names, a theme switch.

The first version of the fix supplied the four rendered values from
``_apply_context_processors`` only when ``mount()`` had run, dropped
``theme_presets_count``, and kept them out of change detection so that a theme
switch left a dj-root template reading them stale. Review found all three. These
tests drive the real converged mount/event path (``ViewRuntime`` with a real
session and request) with ``theme_context`` removed from the settings, because
that is the configuration under which the mixin has to supply the values itself.
"""

from __future__ import annotations

import copy
import re

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.runtime import ViewRuntime
from djust.theming import ThemeMixin

from .test_stale_baseline_restore_1977 import (
    MockTransport,
    _make_db_session,
    _make_request,
)

pytestmark = [pytest.mark.theming, pytest.mark.django_db]

PAGE_URL = "/feed/"
HEAVY = ("theme_head", "theme_css", "theme_switcher", "theme_presets")

# What the per-event session save of a mounted ThemeMixin view holds: the small
# attributes the mixin still assigns, never the four rendered values.
SAVED = {
    "theme_preset": "default",
    "theme_mode": "system",
    "theme_resolved_mode": "light",
}

_ROOT = (
    '<div dj-root dj-id="0" dj-view="{path}">H[{{{{ theme_head|length }}}}] '
    "C[{{{{ theme_css|length }}}}] S[{{{{ theme_switcher|length }}}}] "
    "P[{{{{ theme_presets|length }}}}] N[{{{{ theme_presets_count }}}}] "
    "M[{{{{ theme_mode }}}}] n={{{{ count }}}}</div>"
)


class Snapshotted(ThemeMixin, LiveView):
    """Opts in to state restore, so a reconnect skips ``mount()``."""

    enable_state_snapshot = True
    template = _ROOT.format(path=__name__ + ".Snapshotted")

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


class Plain(Snapshotted):
    enable_state_snapshot = False
    template = _ROOT.format(path=__name__ + ".Plain")


def _without_the_processor():
    templates = copy.deepcopy(settings.TEMPLATES)
    for config in templates:
        options = config.get("OPTIONS", {})
        options["context_processors"] = [
            p for p in options.get("context_processors", []) if "theme_context" not in p
        ]
    from djust.mixins.context import _context_processors_cache

    _context_processors_cache.clear()
    return override_settings(TEMPLATES=templates, LIVEVIEW_ALLOWED_MODULES=["djust"])


def _numbers(text: str) -> dict[str, str]:
    return dict(re.findall(r"([HCSPNM])\[([^\]]*)\]", text))


async def _runtime(view_class, saved_state=None):
    session = await sync_to_async(_make_db_session)()
    if saved_state is not None:
        await session.aset(f"liveview_{PAGE_URL}", saved_state)
        await sync_to_async(session.save)()
    request = _make_request(session)
    transport = MockTransport(request)
    return ViewRuntime(transport, scope=None), transport


async def _mount(runtime, view_class, **extra):
    await runtime.dispatch_mount(
        {"type": "mount", "view": f"{__name__}.{view_class.__name__}", "url": PAGE_URL, **extra}
    )


async def _event(runtime, name, **params):
    await runtime.dispatch_event({"type": "event", "event": name, "params": params, "ref": 1})


def _expected(view):
    """What the page must show, computed from the view's own Python reads."""
    return {
        "H": str(len(str(view.theme_head))),
        "C": str(len(str(view.theme_css))),
        "S": str(len(str(view.theme_switcher))),
        "P": str(len(view.theme_presets)),
        "N": str(len(view.theme_presets)),
        "M": view.theme_mode,
    }


class TestAViewThatSkippedMount:
    """Finding 1: a reconnect restores state in lieu of ``mount()``."""

    @pytest.mark.asyncio
    async def test_a_restored_view_renders_the_values_without_the_processor(self):
        with _without_the_processor():
            runtime, transport = await _runtime(Snapshotted, {**SAVED, "count": 3})
            await _mount(runtime, Snapshotted)
            view = runtime.view_instance
            assert view._mounted_from_restore is True, "the saved state must restore, not mount()"
            assert view._theme_manager is None, "the premise: mount() did not run"
            html = transport.mount_frame["html"]
            shown = _numbers(html)
            assert int(shown["H"]) > 1000, f"empty theme_head on a restored view: {html[:300]}"
            assert shown == _expected(view)
            assert "n=3" in html

    @pytest.mark.asyncio
    async def test_a_restored_view_matches_a_freshly_mounted_one(self):
        with _without_the_processor():
            restored_rt, restored_tp = await _runtime(Snapshotted, {**SAVED, "count": 0})
            await _mount(restored_rt, Snapshotted)
            fresh_rt, fresh_tp = await _runtime(Plain)
            await _mount(fresh_rt, Plain)
            assert _numbers(restored_tp.mount_frame["html"]) == _numbers(
                fresh_tp.mount_frame["html"]
            )


class TestTheDerivedNames:
    """Finding 2: every name the old attributes made available to a template."""

    @pytest.mark.asyncio
    async def test_theme_presets_count_renders(self):
        with _without_the_processor():
            runtime, transport = await _runtime(Plain)
            await _mount(runtime, Plain)
            shown = _numbers(transport.mount_frame["html"])
            assert shown["N"] == str(len(runtime.view_instance.theme_presets)) != "", shown

    def test_theme_presets_count_renders_in_the_page_shell_too(self):
        """The Django engine renders the shell outside dj-root."""
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        class Shell(ThemeMixin, LiveView):
            template = (
                "<html><head>[{{ theme_presets_count }}]</head><body>"
                "<div dj-root>{{ theme_presets_count }}</div></body></html>"
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)

        with _without_the_processor():
            request = RequestFactory().get("/")
            request.user = AnonymousUser()
            request.tenant = None
            request.session = _make_db_session()
            view = Shell()
            view.setup(request)
            html = view.get(request).content.decode()
        count = len(view.theme_presets)
        assert f"[{count}]" in html and f">{count}<" in html

    def test_the_get_context_data_keys_are_exactly_the_documented_set(self):
        """The old attributes put five names in ``get_context_data()``. They are
        supplied at render time now; this pins the decision and the one derived
        name that stays visible."""
        from djust.theming.mixins import _RENDERED_THEME_NAMES

        with _without_the_processor():
            request = _make_request(_make_db_session())
            view = Plain()
            view.setup(request)
            view.mount(request)
            keys = set(view.get_context_data())
        assert not keys & set(_RENDERED_THEME_NAMES)
        assert {"theme_preset", "theme_mode", "theme_resolved_mode"} <= keys


class TestAThemeSwitch:
    """Finding 3: the values are out of state, so the switch must re-send them."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "switch,params",
        [("set_theme_preset", {"preset": "blue"}), ("set_theme_mode", {"mode": "dark"})],
    )
    async def test_the_next_render_after_a_switch_shows_the_new_values(self, switch, params):
        with _without_the_processor():
            runtime, transport = await _runtime(Plain)
            await _mount(runtime, Plain)
            view = runtime.view_instance
            before = _expected(view)
            await _event(runtime, switch, **params)
            after = _expected(view)
            assert after != before, "the switch must change what the page would show"
            transport.sent.clear()
            await _event(runtime, "bump")
            frames = transport.frames_of("patch", "html_update")
            assert frames, transport.sent
            blob = repr(frames)
            for letter in ("H", "C", "S"):
                if after[letter] != before[letter]:
                    assert f"{letter}[{after[letter]}]" in blob, (
                        f"{letter} stayed at {before[letter]} after {switch}: {blob[:500]}"
                    )
            assert f"M[{after['M']}]" in blob or after["M"] == before["M"]


class TestAFirstRenderIsStillAFullRender:
    def test_forced_keys_do_not_turn_the_first_render_into_a_partial_one(self):
        """The hand-off is for a render that follows a switch; naming changed keys
        on a view that has never rendered must still produce the whole page."""
        with _without_the_processor():
            request = _make_request(_make_db_session())
            view = Plain()
            view.setup(request)
            view.mount(request)
            view._mark_theme_values_changed()
            html, _patches, _version = view.render_with_diff(request)
        assert "n=0" in html and "H[" in html and "M[" in html, html[:300]


class TestTheLeftovers:
    def test_a_stale_string_from_an_older_session_is_not_restored(self):
        """A session written before #3626 holds the four strings. Restoring them
        onto the instance would shadow the computed values and be saved again."""
        from djust._state_restore import restore_public_state

        with _without_the_processor():
            request = _make_request(_make_db_session())
            view = Plain()
            view.setup(request)
            applied = restore_public_state(
                view, {"theme_head": "STALE", "theme_presets": ["STALE"], "count": 5}
            )
            assert applied == {"count": 5}
            assert "STALE" not in str(view.theme_head)
            assert [n for n in HEAVY if n in view.__dict__] == []

    def test_the_debug_panel_does_not_render_the_values(self):
        from unittest.mock import patch

        with _without_the_processor():
            request = _make_request(_make_db_session())
            view = Plain()
            view.setup(request)
            view.mount(request)
            with patch.object(Plain, "_render_theme_head", side_effect=AssertionError("rendered")):
                info = view.get_debug_info()
        assert "theme_head" in info["variables"]
        assert "not evaluated" in info["variables"]["theme_head"]["value"]
