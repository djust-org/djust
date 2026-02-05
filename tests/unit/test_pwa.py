"""
Unit tests for PWA/offline support features.

Tests the templatetags in djust_pwa.py and the generate_sw management command.
"""

import json
import os
import tempfile
from html import unescape
from io import StringIO

from django.template import Template, Context
from django.test import override_settings


class TestDjustPwaManifest:
    """Tests for the djust_pwa_manifest template tag."""

    def test_manifest_with_defaults(self):
        """Test manifest generation with default values."""
        template = Template("{% load djust_pwa %}{% djust_pwa_manifest %}")
        result = template.render(Context({}))

        # Should contain theme-color meta tag
        assert '<meta name="theme-color" content="#007bff">' in result

        # Should contain manifest link with data URI
        assert 'rel="manifest"' in result
        assert "data:application/manifest+json," in result

        # Parse the manifest JSON from the data URI
        manifest_json = unescape(result.split("data:application/manifest+json,")[1].split("'")[0])
        manifest = json.loads(manifest_json)

        assert manifest["name"] == "djust App"
        assert manifest["short_name"] == "djust"
        assert manifest["theme_color"] == "#007bff"
        assert manifest["background_color"] == "#ffffff"
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"

    def test_manifest_with_custom_values(self):
        """Test manifest generation with custom values."""
        template = Template(
            "{% load djust_pwa %}"
            '{% djust_pwa_manifest name="My App" short_name="MyApp" '
            'theme_color="#ff5500" background_color="#000000" display="fullscreen" %}'
        )
        result = template.render(Context({}))

        # Should contain custom theme-color
        assert '<meta name="theme-color" content="#ff5500">' in result

        # Parse manifest
        manifest_json = unescape(result.split("data:application/manifest+json,")[1].split("'")[0])
        manifest = json.loads(manifest_json)

        assert manifest["name"] == "My App"
        assert manifest["short_name"] == "MyApp"
        assert manifest["theme_color"] == "#ff5500"
        assert manifest["background_color"] == "#000000"
        assert manifest["display"] == "fullscreen"

    @override_settings(
        DJUST_PWA_NAME="Settings App",
        DJUST_PWA_SHORT_NAME="SetApp",
        DJUST_PWA_THEME_COLOR="#123456",
    )
    def test_manifest_uses_django_settings(self):
        """Test manifest respects Django settings."""
        template = Template("{% load djust_pwa %}{% djust_pwa_manifest %}")
        result = template.render(Context({}))

        manifest_json = unescape(result.split("data:application/manifest+json,")[1].split("'")[0])
        manifest = json.loads(manifest_json)

        assert manifest["name"] == "Settings App"
        assert manifest["short_name"] == "SetApp"
        assert manifest["theme_color"] == "#123456"

    def test_manifest_explicit_values_override_settings(self):
        """Test that explicit values override Django settings."""
        with override_settings(DJUST_PWA_NAME="Settings Name"):
            template = Template('{% load djust_pwa %}{% djust_pwa_manifest name="Explicit Name" %}')
            result = template.render(Context({}))

            manifest_json = unescape(
                result.split("data:application/manifest+json,")[1].split("'")[0]
            )
            manifest = json.loads(manifest_json)

            assert manifest["name"] == "Explicit Name"

    def test_manifest_default_icons(self):
        """Test that default icons are included."""
        template = Template("{% load djust_pwa %}{% djust_pwa_manifest %}")
        result = template.render(Context({}))

        manifest_json = unescape(result.split("data:application/manifest+json,")[1].split("'")[0])
        manifest = json.loads(manifest_json)

        assert "icons" in manifest
        assert len(manifest["icons"]) == 2

        sizes = [icon["sizes"] for icon in manifest["icons"]]
        assert "192x192" in sizes
        assert "512x512" in sizes


class TestDjustSwRegister:
    """Tests for the djust_sw_register template tag."""

    def test_sw_register_default_url(self):
        """Test service worker registration with default URL."""
        template = Template("{% load djust_pwa %}{% djust_sw_register %}")
        result = template.render(Context({}))

        assert "<script>" in result
        assert "</script>" in result
        assert "serviceWorker" in result
        assert "navigator.serviceWorker.register" in result
        # Default URL is STATIC_URL/sw.js
        assert "/static/sw.js" in result

    def test_sw_register_custom_url(self):
        """Test service worker registration with custom URL."""
        template = Template('{% load djust_pwa %}{% djust_sw_register sw_url="/my-sw.js" %}')
        result = template.render(Context({}))

        assert 'navigator.serviceWorker.register("/my-sw.js"' in result

    def test_sw_register_custom_scope(self):
        """Test service worker registration with custom scope."""
        template = Template('{% load djust_pwa %}{% djust_sw_register scope="/app/" %}')
        result = template.render(Context({}))

        assert 'scope: "/app/"' in result

    def test_sw_register_contains_update_handler(self):
        """Test that registration includes update handler."""
        template = Template("{% load djust_pwa %}{% djust_sw_register %}")
        result = template.render(Context({}))

        assert "updatefound" in result
        assert "onUpdateAvailable" in result

    def test_sw_register_contains_error_handler(self):
        """Test that registration includes error handling."""
        template = Template("{% load djust_pwa %}{% djust_sw_register %}")
        result = template.render(Context({}))

        assert ".catch(function(error)" in result
        assert "registration failed" in result

    def test_sw_register_checks_browser_support(self):
        """Test that registration checks for browser support."""
        template = Template("{% load djust_pwa %}{% djust_sw_register %}")
        result = template.render(Context({}))

        assert "'serviceWorker' in navigator" in result


class TestDjustOfflineIndicator:
    """Tests for the djust_offline_indicator template tag."""

    def test_indicator_default_values(self):
        """Test offline indicator with default values."""
        template = Template("{% load djust_pwa %}{% djust_offline_indicator %}")
        result = template.render(Context({}))

        assert 'class="djust-offline-indicator"' in result
        assert 'data-online-text="Online"' in result
        assert 'data-offline-text="Offline"' in result
        assert 'data-online-class="djust-status-online"' in result
        assert 'data-offline-class="djust-status-offline"' in result

    def test_indicator_custom_text(self):
        """Test offline indicator with custom text."""
        template = Template(
            "{% load djust_pwa %}"
            '{% djust_offline_indicator online_text="Connected" offline_text="Disconnected" %}'
        )
        result = template.render(Context({}))

        assert 'data-online-text="Connected"' in result
        assert 'data-offline-text="Disconnected"' in result

    def test_indicator_custom_classes(self):
        """Test offline indicator with custom CSS classes."""
        template = Template(
            "{% load djust_pwa %}"
            '{% djust_offline_indicator online_class="my-online" offline_class="my-offline" %}'
        )
        result = template.render(Context({}))

        assert 'data-online-class="my-online"' in result
        assert 'data-offline-class="my-offline"' in result

    def test_indicator_show_when_offline(self):
        """Test indicator configured to show only when offline."""
        template = Template('{% load djust_pwa %}{% djust_offline_indicator show_when="offline" %}')
        result = template.render(Context({}))

        # Should have dj-offline-show attribute and be hidden by default
        assert "dj-offline-show" in result
        assert 'style="display: none;"' in result

    def test_indicator_show_when_always(self):
        """Test indicator configured to show always."""
        template = Template('{% load djust_pwa %}{% djust_offline_indicator show_when="always" %}')
        result = template.render(Context({}))

        # Should not have visibility attributes
        assert "dj-offline-show" not in result
        assert "dj-offline-hide" not in result

    def test_indicator_contains_styles(self):
        """Test that indicator includes embedded styles."""
        template = Template("{% load djust_pwa %}{% djust_offline_indicator %}")
        result = template.render(Context({}))

        assert "<style>" in result
        assert ".djust-offline-indicator" in result
        assert ".djust-indicator-dot" in result
        assert ".djust-status-online" in result
        assert ".djust-status-offline" in result

    def test_indicator_contains_dot_element(self):
        """Test that indicator contains the status dot."""
        template = Template("{% load djust_pwa %}{% djust_offline_indicator %}")
        result = template.render(Context({}))

        assert 'class="djust-indicator-dot"' in result
        assert 'class="djust-indicator-text"' in result


class TestDjustOfflineStyles:
    """Tests for the djust_offline_styles template tag."""

    def test_offline_styles_output(self):
        """Test that offline styles are generated."""
        template = Template("{% load djust_pwa %}{% djust_offline_styles %}")
        result = template.render(Context({}))

        assert "<style>" in result
        assert "</style>" in result

    def test_offline_styles_contains_hide_directive(self):
        """Test that styles include dj-offline-hide rules."""
        template = Template("{% load djust_pwa %}{% djust_offline_styles %}")
        result = template.render(Context({}))

        assert "[dj-offline-hide]" in result
        assert "display: none !important" in result

    def test_offline_styles_contains_show_directive(self):
        """Test that styles include dj-offline-show rules."""
        template = Template("{% load djust_pwa %}{% djust_offline_styles %}")
        result = template.render(Context({}))

        assert "[dj-offline-show]" in result

    def test_offline_styles_contains_disable_directive(self):
        """Test that styles include dj-offline-disable rules."""
        template = Template("{% load djust_pwa %}{% djust_offline_styles %}")
        result = template.render(Context({}))

        assert "[dj-offline-disable]" in result
        assert "pointer-events: none !important" in result
        assert "opacity: 0.5 !important" in result
        assert "cursor: not-allowed !important" in result

    def test_offline_styles_contains_pulse_animation(self):
        """Test that styles include pulse animation for indicator."""
        template = Template("{% load djust_pwa %}{% djust_offline_styles %}")
        result = template.render(Context({}))

        assert "@keyframes djust-pulse" in result
        assert ".djust-indicator-dot" in result

    def test_offline_styles_contains_queued_indicator(self):
        """Test that styles include queued event indicator."""
        template = Template("{% load djust_pwa %}{% djust_offline_styles %}")
        result = template.render(Context({}))

        assert ".djust-queued-indicator" in result
        assert ".has-queued" in result


class TestOfflineFallbackFilter:
    """Tests for the offline_fallback template filter."""

    def test_fallback_returns_value_when_present(self):
        """Test that filter returns original value when present."""
        template = Template('{% load djust_pwa %}{{ name|offline_fallback:"Guest" }}')
        result = template.render(Context({"name": "John"}))

        assert result.strip() == "John"

    def test_fallback_returns_fallback_when_none(self):
        """Test that filter returns fallback when value is None."""
        template = Template('{% load djust_pwa %}{{ name|offline_fallback:"Guest" }}')
        result = template.render(Context({"name": None}))

        assert result.strip() == "Guest"

    def test_fallback_returns_fallback_when_empty_string(self):
        """Test that filter returns fallback when value is empty string."""
        template = Template('{% load djust_pwa %}{{ name|offline_fallback:"Guest" }}')
        result = template.render(Context({"name": ""}))

        assert result.strip() == "Guest"

    def test_fallback_returns_fallback_when_missing(self):
        """Test that filter returns fallback when variable is missing."""
        template = Template('{% load djust_pwa %}{{ undefined_var|offline_fallback:"Default" }}')
        result = template.render(Context({}))

        assert result.strip() == "Default"


class TestDjustPwaHead:
    """Tests for the djust_pwa_head inclusion tag."""

    def test_pwa_head_renders_template(self):
        """Test that PWA head tag renders the template."""
        template = Template("{% load djust_pwa %}{% djust_pwa_head %}")
        result = template.render(Context({}))

        # Should contain meta tags
        assert '<meta name="theme-color"' in result
        assert '<meta name="apple-mobile-web-app-capable"' in result
        assert '<meta name="mobile-web-app-capable"' in result

    def test_pwa_head_includes_manifest_link(self):
        """Test that PWA head includes manifest link."""
        template = Template("{% load djust_pwa %}{% djust_pwa_head %}")
        result = template.render(Context({}))

        assert 'rel="manifest"' in result
        assert "manifest.json" in result

    def test_pwa_head_includes_icons(self):
        """Test that PWA head includes icon links."""
        template = Template("{% load djust_pwa %}{% djust_pwa_head %}")
        result = template.render(Context({}))

        assert 'rel="apple-touch-icon"' in result
        assert "icon-192.png" in result
        assert "icon-512.png" in result

    def test_pwa_head_includes_sw_registration(self):
        """Test that PWA head includes service worker registration."""
        template = Template("{% load djust_pwa %}{% djust_pwa_head %}")
        result = template.render(Context({}))

        assert "serviceWorker" in result
        assert "navigator.serviceWorker.register" in result

    def test_pwa_head_includes_offline_styles(self):
        """Test that PWA head includes offline styles."""
        template = Template("{% load djust_pwa %}{% djust_pwa_head %}")
        result = template.render(Context({}))

        assert "[dj-offline-hide]" in result
        assert "[dj-offline-show]" in result

    def test_pwa_head_custom_name(self):
        """Test PWA head with custom app name."""
        template = Template('{% load djust_pwa %}{% djust_pwa_head name="Custom App" %}')
        result = template.render(Context({}))

        assert "Custom App" in result

    def test_pwa_head_custom_theme_color(self):
        """Test PWA head with custom theme color."""
        template = Template('{% load djust_pwa %}{% djust_pwa_head theme_color="#ff0000" %}')
        result = template.render(Context({}))

        assert 'content="#ff0000"' in result


class TestGenerateSwCommand:
    """Tests for the generate_sw management command.

    Note: Tests use the Command class directly because the --version argument
    in the command conflicts with Django's built-in --version flag.
    """

    def _run_command(self, output_path, **options):
        """Helper to run the generate_sw command directly."""
        from djust.management.commands.generate_sw import Command

        cmd = Command()
        cmd.stdout = options.get("stdout", StringIO())
        cmd.stderr = StringIO()

        # Set defaults matching the command's add_arguments defaults
        opts = {
            "output": output_path,
            "cache_static": False,
            "cache_templates": False,
            "version": None,
            "static_extensions": "js,css,png,jpg,jpeg,gif,svg,woff,woff2,ico",
            "exclude_patterns": "admin,debug",
        }
        opts.update(options)
        cmd.handle(**opts)
        return cmd

    def test_command_generates_service_worker(self):
        """Test that command generates a service worker file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            out = StringIO()

            self._run_command(output_path, stdout=out)

            # Check file was created
            assert os.path.exists(output_path)

            # Check content
            with open(output_path) as f:
                content = f.read()

            assert "djust Service Worker" in content
            assert "CACHE_NAME" in content
            assert "addEventListener('install'" in content
            assert "addEventListener('activate'" in content
            assert "addEventListener('fetch'" in content

    def test_command_includes_version(self):
        """Test that service worker includes version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            out = StringIO()

            self._run_command(output_path, version="1.0.0", stdout=out)

            with open(output_path) as f:
                content = f.read()

            assert "1.0.0" in content
            assert "djust-cache-v1.0.0" in content

    def test_command_auto_generates_version(self):
        """Test that command auto-generates version when not provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            out = StringIO()

            self._run_command(output_path, stdout=out)

            output = out.getvalue()
            assert "Version:" in output

    def test_command_generates_manifest(self):
        """Test that command generates manifest.json if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            manifest_path = os.path.join(tmpdir, "manifest.json")
            out = StringIO()

            self._run_command(output_path, stdout=out)

            # Check manifest was created
            assert os.path.exists(manifest_path)

            # Check manifest content
            with open(manifest_path) as f:
                manifest = json.load(f)

            assert "name" in manifest
            assert "short_name" in manifest
            assert "icons" in manifest
            assert manifest["display"] == "standalone"

    def test_command_skips_existing_manifest(self):
        """Test that command does not overwrite existing manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            manifest_path = os.path.join(tmpdir, "manifest.json")
            out = StringIO()

            # Create existing manifest
            existing_manifest = {"name": "Existing App"}
            with open(manifest_path, "w") as f:
                json.dump(existing_manifest, f)

            self._run_command(output_path, stdout=out)

            # Check manifest was not overwritten
            with open(manifest_path) as f:
                manifest = json.load(f)

            assert manifest["name"] == "Existing App"
            assert "short_name" not in manifest

    def test_service_worker_contains_install_handler(self):
        """Test that service worker has install event handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "self.addEventListener('install'" in content
            assert "caches.open(CACHE_NAME)" in content
            assert "self.skipWaiting()" in content

    def test_service_worker_contains_activate_handler(self):
        """Test that service worker has activate event handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "self.addEventListener('activate'" in content
            assert "caches.keys()" in content
            assert "caches.delete" in content
            assert "self.clients.claim()" in content

    def test_service_worker_contains_fetch_handler(self):
        """Test that service worker has fetch event handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "self.addEventListener('fetch'" in content
            assert "caches.match(event.request)" in content
            assert "fetch(event.request)" in content

    def test_service_worker_skips_non_get_requests(self):
        """Test that service worker skips non-GET requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "event.request.method !== 'GET'" in content

    def test_service_worker_skips_websocket_requests(self):
        """Test that service worker skips WebSocket requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "/ws/" in content

    def test_service_worker_contains_background_sync(self):
        """Test that service worker supports background sync."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "self.addEventListener('sync'" in content
            assert "djust-event-sync" in content

    def test_service_worker_contains_message_handler(self):
        """Test that service worker handles messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "self.addEventListener('message'" in content
            assert "SKIP_WAITING" in content
            assert "GET_VERSION" in content
            assert "CLEAR_CACHE" in content

    def test_service_worker_contains_push_handler(self):
        """Test that service worker supports push notifications."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            self._run_command(output_path)

            with open(output_path) as f:
                content = f.read()

            assert "self.addEventListener('push'" in content
            assert "showNotification" in content
            assert "notificationclick" in content

    def test_command_creates_output_directory(self):
        """Test that command creates output directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = os.path.join(tmpdir, "nested", "dir", "sw.js")
            out = StringIO()

            self._run_command(nested_path, stdout=out)

            assert os.path.exists(nested_path)

    def test_command_output_message(self):
        """Test that command outputs success message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "sw.js")
            out = StringIO()

            self._run_command(output_path, stdout=out)

            output = out.getvalue()
            assert "Generated service worker" in output
            assert output_path in output


class TestGenerateSwStaticCollection:
    """Tests for static asset collection in generate_sw command."""

    def test_collects_static_assets_with_flag(self):
        """Test that --cache-static flag triggers asset collection."""
        from djust.management.commands.generate_sw import Command

        cmd = Command()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some static files
            js_file = os.path.join(tmpdir, "app.js")
            css_file = os.path.join(tmpdir, "style.css")
            txt_file = os.path.join(tmpdir, "readme.txt")

            with open(js_file, "w") as f:
                f.write("// JS")
            with open(css_file, "w") as f:
                f.write("/* CSS */")
            with open(txt_file, "w") as f:
                f.write("Text file")

            with override_settings(STATICFILES_DIRS=[tmpdir]):
                assets = cmd._collect_static_assets(
                    extensions=["js", "css"],
                    exclude_patterns=[],
                )

            # Should include js and css but not txt
            urls = [a for a in assets]
            assert any("app.js" in url for url in urls)
            assert any("style.css" in url for url in urls)
            assert not any("readme.txt" in url for url in urls)

    def test_excludes_patterns(self):
        """Test that exclude patterns work."""
        from djust.management.commands.generate_sw import Command

        cmd = Command()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create static files including admin
            app_js = os.path.join(tmpdir, "app.js")
            admin_dir = os.path.join(tmpdir, "admin")
            os.makedirs(admin_dir)
            admin_js = os.path.join(admin_dir, "admin.js")

            with open(app_js, "w") as f:
                f.write("// App")
            with open(admin_js, "w") as f:
                f.write("// Admin")

            with override_settings(STATICFILES_DIRS=[tmpdir]):
                assets = cmd._collect_static_assets(
                    extensions=["js"],
                    exclude_patterns=["admin"],
                )

            urls = [a for a in assets]
            assert any("app.js" in url for url in urls)
            assert not any("admin" in url for url in urls)

    def test_always_includes_client_js(self):
        """Test that djust client.js is always included."""
        from djust.management.commands.generate_sw import Command

        cmd = Command()

        with override_settings(STATICFILES_DIRS=[]):
            assets = cmd._collect_static_assets(
                extensions=["js"],
                exclude_patterns=[],
            )

        assert any("djust/client.js" in url for url in assets)
