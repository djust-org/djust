"""R1 recovery targets are only looked up where they can change a policy (PR #3122 review).

A recovery target downgrades a *strict* handler to legacy (ADR-036 owner
decision R1). Under the default legacy policy it changes nothing, so legacy
apps must not pay for the per-render HTML scan or the class-level template
scan.
"""

from unittest import mock

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust import validation


class LegacyView(LiveView):
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'

    @event_handler()
    def restore(self, **kwargs):
        pass


class OneStrictHandlerView(LiveView):
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'

    @event_handler()
    def restore(self, **kwargs):
        pass

    @event_handler(parameter_policy="strict")
    def save(self, value: str = ""):
        pass


class PlainView(LiveView):
    template = "<div dj-root></div>"

    @event_handler()
    def click(self, **kwargs):
        pass


HTML = '<div dj-root><form dj-auto-recover="restore"></form></div>'


@pytest.fixture
def project_policy():
    from djust.config import config

    previous = config.get("event_parameter_policy", "legacy")

    def set_policy(value):
        config.set("event_parameter_policy", value)

    yield set_policy
    config.set("event_parameter_policy", previous)


def _parser_feeds(view, html=HTML):
    with mock.patch("html.parser.HTMLParser.feed", autospec=True) as feed:
        validation.note_rendered_recovery_targets(view, html)
    return feed.call_count


def test_legacy_project_without_strict_handlers_skips_the_render_scan(project_policy):
    project_policy("legacy")
    assert _parser_feeds(LegacyView()) == 0


def test_strict_project_scans_the_render(project_policy):
    project_policy("strict")
    view = LegacyView()
    validation.note_rendered_recovery_targets(view, HTML)
    assert validation._RENDERED_RECOVERY[view] == frozenset({"restore"})


def test_a_view_with_one_strict_handler_scans_the_render_under_a_legacy_project(project_policy):
    project_policy("legacy")
    view = OneStrictHandlerView()
    validation.note_rendered_recovery_targets(view, HTML)
    assert validation._RENDERED_RECOVERY[view] == frozenset({"restore"})


def test_legacy_policy_lookup_never_runs_the_template_scan(project_policy):
    project_policy("legacy")
    view = LegacyView()
    with mock.patch.object(
        validation, "recovery_handler_names", side_effect=AssertionError("scanned")
    ):
        assert validation.get_handler_parameter_policy(view.restore) == "legacy"


def test_strict_project_still_downgrades_a_recovery_target(project_policy):
    project_policy("strict")
    view = LegacyView()
    assert validation.get_handler_parameter_policy(view.restore) == "legacy"


def test_invalid_project_policy_still_resolves_a_recovery_target_to_legacy(project_policy):
    project_policy("bogus")
    view = LegacyView()
    assert validation.get_handler_parameter_policy(view.restore) == "legacy"


def test_invalid_project_policy_still_raises_for_other_handlers(project_policy):
    project_policy("bogus")
    from djust._parameter_contract import ContractError

    with pytest.raises(ContractError):
        validation.get_handler_parameter_policy(PlainView().click)
