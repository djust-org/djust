from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.template import Context, Template
from django.test import override_settings

from djust.assets.manifest import sri
from djust.assets.tags import asset_tags, asset_url
from djust.tests._asset_fixtures import write_asset

CONTENT = b"console.log(1);\n"


def _settings(tmp_path, **extra):
    static_dir, manifest = write_asset(tmp_path, content=CONTENT, **extra)
    return override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)],
        STATICFILES_DIRS=[str(static_dir)],
        STATIC_URL="/static/",
    )


def test_vendored_script_gets_integrity_from_the_file(tmp_path):
    with _settings(tmp_path):
        html = asset_tags("test-lib")
    assert html == (f'<script src="/static/testlib/lib.js" integrity="{sri(CONTENT)}"></script>')


def test_absolute_static_url_adds_crossorigin(tmp_path):
    with _settings(tmp_path), override_settings(STATIC_URL="https://static.example.com/"):
        html = asset_tags("test-lib")
    assert 'crossorigin="anonymous"' in html
    assert 'src="https://static.example.com/testlib/lib.js"' in html


def test_module_renders_modulepreload_and_url(tmp_path):
    with _settings(tmp_path, rel="testlib/lib.mjs"):
        assert asset_tags("test-lib").startswith(
            '<link rel="modulepreload" href="/static/testlib/lib.mjs"'
        )
        assert asset_url("test-lib") == "/static/testlib/lib.mjs"


def test_style_renders_stylesheet_link(tmp_path):
    with _settings(tmp_path, rel="testlib/lib.css"):
        assert asset_tags("test-lib").startswith(
            '<link rel="stylesheet" href="/static/testlib/lib.css"'
        )


def test_unknown_asset_and_variant_fail_loudly(tmp_path):
    with _settings(tmp_path):
        with pytest.raises(ImproperlyConfigured, match="test-lib"):
            asset_tags("nope")
        with pytest.raises(ImproperlyConfigured, match="no variant 'dark'"):
            asset_tags("test-lib", "dark")


def test_integrity_follows_the_stored_file_not_the_source(tmp_path):
    """ManifestStaticFilesStorage rewrites url() in CSS; SRI must hash what is served."""
    css = b'.a{background:url("img.png")}\n'
    static_dir, manifest = write_asset(tmp_path, rel="testlib/lib.css", content=css)
    (static_dir / "testlib" / "img.png").write_bytes(b"png")
    root = tmp_path / "collected"
    storages = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
    }
    with override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)],
        STATICFILES_DIRS=[str(static_dir)],
        STATIC_ROOT=str(root),
        STORAGES=storages,
        # Narrowed from demo_project's full INSTALLED_APPS: djust_rentals ships
        # a vendored lucide.min.js whose sourceMappingURL points at a
        # lucide.min.js.map that was never committed, which makes
        # ManifestStaticFilesStorage post-processing blow up on ANY
        # collectstatic run against the ambient app registry — unrelated to
        # what this test is checking (SRI over the *stored* file).
        INSTALLED_APPS=["django.contrib.staticfiles", "djust"],
    ):
        from django.core.management import call_command

        call_command("collectstatic", interactive=False, verbosity=0)
        served = next(root.glob("testlib/lib.*.css")).read_bytes()
        assert served != css
        html = asset_tags("test-lib")
    assert f'integrity="{sri(served)}"' in html


def test_template_tag(tmp_path):
    with _settings(tmp_path):
        out = Template('{% load djust_assets %}{% djust_asset "test-lib" %}').render(Context())
    assert out.startswith('<script src="/static/testlib/lib.js" integrity="sha384-')


def test_component_requires_assets_renders_its_tags(tmp_path):
    from djust.components.base import Component

    class Uses(Component):
        requires_assets = ("test-lib",)
        template = "<div></div>"

    with _settings(tmp_path):
        assert "testlib/lib.js" in Uses().asset_tags()
