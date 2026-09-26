from __future__ import annotations

import json
import os

import pytest
from django.test import override_settings

from djust.checks.assets import (
    check_asset_files,
    check_asset_manifests,
    check_required_assets,
    check_sbom_not_served,
    check_undeclared_origins,
)
from djust.tests._asset_fixtures import write_asset


def ids(messages):
    return sorted(m.id for m in messages)


def _with(tmp_path, *manifests, static_dirs=(), **extra):
    return override_settings(
        DJUST_ASSET_MANIFESTS=[str(m) for m in manifests],
        STATICFILES_DIRS=[str(d) for d in static_dirs],
        **extra,
    )


def _templates(tpl_dir):
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tpl_dir)],
            "APP_DIRS": False,
        }
    ]


def test_clean_project_has_no_asset_messages(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with _with(tmp_path, manifest, static_dirs=[static_dir]):
        found = check_asset_manifests(None) + check_asset_files(None)
    assert [m for m in found if "test-lib" in m.msg] == []


def test_b001_b002_b006_come_from_parse_problems(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "schema": 1,
                "assets": {
                    "a": {"files": [], "packages": []},
                    "b": {
                        "files": [{"path": "b.js", "integrity": "sha384-AAAA"}],
                        "packages": [{"purl": "pkg:npm/b", "license": "MIT"}],
                    },
                    "c": {
                        "files": [{"url": "https://x.example/c@1.0.0/c.js"}],
                        "packages": [{"purl": "pkg:npm/c@1.0.0", "license": "MIT"}],
                    },
                },
            }
        )
    )
    with _with(tmp_path, bad):
        found = [m for m in check_asset_manifests(None) if str(bad) in m.msg]
    assert set(ids(found)) == {"djust.B001", "djust.B002", "djust.B006"}


def test_b003_missing_file_and_b004_hash_mismatch(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with _with(tmp_path, manifest):  # no STATICFILES_DIRS: the file can't be found
        assert "djust.B003" in ids(check_asset_files(None))
    (static_dir / "testlib" / "lib.js").write_bytes(b"tampered\n")
    with _with(tmp_path, manifest, static_dirs=[static_dir]):
        (b004,) = [m for m in check_asset_files(None) if m.id == "djust.B004"]
    assert "testlib/lib.js" in b004.msg and "make vendor" in b004.hint


def test_b004_unreadable_vendored_file_is_reported_not_raised(tmp_path, monkeypatch):
    static_dir, manifest = write_asset(tmp_path)
    real_open = open

    def fake_open(file, *args, **kwargs):
        path = os.fspath(file) if hasattr(file, "__fspath__") else file
        if isinstance(path, str) and path.endswith(os.path.join("testlib", "lib.js")):
            raise PermissionError("Permission denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    with _with(tmp_path, manifest, static_dirs=[static_dir]):
        (b004,) = [m for m in check_asset_files(None) if m.id == "djust.B004"]
    assert "could not be read" in b004.msg
    assert "testlib/lib.js" in b004.msg


def test_b003_path_the_finders_refuse_is_reported_not_raised(tmp_path, monkeypatch):
    """SuspiciousFileOperation from finders.find() used to escape the check
    and abort check/runserver/migrate; it is a B003 naming the reason."""
    from django.core.exceptions import SuspiciousFileOperation

    static_dir, manifest = write_asset(tmp_path)

    def refuse(path, *args, **kwargs):
        raise SuspiciousFileOperation("outside of the base path component")

    monkeypatch.setattr("django.contrib.staticfiles.finders.find", refuse)
    with _with(tmp_path, manifest, static_dirs=[static_dir]):
        (b003,) = [m for m in check_asset_files(None) if "test-lib" in m.msg]
    assert b003.id == "djust.B003"
    assert "testlib/lib.js" in b003.msg and "outside of the base path" in b003.msg


def test_b005_external_needs_opt_in(tmp_path):
    manifest = tmp_path / "ext.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "assets": {
                    "cdn-lib": {
                        "files": [
                            {
                                "url": "https://cdn.example/lib@1.0.0/lib.js",
                                "integrity": "sha384-" + "A" * 64,
                            }
                        ],
                        "packages": [{"purl": "pkg:npm/lib@1.0.0", "license": "MIT"}],
                    }
                },
            }
        )
    )
    with _with(tmp_path, manifest):
        assert "djust.B005" in ids(check_asset_files(None))
    with _with(tmp_path, manifest, DJUST_ALLOW_EXTERNAL_ASSETS=True):
        assert "djust.B005" not in ids(check_asset_files(None))


def test_b007_undeclared_required_asset(tmp_path):
    from djust.components.base import Component

    class NeedsGhost(Component):
        requires_assets = ("ghost-lib",)
        template = "<div></div>"

    (b007,) = [m for m in check_required_assets(None) if "ghost-lib" in m.msg]
    assert b007.id == "djust.B007" and "NeedsGhost" in b007.msg


def test_b008_sbom_in_static_dirs(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "pkg").mkdir(parents=True)
    (static_dir / "pkg" / "vendor.cdx.json").write_text("{}")
    with override_settings(STATICFILES_DIRS=[str(static_dir)]):
        (b008,) = [m for m in check_sbom_not_served(None) if "vendor.cdx.json" in m.msg]
    assert b008.id == "djust.B008"


def test_b009_shadowing_names_both_versions(tmp_path):
    s1, first = write_asset(tmp_path / "a", version="2.0.0")
    _, second = write_asset(tmp_path / "b", version="1.0.0")
    with _with(tmp_path, first, second, static_dirs=[s1]):
        (b009,) = [m for m in check_asset_manifests(None) if m.id == "djust.B009"]
    assert "2.0.0" in b009.msg and "1.0.0" in b009.msg


def test_b010_undeclared_cdn_in_project_template(tmp_path):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text(
        '<script src="https://cdn.other.example/x.js"></script>\n'
        '<script src="https://cdn.ok.example/y.js"></script> {# noqa: B010 #}\n'
    )
    with override_settings(TEMPLATES=_templates(tpl)):
        found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010"]
    assert "cdn.other.example" in found[0].msg and "page.html:1" in found[0].msg


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file permissions"
)
def test_b010_unreadable_template_is_skipped_not_raised(tmp_path):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    page = tpl / "page.html"
    page.write_text('<script src="https://cdn.other.example/x.js"></script>\n')
    page.chmod(0)
    try:
        with override_settings(TEMPLATES=_templates(tpl)):
            found = check_undeclared_origins(None)
    finally:
        page.chmod(0o644)
    assert found == []


@pytest.mark.parametrize(
    "snippet",
    [
        # single-quoted attribute
        "<script src='https://cdn.other.example/x.js'></script>",
        # protocol-relative //
        '<script src="//cdn.other.example/x.js"></script>',
        # another attribute precedes src=
        '<script type="text/javascript" src="https://cdn.other.example/x.js"></script>',
        # uppercase tag/attribute
        '<SCRIPT SRC="https://cdn.other.example/x.js"></SCRIPT>',
    ],
    ids=["single-quote", "protocol-relative", "attr-before-src", "uppercase"],
)
def test_b010_positive_variants_still_match(tmp_path, snippet):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text(snippet + "\n")
    with override_settings(TEMPLATES=_templates(tpl)):
        found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010"]
    assert "cdn.other.example" in found[0].msg


def test_b010_ignores_data_src_and_data_href(tmp_path):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text(
        '<script data-src="https://cdn.other.example/x.js" type="text/plain"></script>\n'
        '<link data-href="https://cdn.other.example/y.css">\n'
    )
    with override_settings(TEMPLATES=_templates(tpl)):
        found = check_undeclared_origins(None)
    assert found == []


@pytest.mark.parametrize(
    "snippet",
    [
        '<link rel="canonical" href="https://www.other.example/page/">',
        '<link rel="preconnect" href="https://fonts.other.example">',
        '<link rel="dns-prefetch" href="//fonts.other.example">',
        '<link rel="alternate" type="application/rss+xml" href="https://feeds.other.example/rss">',
        '<link rel="icon" href="https://img.other.example/favicon.ico">',
        '<link href="https://www.other.example/x" rel="manifest">',
    ],
    ids=["canonical", "preconnect", "dns-prefetch", "alternate", "icon", "manifest-rel-last"],
)
def test_b010_ignores_links_that_load_no_resource(tmp_path, snippet):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text(snippet + "\n")
    with override_settings(TEMPLATES=_templates(tpl)):
        assert check_undeclared_origins(None) == []


@pytest.mark.parametrize(
    "snippet",
    [
        '<link rel="stylesheet" href="https://cdn.other.example/x.css">',
        '<link rel="modulepreload" href="https://cdn.other.example/x.mjs">',
        "<link href='https://cdn.other.example/x.js' rel='preload' as='script'>",
        '<link rel="prefetch" href="//cdn.other.example/x.js">',
        '<LINK REL="Alternate Stylesheet" HREF="https://cdn.other.example/alt.css">',
    ],
    ids=["stylesheet", "modulepreload", "preload-rel-last", "prefetch", "multi-token-upper"],
)
def test_b010_flags_links_that_load_a_resource(tmp_path, snippet):
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "page.html").write_text(snippet + "\n")
    with override_settings(TEMPLATES=_templates(tpl)):
        found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010"]
    assert "cdn.other.example" in found[0].msg
