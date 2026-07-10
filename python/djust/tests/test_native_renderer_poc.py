"""LVN-II POC gate test: NativeRenderer actually renders.

Replaces the scaffold's `NotImplementedError` with a real render path
that walks a Django template and emits widget VNodes. Tests mock
``render_to_string`` so they're independent of Django settings setup.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            }
        ],
        SECRET_KEY="test-secret",
        USE_TZ=True,
    )
    django.setup()


def _make_view(template_name="home.html"):
    view = MagicMock()
    view.template_name = template_name
    view.get_context_data.return_value = {}
    return view


class TestNativeRendererRenders:
    def test_renders_widget_vnode_tree(self):
        from djust.renderers import ComposeRenderer

        view = _make_view()
        r = ComposeRenderer(view)

        rendered_html = (
            "<Stack spacing='12' padding='16'>"
            "<Text font='title'>Hello, Margaret</Text>"
            "<Button dj-tap='dismiss_alert'>Dismiss</Button>"
            "</Stack>"
        )
        with (
            patch.object(r, "resolve_template", return_value="home.compose.html"),
            patch("djust.renderers.native.render_to_string", return_value=rendered_html),
        ):
            html, patches_json, version = r.render_with_diff()

        assert html == ""
        assert version == 1
        patches = json.loads(patches_json)
        assert len(patches) == 1
        assert patches[0]["type"] == "replace"
        assert patches[0]["path"] == []

        root = patches[0]["node"]
        assert root["tag"] == "stack"  # HTMLParser lowercases
        assert root["attrs"]["spacing"] == "12"
        assert root["attrs"]["padding"] == "16"
        assert "id" in root
        assert len(root["children"]) == 2

        text_node, button_node = root["children"]
        assert text_node["tag"] == "text"
        assert text_node["text"] == "Hello, Margaret"
        assert text_node["attrs"]["font"] == "title"
        assert button_node["tag"] == "button"
        assert button_node["attrs"]["dj-tap"] == "dismiss_alert"
        assert button_node["text"] == "Dismiss"

    def test_version_monotonically_increases(self):
        from djust.renderers import ComposeRenderer

        r = ComposeRenderer(_make_view())
        with (
            patch.object(r, "resolve_template", return_value="home.compose.html"),
            patch("djust.renderers.native.render_to_string", return_value="<Stack></Stack>"),
        ):
            _, _, v1 = r.render_with_diff()
            _, _, v2 = r.render_with_diff()
            _, _, v3 = r.render_with_diff()
        assert (v1, v2, v3) == (1, 2, 3)

    def test_self_closing_widget(self):
        from djust.renderers import ComposeRenderer

        r = ComposeRenderer(_make_view())
        with (
            patch.object(r, "resolve_template", return_value="home.compose.html"),
            patch(
                "djust.renderers.native.render_to_string",
                return_value="<Stack><Spacer/><Text>a</Text></Stack>",
            ),
        ):
            _, patches_json, _ = r.render_with_diff()
        root = json.loads(patches_json)[0]["node"]
        assert [c["tag"] for c in root["children"]] == ["spacer", "text"]

    def test_no_template_name_raises_clearly(self):
        from djust.renderers import ComposeRenderer

        view = MagicMock(spec=[])  # no template_name attr
        r = ComposeRenderer(view)
        import pytest

        with pytest.raises(RuntimeError, match="template_name"):
            r.render_with_diff()

    def test_empty_template_raises_clearly(self):
        from djust.renderers import ComposeRenderer

        r = ComposeRenderer(_make_view())
        with (
            patch.object(r, "resolve_template", return_value="empty.compose.html"),
            patch("djust.renderers.native.render_to_string", return_value="   "),
        ):
            import pytest

            with pytest.raises(RuntimeError, match="no.*VNode root"):
                r.render_with_diff()
