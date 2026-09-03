"""``{% translate %}`` / ``{% blocktranslate %}`` / ``_("…")`` / the scope tags
through the Django-library bridge (#2558).

Every parity assertion renders the SAME source through live Django in
process and through djust — the plain backend (``DjustTemplateBackend``) AND
the real LiveView entry (``LiveViewTestClient``, #1650) — under
``translation.override`` for the languages Django's own suite uses, and
compares bytes (or exception type + message). The German strings come from
``django.contrib.admin``'s catalog (``"Page not found"`` → ``"Seite nicht
gefunden"``, ``"Password"`` → ``"Passwort"``), NOT from ``conf/locale`` — a
harness without ``contrib.admin`` installed would pass by rendering English
on both sides, so :func:`test_the_differential_is_not_tautological` pins one
row where the languages actually differ (#1200). ``pgettext`` /
``npgettext`` / the ``%``-formatting rows use Django's OWN ``tests/i18n/other/
locale`` fixture from the ``.django-src`` checkout, skip-guarded.

Mechanisms and the gate-off split (#1468 / #2129 / #2135)
---------------------------------------------------------
Each mechanism below has rows that go red when ONLY it is removed; the
measurement is in the PR body.

* **raw-body kind** (``template_libraries._bridge_library`` routing
  ``_RAW_BLOCK_TAGS`` to ``_bridge_raw_block_tag`` → the parser's
  ``collect_raw_source`` arm): gate it off and every ``blocktranslate`` row
  is red (the body reaches Django pre-rendered or the tag is refused) and
  nothing else.
* **the ``_()`` translator hook** (``template_libraries.translate_msgid`` →
  ``registry::TRANSLATOR`` → ``renderer::django_literal``): gate it off and
  only the ``_()`` rows under ``de``/``fr`` are red — the ``100%%`` quirk and
  the ``<`` rows survive, because they are the msgid's own bytes.
* **the ``language`` scope hook** (``render_env.language_scope_enter`` →
  ``translation.override``): gate the override off and only the
  ``language`` rows are red. The ``apply_render_env`` re-push inside the
  same hook is a SECOND mechanism: gate only it off and exactly the
  ``{{ n }}``-inside-``{% language %}`` row is red (the #2129 separation).
* **the ``localize`` / ``localtime`` flags** (``renderer::USE_L10N_STACK``,
  ``timezone::set_active_timezone(None)``): each gates off its own
  ``localize off`` / ``localtime off`` rows.
* **``mark_safe`` on the raw-block return** and **the bindings diff**
  (``LibraryRawBlockTagHandler.render``): the ``<b>``-in-body rows, and the
  ``asvar`` rows, respectively.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import random
import re
import sys
import zoneinfo
from decimal import Decimal

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.template import TemplateSyntaxError  # noqa: E402
from django.template.backends.django import DjangoTemplates  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from django.utils import timezone as dj_timezone  # noqa: E402
from django.utils import translation  # noqa: E402
from django.utils.safestring import SafeString, mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust import template_libraries  # noqa: E402
from djust.live_view import LiveView  # noqa: E402
from djust.template.backend import DjustTemplateBackend  # noqa: E402
from djust.testing import LiveViewTestClient  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
CRATE = REPO / "crates" / "djust_templates" / "src"

DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*)</div>", re.S)

#: The languages Django's own ``syntax_tests/i18n`` exercise. ``de`` strings
#: come from ``contrib.admin``, ``fr``/``nl`` from ``conf/locale``.
LANGUAGES = ["de", "fr", "nl", None]

UTC = datetime.timezone.utc
DT = datetime.datetime(2011, 9, 1, 13, 20, 30, tzinfo=UTC)

CTX = {
    "anton": "α",
    "berta": "β",
    "number": 2,
    "one": 1,
    "zero": 0,
    "percent": 42,
    "hostile": "<img src=x onerror=alert(1)>",
    "safe": mark_safe("<b>ok</b>"),
    "amp": "a & b",
    "var": "Password",
    "n": 1234567.891,
    "i": 1455,
    "f": 3.14,
    "dt": DT,
    "encoded": "&lt;img&gt;",
    "pct": "%3Cimg%3E",
    "name": "Jack",
    "num": 1,
    "num2": 2,
    "strnum": "1",
    "lang": "fr",
    "tzname": "Asia/Tokyo",
}


# ---------------------------------------------------------------------------
# The three render paths
# ---------------------------------------------------------------------------

DJANGO = DjangoTemplates({"NAME": "django2558", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
DJUST = DjustTemplateBackend({"NAME": "djust2558", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


def django_render(source: str, context: dict) -> str:
    return str(DJANGO.from_string(source).render(dict(context)))


def plain_render(source: str, context: dict) -> str:
    return str(DJUST.from_string(source).render(dict(context)))


def liveview_render(source: str, context: dict) -> str:
    """The REAL LiveView entry: ``LiveViewTestClient.mount()`` + ``.render()``
    → ``_sync_state_to_rust`` → ``RustLiveView.render`` (#1650)."""

    class _V(LiveView):
        def mount(self, request, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    # Take the WebSocket cache-key branch (`rust_bridge.py:382`), which is the
    # branch a live page actually renders through — and which needs no
    # `session.create()`. Without it `_initialize_rust_view` falls to the HTTP
    # branch and mints a session row, which pytest-django blocks in this
    # module (the blocker is per-test, and the same call is allowed in
    # `test_load_imports_django_libraries_2547.py` — a harness accident, not a
    # framework difference).
    client.view_instance._websocket_session_id = "test-2558"
    html = client.render()
    match = DJ_ROOT.search(html)
    assert match is not None, html
    return match.group(1)


RENDER = {"plain": plain_render, "liveview": liveview_render}


def _outcome(fn, source, context=CTX):
    """Bytes, or the exception TYPE + message — a library error crosses whole
    (#2547), so Django's own text is the contract."""
    try:
        return "OK:" + fn(source, context)
    except TemplateSyntaxError as e:
        return "TSE:" + str(e)
    except Exception as e:  # noqa: BLE001 — the differential compares classes
        return "EXC:%s:%s" % (type(e).__name__, e)


def _outcome_class(fn, source, context=CTX):
    try:
        fn(source, context)
        return None
    except Exception as e:  # noqa: BLE001
        return type(e)


def _django_src() -> pathlib.Path | None:
    override = os.environ.get("DJUST_DJANGO_SRC")
    candidates = [pathlib.Path(override)] if override else []
    candidates.append(REPO / ".django-src" / django.get_version())
    for c in candidates:
        if (c / "tests" / "i18n" / "other" / "locale").is_dir():
            return c
    return None


def _fixture_locale() -> str:
    src = _django_src()
    if src is None:
        pytest.skip(
            "Django's tests/i18n/other/locale fixture needs the .django-src checkout "
            "(`make django-template-suite` fetches it; or set DJUST_DJANGO_SRC)"
        )
    return str(src / "tests" / "i18n" / "other" / "locale")


# ---------------------------------------------------------------------------
# Every shape from the plan's variant inventory, byte-for-byte
# ---------------------------------------------------------------------------

L = "{% load i18n %}"
LT = "{% load i18n l10n tz %}"

#: Rendered under every language in LANGUAGES, on both paths.
SHAPES = {
    # {% translate %} — all option forms, both spellings
    "translate-literal-dq": L + '{% translate "Page not found" %}',
    "trans-literal-sq": L + "{% trans 'Page not found' %}",
    "translate-noop": L + '{% translate "Page not found" noop %}',
    "translate-variable-escaped": L + "{% translate amp %}",
    "translate-variable-safe": L + "{% translate safe %}",
    "translate-variable-hostile": L + "{% translate hostile %}",
    "translate-variable-html-encoded": L + "{% translate encoded %}",
    "translate-variable-percent-encoded": L + "{% translate pct %}",
    "translate-literal-markup-raw": L + '{% translate "<b>" %}',
    "translate-literal-filter-chain": L + '{% translate "Page not found"|capfirst|slice:"6:" %}',
    "translate-literal-upper": L + "{% translate 'Page not found'|upper %}",
    "translate-variable-filter": L + "{% translate amp|upper %}",
    "translate-as": L + '{% translate "Page not found" as v %}[{{ v }}]',
    "translate-noop-as": L + '{% translate "Page not found" noop as v %}[{{ v }}]',
    "translate-hostile-as": L + "{% translate hostile as v %}[{{ v }}]",
    "translate-as-in-if": L
    + '{% translate "Page not found" as v %}{% if v %}{{ v|upper }}{% endif %}',
    "translate-percent-s": L + '{% translate "%s" %}',
    "translate-100-percent": L + '{% translate "100%" %}',
    "translate-context-literal": L + '{% translate "Yes" context "verb" %}',
    "translate-context-variable": L + '{% translate "Yes" context var|lower %}',
    "translate-as-then-context": L + '{% translate "Yes" as v context "verb" %}{{ v }}',
    "translate-in-for": L + '{% for x in "ab" %}{% translate "Yes" %};{% endfor %}',
    # {% blocktranslate %} — every option, both legacy spellings
    "bt-bare-var": L + "{% blocktranslate %}{{ anton }}{% endblocktranslate %}",
    "bt-legacy-bare-var": L + "{% blocktrans %}{{ anton }}{% endblocktrans %}",
    "bt-text-around-var": L + "{% blocktranslate %}xxx{{ anton }}xxx{% endblocktranslate %}",
    "bt-with-filter": L
    + "{% blocktranslate with berta=anton|lower %}{{ berta }}{% endblocktranslate %}",
    "bt-legacy-with-filter": L
    + "{% blocktranslate with anton|lower as berta %}{{ berta }}{% endblocktranslate %}",
    "bt-with-two": L
    + "{% blocktranslate with a=anton b=berta %}{{ a }} {{ b }}{% endblocktranslate %}",
    "bt-legacy-with-two": L
    + "{% blocktranslate with anton as a and berta as b %}{{ a }} {{ b }}{% endblocktranslate %}",
    "bt-count-plural": L
    + "{% blocktranslate count counter=number %}singular{% plural %}{{ counter }} plural{% endblocktranslate %}",
    "bt-legacy-count-plural": L
    + "{% blocktranslate count number as counter %}singular{% plural %}{{ counter }} plural{% endblocktranslate %}",
    "bt-count-one": L
    + "{% blocktranslate count counter=one %}singular{% plural %}{{ counter }} plural{% endblocktranslate %}",
    "bt-count-zero": L
    + "{% blocktranslate count counter=zero %}singular{% plural %}{{ counter }} plural{% endblocktranslate %}",
    "bt-with-count": L
    + "{% blocktranslate with a=anton count counter=number %}{{ a }} singular{% plural %}{{ a }} {{ counter }} plural{% endblocktranslate %}",
    "bt-legacy-with-count": L
    + "{% blocktranslate with anton as a count number as counter %}{{ a }} singular{% plural %}{{ a }} {{ counter }} plural{% endblocktranslate %}",
    "bt-counter-both-arms": L
    + "{% blocktranslate count counter=number %}{{ counter }} s{% plural %}{{ counter }} p{% endblocktranslate %}",
    "bt-with-escape": L
    + "{% blocktranslate with berta=anton|escape %}{{ berta }}{% endblocktranslate %}",
    "bt-with-force-escape-hostile": L
    + "{% blocktranslate with berta=hostile|force_escape %}{{ berta }}{% endblocktranslate %}",
    "bt-hostile-placeholder": L + "{% blocktranslate %}{{ hostile }}{% endblocktranslate %}",
    "bt-safe-placeholder": L + "{% blocktranslate %}{{ safe }}{% endblocktranslate %}",
    "bt-markup-text-hostile-placeholder": L
    + "{% blocktranslate %}<b>{{ hostile }}</b>{% endblocktranslate %}",
    "bt-missing-placeholder": L + "{% blocktranslate %}{{ missing }}{% endblocktranslate %}",
    "bt-missing-with": L + "{% blocktranslate with a='α' %}{{ missing }}{% endblocktranslate %}",
    "bt-translate-as-binding-inside": L
    + '{% translate "Page not found" as v %}{% blocktranslate %}{{ v }}{% endblocktranslate %}',
    "bt-asvar-used": L + "{% blocktranslate asvar o %}{{ anton }}{% endblocktranslate %}>{{ o }}<",
    "bt-asvar-unused": L + "{% blocktranslate asvar o %}{{ anton }}{% endblocktranslate %}",
    "bt-asvar-safestring": L
    + "{% blocktranslate asvar o %}<{{hostile}}>{% endblocktranslate %}>{{ o }}<",
    "bt-asvar-in-if": L
    + "{% blocktranslate asvar o %}{{ anton }}{% endblocktranslate %}{% if o %}[{{ o }}]{% endif %}",
    "bt-percent-s-body": L + "{% blocktranslate %}%s{% endblocktranslate %}",
    "bt-percent-after-placeholder": L
    + "{% blocktranslate %}The result was {{ percent }}%{% endblocktranslate %}",
    "bt-percent-paren-is-text": L
    + "{% blocktranslate %}%(percent)s literal {{ percent }}{% endblocktranslate %}",
    "bt-trimmed": L
    + "{% blocktranslate trimmed %}\n  The result\n  was {{ percent }}%\n{% endblocktranslate %}",
    "bt-untrimmed": L
    + "{% blocktranslate %}\n  The result\n  was {{ percent }}%\n{% endblocktranslate %}",
    "bt-adjacent-placeholders": L
    + "{% blocktranslate %}{{ anton }}{{ berta }}{% endblocktranslate %}",
    "bt-placeholder-spacing": L
    + "{% blocktranslate %}{{anton}} and {{ berta  }}{% endblocktranslate %}",
    "bt-templatetag-inside": L
    + "{% blocktranslate %}{% templatetag openblock %}{% endblocktranslate %}",
    "bt-context-yes": L + '{% blocktranslate context "verb" %}Yes{% endblocktranslate %}',
    "bt-in-for": L
    + "{% for x in 'ab' %}{% blocktranslate %}{{ x }}{% endblocktranslate %};{% endfor %}",
    "bt-unicode-body": L + "{% blocktranslate %}ünïcödé {{ anton }} 日本{% endblocktranslate %}",
    "bt-braces-body": L + "{% blocktranslate %}{ {{ anton }} }{% endblocktranslate %}",
    # _("…") in every position
    "us-variable-dq": L + '{{ _("Password") }}',
    "us-variable-sq": L + "{{ _('Password') }}",
    "us-no-load": '{{ _("Password") }}',
    "us-cycle-operand": L
    + '{% cycle "foo" _("Password") _(\'Password\') as c %}{% cycle c %}{% cycle c %}',
    "us-filter-argument": L + '{{ absent|default:_("Password") }}',
    "us-filter-argument-present": L + '{{ var|default:_("Password") }}',
    "us-lt-unescaped": L + '{{ _("<") }}',
    "us-lt-upper-retaints": L + '{{ _("<")|upper }}',
    "us-100-percent-quirk": L + '{{ _("100%") }}',
    "us-yesno-argument": L + "{{ 0|yesno:_('yes,no,maybe') }}",
    "us-if-operand": L + '{% if _("x") %}yes{% endif %}',
    "us-upper": L + '{{ _("Password")|upper }}',
    "us-default-lt": L + '{{ absent|default:_("<") }}',
    "us-with": L + '{% with x=_("Password") %}{{ x }}{% endwith %}',
    "us-firstof": L + '{% firstof _("Password") %}',
    # {% language %} — nesting, every child kind, the number-format re-push
    "lang-translate": L
    + '{% language "de" %}{% translate "Page not found" %}{% endlanguage %}|{% translate "Page not found" %}',
    "lang-number-repush": L + '{% language "de" %}{{ n }}{% endlanguage %}|{{ n }}',
    "lang-nested": L
    + '{% language "de" %}{% language "fr" %}{% translate "Yes" %}{% endlanguage %}|{% translate "Page not found" %}{% endlanguage %}',
    "lang-underscore": L
    + '{% language "de" %}{{ _("Password") }}{% endlanguage %}|{{ _("Password") }}',
    "lang-blocktranslate-and-current": L
    + '{% language "de" %}{% blocktranslate %}{{ anton }}{% endblocktranslate %}{% get_current_language as l %}{{ l }}{% endlanguage %}',
    "lang-variable-operand": L
    + "{% language lang %}{% get_current_language as l %}{{ l }}{% endlanguage %}",
    "lang-none-operand-deactivates": L
    + "{% language None %}{% get_current_language as l %}[{{ l }}]{% endlanguage %}",
    "lang-missing-operand-is-fallback": L
    + "{% language missing %}{% get_current_language as l %}[{{ l }}]{% endlanguage %}",
    "lang-filter-inside": L
    + '{% language "de" %}{{ "de"|language_name_translated }}{% endlanguage %}',
    # get_* tags and the i18n filters
    "get-current-language": L + "{% get_current_language as l %}{{ l }}",
    "get-current-language-bidi": L + "{% get_current_language_bidi as b %}{{ b }}",
    "get-language-info": L
    + '{% get_language_info for "de" as li %}{{ li.name }}/{{ li.name_local }}/{{ li.code }}/{{ li.bidi }}/{{ li.name_translated }}',
    "get-language-info-variable": L + "{% get_language_info for lang as li %}{{ li.name_local }}",
    "get-available-languages": L + "{% get_available_languages as langs %}{{ langs|length }}",
    "get-language-info-list": L
    + "{% get_available_languages as langs %}{% get_language_info_list for langs as l %}{% for x in l %}{{ x.code }};{% endfor %}",
    "filters-language": L
    + '{{ "de"|language_name }}|{{ "de"|language_name_local }}|{{ "de"|language_bidi }}|{{ "de"|language_name_translated }}',
    # tz / l10n scope nodes
    "tz-scope-literal": LT
    + '{% timezone "Europe/Paris" %}{{ dt|date:"H:i T" }}{% endtimezone %}|{{ dt|date:"H:i T" }}',
    "tz-scope-variable": LT + '{% timezone tzname %}{{ dt|date:"H:i T e" }}{% endtimezone %}',
    "tz-scope-current": LT
    + '{% timezone "Europe/Paris" %}{% get_current_timezone as tz %}{{ tz }}{% endtimezone %}|{% get_current_timezone as tz %}{{ tz }}',
    "tz-scope-none-deactivates": LT + '{% timezone None %}{{ dt|date:"H:i T" }}{% endtimezone %}',
    "tz-scope-nested": LT
    + '{% timezone "Asia/Tokyo" %}{% timezone "Europe/Paris" %}{{ dt|date:"H:i T" }}{% endtimezone %}|{{ dt|date:"H:i T" }}{% endtimezone %}',
    "localtime-off": LT
    + '{% localtime off %}{{ dt|date:"H:i T e O" }}{% endlocaltime %}|{{ dt|date:"H:i T" }}',
    "localtime-on": LT + '{% localtime on %}{{ dt|date:"H:i T" }}{% endlocaltime %}',
    "localtime-off-inside-tz": LT
    + '{% timezone "Europe/Paris" %}{% localtime off %}{{ dt|date:"H:i T" }}{% endlocaltime %}|{{ dt|date:"H:i T" }}{% endtimezone %}',
    "localize-off": LT
    + "{% localize off %}{{ i }}/{{ f }}/{{ n }}{% endlocalize %}|{{ i }}/{{ f }}",
    "localize-on": LT + "{% localize on %}{{ i }}/{{ f }}{% endlocalize %}",
    "localize-bare": LT + "{% localize %}{{ i }}{% endlocalize %}",
    "localize-nested": LT
    + "{% localize off %}{% localize on %}{{ n }}{% endlocalize %}|{{ n }}{% endlocalize %}|{{ n }}",
    "localize-filters": LT + "{{ i|localize }}|{{ n|unlocalize }}",
}

#: Shapes where Django's error TEXT is unreachable by construction and only
#: the exception TYPE is pinned — named, not silent.
TYPE_ONLY = {
    # Django's lexer sees `{# c #}` as a COMMENT token and `do_block_translate`
    # refuses it as "other block tags (seen 'c')"; the Rust lexer drops a
    # comment's text, so the body reaches Django without it and renders.
    # Both engines agree this is not a sane msgid; djust renders it.
    "bt-unclosed": (
        L + "{% blocktranslate %}x",
        # Django walks off the end of the token list and reports the LAST
        # token; djust's collector reports the missing end tag.
        "TemplateSyntaxError",
    ),
    "bt-nested-blocktranslate": (
        L
        + "{% blocktranslate %}{% blocktranslate %}x{% endblocktranslate %}{% endblocktranslate %}",
        # The collector stops at the FIRST end tag, so the trailing one is
        # the unsupported tag the parser refuses at construction (#2549);
        # Django refuses the inner open tag. Same type, different text.
        "TemplateSyntaxError",
    ),
}


def _tagged(shapes):
    return sorted(shapes)


@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: str(x))
@pytest.mark.parametrize("shape", _tagged(SHAPES))
def test_plain_backend_matches_django(shape, lang):
    source = SHAPES[shape]
    with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override(lang):
        assert _outcome(plain_render, source) == _outcome(django_render, source)


@pytest.mark.parametrize("lang", ["de", "fr"])
@pytest.mark.parametrize("shape", _tagged(SHAPES))
def test_liveview_entry_matches_django(shape, lang):
    source = SHAPES[shape]
    with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override(lang):
        assert _outcome(liveview_render, source) == _outcome(django_render, source)


@pytest.mark.parametrize("shape", sorted(TYPE_ONLY))
def test_type_only_rows_raise_the_same_type(shape):
    source, expected = TYPE_ONLY[shape]
    theirs = _outcome_class(django_render, source)
    ours = _outcome_class(plain_render, source)
    assert theirs is not None and theirs.__name__ == expected
    # djust raises the `DjustTemplateSyntaxError` SUBCLASS at construction
    # (#2549); Django's `except TemplateSyntaxError` catches both.
    assert ours is not None and issubclass(ours, theirs)


def test_the_differential_is_not_tautological():
    """#1200 / plan §8.6: a harness without ``contrib.admin`` renders English on
    BOTH sides and every parity row passes for the wrong reason. At least one
    row must actually DIFFER between languages, and the German bytes must be
    the admin catalog's."""
    source = L + '{% translate "Page not found" %}|{{ _("Password") }}'
    with translation.override("de"):
        de = plain_render(source, CTX)
    with translation.override(None):
        en = plain_render(source, CTX)
    assert de == "Seite nicht gefunden|Passwort"
    assert en == "Page not found|Password"
    assert de != en
    with translation.override("fr"):
        assert plain_render(L + '{% translate "Yes" %}', CTX) == "Oui"
    with translation.override("nl"):
        assert plain_render(L + '{% translate "No" %}', CTX) == "Nee"


# ---------------------------------------------------------------------------
# Django's own .po fixture: pgettext / npgettext / the `%` rows
# ---------------------------------------------------------------------------

FIXTURE_SHAPES = {
    "pgettext-month": L + '{% translate "May" context "month name" %}',
    "pgettext-verb": L + '{% translate "May" context "verb" %}',
    "pgettext-as": L + '{% translate "May" as v context "verb" %}[{{ v }}]',
    "bt-context-month": L + '{% blocktranslate context "month name" %}May{% endblocktranslate %}',
    "bt-context-verb": L + '{% blocktranslate context "verb" %}May{% endblocktranslate %}',
    "npgettext-1": L
    + '{% blocktranslate count number=num context "super search" %}{{ number }} super result{% plural %}{{ number }} super results{% endblocktranslate %}',
    "npgettext-2": L
    + '{% blocktranslate count number=num2 context "super search" %}{{ number }} super result{% plural %}{{ number }} super results{% endblocktranslate %}',
    "npgettext-other-1": L
    + '{% blocktranslate count number=num context "other super search" %}{{ number }} super result{% plural %}{{ number }} super results{% endblocktranslate %}',
    "npgettext-other-2": L
    + '{% blocktranslate count number=num2 context "other super search" %}{{ number }} super result{% plural %}{{ number }} super results{% endblocktranslate %}',
    "percent-formatting": L
    + "{% blocktranslate %}The result was {{ percent }}%{% endblocktranslate %}",
    "percent-plural-1": L
    + "{% blocktranslate count num=num %}{{ percent }}% represents {{ num }} object{% plural %}{{ percent }}% represents {{ num }} objects{% endblocktranslate %}",
    "percent-plural-2": L
    + "{% blocktranslate count num=num2 %}{{ percent }}% represents {{ num }} object{% plural %}{{ percent }}% represents {{ num }} objects{% endblocktranslate %}",
    "trimmed-catalog": L
    + "{% blocktranslate trimmed %}\n   Hi {{ name }},\n   {{ num }} good result\n{% endblocktranslate %}",
    "with-name-count": L
    + "{% blocktranslate with name=name count num=num2 %}Hi {{ name }}, {{ num }} good result{% plural %}Hi {{ name }}, {{ num }} good results{% endblocktranslate %}",
    "localepaths-time": L
    + '{% translate "Time" %}|{% blocktranslate %}Date/time{% endblocktranslate %}',
}


@pytest.fixture
def fixture_locale():
    path = _fixture_locale()
    with override_settings(LOCALE_PATHS=[path]):
        yield path


@pytest.mark.parametrize("lang", ["de", "fr"])
@pytest.mark.parametrize("shape", sorted(FIXTURE_SHAPES))
def test_django_po_fixture_matches(fixture_locale, shape, lang):
    source = FIXTURE_SHAPES[shape]
    with translation.override(lang):
        theirs = _outcome(django_render, source)
        assert _outcome(plain_render, source) == theirs
        assert _outcome(liveview_render, source) == theirs


def test_django_po_fixture_actually_loaded(fixture_locale):
    with translation.override("de"):
        assert plain_render(FIXTURE_SHAPES["pgettext-verb"], CTX) == "Kann"
        assert plain_render(FIXTURE_SHAPES["pgettext-month"], CTX) == "Mai"
        assert plain_render(FIXTURE_SHAPES["percent-formatting"], CTX) == "Das Ergebnis war 42%"
        assert plain_render(FIXTURE_SHAPES["npgettext-2"], CTX) == "2 Super-Ergebnisse"


# ---------------------------------------------------------------------------
# Escaping / security matrix (plan §3), on both paths
# ---------------------------------------------------------------------------

ESCAPING = {
    # (source, expected bytes) — the expectation is Django's, asserted on
    # Django too so a Django upgrade that moves cannot leave a stale pin.
    "author-literal-raw": (L + '{% translate "<b>" %}', "<b>"),
    "value-escaped": (L + "{% translate amp %}", "a &amp; b"),
    "value-safe-kept": (L + "{% translate safe %}", "<b>ok</b>"),
    "as-binds-escaped-then-safe": (L + "{% translate amp as v %}{{ v }}", "a &amp; b"),
    "bt-text-raw-placeholder-escaped": (
        L + "{% blocktranslate %}<b>{{ hostile }}</b>{% endblocktranslate %}",
        "<b>&lt;img src=x onerror=alert(1)&gt;</b>",
    ),
    "bt-asvar-binds-safestring": (
        L + "{% blocktranslate asvar o %}<b>{{ hostile }}</b>{% endblocktranslate %}{{ o }}",
        "<b>&lt;img src=x onerror=alert(1)&gt;</b>",
    ),
    "underscore-literal-unescaped": (L + '{{ _("<") }}', "<"),
    "underscore-upper-retaints": (L + '{{ _("<")|upper }}', "&lt;"),
    "translate-100-percent-restored": (L + '{% translate "100%" %}', "100%"),
    "underscore-100-percent-quirk": (L + '{{ _("100%") }}', "100%%"),
    "hostile-value-translate": (
        L + "{% translate hostile %}",
        "&lt;img src=x onerror=alert(1)&gt;",
    ),
    "hostile-value-bt": (
        L + "{% blocktranslate %}{{ hostile }}{% endblocktranslate %}",
        "&lt;img src=x onerror=alert(1)&gt;",
    ),
    "hostile-with-filter-bt": (
        L + "{% blocktranslate with h=hostile|lower %}{{ h }}{% endblocktranslate %}",
        "&lt;img src=x onerror=alert(1)&gt;",
    ),
    # Encoded variants (#1825): no decode happens on either side — inert,
    # and RUN rather than reasoned about.
    "encoded-html-entities-stay": (L + "{% translate encoded %}", "&amp;lt;img&amp;gt;"),
    "encoded-percent-stays": (L + "{% translate pct %}", "%3Cimg%3E"),
    "encoded-in-bt": (
        L + "{% blocktranslate %}{{ encoded }}{% endblocktranslate %}",
        "&amp;lt;img&amp;gt;",
    ),
}


@pytest.mark.parametrize("path", sorted(RENDER))
@pytest.mark.parametrize("row", sorted(ESCAPING))
def test_escaping_matrix(row, path):
    source, expected = ESCAPING[row]
    assert django_render(source, CTX) == expected, "the pin drifted from Django itself"
    assert RENDER[path](source, CTX) == expected


def test_catalog_markup_renders_raw_because_it_is_author_content(fixture_locale):
    """A `.po` entry is the project's own text, as unreachable by a request as
    the template source is; a translation carrying markup renders raw on
    Django and here. Nothing user-controlled reaches a msgid except through
    ``{% translate var %}``, whose OUTPUT is escaped whatever the catalog
    answers (the ``hostile-value-*`` rows above)."""
    from django.utils.translation import trans_real

    with translation.override("de"):
        catalog = trans_real.translation("de")
        # Plant a markup translation for this test only; the fixture's
        # gettext object is per-language and cached, so restore after.
        original = catalog._catalog.get("Time")
        catalog._catalog["Time"] = "<em>Zeit</em>"
        try:
            source = L + '{% translate "Time" %}'
            assert django_render(source, CTX) == "<em>Zeit</em>"
            assert plain_render(source, CTX) == "<em>Zeit</em>"
        finally:
            if original is None:
                del catalog._catalog["Time"]
            else:
                catalog._catalog["Time"] = original


class TestAutoescapeOffIsNamedNotFixed:
    """Plan §3(4): the bridge builds ``Context(autoescape=True)`` until #2556
    wires the engine's flag through. The row is a NAMED divergence: this
    test fails the day djust parses ``{% autoescape off %}``, which is the
    signal to replace it with a parity row."""

    SOURCE = "{% autoescape off %}{% load i18n %}{% translate amp %}{% endautoescape %}"

    def test_django_renders_unescaped(self):
        assert django_render(self.SOURCE, CTX) == "a & b"

    def test_djust_still_refuses_the_tag(self):
        outcome = _outcome(plain_render, self.SOURCE)
        assert outcome.startswith("TSE:") and "autoescape" in outcome, (
            "djust now handles {% autoescape off %} — replace this named "
            "divergence with a parity row (#2556)"
        )


# ---------------------------------------------------------------------------
# Scope nodes: restore on exit AND on a raising child (plan §4 / §7.4)
# ---------------------------------------------------------------------------


class _Boom:
    @property
    def boom(self):
        raise RuntimeError("child raised (2558)")


def test_language_scope_restores_on_exit_and_number_format_follows():
    with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("en"):
        source = (
            L
            + '{% language "de" %}{{ n }}|{% translate "Password" %}{% endlanguage %}|{{ n }}|{% translate "Password" %}'
        )
        assert plain_render(source, CTX) == "1.234.567,891|Passwort|1,234,567.891|Password"
        assert translation.get_language() == "en"


def test_language_scope_restores_after_a_raising_child():
    with translation.override("en"):
        source = L + '{% language "de" %}{{ x.boom }}{% endlanguage %}'
        # A bare `RuntimeError` from a context property is not in
        # `rendering._is_user_raised`'s passthrough set, so the engine wraps
        # it — the message crosses, which is all this row needs.
        with pytest.raises(Exception, match="child raised"):
            plain_render(source, {"x": _Boom()})
        assert translation.get_language() == "en"
        # And the Rust locale state was re-pushed on the error path too: the
        # next render on this thread formats as `en`, not `de`.
        with override_settings(USE_THOUSAND_SEPARATOR=True):
            assert plain_render("{{ n }}", CTX) == "1,234,567.891"


def test_language_scope_restores_after_a_library_syntax_error_inside():
    with translation.override("en"):
        source = (
            L
            + '{% language "de" %}{% blocktranslate count c=strnum %}x{% plural %}y{% endblocktranslate %}{% endlanguage %}'
        )
        with pytest.raises(TemplateSyntaxError, match="must be a number"):
            plain_render(source, CTX)
        assert translation.get_language() == "en"


def test_timezone_scope_restores_on_exit_and_after_a_raising_child():
    with dj_timezone.override(zoneinfo.ZoneInfo("Asia/Tokyo")):
        source = (
            LT
            + '{% timezone "Europe/Paris" %}{{ dt|date:"H:i T" }}{% endtimezone %}|{{ dt|date:"H:i T" }}'
        )
        assert plain_render(source, CTX) == "15:20 CEST|22:20 JST"
        assert dj_timezone.get_current_timezone_name() == "Asia/Tokyo"
        with pytest.raises(Exception, match="child raised"):
            plain_render(
                LT + '{% timezone "Europe/Paris" %}{{ x.boom }}{% endtimezone %}', {"x": _Boom()}
            )
        assert dj_timezone.get_current_timezone_name() == "Asia/Tokyo"
        assert plain_render(LT + '{{ dt|date:"H:i T" }}', CTX) == "22:20 JST"


def test_timezone_scope_errors_cross_with_djangos_type():
    with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
        django_render(LT + '{% timezone "Bogus/Zone" %}x{% endtimezone %}', CTX)
    with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
        plain_render(LT + '{% timezone "Bogus/Zone" %}x{% endtimezone %}', CTX)
    # `""` is not `None` to Django's `override`: a ValueError, on both.
    with pytest.raises(ValueError, match="normalized"):
        django_render(LT + "{% timezone tzname %}x{% endtimezone %}", dict(CTX, tzname=""))
    with pytest.raises(ValueError, match="normalized"):
        plain_render(LT + "{% timezone tzname %}x{% endtimezone %}", dict(CTX, tzname=""))


def test_localize_and_localtime_scopes_restore_after_a_raising_child():
    with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
        with pytest.raises(Exception, match="child raised"):
            plain_render(LT + "{% localize off %}{{ x.boom }}{% endlocalize %}", {"x": _Boom()})
        assert plain_render("{{ n }}", CTX) == "1.234.567,891"
        with pytest.raises(Exception, match="child raised"):
            plain_render(LT + "{% localtime off %}{{ x.boom }}{% endlocaltime %}", {"x": _Boom()})
        assert plain_render(LT + '{{ dt|date:"H:i T" }}', CTX) == "13:20 UTC"


def test_localtime_scope_under_use_tz():
    source = (
        LT
        + '{% localtime on %}{{ dt|date:"H:i T" }}{% endlocaltime %}|{% localtime off %}{{ dt|date:"H:i T" }}{% endlocaltime %}'
    )
    with override_settings(USE_TZ=True, TIME_ZONE="Europe/Paris"):
        assert django_render(source, CTX) == "15:20 CEST|13:20 UTC"
        assert plain_render(source, CTX) == "15:20 CEST|13:20 UTC"
        assert liveview_render(source, CTX) == "15:20 CEST|13:20 UTC"


def test_localtime_on_under_use_tz_false_is_a_named_divergence():
    """``{% localtime on %}`` FORCES conversion on Django even when
    ``USE_TZ`` is off (``tz.py:92-106`` passes ``use_tz=True`` to
    ``template_localtime``). djust's ``on`` arm keeps whatever the render env
    pushed, and under ``USE_TZ=False`` that is "no zone" — so the value is not
    converted. Declared, not silent: the ``off`` half and the whole
    ``USE_TZ=True`` matrix agree byte-for-byte (the row above)."""
    source = LT + '{% localtime on %}{{ dt|date:"H:i T" }}{% endlocaltime %}'
    with override_settings(USE_TZ=False, TIME_ZONE="Europe/Paris"):
        assert django_render(source, CTX) == "15:20 CEST"
        assert plain_render(source, CTX) == "13:20 UTC"


def test_localtime_off_names_the_values_own_zone():
    """Plan §4.3: inside ``localtime off`` an aware value is NOT converted and
    ``T`` names its own zone — ``UTC`` / ``UTC+02:00`` for a fixed offset
    (Python's ``datetime.timezone``); a ``ZoneInfo`` value's ``CEST`` is the
    #2216 wire residue and is declared, not silent."""
    fixed = datetime.datetime(
        2011, 9, 1, 13, 20, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
    )
    source = LT + '{% localtime off %}{{ d|date:"H:i T e O" }}{% endlocaltime %}'
    for d in (DT, fixed):
        assert plain_render(source, {"d": d}) == django_render(source, {"d": d})
    zone = datetime.datetime(2011, 9, 1, 13, 20, tzinfo=zoneinfo.ZoneInfo("Europe/Paris"))
    assert django_render(source, {"d": zone}) == "13:20 CEST CEST +0200"
    assert plain_render(source, {"d": zone}) == "13:20 UTC+02:00 UTC+02:00 +0200"


def test_localize_off_under_de_with_thousand_separator():
    with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
        source = LT + "{% localize off %}{{ i }}/{{ f }}{% endlocalize %}|{{ i }}/{{ f }}"
        assert django_render(source, CTX) == "1455/3.14|1.455/3,14"
        assert plain_render(source, CTX) == "1455/3.14|1.455/3,14"
        assert liveview_render(source, CTX) == "1455/3.14|1.455/3,14"


def test_localize_off_date_half_is_the_declared_residue():
    """The DATE half of ``{% localize off %}`` (a bare ``{{ date }}``) is the
    #2221 piece-3 residue: djust renders ISO on both sides of the scope."""
    d = datetime.date(2011, 9, 1)
    source = LT + "{% localize off %}{{ d }}{% endlocalize %}|{{ d }}"
    with translation.override("de"):
        # Django: `use_l10n=False` reads the RAW `DATE_FORMAT` setting
        # (`N j, Y`); outside the block the `de` locale format applies.
        # djust: a date crosses the wire as its ISO string (#2216), so a BARE
        # `{{ d }}` is ISO on both sides of the scope — the #2221 piece-3
        # residue, declared here rather than silently absent.
        assert django_render(source, {"d": d}) == "Sept. 1, 2011|1. September 2011"
        assert plain_render(source, {"d": d}) == "2011-09-01|2011-09-01"


# ---------------------------------------------------------------------------
# Syntax errors, verbatim
# ---------------------------------------------------------------------------

BT_SYNTAX_ERRORS = {
    "asvar-no-name": L + "{% blocktranslate asvar %}x{% endblocktranslate %}",
    "block-inside": L
    + "{% blocktranslate %}Hello {% block b %}world{% endblock %}{% endblocktranslate %}",
    "for-inside": L
    + "{% blocktranslate %}{% for b in [1, 2, 3] %}{{ b }}{% endfor %}{% endblocktranslate %}",
    "with-twice": L + "{% blocktranslate with a=1 with b=2 %}x{% endblocktranslate %}",
    "with-no-kwargs": L + "{% blocktranslate with %}x{% endblocktranslate %}",
    "count-not-kwarg": L + "{% blocktranslate count a %}x{% plural %}y{% endblocktranslate %}",
    "count-not-a-number": L
    + "{% blocktranslate count counter=strnum %}x{% plural %}y{% endblocktranslate %}",
    "count-then-block": L
    + "{% blocktranslate count counter=number %}x{% block b %}y{% endblock %}{% endblocktranslate %}",
}

TRANSLATE_SYNTAX_ERRORS = {
    "no-arguments": L + "{% translate %}",
    "bad-option": L + '{% translate "x" badoption %}',
    "missing-assignment": L + '{% translate "x" as %}',
    "missing-context": L + '{% translate "x" context %}',
    "context-as": L + '{% translate "x" context as %}',
    "context-noop": L + '{% translate "x" context noop %}',
    "duplicate-option": L + '{% translate "x" noop noop %}',
}

SCOPE_SYNTAX_ERRORS = {
    "language-no-arg": (
        L + "{% language %}x{% endlanguage %}",
        "'language' takes one argument (language)",
    ),
    "language-two-args": (
        L + '{% language "de" "fr" %}x{% endlanguage %}',
        "'language' takes one argument (language)",
    ),
    "timezone-no-arg": (
        LT + "{% timezone %}x{% endtimezone %}",
        "'timezone' takes one argument (timezone)",
    ),
    "localtime-bad": (
        LT + "{% localtime foo %}x{% endlocaltime %}",
        "'localtime' argument should be 'on' or 'off'",
    ),
    "localize-bad": (
        LT + "{% localize maybe %}x{% endlocalize %}",
        "'localize' argument should be 'on' or 'off'",
    ),
}


@pytest.mark.parametrize("case", sorted(BT_SYNTAX_ERRORS))
def test_blocktranslate_syntax_errors_are_djangos_text(case):
    source = BT_SYNTAX_ERRORS[case]
    theirs = _outcome(django_render, source)
    assert theirs.startswith("TSE:"), theirs
    assert _outcome(plain_render, source) == theirs
    assert _outcome(liveview_render, source) == theirs


@pytest.mark.parametrize("case", sorted(TRANSLATE_SYNTAX_ERRORS))
def test_translate_syntax_errors_are_djangos_text(case):
    source = TRANSLATE_SYNTAX_ERRORS[case]
    theirs = _outcome(django_render, source)
    assert theirs.startswith("TSE:"), theirs
    assert _outcome(plain_render, source) == theirs


@pytest.mark.parametrize("case", sorted(SCOPE_SYNTAX_ERRORS))
def test_scope_tag_argument_errors_are_djangos_text(case):
    source, message = SCOPE_SYNTAX_ERRORS[case]
    assert _outcome(django_render, source) == "TSE:" + message
    assert _outcome(plain_render, source) == "TSE:" + message


# ---------------------------------------------------------------------------
# The tz-filter refusal pin (#2216 / #2541)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        LT + "{{ dt|localtime }}",
        LT + "{{ dt|utc }}",
        LT + '{{ dt|timezone:"Europe/Paris" }}',
    ],
)
def test_tz_filters_are_refused_loudly_never_blank(source):
    # Django renders the converted datetime; djust cannot carry the object
    # (#2216) and refuses by name rather than answering `""`.
    assert django_render(source, CTX) != ""
    with pytest.raises(Exception, match="needs a datetime object") as info:
        plain_render(source, CTX)
    assert "#2216" in str(info.value)
    with pytest.raises(Exception, match="needs a datetime object"):
        liveview_render(source, CTX)


def test_refused_filter_set_is_exactly_the_tz_three():
    assert template_libraries.refused_filters("django.templatetags.tz") == frozenset(
        {"localtime", "utc", "timezone"}
    )
    assert template_libraries.refused_filters("django.templatetags.l10n") == frozenset()
    assert template_libraries.refused_filters("django.templatetags.i18n") == frozenset()


# ---------------------------------------------------------------------------
# Structural pins (#1125 / #1646)
# ---------------------------------------------------------------------------


def test_blocktranslate_is_registered_through_the_raw_body_kind_only():
    plain_render(L + "{% blocktranslate %}x{% endblocktranslate %}", CTX)
    for name in ("blocktranslate", "blocktrans"):
        assert _rust.has_raw_block_tag_handler(name)
        assert not _rust.has_tag_handler(name)
        assert not _rust.has_block_tag_handler(name)
        assert not _rust.has_assign_tag_handler(name)
    owned = template_libraries.owned_tags()
    assert owned["blocktranslate"] == owned["blocktrans"] == "i18n"


def test_the_two_legacy_spellings_get_distinct_handlers_with_their_own_end_tag():
    plain_render(L + "{% blocktrans %}x{% endblocktrans %}", CTX)
    handlers = {
        name: handler
        for name, (label, handler) in template_libraries._owned_tags.items()
        if name in ("blocktranslate", "blocktrans")
    }
    assert isinstance(handlers["blocktranslate"], template_libraries.LibraryRawBlockTagHandler)
    assert handlers["blocktranslate"] is not handlers["blocktrans"]
    assert handlers["blocktranslate"].end_name == "endblocktranslate"
    assert handlers["blocktrans"].end_name == "endblocktrans"


def test_scope_tags_are_never_installed_as_python_handlers():
    plain_render(LT + '{% language "de" %}x{% endlanguage %}', CTX)
    for name in ("language", "localize", "localtime", "timezone"):
        assert not _rust.has_tag_handler(name), name
        assert not _rust.has_block_tag_handler(name), name
        assert not _rust.has_assign_tag_handler(name), name
        assert not _rust.has_raw_block_tag_handler(name), name
        assert name not in template_libraries.owned_tags()


def test_both_paths_share_one_registry():
    source = L + "{% blocktranslate %}{{ anton }}{% endblocktranslate %}"
    assert plain_render(source, CTX) == liveview_render(source, CTX) == "α"


def test_hooks_are_reinstalled_by_reregister_builtins():
    from djust.template_tags import reregister_builtins

    with translation.override("de"):
        assert plain_render(L + '{{ _("Password") }}', CTX) == "Passwort"
        _rust.clear_translator()
        _rust.register_language_scope_hooks(lambda lang: None, lambda token: None)
        assert plain_render(L + '{{ _("Password") }}', CTX) == "Password"
        assert (
            plain_render(L + '{% language "fr" %}{% translate "Yes" %}{% endlanguage %}', CTX)
            == "Ja"
        )
        reregister_builtins()
        assert plain_render(L + '{{ _("Password") }}', CTX) == "Passwort"
        assert (
            plain_render(L + '{% language "fr" %}{% translate "Yes" %}{% endlanguage %}', CTX)
            == "Oui"
        )


def test_install_sites_are_pinned():
    apps = (REPO / "python" / "djust" / "apps.py").read_text(encoding="utf-8")
    tags = (REPO / "python" / "djust" / "template_tags" / "__init__.py").read_text(encoding="utf-8")
    assert apps.count("install_translator()") == 1
    assert apps.count("install_scope_hooks()") == 1
    # `_register_builtins` (module import) and `reregister_builtins` (the
    # test-isolation restore) both re-arm through the ONE helper.
    assert len(re.findall(r"^ +_install_i18n_hooks\(\)$", tags, re.M)) == 2
    assert "def _install_i18n_hooks" in tags
    # The hooks registered are the module-level functions, so a re-install
    # registers the SAME objects (#1646).
    env = (REPO / "python" / "djust" / "render_env.py").read_text(encoding="utf-8")
    assert "register_language_scope_hooks(language_scope_enter, language_scope_exit)" in env
    assert "register_timezone_scope_hooks(timezone_scope_enter, timezone_scope_exit)" in env
    lib = (REPO / "python" / "djust" / "template_libraries.py").read_text(encoding="utf-8")
    assert "register_translator(translate_msgid)" in lib


def test_collect_raw_source_is_the_one_collector_for_verbatim_and_raw_blocks():
    parser = (CRATE / "parser.rs").read_text(encoding="utf-8")
    assert parser.count("fn collect_raw_source(") == 1
    # Two callers: the `verbatim` arm and the raw-block arm.
    assert len(re.findall(r"= collect_raw_source\(", parser)) == 2
    # The raw-block check sits BEFORE the block-handler dispatch in the
    # fallthrough arm: the body must reach Django un-rendered.
    raw = parser.index("raw_block_handler_exists(tag_name)")
    block = parser.index("block_handler_exists(tag_name)")
    assert raw < block


def test_underscore_literal_is_consulted_at_every_filter_argument_site():
    """#1125: `translate_underscore_arg` is called at the ONE builtin
    filter-argument entry, and the custom-filter literal arm consults
    `django_literal` (which carries the `_()` arm)."""
    renderer = (CRATE / "renderer.rs").read_text(encoding="utf-8")
    filters = (CRATE / "filters.rs").read_text(encoding="utf-8")
    custom = (CRATE / "filter_registry.rs").read_text(encoding="utf-8")
    assert renderer.count("fn translate_underscore_arg(") == 1
    assert filters.count("translate_underscore_arg") == 1
    assert "crate::renderer::django_literal(" in custom
    assert renderer.count('expr.starts_with("_(")') == 1, (
        "the `_()` arm lives in django_literal only"
    )


def test_raw_block_handler_declares_bindings_and_marks_output_safe():
    plain_render(L + "{% blocktranslate %}x{% endblocktranslate %}", CTX)
    handler = template_libraries._owned_tags["blocktranslate"][1]
    assert handler.RETURNS_BINDINGS is True
    with translation.override(None):
        output, bindings = handler.render([], "<b>{{ hostile }}</b>", dict(CTX))
    assert isinstance(output, SafeString)
    assert output == "<b>&lt;img src=x onerror=alert(1)&gt;</b>"
    assert bindings == {}
    with translation.override(None):
        output, bindings = handler.render(["asvar", "o"], "{{ anton }}", dict(CTX))
    assert output == ""
    assert isinstance(bindings["o"], SafeString) and bindings["o"] == "α"


def test_scope_hook_operand_none_and_empty_are_kept_distinct():
    """Plan §4.1 amendment: `None` deactivates, `""` is the fallback language."""
    with translation.override("en"):
        source = L + "{% language x %}{% get_current_language as l %}[{{ l }}]{% endlanguage %}"
        # `None` deactivates (`override(None)` → `deactivate_all`).
        assert django_render(source, {"x": None}) == "[None]"
        assert plain_render(source, {"x": None}) == "[None]"
        # `""` does NOT: `activate("")` returns early, so the OUTER language
        # stands. The two operands must therefore render differently — which
        # is the property a `"" is None` collapse would destroy.
        theirs = django_render(source, {"x": ""})
        assert theirs == "[en]"
        assert plain_render(source, {"x": ""}) == theirs
        assert theirs != django_render(source, {"x": None})


# ---------------------------------------------------------------------------
# Randomized differential against Django (plan §7.2)
# ---------------------------------------------------------------------------

# Braces are SPACE-PADDED: two adjacent `{` runs would spell `{{`, and a `{`
# before a `%(x)s` run would spell `{%`, both of which djust's LEXER reads
# differently from Django's — a pre-existing divergence pinned in
# `test_brace_before_a_tag_is_a_preexisting_lexer_divergence` and deliberately
# not measured by this differential.
_TEXT = [
    "The result",
    " was ",
    "%",
    "%(x)s",
    " { ",
    " } ",
    "ü",
    "\n",
    "\t",
    "100% ",
    "a&b",
    "<i>",
    "",
    " %% ",
]
_PLACEHOLDERS = [
    "{{ anton }}",
    "{{anton}}",
    "{{ berta  }}",
    "{{ missing }}",
    "{{ hostile }}",
    "{{ safe }}",
    "{{ percent }}",
    "{{ counter }}",
    "{{ k }}",
]
_WITH = [
    "with k=anton|lower",
    "with k=berta",
    "with anton|upper as k",
    "with k=hostile",
    "with k='<x>'",
    "with k=n|floatformat:1",
]
# `dec` is absent deliberately: a `Decimal` crosses the wire as a number the
# engine localizes (`Decimal("2")` → `2,0` under `de`), which Django renders
# as `2`. Pre-existing and visible with no i18n tag at all — pinned in
# `test_decimal_formatting_is_a_preexisting_divergence`.
_COUNTS = ["number", "one", "zero", "strnum", "fl", "missing"]
_CONTEXTS = ['context "verb"', "context ctx", 'context "month name"']

SWEEP_CTX = dict(CTX, fl=2.0, dec=Decimal("2"), ctx="verb")


def _random_body(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(1, 5)):
        if rng.random() < 0.45:
            parts.append(rng.choice(_PLACEHOLDERS))
        else:
            parts.append(rng.choice(_TEXT))
    return "".join(parts)


def _random_blocktranslate(rng: random.Random) -> str:
    opts = []
    if rng.random() < 0.4:
        opts.append(rng.choice(_WITH))
    count = rng.random() < 0.4
    if count:
        c = rng.choice(_COUNTS)
        opts.append("count counter=%s" % c if rng.random() < 0.7 else "count %s as counter" % c)
    if rng.random() < 0.25:
        opts.append(rng.choice(_CONTEXTS))
    if rng.random() < 0.2:
        opts.append("trimmed")
    asvar = rng.random() < 0.2
    if asvar:
        opts.append("asvar o")
    name = rng.choice(["blocktranslate", "blocktrans"])
    head = "{%% %s%s %%}" % (name, (" " + " ".join(opts)) if opts else "")
    body = _random_body(rng)
    if count:
        body += "{% plural %}" + _random_body(rng)
    tpl = head + body + "{%% end%s %%}" % name
    if asvar:
        tpl += rng.choice(["[{{ o }}]", "[{{ o|upper }}]", "{% if o %}[{{ o }}]{% endif %}"])
    return L + tpl


_TRANSLATE_OPERANDS = [
    '"Page not found"',
    "'Yes'",
    '"100%"',
    '"<b>"',
    "amp",
    "hostile",
    "safe",
    "missing",
    '"Yes"|upper',
    "amp|lower",
    '"%s"',
]
_TRANSLATE_OPTS = [
    "noop",
    'context "verb"',
    "context var",
    "as v",
    "noop noop",
    "as",
    "context",
    "bogus",
]


def _random_translate(rng: random.Random) -> str:
    opts = rng.sample(_TRANSLATE_OPTS, rng.randint(0, 2))
    rng.shuffle(opts)
    tpl = "{%% %s %s%s %%}" % (
        rng.choice(["translate", "trans"]),
        rng.choice(_TRANSLATE_OPERANDS),
        (" " + " ".join(opts)) if opts else "",
    )
    if "as v" in opts:
        tpl += rng.choice(["[{{ v }}]", "[{{ v|upper }}]"])
    return L + tpl


_US_LITERALS = [
    '_("Password")',
    "_('Password')",
    '_("<")',
    '_("100%")',
    '_("Yes")',
    '_("a b")',
    "_('')",
]


def _random_underscore(rng: random.Random) -> str:
    lit = rng.choice(_US_LITERALS)
    return L + rng.choice(
        [
            "{{ %s }}" % lit,
            "{{ %s|upper }}" % lit,
            "{{ absent|default:%s }}" % lit,
            "{{ var|default:%s }}" % lit,
            '{% cycle "a" ' + lit + " as c %}{% cycle c %}",
            "{% if " + lit + " %}y{% endif %}",
            "{% with x=" + lit + " %}{{ x }}{% endwith %}",
            "{% firstof " + lit + " %}",
        ]
    )


def _sweep(cases, lang):
    mismatches = []
    with translation.override(lang):
        for source in sorted(cases):
            theirs = _outcome(django_render, source, SWEEP_CTX)
            ours = _outcome(plain_render, source, SWEEP_CTX)
            if ours != theirs:
                mismatches.append((source, theirs, ours))
    assert not mismatches, "%d mismatches:\n%s" % (
        len(mismatches),
        "\n".join("%r\n  django: %r\n  djust : %r" % m for m in mismatches[:12]),
    )


@pytest.mark.parametrize("lang", ["de", None])
def test_randomized_blocktranslate_sweep(lang):
    rng = random.Random(2558)
    cases = {_random_blocktranslate(rng) for _ in range(1200)}
    assert len(cases) >= 300
    _sweep(cases, lang)


@pytest.mark.parametrize("lang", ["de", None])
def test_randomized_translate_sweep(lang):
    rng = random.Random(25581)
    cases = {_random_translate(rng) for _ in range(600)}
    assert len(cases) >= 100
    _sweep(cases, lang)


@pytest.mark.parametrize("lang", ["de", None])
def test_randomized_underscore_sweep(lang):
    rng = random.Random(25582)
    cases = {_random_underscore(rng) for _ in range(300)}
    assert len(cases) >= 40
    _sweep(cases, lang)


def test_decimal_formatting_is_a_preexisting_divergence():
    """`Decimal("2")` renders `2` on Django and `2,0` here under `de` — the
    engine localizes it as a number. Visible with NO i18n tag in the template,
    so it is not this row's; the `blocktranslate count` sweep leaves `Decimal`
    out for that reason rather than silently passing."""
    with translation.override("de"):
        assert django_render("{{ dec }}", {"dec": Decimal("2")}) == "2"
        assert plain_render("{{ dec }}", {"dec": Decimal("2")}) == "2,0"


def test_brace_before_a_tag_is_a_preexisting_lexer_divergence():
    """`{{%` is tokenized as a variable start by djust's lexer and as text +
    a tag by Django's. NOT introduced by #2558 and NOT fixed by it (#1079
    scope discipline): the same divergence is visible with no i18n tag in
    sight, which is what these two rows pin. The sweep's alphabet avoids the
    shape so the differential measures the bridge rather than the lexer."""
    assert django_render("{% if a %}x{{% endif %}", {"a": 1}) == "x{"
    with pytest.raises(TemplateSyntaxError, match="Unclosed if tag"):
        plain_render("{% if a %}x{{% endif %}", {"a": 1})
    assert django_render("{{%", {}) == "{{%"
    assert plain_render("{{%", {}) == ""


def test_the_sweep_is_not_vacuous():
    """Gate-off of the harness itself (#2135): a wrong render is visible."""
    rng = random.Random(1)
    source = _random_blocktranslate(rng)
    assert _outcome(django_render, source, SWEEP_CTX) != _outcome(
        lambda s, c: "nonsense", source, SWEEP_CTX
    )
    assert sys.getrecursionlimit() > 100
