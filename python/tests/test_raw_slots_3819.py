"""Raw-markup slots now have an escaping contract (#3819).

Two places put a developer-supplied string into markup unescaped, on purpose, with no stated rule:

* ``{% live_render ... lazy={"placeholder": ...} %}`` spliced the placeholder as HTML. It now
  follows Django's own rule (``conditional_escape``): a marked-safe value (``mark_safe``,
  ``format_html``, anything with ``__html__``) is markup and is spliced in as written; a plain
  ``str`` is TEXT and is escaped.
* ``routing.get_route_map_script`` put ``json.dumps`` output inside a ``<script>`` with
  ``mark_safe``. ``json.dumps`` leaves ``<``, ``>`` and ``&`` raw, so a ``</script>`` in a key or
  value ended the element. It now goes through ``escape_json_for_script`` (the helper the debug
  panel and the PWA tags use), as do the two other developer-config blobs inlined in a script: the
  handler-metadata script and ``DJUST_LOADING_GROUPING_CLASSES``.

Refs #3819, #3774, #3766.
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.functional import lazy

from djust import LiveView
from djust.routing import _reset_route_map_cache, get_route_map_script, live_session

pytestmark = [pytest.mark.django_db(transaction=False)]

BREAKOUT = "</script><script>alert(1)</script>"
SLOT_BREAKOUT = "</dj-lazy-slot><img src=x onerror=alert(1)>"


class _Child(LiveView):
    template = "<div>child</div>"

    def mount(self, request, **kwargs):
        pass


class _Parent(LiveView):
    template = "{% load live_tags %}<div dj-root></div>"

    def mount(self, request, **kwargs):
        pass


ALLOWED = [__name__]
CHILD = f"{__name__}._Child"


def _parent():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    parent = _Parent()
    parent.request = request
    parent.mount(request)
    return parent, request


def _django(config):
    parent, request = _parent()
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=ALLOWED):
        return Template("{% load live_tags %}{% live_render '" + CHILD + "' lazy=cfg %}").render(
            Context({"view": parent, "request": request, "cfg": config})
        )


def _native(config):
    """The native engine's ``live_render`` handler, called the way the Rust renderer calls it.

    A dict ``lazy=`` cannot be driven through ``RustLiveView.render()`` itself: the Rust engine
    resolves ``lazy=cfg`` to a JSON string and the handler then refuses it ("accepts True,
    'visible', or a dict"), which is true on ``main`` and unrelated to the placeholder. The
    handler is the native twin of the Django tag (it delegates to the same function), so it is
    driven directly with the already-resolved context.
    """
    from djust.template_tags.live_render import LiveRenderTagHandler

    parent, request = _parent()
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=ALLOWED):
        return LiveRenderTagHandler().render(
            [repr(CHILD), "lazy=cfg"], {"view": parent, "request": request, "cfg": config}
        )


ENGINES = {"django": _django, "native": _native}


@pytest.fixture(params=list(ENGINES))
def engine(request):
    return ENGINES[request.param]


def _slot(out):
    match = re.search(r"<dj-lazy-slot\b[^>]*>(.*?)</dj-lazy-slot>", out, flags=re.S)
    assert match, out
    return match.group(1)


class _HasHtml:
    def __html__(self):
        return "<b>from __html__</b>"


class TestLazyPlaceholderContract:
    def test_a_plain_str_is_text_and_is_escaped(self, engine):
        out = engine({"placeholder": "<div>Loading...</div>"})
        assert _slot(out) == "&lt;div&gt;Loading...&lt;/div&gt;"

    @pytest.mark.parametrize(
        "payload",
        [
            SLOT_BREAKOUT,
            "<script>alert(1)</script>",
            '"><svg onload=alert(1)>',
            "<!--",
            "&lt;already&gt; & '",
        ],
    )
    def test_a_hostile_plain_str_cannot_inject_markup(self, engine, payload):
        out = engine({"placeholder": payload})
        slot = _slot(out)
        assert "<" not in slot and ">" not in slot
        assert out.count("<dj-lazy-slot") == 1 and out.count("</dj-lazy-slot>") == 1

    def test_a_marked_safe_value_is_markup(self, engine):
        out = engine({"placeholder": mark_safe("<div>Loading...</div>")})
        assert _slot(out) == "<div>Loading...</div>"

    def test_format_html_output_is_markup_and_its_arguments_are_escaped(self, engine):
        out = engine({"placeholder": format_html("<i>{}</i>", "<b>x</b>")})
        assert _slot(out) == "<i>&lt;b&gt;x&lt;/b&gt;</i>"

    def test_an_object_with_dunder_html_is_markup(self, engine):
        assert _slot(engine({"placeholder": _HasHtml()})) == "<b>from __html__</b>"

    def test_a_lazy_marked_safe_string_is_markup(self, engine):
        proxy = lazy(lambda: mark_safe("<u>lazy</u>"), str)()
        assert _slot(engine({"placeholder": proxy})) == "<u>lazy</u>"

    def test_a_non_string_is_printed_as_text(self, engine):
        assert _slot(engine({"placeholder": 5})) == "5"

    @pytest.mark.parametrize("empty", [None, "", 0])
    def test_no_placeholder_is_an_empty_slot(self, engine, empty):
        assert _slot(engine({"placeholder": empty})) == ""

    def test_the_other_lazy_options_are_unchanged(self, engine):
        out = engine({"trigger": "visible", "placeholder": "hi"})
        assert 'data-trigger="visible"' in out and _slot(out) == "hi"

    def test_the_two_engines_agree(self):
        for config in (
            {"placeholder": "<div>x</div>"},
            {"placeholder": mark_safe("<div>x</div>")},
            {"placeholder": SLOT_BREAKOUT},
            {},
        ):
            d = re.sub(r"child_\d+", "child_X", _django(config))
            n = re.sub(r"child_\d+", "child_X", _native(config))
            assert d == n, (config, d, n)


# ---------------------------------------------------------------------------
# get_route_map_script
# ---------------------------------------------------------------------------


@pytest.fixture
def routes():
    saved = getattr(live_session, "_route_maps", None)
    _reset_route_map_cache()
    yield lambda entries: setattr(live_session, "_route_maps", {"t": entries})
    if saved is None:
        if hasattr(live_session, "_route_maps"):
            delattr(live_session, "_route_maps")
    else:
        live_session._route_maps = saved
    _reset_route_map_cache()


def _route_map_json(script):
    match = re.fullmatch(
        r'<script(?: nonce="[^"]*")?>window\.djust=window\.djust\|\|\{\};window\.djust\._routeMap=(.*);</script>',
        script,
        flags=re.S,
    )
    assert match, script
    return match.group(1)


HOSTILE_ROUTES = [
    ("/a" + BREAKOUT + "/", "app.views.A"),
    ("/b/", "app.views.B" + BREAKOUT),
    ("/c&d/<e>/", "app.views.C&D"),
    ("/f<!--/", "app.views.F-->"),
    ("/g h /", "app.views.G "),
]


class TestRouteMapScript:
    def test_a_script_breakout_in_a_route_cannot_end_the_script(self, routes):
        routes(HOSTILE_ROUTES)
        # An empty urlconf-derived map is not guaranteed under the test urlconf; the hostile
        # live_session entries are what is under test.
        out = get_route_map_script(RequestFactory().get("/"))
        assert out.count("<script") == 1 and out.count("</script>") == 1
        body = _route_map_json(out)
        for forbidden in ("<", ">", "&", " ", " "):
            assert forbidden not in body, forbidden
        assert out.startswith("<script>") and out.endswith("</script>")

    def test_the_escaped_json_decodes_to_the_same_routes(self, routes):
        routes(HOSTILE_ROUTES)
        decoded = json.loads(_route_map_json(get_route_map_script(RequestFactory().get("/"))))
        for path, view in HOSTILE_ROUTES:
            assert decoded[path] == view

    def test_the_nonce_form_is_escaped_too(self, routes):
        routes(HOSTILE_ROUTES)
        request = RequestFactory().get("/")
        request.csp_nonce = "N0nce"
        out = get_route_map_script(request)
        assert out.startswith('<script nonce="N0nce">')
        assert out.count("<script") == 1 and out.count("</script>") == 1
        assert "<" not in _route_map_json(out)

    def test_an_ordinary_route_map_is_byte_identical(self, routes):
        routes([("/dashboard/", "myapp.views.DashboardView")])
        out = get_route_map_script(RequestFactory().get("/"))
        assert json.loads(_route_map_json(out))["/dashboard/"] == "myapp.views.DashboardView"
        assert '"/dashboard/": "myapp.views.DashboardView"' in out, "json.dumps spelling kept"
        # Nothing but the three escaped characters changes: a map with none of them is exactly
        # what ``json.dumps`` produced before.
        body = _route_map_json(out)
        assert body == json.dumps(json.loads(body))


# ---------------------------------------------------------------------------
# The two other developer-config blobs inlined in a <script>
# ---------------------------------------------------------------------------


class TestOtherScriptBlobs:
    def test_the_handler_metadata_script_cannot_be_ended_by_its_data(self):
        from djust.mixins.template import TemplateMixin

        class _View(TemplateMixin):
            def __init__(self):
                pass

            def _extract_handler_metadata(self):
                return {"h": {"description": BREAKOUT, "x": "a&b "}}

        out = _View()._inject_handler_metadata("<html><body>hi</body></html>")
        assert out.count("<script") == 1 and out.count("</script>") == 1, out
        blob = re.search(r"Object\.assign\(window\.handlerMetadata, (.*)\);\n</script>", out, re.S)
        assert blob
        decoded = json.loads(blob.group(1))
        assert decoded["h"]["description"] == BREAKOUT

    def test_ordinary_handler_metadata_is_byte_identical(self):
        from djust.mixins.template import TemplateMixin

        class _View(TemplateMixin):
            def __init__(self):
                pass

            def _extract_handler_metadata(self):
                return {"my_handler": {"lock": True, "debounce": 300}}

        out = _View()._inject_handler_metadata("<html><body>hi</body></html>")
        assert (
            'Object.assign(window.handlerMetadata, {"my_handler": {"lock": true, "debounce": 300}});'
            in out
        )

    def test_loading_grouping_classes_cannot_end_the_config_script(self):
        class _View(LiveView):
            template = "<div>x</div>"

        classes = [BREAKOUT, "a&b", "ok"]
        with override_settings(LIVEVIEW_CONFIG={"loading_grouping_classes": classes}):
            out = _View()._inject_client_script("<html><head></head><body></body></html>")
        line = next(ln for ln in out.splitlines() if "DJUST_LOADING_GROUPING_CLASSES" in ln)
        value = line.split("=", 1)[1].strip().rstrip(";")
        assert "<" not in value and ">" not in value and "&" not in value
        assert json.loads(value) == classes
        assert out.count("</script>") == out.count("<script")

    def test_default_loading_grouping_classes_are_byte_identical(self):
        class _View(LiveView):
            template = "<div>x</div>"

        out = _View()._inject_client_script("<html><head></head><body></body></html>")
        assert (
            'window.DJUST_LOADING_GROUPING_CLASSES = ["d-flex", "btn-group", "input-group", '
            '"form-group", "btn-toolbar"];' in out
        )
