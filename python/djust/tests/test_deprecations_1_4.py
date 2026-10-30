"""1.4 deprecations: everything djust 2.0 removes warns first (#3500).

Each warning names its replacement and ``2.0.0``, through the standard
``warn_deprecated`` helper. Where a test asserts the warning's filename, the
property is that the warning points at the caller (this file), not at djust.
"""

import importlib
import os
import sys
import warnings

import pytest
from django.test import RequestFactory, override_settings

THIS_FILE = os.path.basename(__file__)


def _one_deprecation(record, needle):
    hits = [
        w for w in record if issubclass(w.category, DeprecationWarning) and needle in str(w.message)
    ]
    assert len(hits) == 1, [str(w.message) for w in record]
    assert "2.0.0" in str(hits[0].message)
    return hits[0]


# --- component mixins (DEP-002) ---------------------------------------------

MIXINS = [
    ("AccordionMixin", "Accordion"),
    ("TabsMixin", "Tabs"),
    ("ModalMixin", "Modal"),
    ("CollapsibleMixin", "Collapsible"),
    ("SheetMixin", "Sheet"),
    ("DropdownMixin", "Dropdown"),
    ("TooltipMixin", "Tooltip"),
    ("CarouselMixin", "Carousel"),
]


@pytest.mark.parametrize("mixin_name,replacement", MIXINS)
def test_subclassing_a_deprecated_mixin_warns_at_the_class_statement(mixin_name, replacement):
    from djust.components import mixins

    mixin = getattr(mixins, mixin_name)
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Page(mixin):  # <- the user frame
            pass

    hit = _one_deprecation(record, mixin_name)
    assert "djust.components.descriptors" in str(hit.message)
    assert replacement in str(hit.message)
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


def test_a_subclass_of_a_subclass_does_not_warn_again():
    from djust.components.mixins import TabsMixin

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        class _Base(TabsMixin):
            pass

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Child(_Base):
            pass

    assert [w for w in record if "TabsMixin" in str(w.message)] == []


def test_the_warning_follows_the_user_frame_behind_liveview_init_subclass():
    """LiveView first: its ``__init_subclass__`` chains to the mixin's."""
    from djust import LiveView
    from djust.components.mixins import ModalMixin

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Page(LiveView, ModalMixin):  # <- the user frame
            template = "<div dj-root></div>"

    hit = _one_deprecation(record, "ModalMixin")
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


def test_using_the_descriptor_components_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        from djust.components.descriptors import Accordion  # noqa: F401


# --- social_auth_providers ----------------------------------------------------


@pytest.mark.django_db
def test_social_auth_providers_warns_through_the_policy_helper():
    from djust.auth.social import social_auth_providers

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        social_auth_providers(RequestFactory().get("/"))
    hit = _one_deprecation(record, "social_auth_providers")
    message = str(hit.message)
    assert "deprecated since djust 1.3" in message
    assert "{% auth_providers auth %}" in message
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


# --- module shims -------------------------------------------------------------


@pytest.mark.parametrize(
    "module,replacement",
    [
        ("djust.template_backend", "djust.template"),
        ("djust.state_backend", "djust.state_backends"),
    ],
)
def test_importing_a_shim_module_warns(module, replacement, monkeypatch):
    """The warning blames the importing statement, not the shim file.

    ``exec`` of a real ``import`` statement under a distinctive filename stands
    in for a user's module: ``warnings`` skips importlib's own frames, so
    stacklevel=3 lands on that statement (stacklevel=2 would blame the shim).
    """
    from djust import _deprecation

    monkeypatch.setattr(_deprecation, "_ANNOUNCED", set())
    saved = sys.modules.pop(module, None)
    try:
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            exec(compile("import %s" % module, "<user import>", "exec"), {})
        hit = _one_deprecation(record, module)
        assert replacement in str(hit.message)
        assert hit.filename == "<user import>", hit.filename
    finally:
        if saved is not None:
            sys.modules[module] = saved


@pytest.mark.parametrize(
    "module,target",
    [
        ("djust.template_backend", "djust.template"),
        ("djust.state_backend", "djust.state_backends"),
    ],
)
def test_a_shim_star_export_is_exactly_its_targets(module, target):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        shim = importlib.import_module(module)
    real = importlib.import_module(target)
    assert shim.__all__ == real.__all__
    assert "warn_deprecated" not in shim.__all__


def test_a_templates_entry_naming_the_shim_warns_once(monkeypatch):
    """The settings check names the ``TEMPLATES`` entry; the import it causes
    would warn about the same cause again, so the shim stays quiet after it."""
    from djust import _deprecation
    from djust._deprecated_settings import warn_deprecated_settings

    module = "djust.template_backend"
    monkeypatch.setattr(_deprecation, "_ANNOUNCED", set())
    saved = sys.modules.pop(module, None)
    try:
        with override_settings(
            TEMPLATES=[{"BACKEND": "djust.template_backend.DjustTemplateBackend"}]
        ):
            with warnings.catch_warnings(record=True) as record:
                warnings.simplefilter("always")
                warn_deprecated_settings()
                importlib.import_module(module)
        messages = [str(w.message) for w in record if issubclass(w.category, DeprecationWarning)]
        assert len([m for m in messages if "TEMPLATES[0]['BACKEND']" in m]) == 1, messages
        assert [m for m in messages if m.startswith("djust.template_backend is deprecated")] == []
    finally:
        if saved is not None:
            sys.modules[module] = saved


def test_star_importing_simple_live_view_keeps_binding_the_old_name():
    ns: dict = {}
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        exec(compile("from djust.simple_live_view import *", "<user import>", "exec"), ns)
    from djust.simple_live_view import SimpleLiveView

    assert ns["LiveView"] is SimpleLiveView
    hit = _one_deprecation(record, "djust.simple_live_view.LiveView")
    assert hit.filename == "<user import>", hit.filename


def test_the_shim_still_re_exports_the_backend():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from djust.template import DjustTemplateBackend
        from djust.template_backend import DjustTemplateBackend as Old

    assert Old is DjustTemplateBackend


def test_is_view_import_allowed_warns_and_delegates():
    from djust._view_resolution import is_view_import_allowed

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        assert is_view_import_allowed("") is False
    hit = _one_deprecation(record, "is_view_import_allowed")
    assert "djust.security.mount.is_view_path_allowed" in str(hit.message)
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


def test_rust_components_button_warns_before_building():
    from djust.rust_components import Button

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        try:
            Button("b", "Label")
        except Exception:  # noqa: BLE001 - the Rust class may be absent or mismatched
            pass
    hit = _one_deprecation(record, "djust.rust_components.Button")
    assert "djust.components.ui.Button" in str(hit.message)
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


# --- the SSE /event/ endpoint ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_sse_event_endpoint_warns():
    from djust.sse import DjustSSEEventView

    request = RequestFactory().post(
        "/djust/sse/missing/event/", data="{}", content_type="application/json"
    )
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        await DjustSSEEventView().post(request, session_id="missing")
    hit = _one_deprecation(record, "DjustSSEEventView")
    assert "message/" in str(hit.message)
    # Deliberate: an HTTP endpoint has no application frame to blame (the
    # caller is Django's request handling), so the warning names the endpoint
    # itself in its text and its location is djust's own ``sse.py``.
    assert hit.filename.endswith(os.path.join("djust", "sse.py")), hit.filename


# --- settings -------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,needle,replacement",
    [
        ({"DJUST_WS_COMPRESSION": False}, "settings.DJUST_WS_COMPRESSION", "websocket_compression"),
        ({"DJUST_VDOM_CACHE_ENABLED": False}, "DJUST_VDOM_CACHE_ENABLED", "vdom_cache_enabled"),
        ({"DJUST_VDOM_CACHE_TTL_SECONDS": 5}, "DJUST_VDOM_CACHE_TTL_SECONDS", "vdom_cache_ttl"),
        ({"DJUST_VDOM_CACHE_MAX_ENTRIES": 5}, "DJUST_VDOM_CACHE_MAX_ENTRIES", "vdom_cache_max"),
        ({"DJUST_STATE_BACKEND": "memory"}, "settings.DJUST_STATE_BACKEND", "STATE_BACKEND']"),
        ({"DJUST_REDIS_URL": "redis://h"}, "settings.DJUST_REDIS_URL", "REDIS_URL']"),
        (
            {"LIVEVIEW_CONFIG": {"template_auto_call": False}},
            "LIVEVIEW_CONFIG['template_auto_call']",
            "delete the key",
        ),
        (
            {"DJUST_CONFIG": {"virtual_keyed_ops": True}},
            "DJUST_CONFIG['virtual_keyed_ops']",
            "delete the key",
        ),
        (
            {"LIVEVIEW_CONFIG": {"django_value_repr": False}},
            "LIVEVIEW_CONFIG['django_value_repr']",
            "delete the key",
        ),
        (
            {"TEMPLATES": [{"BACKEND": "djust.template_backend.DjustTemplateBackend"}]},
            "TEMPLATES[0]['BACKEND']",
            "djust.template.backend.DjustTemplateBackend",
        ),
    ],
)
def test_a_deprecated_setting_warns_at_startup(overrides, needle, replacement):
    from djust._deprecated_settings import warn_deprecated_settings

    with override_settings(**overrides):
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            warn_deprecated_settings()
    hit = _one_deprecation(record, needle)
    assert replacement in str(hit.message)


def test_live_settings_do_not_warn():
    from djust._deprecated_settings import warn_deprecated_settings

    with override_settings(
        LIVEVIEW_CONFIG={"websocket_compression": False, "service_worker": {}},
        DJUST_CONFIG={"STATE_BACKEND": "memory"},
        TEMPLATES=[{"BACKEND": "djust.template.backend.DjustTemplateBackend"}],
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            warn_deprecated_settings()


def test_ready_emits_the_settings_warnings():
    """A real ``django.setup()`` (so ``DjustConfig.ready()``) with a deprecated
    setting warns. Run in a fresh interpreter: ``ready()`` is not re-runnable
    in this process without side effects, and this is the path a server takes."""
    import subprocess

    code = (
        "import warnings\n"
        "warnings.simplefilter('always')\n"
        "import django\n"
        "from django.conf import settings\n"
        "settings.configure(INSTALLED_APPS=['djust'], SECRET_KEY='x', DEBUG=False,\n"
        "                   DJUST_WS_COMPRESSION=False)\n"
        "with warnings.catch_warnings(record=True) as w:\n"
        "    warnings.simplefilter('always')\n"
        "    django.setup()\n"
        "print('\\n'.join('WARN ' + str(x.message) for x in w))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True
    ).stdout
    lines = [
        ln for ln in out.splitlines() if ln.startswith("WARN ") and "DJUST_WS_COMPRESSION" in ln
    ]
    assert len(lines) == 1, out
    assert "websocket_compression" in lines[0] and "2.0.0" in lines[0], lines[0]


# --- @reactive ------------------------------------------------------------------


def test_reactive_warns_at_the_decorated_definition():
    """``@reactive`` cannot work on a LiveView (no ``update()``), and on other
    hosts the decorated body is never called, so 2.0 removes it."""
    from djust.decorators import reactive

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Host:
            def update(self):
                pass

            @reactive  # <- the user frame
            def count(self):
                return 1

    hit = _one_deprecation(record, "@reactive")
    assert "@computed" in str(hit.message)
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


# --- async stream insert/delete names -----------------------------------------


def test_the_old_async_stream_names_warn_and_delegate():
    import asyncio

    from djust.streaming import StreamingMixin

    calls = []

    class _View(StreamingMixin):
        async def astream_insert(self, stream_name, html, at="append", target=None):
            calls.append(("insert", stream_name, html, at, target))

        async def astream_delete(self, stream_name, selector):
            calls.append(("delete", stream_name, selector))

    view = _View()

    async def handler():  # <- the user frame
        await StreamingMixin.stream_insert(view, "feed", "<li>x</li>", at="prepend")
        await StreamingMixin.stream_delete(view, "feed", "#x")

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        asyncio.run(handler())
    insert = _one_deprecation(record, "StreamingMixin.stream_insert")
    delete = _one_deprecation(record, "StreamingMixin.stream_delete")
    assert "astream_insert" in str(insert.message)
    assert "astream_delete" in str(delete.message)
    assert os.path.basename(insert.filename) == THIS_FILE, insert.filename
    assert calls == [("insert", "feed", "<li>x</li>", "prepend", None), ("delete", "feed", "#x")]


def test_liveview_reaches_the_new_async_names():
    import inspect

    from djust import LiveView

    assert inspect.iscoroutinefunction(LiveView.astream_insert)
    assert inspect.iscoroutinefunction(LiveView.astream_delete)
