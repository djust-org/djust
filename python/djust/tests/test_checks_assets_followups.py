"""ADR-040 asset-check follow-ups: #3143 (B010 duplicates and the
unpinnable-origin allowlist) and #3144 (B008 must not walk every static file
on each check run)."""

from __future__ import annotations

import pytest
from django.contrib.staticfiles.finders import BaseFinder
from django.core.management import call_command
from django.test import override_settings

from djust.checks.assets import check_undeclared_origins

_BACKEND = "django.template.backends.django.DjangoTemplates"


def _templates(tpl_dir):
    return [{"BACKEND": _BACKEND, "DIRS": [str(tpl_dir)], "APP_DIRS": False}]


def _b010_page(tmp_path, *lines):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text("\n".join(lines) + "\n")
    return _templates(tpl)


# --- #3143 (1): a template reachable twice is reported once ---------------------


def test_b010_reports_a_template_reachable_twice_once(tmp_path):
    """The same directory in two backends, a symlinked alias of it, and a
    parent directory that also contains it: each hit is reported once."""
    root = tmp_path / "templates"
    tpl = root / "site"
    tpl.mkdir(parents=True)
    (tpl / "page.html").write_text('<script src="https://cdn.other.example/x.js"></script>\n')
    alias = tmp_path / "alias"
    alias.symlink_to(tpl, target_is_directory=True)
    templates = [
        {"BACKEND": _BACKEND, "DIRS": [str(tpl), str(alias)], "APP_DIRS": False},
        {"BACKEND": _BACKEND, "DIRS": [str(tpl), str(root)], "APP_DIRS": False, "NAME": "second"},
    ]
    with override_settings(TEMPLATES=templates):
        found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010"]


def test_template_dirs_are_deduplicated_by_real_path(tmp_path):
    """Every template-scanning check (T0xx, Y0xx, ...) shares this helper."""
    from djust.checks.utils import _get_template_dirs

    tpl = tmp_path / "templates"
    tpl.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(tpl, target_is_directory=True)
    templates = [
        {"BACKEND": _BACKEND, "DIRS": [str(tpl), str(alias)], "APP_DIRS": False},
        {"BACKEND": _BACKEND, "DIRS": [str(tpl) + "/"], "APP_DIRS": False, "NAME": "second"},
    ]
    with override_settings(TEMPLATES=templates):
        assert _get_template_dirs() == [str(tpl)]


def test_template_files_are_deduplicated_by_real_path(tmp_path):
    """A directory nested inside another listed directory yields its files once."""
    from djust.checks.utils import _iter_template_files

    root = tmp_path / "templates"
    (root / "site").mkdir(parents=True)
    (root / "site" / "page.html").write_text("x")
    files = list(_iter_template_files([str(root), str(root / "site")]))
    assert len(files) == 1


# --- #3143 (2): DJUST_ALLOWED_EXTERNAL_ORIGINS -----------------------------------


def test_b010_allowlist_accepts_listed_origins_only(tmp_path):
    templates = _b010_page(
        tmp_path,
        '<script src="https://js.stripe.com/v3/"></script>',
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
        # exact host match: a sibling subdomain is not covered
        '<script src="https://m.stripe.com/x.js"></script>',
        '<script src="https://cdn.other.example/x.js"></script>',
    )
    allowed = ("js.stripe.com", "https://Challenges.Cloudflare.com")
    with override_settings(TEMPLATES=templates, DJUST_ALLOWED_EXTERNAL_ORIGINS=allowed):
        found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010", "djust.B010"]
    assert "m.stripe.com" in found[0].msg and "cdn.other.example" in found[1].msg


def test_b010_allowlist_default_is_empty(tmp_path):
    templates = _b010_page(tmp_path, '<script src="https://js.stripe.com/v3/"></script>')
    with override_settings(TEMPLATES=templates):
        assert [m.id for m in check_undeclared_origins(None)] == ["djust.B010"]


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "https://",
        "js.stripe.com:443",
        "https://user@js.stripe.com",
        "https://js.stripe.com/v3/",
        "js.stripe.com/v3",
        "js.stripe.com?x=1",
    ],
    ids=["empty", "scheme-only", "port", "userinfo", "path", "bare-path", "query"],
)
def test_b010_warns_about_an_allowlist_entry_that_is_not_a_bare_host(tmp_path, entry):
    """Such an entry is reported and ignored, not silently accepted."""
    templates = _b010_page(tmp_path, '<script src="https://js.stripe.com/v3/"></script>')
    allowed = ["challenges.cloudflare.com", entry]
    with override_settings(TEMPLATES=templates, DJUST_ALLOWED_EXTERNAL_ORIGINS=allowed):
        found = check_undeclared_origins(None)
    setting = [m for m in found if "DJUST_ALLOWED_EXTERNAL_ORIGINS" in m.msg]
    assert len(setting) == 1 and repr(entry) in setting[0].msg
    assert len([m for m in found if "loads from js.stripe.com" in m.msg]) == 1


def test_b010_allowlist_accepts_a_bare_origin_with_a_trailing_slash(tmp_path):
    templates = _b010_page(tmp_path, '<script src="https://js.stripe.com/v3/"></script>')
    allowed = ["https://js.stripe.com/"]
    with override_settings(TEMPLATES=templates, DJUST_ALLOWED_EXTERNAL_ORIGINS=allowed):
        assert check_undeclared_origins(None) == []


@pytest.mark.parametrize("value", ["js.stripe.com", 42, ["js.stripe.com", 7]])
def test_b010_malformed_allowlist_is_reported_and_allows_nothing(tmp_path, value):
    """A bare string would otherwise be iterated character by character."""
    templates = _b010_page(tmp_path, '<script src="https://js.stripe.com/v3/"></script>')
    with override_settings(TEMPLATES=templates, DJUST_ALLOWED_EXTERNAL_ORIGINS=value):
        found = check_undeclared_origins(None)
    setting = [m for m in found if "DJUST_ALLOWED_EXTERNAL_ORIGINS" in m.msg]
    loads = [m for m in found if "loads from js.stripe.com" in m.msg]
    assert len(setting) == 1 and setting[0].id == "djust.B010"
    assert len(loads) == 1


# --- #3144: B008 must not walk every static file on each check run -------------


class _RecordingFinder(BaseFinder):
    """A staticfiles finder that records every ``list()`` call."""

    calls: list = []

    def check(self, **kwargs):
        return []

    def find(self, path, find_all=False, **kwargs):
        return []  # what Django's own finders return for "not here"

    def list(self, ignore_patterns):
        type(self).calls.append(ignore_patterns)
        return iter([("pkg/vendor.cdx.json", None)])


_FINDER = f"{__name__}._RecordingFinder"


def _collectstatic_owner(monkeypatch, owner):
    """Which app's collectstatic Django resolves, as B013 asks it."""
    from django.core import management

    commands = {**management.get_commands(), "collectstatic": owner}
    monkeypatch.setattr(management, "get_commands", lambda: commands)


def _b008(messages):
    return [m.id for m in messages if "vendor.cdx.json" in m.msg]


def test_b008_does_not_walk_static_files_on_ordinary_check_runs(monkeypatch):
    """djust listed above staticfiles: runserver, migrate and a plain `check`
    run the non-deploy checks, and none of them may list every static file."""
    from django.core.checks import run_checks

    _collectstatic_owner(monkeypatch, "djust")
    _RecordingFinder.calls = []
    with override_settings(STATICFILES_FINDERS=[_FINDER]):
        messages = run_checks()
    assert _RecordingFinder.calls == []
    assert _b008(messages) == []


def test_b008_runs_under_deploy_checks_when_djust_owns_collectstatic(monkeypatch):
    from django.core.checks import run_checks

    _collectstatic_owner(monkeypatch, "djust")
    with override_settings(STATICFILES_FINDERS=[_FINDER]):
        messages = run_checks(include_deployment_checks=True)
    assert _b008(messages) == ["djust.B008"]


@pytest.mark.parametrize("deploy", [False, True], ids=["check", "check-deploy"])
def test_b008_keeps_running_on_every_check_when_djust_does_not_own_collectstatic(
    monkeypatch, deploy
):
    """djust listed after staticfiles: Django's collectstatic wins and never
    runs B008, so the ordinary check pass keeps it, as before #3144, and a
    deploy run reports it once, not twice."""
    from django.core.checks import run_checks

    _collectstatic_owner(monkeypatch, "django.contrib.staticfiles")
    with override_settings(STATICFILES_FINDERS=[_FINDER]):
        messages = run_checks(include_deployment_checks=deploy)
    assert _b008(messages) == ["djust.B008"]


def test_b008_stops_djusts_collectstatic_before_it_publishes(tmp_path):
    from django.core.management.base import SystemCheckError

    from djust.tests.test_assets_app_sbom import APPS_DJUST_FIRST

    static_dir = tmp_path / "static"
    (static_dir / "pkg").mkdir(parents=True)
    (static_dir / "pkg" / "vendor.cdx.json").write_text("{}")
    root = tmp_path / "collected"
    with override_settings(
        STATICFILES_DIRS=[str(static_dir)],
        STATIC_ROOT=str(root),
        INSTALLED_APPS=APPS_DJUST_FIRST,
    ):
        with pytest.raises(SystemCheckError, match="djust.B008"):
            call_command("collectstatic", interactive=False, verbosity=0, skip_checks=False)
    assert not (root / "pkg" / "vendor.cdx.json").exists()
