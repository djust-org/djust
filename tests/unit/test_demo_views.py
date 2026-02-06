"""
Tests for demo views: PWA demo and Multi-Tenant demo.

Covers:
- Tenant isolation (primary focus)
- Tenant demo event handlers and state management
- PWA demo event handlers
- Security hardening (bounds, fallbacks)
- URL registration
"""

import json

import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware

from djust.tenants import TenantAwareMemoryBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(path="/", tenant=None):
    """Create a GET request with session support and optional ?tenant= param."""
    factory = RequestFactory()
    url = path
    if tenant is not None:
        url = f"{path}?tenant={tenant}"
    request = factory.get(url)
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _make_tenant_view():
    """Instantiate a fresh TenantDemoView with clean class-level state."""
    from djust_demos.views.tenant_demo import TenantDemoView

    # Reset shared class-level state between tests
    TenantDemoView._tenant_state = {}
    return TenantDemoView()


def _mount_tenant_view(tenant="acme"):
    """Create, mount, and return a TenantDemoView for the given tenant."""
    view = _make_tenant_view()
    request = _make_request(tenant=tenant)
    view.request = request
    view.mount(request)
    return view


def _make_pwa_view():
    """Instantiate a fresh PWADemoView."""
    from djust_demos.views.pwa_demo import PWADemoView

    return PWADemoView()


def _mount_pwa_view():
    """Create, mount, and return a PWADemoView."""
    view = _make_pwa_view()
    request = _make_request()
    view.request = request
    view.mount(request)
    return view


# ===========================================================================
# Tenant Isolation Tests — Primary Focus
# ===========================================================================


@pytest.mark.django_db
class TestTenantIsolation:
    """Verify that tenant state, presence, and context are fully isolated."""

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        """Reset shared state before each test."""
        from djust_demos.views.tenant_demo import TenantDemoView

        TenantDemoView._tenant_state = {}
        TenantAwareMemoryBackend.clear_all()
        yield
        TenantDemoView._tenant_state = {}
        TenantAwareMemoryBackend.clear_all()

    def test_state_isolation_between_tenants(self):
        """Incrementing Acme's counter must not affect Globex."""
        from djust_demos.views.tenant_demo import TenantDemoView

        # Mount Acme and increment 3 times
        acme = _make_tenant_view()
        req_acme = _make_request(tenant="acme")
        acme.request = req_acme
        acme.mount(req_acme)
        for _ in range(3):
            acme.increment()

        # Mount Globex on a *fresh* view (class state persists)
        globex = TenantDemoView()
        req_globex = _make_request(tenant="globex")
        globex.request = req_globex
        globex.mount(req_globex)

        assert globex.counter == 0, "Globex counter should start at 0"
        # Re-mount Acme to read persisted state
        acme2 = TenantDemoView()
        acme2.request = req_acme
        acme2.mount(req_acme)
        assert acme2.counter == 3, "Acme counter should still be 3"

    def test_messages_isolation_between_tenants(self):
        """Messages added to Acme must not appear in Globex."""
        from djust_demos.views.tenant_demo import TenantDemoView

        acme = _make_tenant_view()
        req_acme = _make_request(tenant="acme")
        acme.request = req_acme
        acme.mount(req_acme)
        acme.add_message(text="Hello from Acme")

        globex = TenantDemoView()
        req_globex = _make_request(tenant="globex")
        globex.request = req_globex
        globex.mount(req_globex)

        assert len(globex.messages) == 0, "Globex should have no messages"

        # Verify Acme still has its message
        acme2 = TenantDemoView()
        acme2.request = req_acme
        acme2.mount(req_acme)
        assert len(acme2.messages) == 1
        assert acme2.messages[0]["text"] == "Hello from Acme"

    def test_save_state_writes_to_correct_tenant(self):
        """_save_state() must only write to the current tenant's bucket."""
        from djust_demos.views.tenant_demo import TenantDemoView

        acme = _make_tenant_view()
        req = _make_request(tenant="acme")
        acme.request = req
        acme.mount(req)
        acme.increment()

        assert TenantDemoView._tenant_state["acme"]["counter"] == 1
        assert "globex" not in TenantDemoView._tenant_state

    def test_presence_isolation(self):
        """Presence joined on Acme must not be visible to Globex."""
        backend_acme = TenantAwareMemoryBackend(tenant_id="acme")
        backend_acme.join("demo-room", "user-1", {})

        backend_globex = TenantAwareMemoryBackend(tenant_id="globex")
        assert backend_globex.list("demo-room") == []
        assert backend_acme.count("demo-room") == 1

    def test_invalid_tenant_id_cannot_create_phantom_state(self):
        """?tenant=evil must fall back to 'acme'; no phantom state entries."""
        from djust_demos.views.tenant_demo import TenantDemoView

        view = _make_tenant_view()
        req = _make_request(tenant="evil")
        view.request = req
        view.mount(req)

        assert view.tenant.id == "acme", "Invalid tenant should fall back to acme"
        assert "evil" not in TenantDemoView._tenant_state
        assert "unknown" not in TenantDemoView._tenant_state

    def test_context_only_exposes_current_tenant(self):
        """get_context_data must expose only the mounted tenant's info."""
        view = _mount_tenant_view("acme")
        context = view.get_context_data()

        assert context["tenant"]["id"] == "acme"
        assert context["tenant"]["name"] == "Acme Corp"
        # No Globex data in context
        assert "globex" not in str(context["tenant"])

    def test_tenant_switching_preserves_each_tenants_state(self):
        """Full round-trip: Acme +3, Globex +1, re-mount Acme → still 3."""
        from djust_demos.views.tenant_demo import TenantDemoView

        # Acme: increment 3 times
        acme = _make_tenant_view()
        req_acme = _make_request(tenant="acme")
        acme.request = req_acme
        acme.mount(req_acme)
        for _ in range(3):
            acme.increment()

        # Globex: increment 1 time
        globex = TenantDemoView()
        req_globex = _make_request(tenant="globex")
        globex.request = req_globex
        globex.mount(req_globex)
        globex.increment()

        # Re-mount Acme
        acme2 = TenantDemoView()
        acme2.request = req_acme
        acme2.mount(req_acme)

        assert acme2.counter == 3, "Acme counter should be preserved"

        # Re-mount Globex
        globex2 = TenantDemoView()
        globex2.request = req_globex
        globex2.mount(req_globex)
        assert globex2.counter == 1, "Globex counter should be preserved"

    def test_available_tenants_matches_demo_tenants(self):
        """available_tenants should list exactly the DEMO_TENANTS keys."""
        from djust_demos.views.tenant_demo import DEMO_TENANTS

        view = _mount_tenant_view("acme")
        ids = {t["id"] for t in view.available_tenants}
        assert ids == set(DEMO_TENANTS.keys())

    def test_concurrent_tenant_sessions_dont_interfere(self):
        """Two view instances (Acme + Globex) mounted simultaneously."""
        from djust_demos.views.tenant_demo import TenantDemoView

        TenantDemoView._tenant_state = {}

        acme = TenantDemoView()
        req_acme = _make_request(tenant="acme")
        acme.request = req_acme
        acme.mount(req_acme)

        globex = TenantDemoView()
        req_globex = _make_request(tenant="globex")
        globex.request = req_globex
        globex.mount(req_globex)

        # Operate on both concurrently
        acme.increment()
        acme.increment()
        globex.decrement()

        assert acme.counter == 2
        assert globex.counter == -1

        # Verify persisted state
        assert TenantDemoView._tenant_state["acme"]["counter"] == 2
        assert TenantDemoView._tenant_state["globex"]["counter"] == -1


# ===========================================================================
# Tenant Demo View Tests
# ===========================================================================


@pytest.mark.django_db
class TestTenantDemoView:
    """Test TenantDemoView event handlers, state, and hardening."""

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        from djust_demos.views.tenant_demo import TenantDemoView

        TenantDemoView._tenant_state = {}
        TenantAwareMemoryBackend.clear_all()
        yield
        TenantDemoView._tenant_state = {}
        TenantAwareMemoryBackend.clear_all()

    def test_mount_resolves_tenant_from_query_param(self):
        """?tenant=acme should resolve to Acme Corp."""
        view = _mount_tenant_view("acme")
        assert view.tenant.id == "acme"
        assert view.tenant.name == "Acme Corp"

    def test_mount_resolves_default_tenant(self):
        """No query param defaults to acme."""
        from djust_demos.views.tenant_demo import TenantDemoView

        view = TenantDemoView()
        TenantDemoView._tenant_state = {}
        req = _make_request()  # no tenant param
        view.request = req
        view.mount(req)
        assert view.tenant.id == "acme"

    def test_mount_invalid_tenant_falls_back(self):
        """?tenant=evil falls back to acme."""
        view = _mount_tenant_view("evil")
        assert view.tenant.id == "acme"

    def test_mount_initializes_state(self):
        """mount() should initialize counter, messages, presence, and available_tenants."""
        view = _mount_tenant_view("acme")
        assert view.counter == 0
        assert view.messages == []
        assert view.presence_count >= 0
        assert len(view.available_tenants) == 2

    def test_increment(self):
        """increment() increases counter by 1 and saves state."""
        from djust_demos.views.tenant_demo import TenantDemoView

        view = _mount_tenant_view("acme")
        view.increment()
        assert view.counter == 1
        assert TenantDemoView._tenant_state["acme"]["counter"] == 1

    def test_decrement(self):
        """decrement() decreases counter by 1."""
        view = _mount_tenant_view("acme")
        view.decrement()
        assert view.counter == -1

    def test_counter_clamped(self):
        """Counter must be clamped to COUNTER_MAX (999)."""
        view = _mount_tenant_view("acme")
        view.counter = 998
        view.increment()
        assert view.counter == 999
        view.increment()
        assert view.counter == 999, "Counter should not exceed 999"

        # Test lower bound
        view.counter = -998
        view.decrement()
        assert view.counter == -999
        view.decrement()
        assert view.counter == -999, "Counter should not go below -999"

    def test_add_message(self):
        """add_message() appends a message with expected keys."""
        view = _mount_tenant_view("acme")
        view.add_message(text="Hello world")

        assert len(view.messages) == 1
        msg = view.messages[0]
        assert "id" in msg
        assert msg["text"] == "Hello world"
        assert "time" in msg
        assert msg["tenant"] == "acme"

    def test_add_message_empty_ignored(self):
        """Empty or whitespace-only text should not add a message."""
        view = _mount_tenant_view("acme")
        view.add_message(text="")
        view.add_message(text="   ")
        assert len(view.messages) == 0

    def test_add_message_max_length(self):
        """Text longer than MAX_TEXT_LENGTH should be truncated."""
        view = _mount_tenant_view("acme")
        long_text = "x" * 600
        view.add_message(text=long_text)
        assert len(view.messages[0]["text"]) == 500

    def test_add_message_max_items(self):
        """Adding more than MAX_MESSAGES should drop the oldest."""
        view = _mount_tenant_view("acme")
        for i in range(55):
            view.add_message(text=f"msg-{i}")
        assert len(view.messages) == 50
        assert view.messages[0]["text"] == "msg-5"  # First 5 dropped

    def test_clear_messages(self):
        """clear_messages() empties the message list."""
        view = _mount_tenant_view("acme")
        view.add_message(text="Hello")
        view.clear_messages()
        assert view.messages == []

    def test_get_context_data_converts_tenant_to_dict(self):
        """context['tenant'] should be a plain dict with id, name, settings."""
        view = _mount_tenant_view("acme")
        context = view.get_context_data()
        tenant = context["tenant"]

        assert isinstance(tenant, dict)
        assert tenant["id"] == "acme"
        assert tenant["name"] == "Acme Corp"
        assert "settings" in tenant

    def test_ensure_tenant_in_mount(self):
        """self.tenant must be set after mount() — WebSocket compat."""
        view = _mount_tenant_view("globex")
        assert view.tenant is not None
        assert view.tenant.id == "globex"

    def test_tenant_display_attributes(self):
        """Computed display attrs should match DEMO_TENANTS settings."""
        acme = _mount_tenant_view("acme")
        assert acme.tenant_theme == "#3b82f6"
        assert acme.tenant_plan == "Enterprise"
        assert acme.tenant_max_users == 100

        from djust_demos.views.tenant_demo import TenantDemoView

        TenantDemoView._tenant_state = {}
        TenantAwareMemoryBackend.clear_all()

        globex = TenantDemoView()
        req = _make_request(tenant="globex")
        globex.request = req
        globex.mount(req)
        assert globex.tenant_theme == "#10b981"
        assert globex.tenant_plan == "Startup"
        assert globex.tenant_max_users == 25


# ===========================================================================
# PWA Demo View Tests
# ===========================================================================


@pytest.mark.django_db
class TestPWADemoView:
    """Test PWADemoView event handlers and hardening."""

    def test_mount_initializes_state(self):
        """mount() sets notes, note_counter, hero_html, back_btn_html."""
        view = _mount_pwa_view()
        assert view.notes == []
        assert view.note_counter == 0
        assert isinstance(view.hero_html, str) and len(view.hero_html) > 0
        assert isinstance(view.back_btn_html, str) and len(view.back_btn_html) > 0

    def test_add_note(self):
        """add_note() appends a note with id, text, time keys."""
        view = _mount_pwa_view()
        view.add_note(text="Buy milk")

        assert len(view.notes) == 1
        note = view.notes[0]
        assert note["id"] == 1
        assert note["text"] == "Buy milk"
        assert "time" in note

    def test_add_note_empty_ignored(self):
        """Empty or whitespace-only text should not add a note."""
        view = _mount_pwa_view()
        view.add_note(text="")
        view.add_note(text="   ")
        assert len(view.notes) == 0

    def test_add_note_max_length(self):
        """Text longer than MAX_TEXT_LENGTH should be truncated."""
        view = _mount_pwa_view()
        view.add_note(text="a" * 600)
        assert len(view.notes[0]["text"]) == 500

    def test_add_note_max_items(self):
        """Adding more than MAX_NOTES should drop the oldest."""
        view = _mount_pwa_view()
        for i in range(55):
            view.add_note(text=f"note-{i}")
        assert len(view.notes) == 50
        assert view.notes[0]["text"] == "note-5"

    def test_clear_notes(self):
        """clear_notes() resets both list and counter."""
        view = _mount_pwa_view()
        view.add_note(text="Note 1")
        view.add_note(text="Note 2")
        view.clear_notes()
        assert view.notes == []
        assert view.note_counter == 0

    def test_get_context_data_has_pwa_config(self):
        """Context must include pwa_config dict from PWAMixin."""
        view = _mount_pwa_view()
        context = view.get_context_data()
        assert "pwa_config" in context
        assert isinstance(context["pwa_config"], dict)
        assert context["pwa_config"]["name"] == "djust PWA Demo"

    def test_manifest_preview_is_json(self):
        """manifest_preview should be a valid JSON string."""
        view = _mount_pwa_view()
        view.get_context_data()  # triggers manifest_preview creation
        parsed = json.loads(view.manifest_preview)
        assert isinstance(parsed, dict)
        assert "name" in parsed

    def test_manifest_preview_in_context(self):
        """manifest_preview must be in the returned context dict."""
        view = _mount_pwa_view()
        context = view.get_context_data()
        assert "manifest_preview" in context
        parsed = json.loads(context["manifest_preview"])
        assert isinstance(parsed, dict)
        assert parsed["name"] == "djust PWA Demo"

    def test_pwa_head_html_in_context(self):
        """pwa_head_html must contain SW registration and theme-color meta."""
        view = _mount_pwa_view()
        context = view.get_context_data()
        assert "pwa_head_html" in context
        html = context["pwa_head_html"]
        assert "theme-color" in html
        assert "serviceWorker" in html
        assert "manifest.json" in html

    def test_pwa_head_html_contains_configured_values(self):
        """pwa_head_html should use the view's pwa_name and pwa_theme_color."""
        view = _mount_pwa_view()
        context = view.get_context_data()
        html = context["pwa_head_html"]
        assert "djust PWA Demo" in html
        assert "#6366f1" in html


# ===========================================================================
# Integration Tests — URL Registration
# ===========================================================================


class TestDemoRegistration:
    """Verify demo views are registered in the URL config."""

    def test_pwa_view_in_urlconf(self):
        """PWA demo URL resolves."""
        from django.urls import resolve

        match = resolve("/demos/pwa/")
        assert match is not None

    def test_tenant_view_in_urlconf(self):
        """Tenant demo URL resolves."""
        from django.urls import resolve

        match = resolve("/demos/tenant/")
        assert match is not None

    @pytest.mark.django_db
    def test_pwa_view_http_get(self):
        """GET /demos/pwa/ returns 200."""
        from django.test import Client

        client = Client()
        response = client.get("/demos/pwa/")
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_tenant_view_http_get(self):
        """GET /demos/tenant/?tenant=acme returns 200."""
        from django.test import Client

        client = Client()
        response = client.get("/demos/tenant/?tenant=acme")
        assert response.status_code == 200

    def test_service_worker_url_resolves(self):
        """Service worker URL /sw.js resolves."""
        from django.urls import resolve

        match = resolve("/sw.js")
        assert match is not None

    def test_manifest_url_resolves(self):
        """Manifest URL /manifest.json resolves."""
        from django.urls import resolve

        match = resolve("/manifest.json")
        assert match is not None

    @pytest.mark.django_db
    def test_service_worker_http_get(self):
        """GET /sw.js returns 200 with JavaScript content type."""
        from django.test import Client

        client = Client()
        response = client.get("/sw.js")
        assert response.status_code == 200
        assert "javascript" in response["Content-Type"]

    @pytest.mark.django_db
    def test_manifest_http_get(self):
        """GET /manifest.json returns 200 with JSON content type."""
        from django.test import Client

        client = Client()
        response = client.get("/manifest.json")
        assert response.status_code == 200
        assert "json" in response["Content-Type"]
        data = json.loads(response.content)
        assert "name" in data
