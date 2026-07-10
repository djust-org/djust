"""LVN-II PR-1 gate test: frozen widget vocabulary spec.

The 12-widget set in ``djust.renderers.widgets`` is the structural
freeze that LVN-II PR-2 (NativeRenderer) and the native client libraries
build against. This test pins the exact set + the constraint sets
(event attrs, style attrs).

See:
- ``docs/adr/019-liveview-native.md`` §"Three layers" §3
- ``docs/native-widget-vocabulary.md`` (human-readable spec)
"""

from __future__ import annotations


class TestWidgetVocabularyExports:
    def test_imports_from_renderers(self):
        from djust.renderers.widgets import (  # noqa: F401
            EVENT_ATTRS,
            STYLE_ATTRS,
            WIDGET_TAGS,
            is_widget_tag,
        )


class TestFrozenWidgetTags:
    """The 12-widget set is frozen at v1 per ADR-019. This test pins
    the exact contents so an unintentional addition fails CI.
    """

    def test_widget_tags_is_frozenset(self):
        from djust.renderers.widgets import WIDGET_TAGS

        assert isinstance(WIDGET_TAGS, frozenset)

    def test_widget_tags_count_is_12(self):
        from djust.renderers.widgets import WIDGET_TAGS

        assert len(WIDGET_TAGS) == 12

    def test_widget_tags_exact_contents(self):
        from djust.renderers.widgets import WIDGET_TAGS

        assert WIDGET_TAGS == frozenset(
            {
                "Stack",
                "HStack",
                "ZStack",
                "Text",
                "Button",
                "TextField",
                "Toggle",
                "List",
                "Image",
                "ScrollView",
                "Spacer",
                "NavigationView",
            }
        )

    def test_is_widget_tag_positive(self):
        from djust.renderers.widgets import is_widget_tag

        assert is_widget_tag("Stack") is True
        assert is_widget_tag("Button") is True

    def test_is_widget_tag_negative(self):
        from djust.renderers.widgets import is_widget_tag

        assert is_widget_tag("div") is False
        assert is_widget_tag("Span") is False  # case-sensitive
        assert is_widget_tag("") is False


class TestFrozenEventAttrs:
    def test_event_attrs_count(self):
        from djust.renderers.widgets import EVENT_ATTRS

        assert EVENT_ATTRS == frozenset({"dj-tap", "dj-change", "dj-input"})


class TestFrozenStyleAttrs:
    def test_style_attrs_count(self):
        from djust.renderers.widgets import STYLE_ATTRS

        assert STYLE_ATTRS == frozenset(
            {
                "padding",
                "spacing",
                "alignment",
                "foregroundColor",
                "font",
            }
        )
