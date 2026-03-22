"""
Tests for HTTP POST side-channel delivery of flash and page_metadata.

Verifies that _inject_side_channels() includes _flash and _page_metadata
in JSON responses when put_flash() or page_title are used during an event.
"""

import json

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


class FlashView(LiveView):
    """View that uses put_flash in an event handler."""

    template = "<div dj-root><span>{{ message }}</span></div>"

    def mount(self, request, **kwargs):
        self.message = "Hello"

    @event_handler()
    def save(self, **kwargs):
        self.message = "Saved"
        self.put_flash("success", "Record saved!")

    @event_handler()
    def warn_and_save(self, **kwargs):
        self.message = "Done"
        self.put_flash("success", "Saved!")
        self.put_flash("warning", "But check logs")

    @event_handler()
    def clear_errors(self, **kwargs):
        self.clear_flash("error")

    @event_handler()
    def update_title(self, **kwargs):
        self.message = "Updated"
        self.page_title = "New Title"

    @event_handler()
    def flash_and_title(self, **kwargs):
        self.message = "Both"
        self.put_flash("info", "FYI")
        self.page_title = "Info Page"

    @event_handler()
    def noop(self, **kwargs):
        self.message = "Same"


def _add_session(request):
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _setup_view():
    view = FlashView()
    factory = RequestFactory()
    get_request = factory.get("/test/")
    get_request = _add_session(get_request)
    view.get(get_request)
    return view, factory, get_request


def _post_event(view, factory, session, event_name, params=None):
    body = json.dumps({"event": event_name, "params": params or {}})
    post_request = factory.post("/test/", data=body, content_type="application/json")
    post_request.session = session
    response = view.post(post_request)
    return json.loads(response.content.decode("utf-8"))


@pytest.mark.django_db
class TestFlashSideChannel:
    def test_flash_included_in_response(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "save")

        assert "_flash" in data
        assert len(data["_flash"]) == 1
        assert data["_flash"][0]["action"] == "put"
        assert data["_flash"][0]["level"] == "success"
        assert data["_flash"][0]["message"] == "Record saved!"

    def test_multiple_flash_messages(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "warn_and_save")

        assert "_flash" in data
        assert len(data["_flash"]) == 2
        assert data["_flash"][0]["level"] == "success"
        assert data["_flash"][1]["level"] == "warning"

    def test_clear_flash_in_response(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "clear_errors")

        assert "_flash" in data
        assert data["_flash"][0]["action"] == "clear"
        assert data["_flash"][0]["level"] == "error"

    def test_no_flash_when_unused(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "noop")

        assert "_flash" not in data


@pytest.mark.django_db
class TestPageMetadataSideChannel:
    def test_page_title_included_in_response(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "update_title")

        assert "_page_metadata" in data
        assert len(data["_page_metadata"]) == 1
        assert data["_page_metadata"][0]["action"] == "title"
        assert data["_page_metadata"][0]["value"] == "New Title"

    def test_no_metadata_when_unused(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "noop")

        assert "_page_metadata" not in data


@pytest.mark.django_db
class TestCombinedSideChannels:
    def test_flash_and_metadata_together(self):
        view, factory, req = _setup_view()
        data = _post_event(view, factory, req.session, "flash_and_title")

        assert "_flash" in data
        assert data["_flash"][0]["level"] == "info"
        assert "_page_metadata" in data
        assert data["_page_metadata"][0]["value"] == "Info Page"
