"""{% djust_asset %} renders through the Rust engine's library bridge (#2547)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from django.test import override_settings  # noqa: E402

from djust.tests._asset_fixtures import write_asset  # noqa: E402
from test_load_imports_django_libraries_2547 import liveview_render  # noqa: E402


def test_djust_asset_in_a_liveview_template(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)], STATICFILES_DIRS=[str(static_dir)]
    ):
        out = liveview_render('<div>{% load djust_assets %}{% djust_asset "test-lib" %}</div>', {})
    assert '<script src="/static/testlib/lib.js" integrity="sha384-' in out, _static_state_report(
        out
    )


def _static_state_report(out):
    """What a failure needs to name its cause (#3751): this test failed once on CI with a
    ``/app/``-prefixed URL, and the log said only that the string was absent. The output, the
    script prefix in force, and the two caches that hold the URL it was built from tell a
    polluter in an earlier test (a leaked ``set_script_prefix`` or ``STATIC_URL``) from a
    product bug, and name which cache kept it."""
    from django.conf import settings
    from django.contrib.staticfiles.storage import staticfiles_storage
    from django.templatetags.static import static
    from django.urls import get_script_prefix

    wrapped = staticfiles_storage.__dict__.get("_wrapped")
    # Read the two caches BEFORE ``static()``: it fills them, and a report that warmed them
    # would show its own reading rather than what an earlier test left.
    cached_settings = settings.__dict__.get("STATIC_URL")
    cached_storage = getattr(wrapped, "__dict__", {}).get("base_url")
    try:
        resolved = static("testlib/lib.js")
    except Exception as exc:  # the report must never hide the failure it describes
        resolved = "raised %r" % (exc,)
    return (
        "no <script src=/static/testlib/lib.js> in %r; script prefix %r, static('testlib/lib.js') "
        "-> %r, cached settings.STATIC_URL %r, storage base_url %r"
        % (out, get_script_prefix(), resolved, cached_settings, cached_storage)
    )


def test_the_failure_report_names_a_leaked_script_prefix_and_what_it_cached(monkeypatch):
    """The report is only worth having if it says what the CI failure needed it to say (#3751):
    the output, the prefix in force, what ``static()`` gave, and the two cached URLs.

    ``static`` is stubbed: with a relative ``STATIC_URL`` (the demo project's ``static/``) the
    first real read under a prefix is exactly what leaks it, and a test of the report must not
    be the polluter it exists to find.
    """
    import django.templatetags.static as static_module
    from django.urls import get_script_prefix, set_script_prefix

    monkeypatch.setattr(static_module, "static", lambda path: "/app/static/" + path)
    before = get_script_prefix()
    set_script_prefix("/app/")
    try:
        report = _static_state_report('<script src="/app/static/testlib/lib.js">')
    finally:
        set_script_prefix(before)
    assert "script prefix '/app/'" in report, report
    assert "-> '/app/static/testlib/lib.js'" in report, report
    assert '<script src="/app/static/testlib/lib.js">' in report, report
    assert "cached settings.STATIC_URL" in report and "storage base_url" in report, report
