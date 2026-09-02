"""
Regression tests for #2562 — djust.C016 validates the shape of
``settings.TEMPLATES``.

C016 (one id, two shapes, like C003):
  (a) a ``DjangoTemplates`` entry PRECEDES the ``DjustTemplateBackend`` entry,
      so Django's engine (tried in list order) shadows djust;
  (b) there is NO ``DjangoTemplates`` entry at all while ``django.contrib.admin``
      / ``admindocs`` is installed.
  A djust-only project without admin — the ``djust new`` default scaffold — is
  silent (the #1060 dogfood case).

There is no duplicate-NAME check (the issue's C017): Django's own
``check_templates`` / admin ``check_dependencies`` raise ``ImproperlyConfigured``
for that before any djust check runs, so it would be decorative.

Every case calls the REAL registered ``check_configuration`` and filters on id,
so the wiring in ``check_configuration`` is exercised, not just the helper.
Settings are patched directly (the ``test_c014_multi_tenant_asgi`` pattern):
``override_settings(INSTALLED_APPS=[... "django.contrib.admin"])`` would make
Django's app loader import admin, which is not what these checks read.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.core.checks import WARNING
from django.template.backends.django import DjangoTemplates

from djust.checks import check_configuration
from djust.checks.configuration import _check_templates_shape
from djust.management.commands.djust_check import _category_for_check

DJUST = "djust.template_backend.DjustTemplateBackend"
DJUST_LONG = "djust.template.backend.DjustTemplateBackend"
DJANGO = "django.template.backends.django.DjangoTemplates"
MY_DJANGO_SUBCLASS = __name__ + ".MyDjangoTemplates"

BASE_APPS = ["django.contrib.contenttypes", "django.contrib.sessions", "channels", "djust"]
ADMIN_APPS = ["django.contrib.admin", *BASE_APPS]
ADMINDOCS_APPS = ["django.contrib.admindocs", *BASE_APPS]


def _entry(backend: str, **extra):
    entry = {"BACKEND": backend, "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    entry.update(extra)
    return entry


class MyDjangoTemplates(DjangoTemplates):
    """A user subclass of Django's engine — must count as the fallback (case 8)."""


class _SettingsPatcher:
    _SENTINEL = object()

    def __init__(self, overrides):
        from django.conf import settings as dj_settings

        self.target = dj_settings
        self.overrides = overrides
        self.originals = {}

    def __enter__(self):
        for key, value in self.overrides.items():
            self.originals[key] = getattr(self.target, key, self._SENTINEL)
            setattr(self.target, key, value)
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, original in self.originals.items():
            if original is self._SENTINEL:
                try:
                    delattr(self.target, key)
                except AttributeError:
                    pass
            else:
                setattr(self.target, key, original)
        return False


def _run(templates, apps=None, **extra):
    """Run the real registered check under the given TEMPLATES / INSTALLED_APPS."""
    overrides = {"TEMPLATES": templates, "DJUST_CONFIG": {}, **extra}
    if apps is not None:
        overrides["INSTALLED_APPS"] = apps
    with _SettingsPatcher(overrides):
        return check_configuration(None)


def _by_id(errors, check_id):
    return [e for e in errors if getattr(e, "id", "") == check_id]


# ---------------------------------------------------------------------------
# C016 (a) — DjangoTemplates BEFORE djust (shadowing)
# ---------------------------------------------------------------------------


def test_c016_fires_when_django_backend_precedes_djust():
    """Case 1: [django, djust] — the Django engine shadows djust."""
    msgs = _by_id(_run([_entry(DJANGO), _entry(DJUST)]), "djust.C016")
    assert len(msgs) == 1
    assert "before" in msgs[0].msg
    assert msgs[0].level == WARNING
    assert "Reorder TEMPLATES" in msgs[0].fix_hint


# ---------------------------------------------------------------------------
# C016 (b) — no DjangoTemplates fallback while admin needs one
# ---------------------------------------------------------------------------


def test_c016_fires_for_djust_only_with_admin_installed():
    """Case 2: [djust] + django.contrib.admin — no fallback for admin templates."""
    msgs = _by_id(_run([_entry(DJUST)], apps=ADMIN_APPS), "djust.C016")
    assert len(msgs) == 1
    assert "no DjangoTemplates entry" in msgs[0].msg
    assert "admin.E403" in msgs[0].hint


def test_c016_fires_for_djust_only_with_admindocs_only():
    """Case 3: admindocs alone also needs the Django engine."""
    msgs = _by_id(_run([_entry(DJUST)], apps=ADMINDOCS_APPS), "djust.C016")
    assert len(msgs) == 1
    assert "no DjangoTemplates entry" in msgs[0].msg


# ---------------------------------------------------------------------------
# C016 silent shapes — the #1060 dogfood table as code
# ---------------------------------------------------------------------------


def test_c016_silent_for_scaffold_default_djust_only_no_admin():
    """Case 4: `djust new` default (djust-only, no admin) must NOT warn."""
    assert _by_id(_run([_entry(DJUST)], apps=BASE_APPS), "djust.C016") == []


def test_c016_silent_for_djust_then_django_fallback():
    """Case 5: the canonical djust.org / `djust new --with-db` shape."""
    templates = [_entry(DJUST, APP_DIRS=False), _entry(DJANGO, APP_DIRS=True)]
    assert _by_id(_run(templates, apps=ADMIN_APPS), "djust.C016") == []


def test_c016_silent_for_django_only():
    """Case 6: demo / djustlive / tests settings — no djust backend at all."""
    assert _by_id(_run([_entry(DJANGO)], apps=ADMIN_APPS), "djust.C016") == []


def test_c016_detects_djust_backend_via_long_import_path():
    """Case 7: `djust.template.backend.` path is the same class — subclass
    detection, not a string match. (Also proves the ORDER branch does not
    misfire when djust is first.)"""
    templates = [_entry(DJUST_LONG), _entry(DJANGO)]
    assert _by_id(_run(templates, apps=ADMIN_APPS), "djust.C016") == []
    # and the same long path IS recognised as djust when shadowed
    msgs = _by_id(_run([_entry(DJANGO), _entry(DJUST_LONG)]), "djust.C016")
    assert len(msgs) == 1


def test_c016_accepts_user_subclass_of_django_templates_as_fallback():
    """Case 8: issubclass semantics (Django's ``_contains_subclass``)."""
    templates = [_entry(DJUST), _entry(MY_DJANGO_SUBCLASS)]
    assert _by_id(_run(templates, apps=ADMIN_APPS), "djust.C016") == []


# ---------------------------------------------------------------------------
# Robustness + suppression
# ---------------------------------------------------------------------------


def test_unimportable_backend_is_skipped_without_crashing():
    """Case 13: Django's own templates.E00x owns the unimportable-BACKEND report."""
    templates = [_entry("nope.missing.Missing"), _entry(DJUST), _entry(DJANGO)]
    errors = _run(templates, apps=ADMIN_APPS)
    assert _by_id(errors, "djust.C016") == []
    # malformed shapes are skipped too, never raised on
    errors = []
    with _SettingsPatcher({"TEMPLATES": "not-a-list"}):
        _check_templates_shape(errors)
    with _SettingsPatcher({"TEMPLATES": [None, {"BACKEND": 42}, {"BACKEND": "nodots"}]}):
        _check_templates_shape(errors)
    assert errors == []


def test_c016_is_suppressible():
    """Case 14."""
    templates = [_entry(DJANGO), _entry(DJUST)]
    assert _by_id(_run(templates), "djust.C016")  # fires unsuppressed
    errors = _run(templates, DJUST_CONFIG={"suppress_checks": ["C016"]})
    assert _by_id(errors, "djust.C016") == []


# ---------------------------------------------------------------------------
# Real settings files (the dogfood table, #1060)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_settings_module(path: Path) -> dict:
    ns = {"__file__": str(path), "__name__": "scaffold_settings"}
    exec(compile(path.read_text(), str(path), "exec"), ns)  # noqa: S102 — test-only settings load
    return ns


@pytest.mark.parametrize("with_db", [False, True], ids=["default", "with-db"])
def test_real_settings_files_are_silent(tmp_path, with_db):
    """Case 16: the demo project and both `djust new` scaffold shapes."""
    from djust.scaffolding.generator import generate_project

    demo = _load_settings_module(_REPO_ROOT / "examples/demo_project/demo_project/settings.py")
    errors = _run(demo["TEMPLATES"], apps=demo["INSTALLED_APPS"])
    assert _by_id(errors, "djust.C016") == []

    project_dir = generate_project(
        "shapeproj", target_dir=str(tmp_path), with_db=with_db, auto_setup=False
    )
    scaffold = _load_settings_module(project_dir / "shapeproj" / "settings.py")
    backends = [t["BACKEND"] for t in scaffold["TEMPLATES"]]
    assert backends == ([DJUST, DJANGO] if with_db else [DJUST])
    assert ("django.contrib.admin" in scaffold["INSTALLED_APPS"]) is with_db
    errors = _run(scaffold["TEMPLATES"], apps=scaffold["INSTALLED_APPS"])
    assert _by_id(errors, "djust.C016") == []


# ---------------------------------------------------------------------------
# Registry / category pins
# ---------------------------------------------------------------------------


def _ids_emitted_by_module(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(), filename=str(module_path))
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    ids.add(kw.value.value)
    return ids


def test_c016_is_emitted_by_configuration_module_and_maps_to_config_category():
    """The id lives in the C0xx module (the #2070 uniqueness test covers
    collisions), `djust_check --category config` routes it, and no C017 is
    emitted anywhere (dropped on purpose; see _check_templates_shape)."""
    import djust.checks.configuration as cfg

    ids = _ids_emitted_by_module(Path(cfg.__file__))
    assert "djust.C016" in ids
    assert "djust.C017" not in ids
    assert _category_for_check("djust.C016") == "config"
    # C5xx (what the issue proposed) would have been invisible to --category config
    assert _category_for_check("djust.C516") == "other"
