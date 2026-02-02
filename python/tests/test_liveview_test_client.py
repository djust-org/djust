"""
Tests for the extended LiveViewTestClient (Phoenix-style testing API).

Tests cover:
- click(), input(), submit() high-level methods
- state property, html property
- has_element(), count_elements() CSS selector queries
- assert_redirect(), assert_push_event()
- ComponentTestClient
- MockUploadFile
- pytest fixtures
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
            }
        ],
        SECRET_KEY="test-secret-key",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

import pytest
from djust.testing import (
    LiveViewTestClient,
    ComponentTestClient,
    MockUploadFile,
    _BaseLiveViewTestClient,
)


# ── Mock Views ──────────────────────────────────────────────────


class CounterView:
    template_name = "counter.html"

    def __init__(self):
        self.count = 0
        self._pending_push_events = []

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.count = kwargs.get("initial_count", 0)

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1

    def increment_by(self, amount: int = 1):
        self.count += amount

    def get_context_data(self):
        return {"count": self.count}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class SearchView:
    template_name = "search.html"

    def __init__(self):
        self.search_query = ""
        self.results = []
        self._pending_push_events = []

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.results = []

    def search(self, query: str = ""):
        self.search_query = query
        # Simulate search results
        self.results = [f"Result for {query} #{i}" for i in range(3)]

    def get_context_data(self):
        return {"search_query": self.search_query, "results": self.results}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class TabView:
    template_name = "tabs.html"

    def __init__(self):
        self.active_tab = "overview"
        self._pending_push_events = []

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.active_tab = "overview"

    def set_tab(self, value: str = "overview"):
        self.active_tab = value

    def get_context_data(self):
        return {"active_tab": self.active_tab}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class PushEventView:
    template_name = "push.html"

    def __init__(self):
        self.saved = False
        self._pending_push_events = []

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.saved = False

    def save(self):
        self.saved = True
        self._pending_push_events.append(("flash", {"message": "Saved!", "type": "success"}))
        self._pending_push_events.append(("scroll_to", {"selector": "#top"}))

    def get_context_data(self):
        return {"saved": self.saved}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class RedirectView:
    template_name = "redirect.html"

    def __init__(self):
        self._redirect_url = None
        self._pending_push_events = []

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        pass

    def go_home(self):
        self._redirect_url = "/home/"

    def get_context_data(self):
        return {}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class HtmlView:
    """View that renders real HTML for selector testing."""

    template_name = "html_test.html"

    def __init__(self):
        self.items = []
        self.title = "Test"
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.items = kwargs.get("items", ["Item 1", "Item 2", "Item 3"])
        self.title = kwargs.get("title", "Test Page")
        self._update_html()

    def add_item(self, text: str = "New Item"):
        self.items.append(text)
        self._update_html()

    def _update_html(self):
        items_html = "".join(
            f'<li class="todo-item">{item}</li>' for item in self.items
        )
        self._html = (
            f'<div id="main" class="container">'
            f'<h1>{self.title}</h1>'
            f'<ul class="item-list">{items_html}</ul>'
            f'<button class="btn btn-primary" id="add-btn">Add Todo</button>'
            f'<button class="btn btn-danger">Delete All</button>'
            f'</div>'
        )

    def get_context_data(self):
        return {"items": self.items, "title": self.title}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class UploadView:
    template_name = "upload.html"

    def __init__(self):
        self.uploaded_name = None
        self.uploaded_size = 0
        self._pending_push_events = []

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        pass

    def handle_upload(self, file=None):
        if file:
            self.uploaded_name = file.name
            self.uploaded_size = file.size

    def get_context_data(self):
        return {"uploaded_name": self.uploaded_name, "uploaded_size": self.uploaded_size}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


# ── Monkey-patch render for HtmlView (no real templates) ────────

_original_render = LiveViewTestClient.render


def _patched_render(self):
    if hasattr(self.view_instance, "_html"):
        return self.view_instance._html
    return _original_render(self)


LiveViewTestClient.render = _patched_render


# ── Tests: click / input / submit ──────────────────────────────


class TestClickMethod:
    def test_click_calls_handler(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.click("increment")
        assert client.state["count"] == 1

    def test_click_multiple_times(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.click("increment")
        client.click("increment")
        client.click("increment")
        assert client.state["count"] == 3

    def test_click_with_value(self):
        client = LiveViewTestClient(TabView)
        client.mount()
        client.click("set_tab", value="settings")
        assert client.state["active_tab"] == "settings"

    def test_click_with_kwargs(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.click("increment_by", amount=5)
        assert client.state["count"] == 5

    def test_click_returns_result(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        result = client.click("increment")
        assert result["success"] is True


class TestInputMethod:
    def test_input_sets_attribute(self):
        client = LiveViewTestClient(SearchView)
        client.mount()
        client.input("search_query", "django")
        assert client.state["search_query"] == "django"

    def test_input_invalidates_html_cache(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        _ = client.html  # Cache html
        client.input("title", "New Title")
        # _html_cache should be None now
        assert client._html_cache is None

    def test_input_raises_if_not_mounted(self):
        client = LiveViewTestClient(SearchView)
        with pytest.raises(RuntimeError, match="View not mounted"):
            client.input("search_query", "test")


class TestSubmitMethod:
    def test_submit_calls_handler(self):
        client = LiveViewTestClient(SearchView)
        client.mount()
        client.submit("search", {"query": "django"})
        assert len(client.state["results"]) == 3

    def test_submit_with_no_data(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.submit("increment")
        assert client.state["count"] == 1


# ── Tests: state and html properties ───────────────────────────


class TestStateProperty:
    def test_state_returns_dict(self):
        client = LiveViewTestClient(CounterView)
        client.mount(initial_count=42)
        assert isinstance(client.state, dict)
        assert client.state["count"] == 42

    def test_state_excludes_private(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        assert "_pending_push_events" not in client.state

    def test_state_updates_after_event(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        assert client.state["count"] == 0
        client.click("increment")
        assert client.state["count"] == 1


class TestHtmlProperty:
    def test_html_returns_string(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert isinstance(client.html, str)
        assert len(client.html) > 0

    def test_html_is_cached(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        html1 = client.html
        html2 = client.html
        assert html1 is html2  # Same object (cached)

    def test_html_invalidated_after_click(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        html_before = client.html
        client.click("add_item", text="New")
        html_after = client.html
        assert html_before != html_after


# ── Tests: has_element / count_elements ─────────────────────────


class TestHasElement:
    def test_has_element_by_tag(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.has_element("button")
        assert client.has_element("h1")
        assert not client.has_element("table")

    def test_has_element_by_class(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.has_element(".container")
        assert client.has_element(".btn")
        assert not client.has_element(".nonexistent")

    def test_has_element_by_tag_and_class(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.has_element("button.btn")
        assert client.has_element("li.todo-item")

    def test_has_element_by_id(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.has_element("#main")
        assert client.has_element("#add-btn")
        assert not client.has_element("#nonexistent")

    def test_has_element_with_text(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.has_element("button", text="Add Todo")
        assert not client.has_element("button", text="Nonexistent")

    def test_has_element_by_tag_class_and_id(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.has_element("button#add-btn.btn")


class TestCountElements:
    def test_count_by_tag(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.count_elements("button") == 2

    def test_count_by_class(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.count_elements("li.todo-item") == 3

    def test_count_zero_for_nonexistent(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.count_elements("table") == 0

    def test_count_updates_after_event(self):
        client = LiveViewTestClient(HtmlView)
        client.mount()
        assert client.count_elements("li.todo-item") == 3
        client.click("add_item", text="Item 4")
        assert client.count_elements("li.todo-item") == 4


# ── Tests: assert_push_event ───────────────────────────────────


class TestAssertPushEvent:
    def test_push_event_captured(self):
        client = LiveViewTestClient(PushEventView)
        client.mount()
        client.click("save")
        client.assert_push_event("flash")
        client.assert_push_event("scroll_to")

    def test_push_event_with_payload(self):
        client = LiveViewTestClient(PushEventView)
        client.mount()
        client.click("save")
        client.assert_push_event("flash", {"message": "Saved!", "type": "success"})

    def test_push_event_fails_when_not_fired(self):
        client = LiveViewTestClient(PushEventView)
        client.mount()
        with pytest.raises(AssertionError, match="was not called"):
            client.assert_push_event("nonexistent")

    def test_push_event_fails_on_payload_mismatch(self):
        client = LiveViewTestClient(PushEventView)
        client.mount()
        client.click("save")
        with pytest.raises(AssertionError, match="payload didn't match"):
            client.assert_push_event("flash", {"message": "Wrong"})

    def test_push_events_property(self):
        client = LiveViewTestClient(PushEventView)
        client.mount()
        client.click("save")
        assert len(client.push_events) == 2
        assert client.push_events[0]["name"] == "flash"
        assert client.push_events[1]["name"] == "scroll_to"


# ── Tests: assert_redirect ─────────────────────────────────────


class TestAssertRedirect:
    def test_redirect_detected(self):
        client = LiveViewTestClient(RedirectView)
        client.mount()
        client.click("go_home")
        client.assert_redirect("/home/")

    def test_redirect_fails_when_none(self):
        client = LiveViewTestClient(RedirectView)
        client.mount()
        with pytest.raises(AssertionError, match="No redirect"):
            client.assert_redirect("/anywhere/")

    def test_redirect_fails_on_url_mismatch(self):
        client = LiveViewTestClient(RedirectView)
        client.mount()
        client.click("go_home")
        with pytest.raises(AssertionError, match="Expected redirect"):
            client.assert_redirect("/wrong/")

    def test_redirect_without_url_check(self):
        client = LiveViewTestClient(RedirectView)
        client.mount()
        client.click("go_home")
        client.assert_redirect()  # Just checks redirect happened


# ── Tests: MockUploadFile ──────────────────────────────────────


class TestMockUploadFile:
    def test_basic_properties(self):
        f = MockUploadFile("test.txt", b"hello", "text/plain")
        assert f.name == "test.txt"
        assert f.content_type == "text/plain"
        assert f.size == 5

    def test_read_all(self):
        f = MockUploadFile("test.txt", b"hello world")
        assert f.read() == b"hello world"

    def test_read_partial(self):
        f = MockUploadFile("test.txt", b"hello world")
        assert f.read(5) == b"hello"
        assert f.read(6) == b" world"

    def test_seek_and_tell(self):
        f = MockUploadFile("test.txt", b"hello")
        f.read(3)
        assert f.tell() == 3
        f.seek(0)
        assert f.tell() == 0
        assert f.read() == b"hello"

    def test_chunks(self):
        data = b"a" * 100
        f = MockUploadFile("test.bin", data)
        chunks = list(f.chunks(chunk_size=30))
        assert len(chunks) == 4  # 30+30+30+10
        assert b"".join(chunks) == data

    def test_custom_size(self):
        f = MockUploadFile("test.txt", b"hi", size=1000)
        assert f.size == 1000

    def test_upload_integration(self):
        client = LiveViewTestClient(UploadView)
        client.mount()
        f = MockUploadFile("photo.jpg", b"\xff\xd8\xff" * 100, "image/jpeg")
        client.click("handle_upload", file=f)
        assert client.state["uploaded_name"] == "photo.jpg"
        assert client.state["uploaded_size"] == 300


# ── Tests: selector parser ─────────────────────────────────────


class TestSelectorParser:
    def test_tag_only(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector("div")
        assert tag == "div"
        assert classes == []
        assert id_val is None

    def test_class_only(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector(".btn")
        assert tag is None or tag == ""
        assert classes == ["btn"]

    def test_id_only(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector("#main")
        assert id_val == "main"

    def test_tag_with_class(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector("button.btn")
        assert tag == "button"
        assert classes == ["btn"]

    def test_tag_with_multiple_classes(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector("button.btn.btn-primary")
        assert tag == "button"
        assert "btn" in classes
        assert "btn-primary" in classes

    def test_tag_with_id(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector("div#main")
        assert tag == "div"
        assert id_val == "main"

    def test_full_selector(self):
        tag, classes, id_val = LiveViewTestClient._parse_selector("div#main.container.wide")
        assert tag == "div"
        assert id_val == "main"
        assert "container" in classes
        assert "wide" in classes


# ── Tests: backward compatibility ──────────────────────────────


class TestBackwardCompatibility:
    """Ensure the extended client is backward-compatible with the base."""

    def test_is_subclass(self):
        assert issubclass(LiveViewTestClient, _BaseLiveViewTestClient)

    def test_send_event_still_works(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        result = client.send_event("increment")
        assert result["success"] is True
        assert client.view_instance.count == 1

    def test_assert_state_still_works(self):
        client = LiveViewTestClient(CounterView)
        client.mount(initial_count=5)
        client.assert_state(count=5)

    def test_get_event_history_still_works(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.click("increment")
        history = client.get_event_history()
        assert len(history) >= 2  # mount + increment


# ── Tests: method chaining ─────────────────────────────────────


class TestMethodChaining:
    def test_mount_returns_self(self):
        client = LiveViewTestClient(CounterView)
        result = client.mount()
        assert result is client


# ── Tests: pytest fixtures ─────────────────────────────────────


class TestPytestFixtures:
    def test_live_view_client_fixture(self, live_view_client):
        client = live_view_client(CounterView)
        assert client.state["count"] == 0
        client.click("increment")
        assert client.state["count"] == 1

    def test_live_view_client_with_params(self, live_view_client):
        client = live_view_client(CounterView, initial_count=10)
        assert client.state["count"] == 10

    def test_mock_upload_fixture(self, mock_upload):
        f = mock_upload("test.txt", b"data", "text/plain")
        assert isinstance(f, MockUploadFile)
        assert f.name == "test.txt"


# Register fixtures for this module
@pytest.fixture
def live_view_client():
    def _factory(view_class, user=None, **mount_params):
        client = LiveViewTestClient(view_class, user=user)
        client.mount(**mount_params)
        return client
    return _factory


@pytest.fixture
def mock_upload():
    def _factory(name, content=b"", content_type="application/octet-stream"):
        return MockUploadFile(name, content, content_type)
    return _factory


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
