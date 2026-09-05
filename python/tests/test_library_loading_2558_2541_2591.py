"""Django-library loading and binding on the Rust engine — #2558 / #2541 / #2591.

Three issues, one root: a Django template library the Rust backend refused
to load, to serve, or to let bind a name. Every parity row renders the SAME
source through live Django in process and through djust — the plain
``DjustTemplateBackend`` and, where the row is about output rather than a
parse-time refusal, the real LiveView entry (``LiveViewTestClient``, #1650)
— and compares bytes (or exception type + message).

* **#2558** — ``{% load i18n %}`` shipped in PR #2597; what still read as
  "``'i18n' is not a registered tag library`` with an EMPTY *Must be one
  of*" is the bare ``django.template.Engine()`` adapter, whose library map
  is exactly its explicit ``libraries`` — and Django's own ``Engine()``
  refuses ``{% load i18n %}`` with the same message. Pinned as parity on
  BOTH shapes: the backend renders, the bare engine refuses identically.
* **#2541** — the ``tz`` filters (``localtime`` / ``utc`` / ``timezone``)
  were bridged as loud refusals while a datetime crossed the boundary as
  its ISO string (#2216). It crosses as a typed ``Value::Encoded`` carrying
  the live object now (#2481), so they bridge verbatim; the one wrinkle is
  Django's ``datetimeobject`` result flagged ``convert_to_local_time =
  False``, which a following ``date`` / ``time`` filter must honour —
  ``crates/djust_core`` carries the flag and ``filters::format_date`` reads
  it. Gate-off split: remove the ``_FILTER_REFUSALS`` change and every tz
  row is red; remove only the Rust flag and exactly the ``timezone:…|date``
  rows under a differing active zone are red.
* **#2591** — ``{% querystring … as var %}`` was refused because the inline
  ``TagHandler`` could not write the context. The handler now rides the
  built-in bridge (``DjangoBuiltinTagHandler`` → Django's own
  ``SimpleNode`` with ``target_var``), whose bindings diff carries the
  write to the sibling nodes.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, Optional

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.http import QueryDict  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import RequestContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template import TemplateSyntaxError  # noqa: E402
from django.template.backends.django import DjangoTemplates  # noqa: E402
from django.template.engine import Engine  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.utils import timezone as dj_timezone  # noqa: E402
from django.utils import translation  # noqa: E402

from djust import _rust, render_env, template_libraries  # noqa: E402
from djust.live_view import LiveView  # noqa: E402
from djust.template.backend import DjustTemplateBackend  # noqa: E402
from djust.testing import LiveViewTestClient  # noqa: E402

DJANGO = DjangoTemplates({"NAME": "django-libload", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
DJUST = DjustTemplateBackend(
    {"NAME": "djust-libload", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
)
FACTORY = RequestFactory()
DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*)</div>", re.S)

UTC = datetime.timezone.utc
PLUS2 = datetime.timezone(datetime.timedelta(hours=2), "+02")
AWARE = datetime.datetime(2026, 1, 1, 5, 30, 15, tzinfo=PLUS2)
NAIVE = datetime.datetime(2026, 7, 4, 23, 45, 0)
CTX: Dict[str, Any] = {
    "d": AWARE,
    "summer": datetime.datetime(2026, 7, 4, 12, 0, tzinfo=UTC),
    "naive": NAIVE,
    "tzname": "Asia/Tokyo",
    "notadt": "2026-01-01",
    "n": 7,
}


@pytest.fixture(autouse=True)
def _restore_render_env():
    """Push the ambient locale/zone back to Rust after each test (the
    ``render_env`` thread-locals are set per render and never restored)."""
    yield
    render_env.apply_render_env()


# ---------------------------------------------------------------------------
# Render paths
# ---------------------------------------------------------------------------


def django_render(source: str, context: Optional[dict] = None, request=None) -> str:
    if request is not None:
        return str(DjangoTemplate(source).render(RequestContext(request, dict(context or {}))))
    return str(DJANGO.from_string(source).render(dict(context or {})))


def plain_render(source: str, context: Optional[dict] = None, request=None) -> str:
    return str(DJUST.from_string(source).render(dict(context or {}), request=request))


def liveview_render(source: str, context: Optional[dict] = None, request=None) -> str:
    """The REAL LiveView entry: ``mount()`` + ``render_with_patches()`` →
    ``_sync_state_to_rust`` → ``RustLiveView.render`` (#1650)."""
    values = dict(context or {})

    class _V(LiveView):
        def mount(self, request, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(values)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    client.view_instance._websocket_session_id = "test-libload"
    if request is not None:
        client.view_instance.request = request
    html = client.render()
    match = DJ_ROOT.search(html)
    assert match is not None, html
    # The LiveView path wraps `{% if %}` in `<!--dj-if-->` VDOM markers.
    return re.sub(r"<!--/?dj-if[^>]*-->", "", match.group(1))


def assert_all_paths_agree(source: str, context: dict, request=None) -> str:
    expected = django_render(source, context, request)
    assert plain_render(source, context, request) == expected, source
    assert liveview_render(source, context, request) == expected, source
    return expected


# ---------------------------------------------------------------------------
# #2558 — `{% load i18n %}` loads; the bare-Engine refusal is Django's own
# ---------------------------------------------------------------------------


class TestLoadI18n2558:
    @pytest.mark.parametrize("lang", ["de", "fr", None])
    def test_translate_renders_the_active_catalog_on_every_path(self, lang):
        source = '{% load i18n %}{% translate "Page not found" %}|{% blocktranslate %}Password{% endblocktranslate %}|{{ _("Password") }}'
        with translation.override(lang):
            expected = assert_all_paths_agree(source, {})
        if lang == "de":
            assert expected == "Seite nicht gefunden|Passwort|Passwort"

    def test_bare_engine_refuses_load_i18n_identically_on_both(self):
        """The symptom in the report — an EMPTY "Must be one of" — is the
        ``django.template.Engine()`` adapter, and Django's own bare ``Engine``
        says the same thing for the same reason: its library map is only what
        it was constructed with."""
        with pytest.raises(TemplateSyntaxError) as on_django:
            Engine().from_string("{% load i18n %}")
        with pytest.raises(TemplateSyntaxError) as on_djust:
            with template_libraries.rendering_with_backend(Engine()):
                _rust.compile_template("{% load i18n %}", None, return_template=True)
        assert str(on_djust.value) == str(on_django.value)
        assert str(on_django.value).endswith("Must be one of:\n")
        # A bare engine GIVEN the library resolves it on both, so the refusal
        # above is about the map, not about i18n.
        with_lib = Engine(libraries={"i18n": "django.templatetags.i18n"})
        assert (
            with_lib.from_string('{% load i18n %}{% translate "x" %}').render(DjangoContext())
            == "x"
        )
        with template_libraries.rendering_with_backend(with_lib):
            _rust.compile_template('{% load i18n %}{% translate "x" %}', None, return_template=True)

    def test_the_backend_map_carries_djangos_libraries(self):
        with template_libraries.rendering_with_backend(DJUST):
            known = template_libraries._library_map()
        assert {"i18n", "l10n", "tz", "static", "cache"} <= set(known)


# ---------------------------------------------------------------------------
# #2541 — the tz filters bridge verbatim and honour convert_to_local_time
# ---------------------------------------------------------------------------

TZ = "{% load tz %}"

#: name → source. Every row is a (filter × downstream) cell; the ``|date``
#: and ``|time`` rows are the ones the ``convert_to_local_time`` flag decides.
TZ_ROWS = {
    "utc-plain": TZ + "{{ d|utc }}",
    "utc-date": TZ + '{{ d|utc|date:"Y-m-d H:i:s e O" }}',
    "utc-time": TZ + '{{ d|utc|time:"H:i" }}',
    "localtime-plain": TZ + "{{ d|localtime }}",
    "localtime-date": TZ + '{{ d|localtime|date:"H:i e" }}',
    "timezone-literal-plain": TZ + '{{ d|timezone:"Asia/Tokyo" }}',
    "timezone-literal-date": TZ + '{{ d|timezone:"Asia/Tokyo"|date:"Y-m-d H:i e O T" }}',
    "timezone-literal-time": TZ + '{{ d|timezone:"Asia/Tokyo"|time:"H:i" }}',
    "timezone-variable-date": TZ + '{{ d|timezone:tzname|date:"H:i e" }}',
    "timezone-naive-date": TZ + '{{ naive|timezone:"Asia/Tokyo"|date:"H:i e" }}',
    "utc-naive-date": TZ + '{{ naive|utc|date:"H:i e" }}',
    "timezone-unknown-zone": TZ + '[{{ d|timezone:"Nope/Zone" }}]',
    "timezone-non-datetime": TZ + "[{{ notadt|timezone:'Asia/Tokyo' }}][{{ n|utc }}]",
    "chained-through-date-then-more": TZ + '{{ d|timezone:"Asia/Tokyo"|date:"H"|add:"1" }}',
    "inside-timezone-block": TZ
    + '{% timezone "America/New_York" %}{{ d|localtime|date:"H:i e" }}|{{ d|date:"H:i e" }}{% endtimezone %}',
    "inside-localtime-off": TZ
    + '{% localtime off %}{{ d|localtime|date:"H:i e" }}|{{ d|date:"H:i e" }}{% endlocaltime %}',
    "with-other-filters-in-chain": TZ + '{{ d|timezone:"Asia/Tokyo"|date:"H:i"|upper }}',
    # `I` (DST) on the pinned path must read the FILTER's zone rule, not the
    # active zone's (PR #2676 review): a DST zone in summer AND winter, a
    # no-DST zone, and the unpinned control next to each.
    "dst-flag-summer": TZ
    + '{{ summer|timezone:"America/New_York"|date:"I" }}|{{ summer|date:"I" }}',
    "dst-flag-winter": TZ + '{{ d|timezone:"America/New_York"|date:"I" }}|{{ d|date:"I" }}',
    "dst-flag-no-dst-zone": TZ
    + '{{ summer|timezone:"Asia/Tokyo"|date:"I" }}|{{ d|timezone:"Asia/Tokyo"|date:"I" }}',
    "dst-flag-localtime-utc": TZ + '{{ summer|utc|date:"I" }}|{{ summer|localtime|date:"I" }}',
}


class TestTzFilters2541:
    @pytest.mark.parametrize("name", sorted(TZ_ROWS))
    def test_agrees_with_django_in_utc(self, name):
        with dj_timezone.override("UTC"):
            assert_all_paths_agree(TZ_ROWS[name], CTX)

    @pytest.mark.parametrize("name", sorted(TZ_ROWS))
    def test_agrees_with_django_under_a_differing_active_zone(self, name):
        """Where the flag matters: the active zone is NOT the filter's."""
        with dj_timezone.override("Europe/Paris"):
            assert_all_paths_agree(TZ_ROWS[name], CTX)

    def test_the_differential_is_not_tautological(self):
        """Rows where the converted and the active-zone answers DIFFER, so a
        Rust side that reconverted would be caught (#1200)."""
        with dj_timezone.override("Europe/Paris"):
            tokyo = django_render(TZ_ROWS["timezone-literal-date"], CTX)
            active = django_render(TZ + '{{ d|date:"Y-m-d H:i e O T" }}', CTX)
        assert tokyo == "2026-01-01 12:30 JST +0900 JST"
        assert active == "2026-01-01 04:30 CET +0100 CET"
        with dj_timezone.override("Europe/Paris"):
            assert plain_render(TZ_ROWS["timezone-literal-date"], CTX) == tokyo

    def test_no_row_is_a_refusal_or_blank(self):
        with dj_timezone.override("UTC"):
            for name, source in TZ_ROWS.items():
                out = plain_render(source, CTX)
                assert "needs a datetime object" not in out, name
                if "unknown-zone" not in name and "non-datetime" not in name:
                    assert out.strip("[]") != "", name

    def test_refused_filter_set_is_empty(self):
        for module in ("tz", "l10n", "i18n"):
            assert (
                template_libraries.refused_filters(f"django.templatetags.{module}") == frozenset()
            )

    def test_the_flag_crosses_on_the_encoded_value(self):
        """The Rust side reads `convert_to_local_time` off the encoded attrs;
        a plain datetime carries none (so nothing else changes)."""
        from django.templatetags.tz import do_timezone

        flagged = do_timezone(AWARE, "Asia/Tokyo")
        assert flagged.convert_to_local_time is False
        src = TZ + "[{{ d.convert_to_local_time }}]"
        assert plain_render(src, {"d": flagged}) == django_render(src, {"d": flagged}) == "[False]"
        assert plain_render(src, {"d": AWARE}) == django_render(src, {"d": AWARE}) == "[]"


# ---------------------------------------------------------------------------
# #2591 — `{% querystring … as var %}` binds the name
# ---------------------------------------------------------------------------

_QS = pytest.mark.skipif(django.VERSION < (5, 1), reason="{% querystring %} is Django 5.1+")

QS_ROWS = [
    # (source, GET query, extra context, expected)
    ("{% querystring as qs %}[{{ qs }}]", "a=1&b=2", {}, "[?a=1&amp;b=2]"),
    ("{% querystring a=2 as qs %}[{{ qs }}]", "a=1&b=2", {}, "[?a=2&amp;b=2]"),
    ("{% querystring a=2 as qs %}", "a=1", {}, ""),
    ("{% querystring a=None as qs %}[{{ qs }}]", "a=1&b=2", {}, "[?b=2]"),
    ("{% querystring a=None as qs %}[{{ qs }}]", "a=1", {}, "[?]"),
    ("{% querystring as qs %}[{{ qs }}]", "", {}, "[]"),
    ("{% querystring a=my_list as qs %}[{{ qs }}]", "", {"my_list": [2, 3]}, "[?a=2&amp;a=3]"),
    (
        "{% querystring qd page=4 as qs %}[{{ qs }}]",
        "x=y",
        {"qd": QueryDict("q=z")},
        "[?q=z&amp;page=4]",
    ),
    (
        "{% querystring page=page_obj.number|add:1 as qs %}[{{ qs|upper }}]",
        "q=x",
        {"page_obj": {"number": 3}},
        "[?Q=X&amp;PAGE=4]",
    ),
    (
        "{% querystring a=2 as qs %}{% querystring b=3 as qs2 %}[{{ qs }}|{{ qs2 }}]",
        "a=1",
        {},
        "[?a=2|?a=1&amp;b=3]",
    ),
    # `as` rebinds an existing name for the siblings that follow
    ("[{{ qs }}]{% querystring a=2 as qs %}[{{ qs }}]", "a=1", {"qs": "before"}, "[before][?a=2]"),
    # the binding is visible inside a following {% if %} / {% with %}
    (
        '{% querystring a=2 as qs %}{% if qs %}<a href="{{ qs }}">x</a>{% endif %}',
        "a=1",
        {},
        '<a href="?a=2">x</a>',
    ),
    # `as` on the inline form still renders inline without it
    ("{% querystring a=2 %}", "a=1", {}, "?a=2"),
]


@_QS
class TestQuerystringAsVar2591:
    @pytest.mark.parametrize("source,query,extra,expected", QS_ROWS)
    def test_binds_like_djangos_simple_tag_on_every_path(self, source, query, extra, expected):
        request = FACTORY.get("/?" + query if query else "/")
        assert assert_all_paths_agree(source, extra, request) == expected

    def test_without_a_request_djangos_own_error_surfaces(self):
        with pytest.raises(AttributeError, match="request") as on_django:
            django_render("{% querystring a=2 as qs %}", {})
        with pytest.raises(AttributeError, match="request") as on_djust:
            plain_render("{% querystring a=2 as qs %}", {})
        assert str(on_djust.value) == str(on_django.value)

    def test_malformed_as_forms_fail_the_way_django_fails(self):
        """`parse_bits` only sees `as` when it is `bits[-2]`: a trailing bare
        `as` is a POSITIONAL on Django (`query_dict="as"` → runtime
        `AttributeError`), `a=2 as` is "positional after keyword". Same
        exception type and message on both, whichever it is."""
        for source in (
            "{% querystring as %}",
            "{% querystring a=2 as %}",
            "{% querystring as qs extra %}",
        ):
            with pytest.raises(Exception) as on_django:
                django_render(source, {}, FACTORY.get("/"))
            with pytest.raises(Exception) as on_djust:
                plain_render(source, {}, FACTORY.get("/"))
            assert type(on_djust.value) is type(on_django.value), source
            assert str(on_djust.value) == str(on_django.value), source

    def test_handler_rides_the_builtin_bridge(self):
        from djust.template_tags._builtin import DjangoBuiltinTagHandler
        from djust.template_tags.querystring import QuerystringTagHandler

        assert issubclass(QuerystringTagHandler, DjangoBuiltinTagHandler)
        assert QuerystringTagHandler.RETURNS_BINDINGS is True
