"""Malformed HTML declarations must not abort template checks (#3430)."""

import pytest
from django.template import Engine

from djust import LiveView
from djust._template_bindings import recovery_scan, scan_source
from djust.checks.bindings import binding_reports, check_event_bindings, coverage


@pytest.mark.parametrize("malformed", ["<![\ue000CDATA[ >", "<!x"])
def test_malformed_inline_template_does_not_abort_checks(malformed):
    class MalformedView(LiveView):
        template = '<button dj-click="missing">before</button>' + malformed

    try:
        report = next(r for r in binding_reports() if r.owner is MalformedView)
        assert report.results[0].binding.name == "missing"
        if malformed.startswith("<!["):
            assert report.gaps
            assert "could not scan" in report.gaps[0].reason
            assert not coverage([report])["templates"][0]["complete"]
            assert recovery_scan(MalformedView)[1] is False
        check_event_bindings(None)
    finally:
        MalformedView.abstract = True


@pytest.mark.parametrize("error", [AssertionError, ValueError])
@pytest.mark.parametrize("method", ["feed", "close"])
def test_parser_failure_is_a_nonfatal_scan_gap(monkeypatch, error, method):
    from djust._template_bindings import _Markup

    def fail(*args):
        raise error("malformed declaration")

    monkeypatch.setattr(_Markup, method, fail)
    engine = Engine()
    source = "<div></div>"
    scan = scan_source(engine, engine.from_string(source), "inline", "inline.html", source)
    assert len(scan.gaps) == 1
    assert "could not scan" in scan.gaps[0].reason
    assert not scan.gaps[0].recovery_safe


@pytest.mark.parametrize("malformed", ["<![\ue000CDATA[ >", "<!x"])
def test_page_shell_scanner_does_not_abort(malformed):
    from djust._page_shell import shell_fingerprint

    source = malformed + "<div dj-root></div>"
    assert shell_fingerprint(source) is None


def test_child_slot_scanner_discards_partial_ownership_graph():
    from djust._child_rendering import _Render

    render = _Render(object(), whole_page=True, owner_wrapper=False)
    render.candidates["child"] = object()
    render.finish('<div dj-root><div dj-sticky-slot="child"></div><![\ue000CDATA[ >')
    assert render.regions == {}


def test_rendered_recovery_scanner_keeps_only_seen_targets(monkeypatch):
    from djust import validation

    class View:
        pass

    view = View()
    monkeypatch.setattr(validation, "_strict_possible", lambda view: True)
    monkeypatch.setattr(validation, "_scan_describes", lambda view: False)
    validation.note_rendered_recovery_targets(
        view,
        '<div dj-auto-recover="before"></div><![\ue000CDATA[ ><div dj-auto-recover="after"></div>',
    )
    assert validation._RENDERED_RECOVERY[view] == frozenset({"before"})


def test_new_lazy_test_views_are_excluded_from_global_binding_discovery():
    from djust.checks.bindings import owner_kinds
    from djust.tests import test_lazy_context_state_3442, test_lazy_expression_literals_3430

    modules = {test_lazy_context_state_3442.__name__, test_lazy_expression_literals_3430.__name__}
    assert not [cls for cls in owner_kinds() if cls.__module__ in modules]


@pytest.mark.parametrize("error", [AssertionError, ValueError])
@pytest.mark.parametrize("method", ["feed", "close"])
@pytest.mark.parametrize("scanner", ["shell", "slots", "recovery"])
def test_other_scanners_handle_parser_failures(monkeypatch, error, method, scanner):
    from html.parser import HTMLParser

    def fail(*args):
        raise error("malformed declaration")

    monkeypatch.setattr(HTMLParser, method, fail)
    if scanner == "shell":
        from djust._page_shell import _entries

        assert _entries("<div dj-root></div>") is None
    elif scanner == "slots":
        from djust._child_rendering import _Render

        render = _Render(object(), whole_page=True, owner_wrapper=False)
        render.candidates["child"] = object()
        render.finish('<div dj-root><div dj-sticky-slot="child"></div></div>')
        assert render.regions == {}
    else:
        from djust import validation

        class View:
            pass

        view = View()
        monkeypatch.setattr(validation, "_strict_possible", lambda view: True)
        monkeypatch.setattr(validation, "_scan_describes", lambda view: False)
        validation.note_rendered_recovery_targets(view, '<div dj-auto-recover="seen"></div>')
        expected = frozenset() if method == "feed" else frozenset({"seen"})
        assert validation._RENDERED_RECOVERY[view] == expected


def test_token_fallback_records_malformed_markup_gap():
    from djust._template_bindings import scan_tokens

    scan = scan_tokens(
        '<![\ue000CDATA[ ><!x<button dj-click="after"></button>', "inline", "inline", "syntax"
    )
    assert any("could not scan" in gap.reason for gap in scan.gaps)
    assert not scan.bindings
