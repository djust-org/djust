"""#4054: a template ``{{ items_count }}`` gets a pointed notice under explicit exposure.

The legacy policy derived ``items_count`` from a supplied ``items`` (a QuerySet
or a list). Explicit exposure (the default) does not, so a 1.x template that
reads it renders empty, silently. The name is not an attribute, so the
ordinary unsupplied-name notice and ``djust.T027`` (both about names the view
*holds*) never saw it. Both now name it and point at ``{{ items|length }}``.
"""

import logging

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from djust import LiveView
from djust.decorators import state
from djust.tests.test_exposure_runtime import make_request

LOGGER = "djust.exposure"


class SuppliedQuerySet(LiveView):
    """The issue's reproduction: a QuerySet handed over, its count read."""

    exposure_policy = "explicit"
    template = (
        "<div dj-root>{% for u in users %}[{{ u.username }}]{% endfor %}"
        "|{{ users|length }}|count={{ users_count }}</div>"
    )

    def mount(self, request, **kwargs):
        self._users = get_user_model().objects.order_by("username")

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "users": self._users}


class DeclaredList(LiveView):
    """A declared list field (rendered by default, #4050) and its count."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ tags|length }}|count={{ tags_count }}</div>"
    tags = state(default_factory=lambda: ["a", "b", "c"])


class LengthFilter(LiveView):
    """The fix the notice recommends: nothing to report."""

    exposure_policy = "explicit"
    template = "<div dj-root>count={{ tags|length }}</div>"
    tags = state(default_factory=lambda: ["a", "b"])


class SuppliesTheCount(LiveView):
    """``tags_count`` supplied by the view: nothing to report."""

    exposure_policy = "explicit"
    template = "<div dj-root>count={{ tags_count }}</div>"
    tags = state(default_factory=lambda: ["a", "b"])

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "tags_count": len(self.tags)}


class NotACollection(LiveView):
    """A ``_count`` name whose stem is a string, or absent: not a legacy count."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ word_count }}|{{ missing_count }}</div>"
    word = state("hello")


class LegacyCount(LiveView):
    """The legacy policy still generates the count: no notice."""

    exposure_policy = "legacy"
    template = "<div dj-root>count={{ tags_count }}</div>"

    def mount(self, request, **kwargs):
        self.tags = ["a", "b"]


def _get(view_class):
    response = view_class().get(make_request())
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def fresh_reports():
    from djust import _exposure_unsupplied

    saved = dict(_exposure_unsupplied._REPORTED)
    _exposure_unsupplied._REPORTED.clear()
    yield
    _exposure_unsupplied._REPORTED.clear()
    _exposure_unsupplied._REPORTED.update(saved)


def _count_records(caplog, view_class):
    return [
        record
        for record in caplog.records
        if record.name == LOGGER
        and view_class.__qualname__ in record.getMessage()
        and "_count }}" in record.getMessage()
    ]


@pytest.fixture
def two_users(db):
    user_model = get_user_model()
    user_model.objects.create(username="ann")
    user_model.objects.create(username="bob")


# ------------------------------------------------------------------ #
# The symptom: explicit exposure renders the count empty
# ------------------------------------------------------------------ #


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_the_count_renders_empty_under_explicit_exposure(two_users):
    html = _get(SuppliedQuerySet)
    assert "[ann][bob]|2|count=<" in html
    assert "count=2" not in html
    assert "3|count=<" in _get(DeclaredList)


# ------------------------------------------------------------------ #
# The DEBUG notice
# ------------------------------------------------------------------ #


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_names_the_count_and_points_at_length_once(caplog, fresh_reports, two_users):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _get(SuppliedQuerySet)
        _get(SuppliedQuerySet)  # once per view class and name
        _get(DeclaredList)
    (queryset_record,) = _count_records(caplog, SuppliedQuerySet)
    message = queryset_record.getMessage()
    assert "{{ users_count }}" in message
    assert "{{ users|length }}" in message
    assert "https://docs.djust.org/guides/migrating-to-2.0/" in message
    # Names only: the QuerySet's rows never appear, and the message is
    # parameterized (CLAUDE.md rule 4), not pre-formatted.
    assert "ann" not in message and "bob" not in message
    assert queryset_record.args
    (list_record,) = _count_records(caplog, DeclaredList)
    assert "{{ tags|length }}" in list_record.getMessage()
    assert "'a'" not in list_record.getMessage()


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_is_quiet_when_the_count_is_supplied_or_not_a_legacy_count(caplog, fresh_reports):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert "count=2" in _get(LengthFilter)
        assert "count=2" in _get(SuppliesTheCount)
        _get(NotACollection)
        assert "count=2" in _get(LegacyCount)
    for view_class in (LengthFilter, SuppliesTheCount, NotACollection, LegacyCount):
        assert _count_records(caplog, view_class) == [], view_class


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_with_debug_off_the_count_check_does_not_run(caplog, fresh_reports, monkeypatch):
    def inspected(*args, **kwargs):
        raise AssertionError("the count check ran with DEBUG off")

    monkeypatch.setattr("djust._exposure_unsupplied.unsupplied_count_names", inspected)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _get(DeclaredList)
    assert [r for r in caplog.records if r.name == LOGGER] == []


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_a_failing_count_check_does_not_break_the_render(caplog, fresh_reports, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("COUNT_SECRET_SENTINEL")

    monkeypatch.setattr("djust._exposure_unsupplied.unsupplied_count_names", broken)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert "count=<" in _get(DeclaredList)
    assert "SENTINEL" not in caplog.text


def test_the_stem_type_test_does_not_evaluate_a_queryset(db, django_assert_num_queries):
    from djust._exposure_unsupplied import unsupplied_count_names

    view = SuppliedQuerySet()
    view.request = make_request()
    queryset = get_user_model().objects.all()
    with django_assert_num_queries(0):
        found = unsupplied_count_names(view, {"users": queryset})
    assert found == [("users_count", "users")]
    assert queryset._result_cache is None


# ------------------------------------------------------------------ #
# djust.T027
# ------------------------------------------------------------------ #


def _t027(view_class):
    from djust.checks.templates import check_unsupplied_template_names

    label = "%s.%s" % (view_class.__module__, view_class.__qualname__)
    return [
        message
        for message in check_unsupplied_template_names(None)
        if message.msg.startswith(label + " ")
    ]


def test_t027_names_a_count_whose_stem_is_supplied():
    (queryset,) = _t027(SuppliedQuerySet)
    assert "'users_count'" in queryset.msg and "'users'" in queryset.msg
    assert "{{ users|length }}" in queryset.hint
    assert "users|length" in queryset.fix_hint
    # A declared field counts as supplied (#4050), so its count is named too.
    (declared,) = _t027(DeclaredList)
    assert "'tags_count'" in declared.msg and "{{ tags|length }}" in declared.hint


def test_t027_is_quiet_when_the_count_is_supplied_or_the_stem_is_not(settings):
    assert _t027(LengthFilter) == []
    assert _t027(SuppliesTheCount) == []
    # word_count: T027 cannot see a type, but the stem is supplied, so it is
    # named; missing_count: nothing supplies the stem, so it is not.
    names = [m.msg.split("reads '")[1].split("'")[0] for m in _t027(NotACollection)]
    assert names == ["word_count"]
    assert _t027(LegacyCount) == []
    settings.DJUST_CONFIG = {"suppress_checks": ["T027"]}
    assert _t027(SuppliedQuerySet) == []
