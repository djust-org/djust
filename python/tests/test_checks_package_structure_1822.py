"""Regression guard for the checks.py -> checks/ package split (#1822).

These tests pin the three contracts the modularization must preserve forever:

1. Discovery — every check still registers under Django's 'djust' tag.
2. Import surface — every public + private symbol the suite historically
   imported from ``djust.checks`` stays importable from the package root.
3. Monkeypatch-by-path — ``patch("djust.checks.<helper>")`` still reaches the
   check that calls the helper, even though the check now lives in a submodule
   (the via-``_root`` indirection). This is the contract that lets ~50 existing
   tests keep patching by path with zero edits; it is also the one most likely
   to silently regress, so it gets an explicit gate-off-sensitive assertion.
"""

import importlib

import pytest

# The 13 registered checks (one per @register("djust")) — the discovery contract.
EXPECTED_CHECKS = {
    "check_configuration",
    "check_service_worker_advanced",
    "check_liveviews",
    "check_sticky_child_optin",
    "check_sticky_child_own_dj_view",
    "check_security",
    "check_accessibility",
    "check_templates",
    "check_code_quality",
    "check_hot_view_replacement",
    "check_time_travel_debugging",
    "check_admin_widgets",
    "check_psycopg3_for_pg_notify",
}

# Private symbols the existing suite imports from / patches on djust.checks.
# Re-export of these is what keeps the rest of the suite green untouched.
HISTORICALLY_IMPORTABLE_PRIVATE = {
    "_build_primitive_return_funcs",
    "_check_non_primitive_assignments_in_mount",
    "_check_service_instances_in_mount",
    "_CLIENT_NAME_SAFE_RE",
    "_DJ_ACTIVITY_NAME_RE",
    "_DJ_ACTIVITY_TAG_RE",
    "_DOC_DISPATCHED_DJUST_EVENTS",
    "_DOC_DJUST_EVENT_RE",
    "_has_asgi_server",
    "_has_multiple_permission_groups",
    "_parse_psycopg_version",
    "_PII_NAME_PATTERN",
    "_routed_liveview_classes",
    "_strip_verbatim_blocks",
    "_get_project_app_dirs",
    "_check_tailwind_cdn_in_production",
    "_check_missing_compiled_css",
    "_check_manual_client_js",
}

# Helpers monkeypatched by the suite via the package namespace. Their callers
# must read them through the root module so the patch takes effect at call time.
MONKEYPATCHED_HELPERS = {
    "_get_project_app_dirs",
    "_has_multiple_permission_groups",
    "_has_asgi_server",
    "_check_tailwind_cdn_in_production",
    "_check_missing_compiled_css",
    "_check_manual_client_js",
}


def test_checks_is_a_package_with_family_submodules():
    import djust.checks as checks

    assert hasattr(checks, "__path__"), "djust.checks must be a package after #1822"
    for sub in (
        "utils",
        "configuration",
        "integrations",
        "components",
        "security",
        "templates",
        "accessibility",
        "quality",
    ):
        importlib.import_module(f"djust.checks.{sub}")


def test_all_checks_registered_under_djust_tag():
    import djust.checks  # noqa: F401 — ensures @register decorators have fired
    from django.core.checks.registry import registry

    registered = {
        c.__name__
        for c in registry.registered_checks
        if "djust" in (getattr(c, "tags", set()) or set())
    }
    missing = EXPECTED_CHECKS - registered
    assert not missing, f"checks no longer registered after #1822 split: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(EXPECTED_CHECKS | HISTORICALLY_IMPORTABLE_PRIVATE))
def test_symbol_still_importable_from_package_root(name):
    import djust.checks as checks

    assert hasattr(checks, name), (
        f"{name} must remain importable as `from djust.checks import {name}` "
        f"after the #1822 package split"
    )


def test_monkeypatch_by_path_reaches_check_in_submodule(settings, monkeypatch):
    """Gate-off-sensitive: patching a helper on the package must reach the check.

    Reverting the via-_root rewrite for ``_has_asgi_server`` makes this fail
    (check_configuration would read the real, uvicorn-backed helper and C003
    would not fire). Mirrors the real pattern at test_checks.py:400.
    """
    settings.ASGI_APPLICATION = "myproject.asgi.application"
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.INSTALLED_APPS = ["django.contrib.staticfiles", "djust"]
    if hasattr(settings, "DJUST_CONFIG"):
        delattr(settings, "DJUST_CONFIG")

    import djust.checks as checks

    monkeypatch.setattr(checks, "_has_asgi_server", lambda: False)
    errors = checks.check_configuration(None)
    c003 = [e for e in errors if e.id == "djust.C003"]
    assert len(c003) == 1, (
        "patch on djust.checks._has_asgi_server must reach check_configuration "
        "(via the _root indirection) — the #1822 zero-test-change contract"
    )


def test_monkeypatched_helpers_are_referenced_via_root():
    """Source pin: every monkeypatched helper's callsites go through _root.

    A future contributor adding a bare-name call to one of these helpers in a
    submodule would silently break patch-by-path; this pins it at the source.
    """
    import djust.checks
    import os
    import re

    pkg_dir = os.path.dirname(djust.checks.__file__)
    offenders = []
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        path = os.path.join(pkg_dir, fname)
        with open(path) as fh:
            for lineno, line in enumerate(fh, 1):
                stripped = line.lstrip()
                # skip the helper's own definition line
                if any(stripped.startswith(f"def {h}(") for h in MONKEYPATCHED_HELPERS):
                    continue
                for h in MONKEYPATCHED_HELPERS:
                    # a bare call `h(` not preceded by `_root.` / word char / dot
                    if re.search(r"(?<![\w.])" + h + r"\(", line):
                        offenders.append(f"{fname}:{lineno}: {stripped.rstrip()}")
    assert not offenders, (
        "monkeypatched helpers must be called via _root.<name>(...) so "
        "patch('djust.checks.<name>') works:\n" + "\n".join(offenders)
    )
