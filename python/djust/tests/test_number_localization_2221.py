"""Rendered numbers must be localized the way Django localizes them (#2221).

The bug
-------
Django localizes a number on its way into the page; the Rust engine used Rust's
defaults. The formatting table lives in
``crates/djust_core/tests/test_number_localization_2221.rs``, pinned against a
live Django 5.2 render across ``en-us`` / ``de`` / ``fr``. What those cases
cannot see is the Python half — whether the active locale's parameters actually
reach Rust on the render path, and whether they are re-read often enough — so
every case here goes through the real ``LiveView.render()``.

Two framings this fix had to get past
-------------------------------------
1. **"Non-English projects only."** ``USE_THOUSAND_SEPARATOR`` applies
   regardless of language, so Django renders ``1,234,567`` where djust rendered
   ``1234567`` **in the default English configuration**.
2. **"``floatformat`` only."** Bare ``{{ n }}`` is affected too — every rendered
   number in every template.

And one flag deliberately NOT read: ``USE_L10N``. It is inert in Django 5.2,
verified across the full ``USE_L10N`` x ``USE_THOUSAND_SEPARATOR`` x language
matrix, where flipping it changed no output.
"""

import pytest
from django.test import RequestFactory, override_settings
from django.utils import translation

from djust import LiveView

NBSP = " "


class _NumView(LiveView):
    template = '<div dj-id="0">{{ n }}</div>'
    _value = 1234.5

    def mount(self, request, **kwargs):
        self.n = type(self)._value


def _render(value=1234.5, template=None):
    attrs = {"_value": value}
    if template is not None:
        attrs["template"] = template
    view = type("_V", (_NumView,), attrs)()
    request = RequestFactory().get("/")
    view.mount(request)
    view.request = request
    return view.render()


@pytest.fixture(autouse=True)
def _reset_language():
    """``deactivate()``, NOT ``deactivate_all()`` — the difference leaks.

    ``deactivate_all()`` installs a ``NullTranslations`` and leaves
    ``get_language()`` returning ``None``, which makes ``get_format`` skip the
    locale modules and fall back to ``global_settings``. There,
    ``NUMBER_GROUPING`` is **0** — so grouping is silently off for every later
    test in the same worker:

        fresh                  get_language()='en-us'  NUMBER_GROUPING=3
        after deactivate()     get_language()='en-us'  NUMBER_GROUPING=3
        after deactivate_all() get_language()=None     NUMBER_GROUPING=0

    This file shipped with ``deactivate_all()`` and poisoned a later test in
    ``test_simple_live_view_2219.py``, which then rendered `1234567` where it
    expected `1,234,567`. Caught only because that test happened to land in the
    same worker — the ordering-dependent flake class this repo keeps paying for
    (#2215, #2187), introduced by the very PR that added these cases.

    ``deactivate()`` restores ``settings.LANGUAGE_CODE``, which is what
    "reset the language" should mean.
    """
    translation.deactivate()
    yield
    translation.deactivate()


# ---------------------------------------------------------------------------
# The bug, including in English.
# ---------------------------------------------------------------------------


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_a_bare_number_is_grouped_in_the_default_english_config():
    # Not an i18n nicety: Django renders '1,234,567' here and djust rendered
    # '1234567'. This is the case that makes the bug affect every project.
    assert "1,234,567" in _render(1234567)


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_floatformat_is_grouped_too():
    assert "1,234.50" in _render(1234.5, '<div dj-id="0">{{ n|floatformat:2 }}</div>')


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="de")
def test_german_swaps_both_separators():
    assert "1.234,5" in _render(1234.5)
    assert "1.234,50" in _render(1234.5, '<div dj-id="0">{{ n|floatformat:2 }}</div>')


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="fr")
def test_french_uses_a_no_break_space():
    # U+00A0, not a plain space. A test written with " " would pass while
    # shipping the wrong byte into every French page.
    out = _render(1234567)
    assert f"1{NBSP}234{NBSP}567" in out
    assert "1 234 567" not in out, "must be U+00A0, not an ordinary space"


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=False, LANGUAGE_CODE="de")
def test_thousand_separator_off_still_localizes_the_decimal_point():
    # The half that is easy to get wrong: the flag suppresses GROUPING only.
    # Django still renders '1234,5' for German.
    out = _render(1234.5)
    assert "1234,5" in out
    assert "1.234,5" not in out


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="de")
def test_the_u_suffix_opts_out_the_way_django_documents():
    # `floatformat:"2u"` is Django's documented escape hatch — and what it
    # produces is exactly what djust produced for every locale before this fix.
    assert "1234.50" in _render(1234.5, '<div dj-id="0">{{ n|floatformat:"2u" }}</div>')


# ---------------------------------------------------------------------------
# The hazard the design had to avoid.
# ---------------------------------------------------------------------------


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_localization_does_not_reach_dict_lookup_keys():
    """``Display`` is the lookup key for ``{% if x in dict %}`` (#2203).

    Localizing there would have been the obvious implementation — it is where
    the number rendering lives — and it would have turned the key ``1234567``
    into ``1,234,567``, breaking every such lookup against a dict Python keyed
    without separators. The localization is applied at the variable-OUTPUT site
    instead, which is also where Django does it
    (``render_value_in_context`` calls ``localize``).

    Asserted rather than assumed, because the failure would be silent: the
    lookup simply misses and the template renders the else-branch.
    """
    view = type(
        "_V",
        (_NumView,),
        {"template": ('<div dj-id="0">{% if n in d %}HIT{% else %}MISS{% endif %}|{{ n }}</div>')},
    )()
    request = RequestFactory().get("/")
    view.mount(request)
    view.n = 1234567
    # An INT-keyed dict, which is what a view keyed by pk actually holds.
    #
    # This case was written string-keyed (`{"1234567": "x"}`) and asserted HIT
    # — which Django answers MISS for, since `1234567 == "1234567"` is False.
    # It passed only because `in` stringified the needle, and #2339 cited THIS
    # test as the reason that coercion could not be removed. Measuring it
    # showed the reverse: an int-keyed dict was not a mapping at all, so the
    # coercion never protected the pk idiom this test is about. With the key
    # type kept (#2339) the idiom works for real, and the case now agrees with
    # Django instead of pinning a divergence.
    view.d = {1234567: "x"}
    view.request = request
    out = view.render()
    assert "HIT" in out, (
        "the dict lookup must still resolve — localizing Display would make the "
        "key '1,234,567' and silently miss"
    )
    assert "1,234,567" in out, "and the OUTPUT must still be localized"


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_a_string_key_is_matched_by_a_string_needle_only():
    """The other half, and the one that keeps the localization guard honest.

    A STRING key is still found by a STRING needle whose text would localize
    if `Display` were the lookup — so the guard this file exists for is still
    exercised — while an INT needle correctly misses it, as Python does.
    """
    tpl = '<div dj-id="0">{% if n in d %}HIT{% else %}MISS{% endif %}</div>'
    view = type("_V", (_NumView,), {"template": tpl})()
    request = RequestFactory().get("/")
    view.mount(request)
    view.n = "1234567"
    view.d = {"1234567": "x"}
    view.request = request
    assert "HIT" in view.render()

    view2 = type("_V", (_NumView,), {"template": tpl})()
    view2.mount(request)
    view2.n = 1234567
    view2.d = {"1234567": "x"}
    view2.request = request
    assert "MISS" in view2.render(), "an int needle must not match a string key"


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_a_string_of_digits_is_left_alone():
    # A user's own text that happens to look numeric is not a number. Localizing
    # it would corrupt account numbers, ZIP codes, IDs and phone numbers.
    assert "1234567" in _render("1234567")


# ---------------------------------------------------------------------------
# WHEN the locale is read.
# ---------------------------------------------------------------------------


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_a_later_language_activation_changes_the_next_render():
    # `translation.activate()` is per-request, exactly like `timezone.activate()`
    # (#2209). A locale captured at app-ready or per view instance would pin the
    # first value and ignore every later switch.
    assert "1,234.5" in _render(1234.5)
    translation.activate("de")
    assert "1.234,5" in _render(1234.5)


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_the_same_view_instance_re_reads_the_locale_between_renders():
    # Sharper: one instance rendered twice. The RustLiveView is session-cached
    # and outlives a request, so a per-instance cache passes the test above
    # while failing this one.
    view = type("_V", (_NumView,), {})()
    request = RequestFactory().get("/")
    view.mount(request)
    view.request = request
    assert "1,234.5" in view.render()
    translation.activate("de")
    assert "1.234,5" in view.render()


@override_settings(USE_I18N=True, USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE="en-us")
def test_the_wiring_reports_the_format_it_applied():
    # A setter with no getter cannot be tested end to end (#2017).
    from djust._rust import active_number_format

    _render()
    decimal_sep, thousand_sep, grouping, use_grouping = active_number_format()
    assert (decimal_sep, thousand_sep, use_grouping) == (".", ",", True)
    assert grouping[0] == 3
