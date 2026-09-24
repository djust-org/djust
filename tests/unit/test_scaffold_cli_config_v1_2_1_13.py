"""ROADMAP v1.2.1-13: scaffolding, CLI, config and checks.

#2889 (scaffold writes "djust" + V015), #2983 (deploy doctor sqlite),
#2982 (`djust deploy <slug> --from-git`), #2883 (C016 context processors),
#2884 (stale `djust init` comment), #2984 (jit_serialization + C018),
#3006 (U001 advisory cache freshness).
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
from django.test import override_settings

from djust import updates

# --- #2889 ----------------------------------------------------------------------


def _scaffold_settings(tmp_path, **kwargs) -> str:
    from djust.scaffolding.generator import generate_project

    project = generate_project("shop", target_dir=str(tmp_path), auto_setup=False, **kwargs)
    return (project / "shop" / "settings.py").read_text()


@pytest.mark.parametrize("kwargs", [{}, {"with_db": True}, {"bare": True}])
def test_scaffold_allowlist_admits_djust(tmp_path, kwargs):
    text = _scaffold_settings(tmp_path, **kwargs)
    ns: dict = {}
    start = text.index("LIVEVIEW_ALLOWED_MODULES = [")
    exec(text[start : text.index("]", start) + 1], ns)
    assert ns["LIVEVIEW_ALLOWED_MODULES"] == ["shop.views", "djust"]


def _v015(routed):
    from djust.checks.components import _check_routed_djust_views_allowlisted

    errors: list = []
    _check_routed_djust_views_allowlisted(errors, set(routed))
    return [e for e in errors if e.id == "djust.V015"]


def _gallery_view():
    from djust.theming.gallery.live_views import ComponentsIndexView

    return ComponentsIndexView


class _ProjectView:
    __module__ = "shop.views"
    __qualname__ = __name__ = "ProjectView"


def test_v015_fires_when_the_allowlist_blocks_a_routed_djust_view():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["shop.views"]):
        found = _v015([_gallery_view(), _ProjectView])
    assert len(found) == 1
    assert "djust.theming.gallery.live_views.ComponentsIndexView" in found[0].msg
    assert "'djust'" in found[0].hint


@pytest.mark.parametrize(
    "allowed", [None, [], ["shop.views", "djust"], ["shop.views", "djust.theming"]]
)
def test_v015_silent_when_the_view_is_admitted_or_no_allowlist(allowed):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=allowed):
        assert _v015([_gallery_view()]) == []


def test_v015_silent_when_no_djust_view_is_routed():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["shop.views"]):
        assert _v015([_ProjectView]) == []


def test_v015_can_be_suppressed():
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["shop.views"], DJUST_CONFIG={"suppress_checks": ["V015"]}
    ):
        assert _v015([_gallery_view()]) == []


# --- #2983 ----------------------------------------------------------------------


def test_doctor_is_silent_about_the_scaffolds_sqlite(tmp_path):
    from djust.deploy_cli import _deploy_doctor_warnings

    warns = _deploy_doctor_warnings(_scaffold_settings(tmp_path))
    assert not any("sqlite" in w.lower() for w in warns), warns


@pytest.mark.parametrize(
    "name",
    [
        'os.environ.get("DJUST_SQLITE_PATH") or (BASE_DIR / "db.sqlite3")',
        "os.environ['SQLITE_PATH']",
        "os.getenv('SQLITE_PATH', '/data/db.sqlite3')",
    ],
)
def test_doctor_is_silent_when_the_sqlite_name_reads_the_environment(name):
    from djust.deploy_cli import _deploy_doctor_warnings

    text = (
        "DATABASES = {\n"
        "    'default': {\n"
        "        'ENGINE': 'django.db.backends.sqlite3',\n"
        "        'NAME': %s,\n"
        "    }\n"
        "}\n" % name
    )
    assert not any("sqlite" in w.lower() for w in _deploy_doctor_warnings(text))


def test_doctor_still_warns_when_only_another_setting_reads_the_environment():
    from djust.deploy_cli import _deploy_doctor_warnings

    text = (
        "SECRET_KEY = os.environ['SECRET_KEY']\n"
        "DATABASES = {\n"
        "    'default': {\n"
        "        'ENGINE': 'django.db.backends.sqlite3',\n"
        "        'NAME': BASE_DIR / 'db.sqlite3',\n"
        "        'HOST': os.environ.get('UNUSED', ''),\n"
        "    }\n"
        "}\n"
    )
    assert any("sqlite" in w.lower() for w in _deploy_doctor_warnings(text))


# --- #2982 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "rest, argv",
    [
        (["my-app", "--from-git"], ["deploy", "my-app"]),
        (["--from-git", "my-app"], ["deploy", "my-app"]),
        (["my-app", "--from-git", "--yes"], ["deploy", "my-app", "--yes"]),
        (["--from-git"], ["deploy"]),
        (["my-app"], ["deploy-dir", "my-app"]),
        (["--yes"], ["deploy-dir", "--yes"]),
        (["status", "my-app"], ["status", "my-app"]),
    ],
)
def test_deploy_dispatch_routes_from_git_in_any_position(rest, argv):
    from djust import cli
    from djust.deploy_cli import cli as deploy_cli

    with patch.object(deploy_cli, "main") as main:
        cli.cmd_deploy(rest)
    assert main.call_args.kwargs["args"] == argv


@pytest.mark.parametrize("rest", [["my-app", "--from-git"], ["--from-git", "my-app"]])
def test_deploy_slug_and_from_git_parse_in_either_order(rest):
    """The documented `<slug> --from-git` form reaches the `deploy` command
    through real click parsing instead of failing with "No such option"."""
    from djust import cli, deploy_cli

    seen = {}

    def fake_deploy(**kwargs):
        seen.update(kwargs)

    with patch.object(deploy_cli.deploy, "callback", side_effect=fake_deploy):
        assert cli.cmd_deploy(rest) in (0, None)
    assert seen["project_slug"] == "my-app"


# --- #2883 ----------------------------------------------------------------------

_ADMIN_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "djust",
]
_DJANGO_FALLBACK = {
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": False,
    "OPTIONS": {
        "loaders": ["django.template.loaders.app_directories.Loader"],
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}


def _djust_entry(processors):
    return {
        "BACKEND": "djust.template_backend.DjustTemplateBackend",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": processors},
    }


def _c016(templates, apps=_ADMIN_APPS):
    from djust.checks.configuration import _check_templates_shape

    errors: list = []
    with override_settings(TEMPLATES=templates, INSTALLED_APPS=apps):
        _check_templates_shape(errors)
    return [e for e in errors if e.id == "djust.C016"]


def test_c016_flags_a_djust_first_entry_without_the_admin_processors():
    found = _c016([_djust_entry([]), _DJANGO_FALLBACK])
    assert len(found) == 1
    assert "django.contrib.auth.context_processors.auth" in found[0].msg
    assert "KeyError: 'user'" in found[0].msg


def test_c016_names_only_the_missing_processors():
    found = _c016(
        [
            _djust_entry(
                [
                    "django.contrib.auth.context_processors.auth",
                    "django.template.context_processors.request",
                ]
            ),
            _DJANGO_FALLBACK,
        ]
    )
    assert len(found) == 1
    assert "messages" in found[0].msg and "auth.context_processors" not in found[0].msg


def test_c016_silent_for_the_generated_shape():
    procs = _DJANGO_FALLBACK["OPTIONS"]["context_processors"]
    assert _c016([_djust_entry(procs), _DJANGO_FALLBACK]) == []


def test_c016_processor_branch_needs_the_admin():
    apps = [a for a in _ADMIN_APPS if a != "django.contrib.admin"]
    assert _c016([_djust_entry([]), _DJANGO_FALLBACK], apps=apps) == []


# --- #2884 ----------------------------------------------------------------------


def test_init_comment_no_longer_cites_the_fixed_admin_bug_as_a_blocker():
    from djust.scaffolding import templates

    source = inspect.getsource(templates)
    block = source[source.index("settings block appended by") : source.index("SETTINGS_BLOCK =")]
    assert "breaks the admin" not in block
    assert "#2872" in block and "fixed" in block


# --- #2984 ----------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("enabled, jit_called", [(True, True), (False, False)])
def test_jit_serialization_setting_is_honoured(enabled, jit_called):
    from django.contrib.auth.models import User

    import djust.mixins.context as context_module
    from djust.config import config
    from djust.live_view import LiveView

    class ModelView(LiveView):
        template = "<div>{{ profile.username }}</div>"

    view = ModelView()
    view.profile = User(username="alice", pk=1)
    old = config.get("jit_serialization")
    config.set("jit_serialization", enabled)
    try:
        with (
            patch.object(context_module, "JIT_AVAILABLE", True),
            patch.object(view, "_get_template_content", return_value=view.template) as gtc,
        ):
            ctx = view.get_context_data()
    finally:
        config.set("jit_serialization", old)
    assert gtc.called is jit_called
    assert ctx["profile"]["username"] == "alice"
    if not enabled:
        assert view._jit_serialized_keys == set()


def _c018(**settings):
    from djust.checks.configuration import _check_dead_config_keys

    errors: list = []
    with override_settings(**settings):
        _check_dead_config_keys(errors)
    return [e for e in errors if e.id == "djust.C018"]


def test_c018_flags_dead_keys_in_either_dict():
    found = _c018(
        LIVEVIEW_CONFIG={"jit_cache_backend": "redis", "jit_debug": True},
        DJUST_CONFIG={"component_loading_class": "busy"},
    )
    assert len(found) == 1
    assert "LIVEVIEW_CONFIG['jit_cache_backend']" in found[0].msg
    assert "DJUST_CONFIG['component_loading_class']" in found[0].msg
    assert "jit_debug" not in found[0].msg
    assert "1.3" in found[0].hint


def test_c018_silent_for_live_keys():
    assert _c018(LIVEVIEW_CONFIG={"jit_serialization": False, "jit_debug": True}) == []


def test_c018_can_be_suppressed():
    assert (
        _c018(
            LIVEVIEW_CONFIG={"debug_components": True},
            DJUST_CONFIG={"suppress_checks": ["C018"]},
        )
        == []
    )


# --- #3006 ----------------------------------------------------------------------

_ADVISORY = {
    "ghsa_id": "GHSA-aaaa-bbbb-cccc",
    "severity": "critical",
    "range": ">= 1.0, < 1.2.0",
    "patched": "1.2.0",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DJUST_CACHE_DIR", str(tmp_path))
    return {"DJUST_CACHE_DIR": str(tmp_path)}


def _fetchers():
    return (
        patch.object(updates, "fetch_latest", return_value="1.2.0"),
        patch.object(updates, "fetch_advisories", return_value=[]),
    )


def test_matching_cached_advisory_is_rechecked_after_an_hour(env):
    updates.save_cache({"checked_at": 0, "latest": "1.2.0", "advisories": [_ADVISORY]}, env)
    fl, fa = _fetchers()
    with fl as latest, fa:
        early = updates.check(now=1800, environ=env, installed="1.1.0")
        assert not latest.called and [a.ghsa_id for a in early.advisories] == [
            "GHSA-aaaa-bbbb-cccc"
        ]
        # The advisory's range was corrected upstream; an hour later it is gone.
        late = updates.check(now=3601, environ=env, installed="1.1.0")
    assert latest.called
    assert late.advisories == []


def test_non_matching_cache_keeps_the_24h_ttl(env):
    updates.save_cache({"checked_at": 0, "latest": "1.2.0", "advisories": [_ADVISORY]}, env)
    fl, fa = _fetchers()
    with fl as latest, fa:
        status = updates.check(now=6 * 3600, environ=env, installed="1.2.0")
    assert not latest.called
    assert status.advisories == []


def test_failure_backoff_still_applies_to_the_short_ttl(env):
    updates.save_cache({"checked_at": 0, "latest": "1.2.0", "advisories": [_ADVISORY]}, env)
    with patch.object(updates, "fetch_latest", side_effect=OSError("down")) as fetch:
        updates.check(now=4000, environ=env, installed="1.1.0")
        updates.check(now=4000 + 1800, environ=env, installed="1.1.0")
    assert fetch.call_count == 1


def test_u001_hint_names_the_cache_file(monkeypatch, env):
    from djust.checks import updates as check_module

    status = updates.UpdateStatus(
        installed="1.1.0",
        latest="1.2.0",
        advisories=[updates.Advisory.from_dict(_ADVISORY)],
    )
    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    monkeypatch.setattr(updates, "check", lambda **kw: status)
    (message,) = check_module.check_updates(None)
    assert str(updates.cache_path()) in message.hint
    assert "Delete that file" in message.hint
