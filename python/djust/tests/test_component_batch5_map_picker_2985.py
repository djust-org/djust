"""MapPicker ships its interaction and a vendored Leaflet (#2985, batch 5).

What has to hold on every render path (the component class, the Django
template tag and the Rust-engine handler): the structure the hook reads is
still emitted, the new tile/attribution/Leaflet attributes are identical
everywhere, nothing a caller passes can break out of an attribute or turn
into markup, and the Leaflet the hook loads is the vendored one (ADR-040):
declared, hashed, with the stylesheet's images shipped and resolvable under
``ManifestStaticFilesStorage``.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

import pytest
from django.template import Context, Template
from django.test import override_settings

from djust.assets.manifest import sri
from djust.assets.registry import get_registry
from djust.components import rust_handlers as rh
from djust.components.components import map_picker as mp
from djust.components.components.map_picker import MapPicker

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"
LEAFLET = STATIC / "vendor" / "leaflet"
HOSTILE = "<img src=x onerror=alert(1)>\"'&<"

# The four attributes the markup always had, in their original order.
LEGACY = (
    '<div class="dj-map-picker" dj-hook="MapPicker" data-lat="40.7128" data-lng="-74.006" '
    'data-zoom="13" data-pick-event="set_location"{extra} style="height:400px" '
    'role="application" aria-label="Map picker"><div class="dj-map-picker__map"></div></div>'
)
ADDED = re.compile(r' data-(tile-url|attribution|attribution-url|max-zoom|leaflet)="[^"]*"')


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _paths(**kw) -> dict[str, str]:
    """The same map on the three render paths, ``kw`` being the new kwargs."""
    args = {"lat": 40.7128, "lng": -74.006, **kw}
    tag_args = " ".join(f"{k}={k}" for k in args)
    return {
        "class": MapPicker(**args).render(),
        "tag": _tag("{% map_picker " + tag_args + " %}", **args),
        "handler": str(rh.MapPickerHandler().render([f"{k}={k}" for k in args], dict(args))),
    }


def _tags(markup: str) -> list[str]:
    from html.parser import HTMLParser

    found: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append(tag)

    P().feed(markup)
    return found


def _event_handler_attrs(markup: str) -> list[str]:
    from html.parser import HTMLParser

    found: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.extend(name for name, _v in attrs if name.startswith("on"))

    P().feed(markup)
    return found


def _attr(markup: str, name: str) -> str:
    found = re.search(rf' {name}="([^"]*)"', markup)
    assert found, (name, markup)
    return html.unescape(found.group(1))


class TestMarkup:
    def test_the_legacy_markup_is_unchanged_apart_from_additive_attributes(self):
        for path, markup in _paths().items():
            assert ADDED.sub("", markup) == LEGACY.format(extra=""), path

    def test_all_three_render_paths_emit_identical_markup(self):
        for kw in (
            {},
            {"tile_url": "https://tiles.example.com/{z}/{x}/{y}.png", "attribution": "(c) Acme"},
            {"tile_url": "", "label": "Pick a depot", "max_zoom": 12},
            {"attribution": HOSTILE, "attribution_url": "https://example.com/a?b=1&c=2"},
        ):
            paths = _paths(**kw)
            assert paths["class"] == paths["tag"] == paths["handler"], kw

    def test_default_is_openstreetmap_with_its_required_attribution(self):
        for path, markup in _paths().items():
            assert (
                _attr(markup, "data-tile-url") == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            ), path
            assert _attr(markup, "data-attribution") == "© OpenStreetMap contributors", path
            assert (
                _attr(markup, "data-attribution-url") == "https://www.openstreetmap.org/copyright"
            ), path
            assert _attr(markup, "data-max-zoom") == "19", path

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_a_blank_attribution_cannot_remove_openstreetmaps_own(self, blank):
        markup = MapPicker(attribution=blank).render()
        assert _attr(markup, "data-attribution") == "\u00a9 OpenStreetMap contributors"

    def test_a_custom_tile_url_does_not_inherit_openstreetmaps_attribution(self):
        markup = MapPicker(tile_url="https://tiles.example.com/{z}/{x}/{y}.png").render()
        assert _attr(markup, "data-attribution") == ""
        assert _attr(markup, "data-attribution-url") == ""

    def test_an_empty_tile_url_is_a_map_with_no_tiles_and_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger=mp.logger.name):
            markup = MapPicker(tile_url="").render()
        assert _attr(markup, "data-tile-url") == ""
        assert "tile_url" not in caplog.text

    def test_the_accessible_name_is_a_kwarg_and_escaped(self):
        for path, markup in _paths(label=HOSTILE).items():
            assert 'aria-label="Map picker"' not in markup, path
            assert "<img" not in markup, path
            assert _attr(markup, "aria-label") == HOSTILE, path

    def test_nothing_a_caller_passes_breaks_out_of_an_attribute(self):
        for path, markup in _paths(
            tile_url=HOSTILE,
            attribution=HOSTILE,
            attribution_url=HOSTILE,
            label=HOSTILE,
            custom_class=HOSTILE,
            pick_event=HOSTILE,
        ).items():
            assert _tags(markup) == ["div", "div"], path
            assert not _event_handler_attrs(markup), path

    @pytest.mark.parametrize(
        "max_zoom, expected", [(18, "18"), (0, "1"), (99, "22"), ("x", "19"), (None, "19")]
    )
    def test_max_zoom_is_clamped_to_a_sane_range(self, max_zoom, expected):
        assert _attr(MapPicker(max_zoom=max_zoom).render(), "data-max-zoom") == expected


class TestTileUrlPolicy:
    GOOD = [
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://{s}.tiles.example.com/{z}/{x}/{y}{r}.png",
        "http://localhost:8080/tiles/{z}/{x}/{y}.png",
        "/tiles/{z}/{x}/{y}.png",
        "  https://tiles.example.com/{z}/{x}/{y}.png  ",
    ]
    BAD = [
        "javascript:alert(1)//{z}/{x}/{y}",
        "data:image/png;base64,AAAA/{z}/{x}/{y}",
        "//evil.example/{z}/{x}/{y}.png",
        "ftp://tiles.example.com/{z}/{x}/{y}.png",
        "https://tiles.example.com/tiles.png",
        "https://tiles.example.com/{z}/{x}.png",
        "https://tiles.example.com/{z}/{x}/{y}/{foo}.png",
        "https://tiles.example.com/{z}/{x}/{y} .png",
        "https://tiles.example.com/{z}/{x}/{y}\n.png",
        "tiles/{z}/{x}/{y}.png",
        "",
        None,
        42,
        ["https://a/{z}/{x}/{y}"],
    ]

    @pytest.mark.parametrize("url", GOOD)
    def test_usable_templates_pass(self, url):
        assert mp.tile_url_or_blank(url) == url.strip()

    @pytest.mark.parametrize("url", BAD)
    def test_unusable_templates_become_no_tiles(self, url):
        assert mp.tile_url_or_blank(url) == ""

    def test_a_rejected_template_is_logged_without_its_value(self, caplog):
        secret = "https://tiles.example.com/KEY-123/tiles.png"
        with caplog.at_level(logging.WARNING, logger=mp.logger.name):
            markup = MapPicker(tile_url=secret).render()
        assert _attr(markup, "data-tile-url") == ""
        assert "tile_url must be" in caplog.text
        assert "KEY-123" not in caplog.text

    def test_the_attribution_link_must_be_http_or_root_relative(self):
        own = "https://tiles.example.com/{z}/{x}/{y}.png"
        for bad in (
            "javascript:alert(1)",
            "data:text/html,x",
            "//evil.example",
            "mailto:a@b.c",
            HOSTILE,
        ):
            markup = MapPicker(tile_url=own, attribution="x", attribution_url=bad).render()
            assert _attr(markup, "data-attribution-url") == "", bad
        good = "https://example.com/copyright"
        markup = MapPicker(tile_url=own, attribution="x", attribution_url=good).render()
        assert _attr(markup, "data-attribution-url") == good

    @pytest.mark.parametrize("link", ["", "javascript:alert(1)", None])
    def test_openstreetmaps_attribution_always_links_to_its_copyright_page(self, link):
        markup = MapPicker(attribution_url=link).render()
        assert _attr(markup, "data-attribution-url") == "https://www.openstreetmap.org/copyright"

    def test_attribution_is_text_and_capped(self):
        markup = MapPicker(attribution="<b>x</b>" + "y" * 1000).render()
        assert "<b>" not in markup
        assert len(_attr(markup, "data-attribution")) == 300


class TestVendoredLeaflet:
    def test_leaflet_is_declared_in_the_components_manifest(self):
        asset = get_registry().assets["leaflet"]
        assert [p.purl for p in asset.packages] == ["pkg:npm/leaflet@1.9.4"]
        assert [p.license for p in asset.packages] == ["BSD-2-Clause"]
        kinds = {f.type: f.path for f in asset.files}
        assert kinds == {
            "script": "djust_components/vendor/leaflet/leaflet.js",
            "style": "djust_components/vendor/leaflet/leaflet.css",
        }
        for f in asset.files:
            assert f.integrity == sri((STATIC.parent / f.path).read_bytes(), "sha384"), f.path
        assert asset.license_file == "djust_components/vendor/leaflet/leaflet.LICENSE.txt"

    def test_the_license_text_is_shipped_and_names_no_version(self):
        text = (LEAFLET / "leaflet.LICENSE.txt").read_text()
        assert "BSD 2-Clause License" in text
        assert "Volodymyr Agafonkin" in text
        assert not re.search(r"\d+\.\d+\.\d+", text)

    def test_every_url_in_the_stylesheet_has_its_file(self):
        css = (LEAFLET / "leaflet.css").read_text()
        urls = [
            u
            for u in re.findall(r"url\(([^)]+)\)", css)
            if not u.strip("'\"").startswith(("#", "data:"))
        ]
        assert urls, "expected leaflet.css to refer to its images"
        for url in urls:
            assert (LEAFLET / url.strip("'\"")).is_file(), url

    def test_vendored_text_files_have_lf_line_endings_whatever_the_checkout(self):
        """leaflet.css and its license are CRLF upstream. They are hashed (SRI,
        djust.cdx.json), so the build writes LF and git is told never to convert
        them: with core.autocrlf=input the committed bytes would otherwise differ
        from the hashed ones on every other checkout."""
        vendored = list((STATIC / "vendor").rglob("*.css")) + list(
            (STATIC / "vendor").rglob("*.LICENSE.txt")
        )
        assert any(f.name == "leaflet.css" for f in vendored)
        for f in vendored:
            assert b"\r" not in f.read_bytes(), f.name
        attributes = (STATIC.parents[4] / ".gitattributes").read_text()
        assert "djust_components/vendor/** -text" in attributes

    def test_the_marker_images_the_hook_uses_are_shipped(self):
        for rel in mp._LEAFLET_ICONS.values():
            assert (STATIC.parent / rel).is_file(), rel

    def test_the_bundle_sets_window_L_and_has_no_remote_urls(self):
        js = (LEAFLET / "leaflet.js").read_text()
        assert "window.L=" in js
        assert not re.search(
            r"https?://(?!www\.w3\.org|leafletjs\.com|www\.openstreetmap)",
            js.replace("https://leafletjs.com", ""),
        )

    def test_hook_gets_the_manifest_urls_and_integrity(self):
        cfg = json.loads(_attr(MapPicker().render(), "data-leaflet"))
        assert cfg["js"].endswith("/djust_components/vendor/leaflet/leaflet.js")
        assert cfg["css"].endswith("/djust_components/vendor/leaflet/leaflet.css")
        assert cfg["jsIntegrity"] == sri((LEAFLET / "leaflet.js").read_bytes(), "sha384")
        assert cfg["cssIntegrity"] == sri((LEAFLET / "leaflet.css").read_bytes(), "sha384")
        assert cfg["crossOrigin"] == ""
        assert set(cfg) == {
            "js",
            "jsIntegrity",
            "css",
            "cssIntegrity",
            "crossOrigin",
            "icon",
            "icon2x",
            "shadow",
        }

    def test_an_absolute_static_url_asks_for_anonymous_cors(self):
        with override_settings(STATIC_URL="https://cdn.example.com/static/"):
            cfg = json.loads(_attr(MapPicker().render(), "data-leaflet"))
        assert cfg["js"].startswith("https://cdn.example.com/static/")
        assert cfg["crossOrigin"] == "anonymous"

    def test_hashed_static_names_work_and_the_stylesheet_images_resolve(self, tmp_path):
        """ManifestStaticFilesStorage renames every file and rewrites the url()s
        in the stylesheet; the hook must get the hashed names and the SRI of
        the stored (rewritten) stylesheet, and the icons must be explicit URLs
        (Leaflet's own path detection cannot read a hashed name)."""
        from django.core.management import call_command

        root = tmp_path / "collected"
        manifest = Path(rh.__file__).resolve().parent / "djust_assets.json"
        with override_settings(
            DJUST_ASSET_MANIFESTS=[str(manifest)],
            STATICFILES_DIRS=[("djust_components/vendor/leaflet", str(LEAFLET))],
            STATIC_ROOT=str(root),
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
                },
            },
            INSTALLED_APPS=["django.contrib.staticfiles", "djust"],
        ):
            from djust.assets.tags import clear_integrity_cache

            clear_integrity_cache()
            call_command("collectstatic", interactive=False, verbosity=0)
            markup = MapPicker().render()
            clear_integrity_cache()
        cfg = json.loads(_attr(markup, "data-leaflet"))
        stored_css = next(root.glob("djust_components/vendor/leaflet/leaflet.*.css"))
        assert re.search(r"leaflet\.[0-9a-f]{12}\.css$", cfg["css"]), cfg["css"]
        assert stored_css.read_bytes() != (LEAFLET / "leaflet.css").read_bytes()
        assert cfg["cssIntegrity"] == sri(stored_css.read_bytes(), "sha384")
        for key in ("icon", "icon2x", "shadow"):
            assert re.search(r"/marker-[a-z0-9-]+\.[0-9a-f]{12}\.png$", cfg[key]), (key, cfg[key])
            assert (root / cfg[key].removeprefix("/static/")).is_file(), cfg[key]
        # The stylesheet's own images were rewritten to the hashed names.
        for name in ("layers", "layers-2x", "marker-icon"):
            hashed = re.search(
                rf'url\("images/{name}\.([0-9a-f]{{12}})\.png"\)', stored_css.read_text()
            )
            assert hashed, name
            assert (stored_css.parent / "images" / f"{name}.{hashed.group(1)}.png").is_file(), name

    def test_an_unresolvable_asset_renders_a_map_without_the_attribute_and_logs(self, caplog):
        from djust.assets import tags

        def boom(*args, **kwargs):
            from django.core.exceptions import ImproperlyConfigured

            raise ImproperlyConfigured("no such asset")

        with pytest.MonkeyPatch.context() as mpatch:
            mpatch.setattr(tags, "asset_source", boom)
            with caplog.at_level(logging.WARNING, logger=mp.logger.name):
                markup = MapPicker().render()
        assert "data-leaflet" not in markup
        assert 'dj-hook="MapPicker"' in markup
        assert "could not be resolved" in caplog.text

    def test_component_declares_the_asset_and_can_render_its_tags(self):
        assert MapPicker.requires_assets == ("leaflet",)
        tags = str(MapPicker().asset_tags())
        assert (
            '<script src="/static/djust_components/vendor/leaflet/leaflet.js" integrity="sha384-'
            in tags
        )
        assert (
            '<link rel="stylesheet" href="/static/djust_components/vendor/leaflet/leaflet.css" integrity="sha384-'
            in tags
        )

    def test_the_startup_check_finds_the_declaration(self):
        from djust.checks.assets import (
            check_asset_files,
            check_asset_manifests,
            check_required_assets,
        )

        assert (
            check_asset_manifests(None) + check_asset_files(None) + check_required_assets(None)
            == []
        )


class TestScriptAndCatalogue:
    SOURCE = (STATIC / "map-picker.js").read_text()

    def test_the_catalogue_finds_the_script_and_the_hook(self):
        from djust.theming.gallery.component_registry import component_client

        client = component_client("map_picker")
        assert client == {
            "hook": "MapPicker",
            "script": "djust_components/map-picker.js",
            "hook_shipped": True,
        }

    @pytest.mark.parametrize(
        "banned",
        [
            r"\binnerHTML\b",
            r"\bouterHTML\b",
            r"insertAdjacentHTML",
            r"\beval\s*\(",
            r"new Function",
            r"document\.write",
            r"\.html\s*\(",
            r"setAttribute\(\s*[\"']style",
        ],
    )
    def test_script_never_parses_markup_or_runs_text(self, banned):
        assert not re.search(banned, self.SOURCE), banned

    def test_script_has_no_hard_coded_tile_host_or_key(self):
        assert "openstreetmap" not in self.SOURCE.lower()
        assert not re.search(r"api[_-]?key|access[_-]?token", self.SOURCE, re.I)

    def test_script_registers_without_replacing_an_app_hook(self):
        assert "window.DjustHooks.MapPicker" in self.SOURCE
        assert "!appHooks.MapPicker && !window.DjustHooks.MapPicker" in self.SOURCE
