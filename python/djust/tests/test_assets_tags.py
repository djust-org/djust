from __future__ import annotations

import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.template import Context, Template
from django.test import override_settings

from djust.assets.manifest import sri
from djust.assets.tags import asset_tags, asset_url, clear_integrity_cache
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


def test_storage_open_failure_falls_back_to_finder_with_a_warning(tmp_path, monkeypatch, caplog):
    """R8: a swallowed storage exception (e.g. the remote-storage credentials/
    network case) must not be silent — it's logged, and the finder fallback
    (runserver before collectstatic) still renders the tag."""
    from django.contrib.staticfiles.storage import staticfiles_storage

    clear_integrity_cache()

    def _raise(*args, **kwargs):
        raise PermissionError("denied")

    # STATIC_URL (set by _settings) resets staticfiles_storage's lazy wrapped
    # instance, so the storage must be patched AFTER entering that context —
    # patching first would be discarded before `asset_tags` ever runs.
    with _settings(tmp_path), caplog.at_level(logging.WARNING, logger="djust.assets"):
        monkeypatch.setattr(staticfiles_storage, "open", _raise)
        html = asset_tags("test-lib")

    assert html == (f'<script src="/static/testlib/lib.js" integrity="{sri(CONTENT)}"></script>')
    assert any(
        record.name == "djust.assets"
        and record.levelno == logging.WARNING
        and "PermissionError" in record.getMessage()
        for record in caplog.records
    )


def test_storage_and_finder_both_failing_reports_the_storage_exception(tmp_path, monkeypatch):
    """R8: when the fallback also can't find the file, the real cause (the
    storage exception) must be in the error message and chained, not hidden
    behind a generic "neither storage nor finder" message."""
    from django.contrib.staticfiles.storage import staticfiles_storage

    clear_integrity_cache()

    def _raise(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("django.contrib.staticfiles.finders.find", lambda path: None)

    with _settings(tmp_path):
        # See the sibling test above: patch AFTER entering, since STATIC_URL
        # resets staticfiles_storage's lazy wrapped instance on entry.
        monkeypatch.setattr(staticfiles_storage, "open", _raise)
        with pytest.raises(ImproperlyConfigured, match="PermissionError") as exc_info:
            asset_tags("test-lib")
    assert isinstance(exc_info.value.__cause__, PermissionError)


def test_component_requires_assets_renders_its_tags(tmp_path):
    from djust.components.base import Component

    class Uses(Component):
        requires_assets = ("test-lib",)
        template = "<div></div>"

    with _settings(tmp_path):
        assert "testlib/lib.js" in Uses().asset_tags()


def test_debug_hashes_the_finder_file_not_a_stale_static_root(tmp_path):
    """runserver serves finder files in DEBUG, so a previously collected (now
    stale) STATIC_ROOT copy must not decide the integrity value — the browser
    would block djust's own assets."""
    root = tmp_path / "collected"
    (root / "testlib").mkdir(parents=True)
    (root / "testlib" / "lib.js").write_bytes(b"console.log('stale');\n")
    with _settings(tmp_path), override_settings(DEBUG=True, STATIC_ROOT=str(root)):
        html = asset_tags("test-lib")
    assert f'integrity="{sri(CONTENT)}"' in html


def test_production_still_hashes_the_stored_file(tmp_path):
    served = b"console.log('collected');\n"
    root = tmp_path / "collected"
    (root / "testlib").mkdir(parents=True)
    (root / "testlib" / "lib.js").write_bytes(served)
    with _settings(tmp_path), override_settings(DEBUG=False, STATIC_ROOT=str(root)):
        html = asset_tags("test-lib")
    assert f'integrity="{sri(served)}"' in html


@pytest.mark.parametrize("debug", [True, False])
def test_unset_static_root_is_not_a_warning(tmp_path, caplog, debug):
    """STATIC_ROOT unset (ImproperlyConfigured from the storage) is the normal
    dev setup, like FileNotFoundError: DEBUG-level, never WARNING."""
    with (
        _settings(tmp_path),
        override_settings(DEBUG=debug, STATIC_ROOT=None),
        caplog.at_level(logging.DEBUG, logger="djust.assets"),
    ):
        html = asset_tags("test-lib")
    assert f'integrity="{sri(CONTENT)}"' in html
    assert not [
        r for r in caplog.records if r.name == "djust.assets" and r.levelno >= logging.WARNING
    ]


@pytest.mark.parametrize("debug", [True, False])
def test_path_outside_static_raises_improperly_configured(tmp_path, debug):
    from djust.assets.tags import stored_integrity

    with _settings(tmp_path), override_settings(DEBUG=debug):
        with pytest.raises(ImproperlyConfigured, match=r"\.\./\.\./x\.js"):
            stored_integrity("../../x.js", "sha384")


def test_documented_component_rendering_patterns(tmp_path):
    """The vendored assets guide's two ways to render requires_assets: the
    page template through the instance, and the component's own template
    via self.asset_tags() from get_context_data."""
    from djust.components.base import Component

    class ChartWidget(Component):
        requires_assets = ("test-lib",)
        template = "<div>{{ asset_tags }}<canvas></canvas></div>"

        def get_context_data(self):
            return {"asset_tags": self.asset_tags()}

    tag = f'<script src="/static/testlib/lib.js" integrity="{sri(CONTENT)}"></script>'
    with _settings(tmp_path):
        chart = ChartWidget()
        page = Template("{{ chart.asset_tags }}").render(Context({"chart": chart}))
        own = chart.render()
    assert page == tag
    assert own == f"<div>{tag}<canvas></canvas></div>"


class BucketStorage:
    """Static storage serving from another origin while STATIC_URL stays
    relative (e.g. a CDN-backed storage that builds its own URLs)."""

    def __new__(cls, *args, **kwargs):
        from django.contrib.staticfiles.storage import StaticFilesStorage

        class _Bucket(StaticFilesStorage):
            def url(self, name):
                return "https://bucket.example/" + name

        return _Bucket(*args, **kwargs)


def test_crossorigin_follows_the_rendered_url_not_static_url(tmp_path):
    storages = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "djust.tests.test_assets_tags.BucketStorage"},
    }
    with _settings(tmp_path), override_settings(STORAGES=storages):
        html = asset_tags("test-lib")
    assert 'src="https://bucket.example/testlib/lib.js"' in html
    assert 'crossorigin="anonymous"' in html


def test_production_does_not_cache_the_finder_fallback_hash(tmp_path):
    """DEBUG off: before collectstatic the source file's hash is used, but it
    must not be pinned — once the stored file exists (and differs), its hash
    is what the browser needs."""
    root = tmp_path / "collected"
    root.mkdir()
    with _settings(tmp_path), override_settings(DEBUG=False, STATIC_ROOT=str(root)):
        assert f'integrity="{sri(CONTENT)}"' in asset_tags("test-lib")
        served = b"console.log('collected');\n"
        (root / "testlib").mkdir()
        (root / "testlib" / "lib.js").write_bytes(served)
        assert f'integrity="{sri(served)}"' in asset_tags("test-lib")


def test_production_caches_the_stored_file_hash(tmp_path, monkeypatch):
    from django.contrib.staticfiles.storage import staticfiles_storage

    root = tmp_path / "collected"
    (root / "testlib").mkdir(parents=True)
    (root / "testlib" / "lib.js").write_bytes(CONTENT)
    with _settings(tmp_path), override_settings(DEBUG=False, STATIC_ROOT=str(root)):
        first = asset_tags("test-lib")

        def _raise(*args, **kwargs):
            raise AssertionError("storage re-read despite a cached hash")

        monkeypatch.setattr(staticfiles_storage, "open", _raise)
        assert asset_tags("test-lib") == first


def test_debug_rehashes_an_edited_vendored_file(tmp_path):
    """DEBUG: the cache is keyed by the finder file's mtime, so rebuilding or
    editing a vendored file changes its integrity without a restart."""
    import os

    with _settings(tmp_path), override_settings(DEBUG=True):
        assert f'integrity="{sri(CONTENT)}"' in asset_tags("test-lib")
        source = tmp_path / "static" / "testlib" / "lib.js"
        rebuilt = b"console.log('rebuilt');\n"
        before = source.stat().st_mtime_ns
        source.write_bytes(rebuilt)
        os.utime(source, ns=(before + 10**9, before + 10**9))
        assert f'integrity="{sri(rebuilt)}"' in asset_tags("test-lib")


def test_debug_rehashes_an_edited_stored_file_no_finder_locates(tmp_path):
    """#3145: DEBUG, and no finder has the file, so the hash comes from static
    storage: editing the stored file must change the integrity without a
    restart."""
    import os

    root = tmp_path / "collected"
    (root / "testlib").mkdir(parents=True)
    stored = root / "testlib" / "lib.js"
    stored.write_bytes(CONTENT)
    with (
        _settings(tmp_path),
        override_settings(DEBUG=True, STATIC_ROOT=str(root), STATICFILES_DIRS=[]),
    ):
        assert f'integrity="{sri(CONTENT)}"' in asset_tags("test-lib")
        edited = b"console.log('recollected');\n"
        before = stored.stat().st_mtime_ns
        stored.write_bytes(edited)
        os.utime(stored, ns=(before + 10**9, before + 10**9))
        assert f'integrity="{sri(edited)}"' in asset_tags("test-lib")


def test_debug_storage_hash_is_not_cached_when_storage_has_no_mtime(tmp_path, monkeypatch):
    """#3145: a storage backend without get_modified_time is re-read every
    time in DEBUG rather than pinned for the life of the process."""
    from django.contrib.staticfiles.storage import staticfiles_storage

    root = tmp_path / "collected"
    (root / "testlib").mkdir(parents=True)
    stored = root / "testlib" / "lib.js"
    stored.write_bytes(CONTENT)

    def _unsupported(name):
        raise NotImplementedError

    with (
        _settings(tmp_path),
        override_settings(DEBUG=True, STATIC_ROOT=str(root), STATICFILES_DIRS=[]),
    ):
        monkeypatch.setattr(staticfiles_storage, "get_modified_time", _unsupported)
        assert f'integrity="{sri(CONTENT)}"' in asset_tags("test-lib")
        edited = b"console.log('recollected');\n"
        stored.write_bytes(edited)
        assert f'integrity="{sri(edited)}"' in asset_tags("test-lib")
