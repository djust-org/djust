"""
Tests for PageMetadataMixin — page_title / page_meta / _drain_page_metadata.
"""

from djust.mixins.page_metadata import PageMetadataMixin


class FakeView(PageMetadataMixin):
    """Minimal view-like class for testing the mixin."""

    pass


class TestPageTitle:
    def test_page_title_setter_queues_command(self):
        view = FakeView()
        view.page_title = "Dashboard"

        commands = view._drain_page_metadata()
        assert len(commands) == 1
        assert commands[0] == {"action": "title", "value": "Dashboard"}

    def test_page_title_getter_returns_value(self):
        view = FakeView()
        view.page_title = "My Page"

        assert view.page_title == "My Page"

    def test_page_title_default_is_empty_string(self):
        view = FakeView()
        assert view.page_title == ""

    def test_page_title_multiple_sets(self):
        view = FakeView()
        view.page_title = "First"
        view.page_title = "Second"

        commands = view._drain_page_metadata()
        assert len(commands) == 2
        assert commands[0]["value"] == "First"
        assert commands[1]["value"] == "Second"


class TestPageMeta:
    def test_page_meta_setter_queues_commands(self):
        view = FakeView()
        view.page_meta = {"description": "A test page"}

        commands = view._drain_page_metadata()
        assert len(commands) == 1
        assert commands[0] == {
            "action": "meta",
            "name": "description",
            "content": "A test page",
        }

    def test_page_meta_multiple_keys(self):
        view = FakeView()
        view.page_meta = {"description": "Desc", "keywords": "a, b, c"}

        commands = view._drain_page_metadata()
        assert len(commands) == 2
        names = {cmd["name"] for cmd in commands}
        assert names == {"description", "keywords"}

    def test_page_meta_getter_returns_value(self):
        view = FakeView()
        view.page_meta = {"description": "Hello"}

        assert view.page_meta == {"description": "Hello"}

    def test_page_meta_default_is_empty_dict(self):
        view = FakeView()
        assert view.page_meta == {}

    def test_og_meta_tags(self):
        view = FakeView()
        view.page_meta = {"og:image": "https://example.com/img.png"}

        commands = view._drain_page_metadata()
        assert len(commands) == 1
        assert commands[0]["name"] == "og:image"
        assert commands[0]["content"] == "https://example.com/img.png"


class TestDrainPageMetadata:
    def test_drain_returns_all(self):
        view = FakeView()
        view.page_title = "Title"
        view.page_meta = {"description": "Desc"}

        result = view._drain_page_metadata()
        assert len(result) == 2
        assert result[0]["action"] == "title"
        assert result[1]["action"] == "meta"

    def test_drain_clears_queue(self):
        view = FakeView()
        view.page_title = "Title"

        view._drain_page_metadata()

        assert view._drain_page_metadata() == []

    def test_drain_empty(self):
        view = FakeView()
        assert view._drain_page_metadata() == []

    def test_drain_mixed_title_and_meta(self):
        view = FakeView()
        view.page_title = "First"
        view.page_meta = {"description": "Desc"}
        view.page_title = "Second"

        result = view._drain_page_metadata()
        assert len(result) == 3
        assert result[0]["action"] == "title"
        assert result[1]["action"] == "meta"
        assert result[2]["action"] == "title"


class TestPageMetadataTypeHints:
    def test_page_title_is_property(self):
        assert isinstance(PageMetadataMixin.__dict__["page_title"], property)

    def test_page_meta_is_property(self):
        assert isinstance(PageMetadataMixin.__dict__["page_meta"], property)

    def test_drain_page_metadata_signature(self):
        import inspect

        sig = inspect.signature(PageMetadataMixin._drain_page_metadata)
        params = list(sig.parameters.keys())
        assert "self" in params
