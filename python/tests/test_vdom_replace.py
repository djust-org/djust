"""
Test VDOM replace behavior (data-djust-replace).

Verifies that when using data-djust-replace, the patch sequence correctly
removes old children before inserting new ones. Regression test for Issue #142.
"""

import json
import pytest
from djust import LiveView
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware


class ReplaceContainerView(LiveView):
    """View with a data-djust-replace container that switches content."""

    template = """<div data-djust-root>
    <div data-djust-replace id="messages">
        {% if show_messages %}
            <p>Message 1</p>
            <p>Message 2</p>
            <p>Message 3</p>
        {% else %}
            <p>No messages</p>
        {% endif %}
    </div>
</div>"""

    def mount(self, request):
        self.show_messages = False

    def toggle(self):
        self.show_messages = not self.show_messages


def add_session_to_request(request):
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
def test_replace_patch_order_removes_before_inserts():
    """RemoveChild patches must precede InsertChild patches for data-djust-replace.

    When the JS client batching path (>10 patches) processes patches, it must
    apply RemoveChild before InsertChild. Otherwise, inserted nodes shift
    indices and RemoveChild targets wrong nodes, leaving old children visible.
    """
    view = ReplaceContainerView()
    factory = RequestFactory()

    # Initial render (show_messages=False → 1 child: "No messages")
    get_request = factory.get("/messages/")
    get_request = add_session_to_request(get_request)
    response = view.get(get_request)
    initial_html = response.content.decode("utf-8")
    assert "No messages" in initial_html

    # Toggle to show_messages=True → 3 children via POST
    post_request = factory.post(
        "/messages/",
        data='{"event":"toggle","params":{}}',
        content_type="application/json",
    )
    post_request.session = get_request.session

    response = view.post(post_request)
    response_data = json.loads(response.content.decode("utf-8"))

    assert "patches" in response_data, "Response should contain patches"
    patches_json = response_data["patches"]
    patches = json.loads(patches_json) if isinstance(patches_json, str) else patches_json

    # Extract patch types in order
    remove_indices = []
    insert_indices = []
    last_remove_pos = -1
    first_insert_pos = None

    for i, patch in enumerate(patches):
        patch_type = patch.get("type")
        if patch_type == "RemoveChild":
            remove_indices.append(patch.get("index"))
            last_remove_pos = i
        elif patch_type == "InsertChild":
            insert_indices.append(patch.get("index"))
            if first_insert_pos is None:
                first_insert_pos = i

    # Must have both remove and insert patches
    assert (
        len(remove_indices) > 0
    ), f"Should have RemoveChild patches. All patches: {[p['type'] for p in patches]}"
    assert (
        len(insert_indices) > 0
    ), f"Should have InsertChild patches. All patches: {[p['type'] for p in patches]}"

    # All removes must come before all inserts
    assert last_remove_pos < first_insert_pos, (
        f"All RemoveChild patches must precede InsertChild patches. "
        f"Last RemoveChild at position {last_remove_pos}, "
        f"first InsertChild at position {first_insert_pos}. "
        f"Patch types: {[p['type'] for p in patches]}"
    )

    # RemoveChild indices should be in descending order (safe removal)
    for i in range(len(remove_indices) - 1):
        assert (
            remove_indices[i] >= remove_indices[i + 1]
        ), f"RemoveChild indices should be descending: {remove_indices}"
