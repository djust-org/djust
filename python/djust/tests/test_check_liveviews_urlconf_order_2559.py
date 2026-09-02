"""``check_liveviews`` must see NON-routed LiveView subclasses even when the
URLconf has not been imported yet (#2559 review, PR #2573).

``check_liveviews`` unions two discovery walks: ``__subclasses__()`` (which
sees a class only once its defining module has been imported) and the root
URLconf (importing which is what imports most view modules in a real
project). Django's check registry is an id-hashed ``set``, so whether
Django's own ``check_url_config`` has already imported the URLconf when
djust's check runs depends on memory layout. The eager package init happened
to land the good order on the demo project; the lazy init (#2559) landed the
bad one and silently dropped 53 messages (545 -> 492) — every non-routed
subclass (``demo_app.views_old.*``, ``sticky_demo.views``, ...) vanished.

The fix evaluates the URLconf walk FIRST. These tests run the check in a
fresh interpreter with the URLconf deliberately NOT imported and assert a
non-routed subclass (defined in a scratch module that only the URLconf
imports) IS reported. Gate-off: swap the two walks back to
``{*_walk_subclasses(LiveView), *_routed_liveview_classes()}`` and
``test_non_routed_subclass_is_reported_when_urlconf_not_yet_imported`` goes
red.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

import djust

# The scratch project: a URLconf that routes ONE LiveView and imports a
# sibling module holding a second, NON-routed LiveView whose ``handle_x``
# method trips V004 (the cheapest per-class check that fires on a bare
# subclass). Nothing but the URLconf imports ``scratch2559_unrouted``.
_SCRATCH_FILES = {
    "scratch2559_settings.py": """
        SECRET_KEY = "x"
        DEBUG = False
        INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
        ROOT_URLCONF = "scratch2559_urls"
        TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}]
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        LIVEVIEW_ALLOWED_MODULES = ["scratch2559_urls", "scratch2559_unrouted"]
    """,
    "scratch2559_urls.py": """
        from django.urls import path
        from djust import LiveView
        import scratch2559_unrouted  # noqa: F401  -- the ONLY importer of the unrouted module

        class Routed(LiveView):
            template_name = "routed.html"

        urlpatterns = [path("routed/", Routed.as_view())]
    """,
    "scratch2559_unrouted.py": """
        from djust import LiveView

        class Unrouted(LiveView):
            template_name = "unrouted.html"

            def handle_click(self, **kwargs):
                pass
    """,
}

# Child program. ``MODE`` selects whether the URLconf is pre-imported.
_CHILD = """
import json, os, sys
import django
os.environ["DJANGO_SETTINGS_MODULE"] = "scratch2559_settings"
django.setup()
mode = sys.argv[1]
if mode == "preimport":
    import scratch2559_urls  # noqa: F401
assert ("scratch2559_urls" in sys.modules) == (mode == "preimport"), mode
assert ("scratch2559_unrouted" in sys.modules) == (mode == "preimport"), mode
from djust.checks.components import check_liveviews
msgs = check_liveviews(None)
print("@@RESULT@@" + json.dumps({
    "ids_for_unrouted": sorted(m.id for m in msgs if "scratch2559_unrouted.Unrouted" in m.msg),
    "ids_for_routed": sorted(m.id for m in msgs if "scratch2559_urls.Routed" in m.msg),
    "urlconf_imported_after": "scratch2559_urls" in sys.modules,
}))
"""


def _run_child(tmp_path, mode: str) -> dict:
    for name, body in _SCRATCH_FILES.items():
        (tmp_path / name).write_text(textwrap.dedent(body))
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(djust.__file__)))
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), pkg_root] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"child failed (rc={proc.returncode}, mode={mode}):\n{proc.stderr}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    assert len(lines) == 1, f"no result line from child:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(lines[0][len("@@RESULT@@") :])


class TestCheckLiveviewsUrlconfOrder:
    def test_non_routed_subclass_is_reported_when_urlconf_not_yet_imported(self, tmp_path):
        """The load-bearing case: URLconf NOT imported before the check runs.

        The routed walk must go first so its URLconf import makes the
        non-routed ``Unrouted`` class visible to the subclass walk.
        """
        report = _run_child(tmp_path, "fresh")
        assert report["urlconf_imported_after"], "the check never imported the URLconf"
        assert "djust.V004" in report["ids_for_unrouted"], (
            "non-routed LiveView subclass was NOT checked when the URLconf was "
            f"un-imported at check time (#2559 review): {report}"
        )
        assert report["ids_for_routed"], "routed view vanished too — harness broken"

    def test_non_routed_subclass_is_reported_when_urlconf_already_imported(self, tmp_path):
        """The order Django's ``check_url_config``-first registry layout gives:
        same result, so the two registry orders can no longer disagree."""
        report = _run_child(tmp_path, "preimport")
        assert "djust.V004" in report["ids_for_unrouted"], report
        assert report["ids_for_routed"], report

    def test_both_registry_orders_report_the_same_ids(self, tmp_path):
        fresh = _run_child(tmp_path, "fresh")
        pre = _run_child(tmp_path, "preimport")
        assert fresh["ids_for_unrouted"] == pre["ids_for_unrouted"]
        assert fresh["ids_for_routed"] == pre["ids_for_routed"]


@pytest.mark.parametrize("mode", ["fresh", "preimport"])
def test_harness_asserts_its_own_import_state(tmp_path, mode):
    """The child asserts the URLconf import state it was asked for, so a
    harness that accidentally pre-imports the URLconf fails loudly rather
    than silently passing the fresh case (#2135)."""
    _run_child(tmp_path, mode)
