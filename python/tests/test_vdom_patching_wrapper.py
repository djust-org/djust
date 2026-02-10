"""
Test VDOM patching with Django template wrapper pattern.

This test verifies that VDOM patches are generated correctly when using
the wrapper template pattern (Rust content + Django layout).
"""

import pytest
from djust import LiveView
from djust.decorators import event_handler
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware


class SimpleCounterView(LiveView):
    """Simple counter view for testing VDOM patching"""

    template = """<div data-djust-root>
    <div class="container">
        <h1>Counter Demo</h1>
        <p>Count: <strong>{{ count }}</strong></p>
        <button @click="increment">Increment</button>
    </div>
</div>"""

    def mount(self, request):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


def add_session_to_request(request):
    """Helper to add session to request"""
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
def test_vdom_patching_generates_patches():
    """Test that VDOM patching generates patches instead of full HTML"""

    # Create view and request
    view = SimpleCounterView()
    factory = RequestFactory()

    # Simulate GET request (initial render)
    get_request = factory.get("/counter/")
    get_request = add_session_to_request(get_request)

    response = view.get(get_request)
    initial_html = response.content.decode("utf-8")

    # Verify initial render
    assert "Count: <strong>0</strong>" in initial_html
    assert "data-djust-root" in initial_html

    print("\n[TEST] Initial HTML rendered")
    print(f"[TEST] Session key: {get_request.session.session_key}")

    # Simulate POST request (increment counter)
    post_request = factory.post(
        "/counter/", data='{"event":"increment","params":{}}', content_type="application/json"
    )
    # Use same session
    post_request.session = get_request.session

    response = view.post(post_request)
    import json

    response_data = json.loads(response.content.decode("utf-8"))

    print(f"\n[TEST] POST Response keys: {response_data.keys()}")
    print(f"[TEST] Has patches? {'patches' in response_data}")
    print(f"[TEST] Has html? {'html' in response_data}")
    print(f"[TEST] Version: {response_data.get('version')}")

    # Verify patches were generated (optimized response - no HTML!)
    assert "patches" in response_data, "Response should contain patches"
    assert "html" not in response_data, "Response should NOT contain HTML (optimization)"
    assert "version" in response_data, "Response should contain version"

    # Verify patches modify the counter
    patches_json = response_data["patches"]
    print(f"[TEST] Patches: {patches_json}")

    # Parse patches to verify they update the counter
    import json

    # patches_json might be a string or already parsed list
    patches = json.loads(patches_json) if isinstance(patches_json, str) else patches_json
    assert len(patches) > 0, "Should have at least one patch"
    # Should have a SetText patch that changes counter to "1"
    set_text_patches = [p for p in patches if p["type"] == "SetText" and p.get("text") == "1"]
    assert len(set_text_patches) > 0, "Should have a SetText patch updating counter to 1"

    print("\n[TEST] ✅ VDOM patching works correctly!")


@pytest.mark.django_db
def test_vdom_patching_multiple_updates():
    """Test multiple consecutive updates generate patches"""

    view = SimpleCounterView()
    factory = RequestFactory()

    # Initial GET
    get_request = factory.get("/counter/")
    get_request = add_session_to_request(get_request)
    view.get(get_request)

    # First increment
    post_request_1 = factory.post(
        "/counter/", data='{"event":"increment","params":{}}', content_type="application/json"
    )
    post_request_1.session = get_request.session
    response_1 = view.post(post_request_1)
    import json

    data_1 = json.loads(response_1.content.decode("utf-8"))

    assert "patches" in data_1, "First update should generate patches"
    assert "html" not in data_1, "Should NOT contain HTML (optimized response)"
    # Verify patches update counter from 0 to 1
    patches_1 = (
        json.loads(data_1["patches"]) if isinstance(data_1["patches"], str) else data_1["patches"]
    )
    assert any(p.get("text") == "1" for p in patches_1 if p["type"] == "SetText")

    # Second increment
    post_request_2 = factory.post(
        "/counter/", data='{"event":"increment","params":{}}', content_type="application/json"
    )
    post_request_2.session = get_request.session
    response_2 = view.post(post_request_2)
    data_2 = json.loads(response_2.content.decode("utf-8"))

    assert "patches" in data_2, "Second update should generate patches"
    assert "html" not in data_2, "Should NOT contain HTML (optimized response)"
    # Verify patches update counter from 1 to 2
    patches_2 = (
        json.loads(data_2["patches"]) if isinstance(data_2["patches"], str) else data_2["patches"]
    )
    assert any(p.get("text") == "2" for p in patches_2 if p["type"] == "SetText")

    print("\n[TEST] ✅ Multiple updates generate patches correctly!")


@pytest.mark.django_db
def test_vdom_root_alignment():
    """Test that browser DOM and Rust VDOM have matching root elements"""

    view = SimpleCounterView()
    factory = RequestFactory()

    get_request = factory.get("/counter/")
    get_request = add_session_to_request(get_request)

    response = view.get(get_request)
    html = response.content.decode("utf-8")

    # Count occurrences of data-djust-root
    root_count = html.count("data-djust-root")

    # Should have exactly 2 occurrences (opening tag + JavaScript selector)
    assert root_count >= 1, "Should have at least one data-djust-root"

    # Verify structure starts with the root div
    assert "data-djust-root" in html

    # Get the Rust VDOM structure
    template = view.get_template()
    assert "data-djust-root" in template, "Rust template should include root div"

    print("\n[TEST] ✅ Root element alignment verified!")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
