"""
Multi-Tenant Demo - Tenant isolation with djust LiveView

Demonstrates:
- TenantMixin with custom resolve_tenant override
- Tenant-scoped state isolation
- TenantAwareMemoryBackend for presence tracking
- Tenant switching via query parameter
"""

import time

from djust import state
from djust.decorators import event_handler
from djust.tenants import TenantMixin, TenantInfo, TenantAwareMemoryBackend
from djust_shared.views import BaseViewWithNavbar


# Pre-defined demo tenants
DEMO_TENANTS = {
    "acme": TenantInfo(
        tenant_id="acme",
        name="Acme Corp",
        settings={
            "theme_color": "#3b82f6",
            "plan": "Enterprise",
            "max_users": 100,
        },
    ),
    "globex": TenantInfo(
        tenant_id="globex",
        name="Globex Inc",
        settings={
            "theme_color": "#10b981",
            "plan": "Startup",
            "max_users": 25,
        },
    ),
}


class TenantDemoView(TenantMixin, BaseViewWithNavbar):
    """
    Multi-tenant demo showcasing tenant resolution and data isolation.

    Proves:
    - TenantMixin resolves tenant from request
    - Custom resolve_tenant override (query param instead of subdomain)
    - Tenant-scoped state (counter, messages per tenant)
    - TenantAwareMemoryBackend isolates presence per tenant
    - Context injection of tenant info into templates
    """

    template_name = "demos/tenant_demo.html"
    tenant_required = False  # Don't 404 without tenant — fall back to default

    # Per-tenant state stored at class level for demo persistence across requests
    _tenant_state = {}

    # Bounds for demo safety
    MAX_MESSAGES = 50
    MAX_TEXT_LENGTH = 500
    COUNTER_MIN = -999
    COUNTER_MAX = 999

    def resolve_tenant(self, request):
        """
        Override to resolve tenant from query parameter for demo purposes.

        In production you'd use subdomain, header, or session resolvers.
        Falls back to "acme" for unknown tenant IDs.
        """
        tenant_id = request.GET.get("tenant", "acme")
        return DEMO_TENANTS.get(tenant_id) or DEMO_TENANTS["acme"]

    # Tenant-scoped values live in the class-level store above and are loaded
    # in mount(); the fields below are what the template renders. They are not
    # persisted: the tenant store is the source of truth, and a session-saved
    # copy would carry one tenant's values to another after a tenant switch.
    counter = state(0)
    messages = state(default_factory=list)
    presence_list = state(default_factory=list, render=False)
    presence_count = state(0)
    tenant_theme = state("#6b7280")
    tenant_plan = state("N/A")
    tenant_max_users = state(0)
    available_tenants = state(default_factory=list)

    def mount(self, request, **kwargs):
        # Ensure tenant is resolved for WebSocket mount (dispatch() isn't called
        # by the WebSocket consumer, so TenantMixin._ensure_tenant() needs
        # to be called explicitly here)
        self._ensure_tenant(request)

        tenant_id = self._tenant_id()
        store = self._store()
        self.counter = store["counter"]
        self.messages = list(store["messages"])

        # Set up presence for this tenant
        self._presence_backend = TenantAwareMemoryBackend(tenant_id=tenant_id)
        session_id = request.session.session_key or "demo-user"
        self._presence_backend.join(
            presence_key="tenant-demo",
            user_id=session_id,
            meta={"joined": time.strftime("%H:%M:%S")},
        )

        # Store presence info
        self.presence_list = self._presence_backend.list("tenant-demo")
        self.presence_count = len(self.presence_list)

        # Computed tenant display values
        if self.tenant:
            self.tenant_theme = self.tenant.get_setting("theme_color", "#6b7280")
            self.tenant_plan = self.tenant.get_setting("plan", "Free")
            self.tenant_max_users = self.tenant.get_setting("max_users", 10)

        # Available tenants for the switcher
        self.available_tenants = [
            {"id": tid, "name": t.name, "active": tid == tenant_id}
            for tid, t in DEMO_TENANTS.items()
        ]

    def _tenant_id(self):
        return self.tenant.id if self.tenant else "unknown"

    def _store(self):
        """This tenant's entry in the class-level store, created on first use."""
        return self._tenant_state.setdefault(
            self._tenant_id(), {"counter": 0, "messages": [], "msg_counter": 0}
        )

    @event_handler
    def increment(self, **kwargs):
        """Increment the tenant-scoped counter."""
        store = self._store()
        store["counter"] = min(store["counter"] + 1, self.COUNTER_MAX)
        self.counter = store["counter"]

    @event_handler
    def decrement(self, **kwargs):
        """Decrement the tenant-scoped counter."""
        store = self._store()
        store["counter"] = max(store["counter"] - 1, self.COUNTER_MIN)
        self.counter = store["counter"]

    @event_handler
    def add_message(self, text: str = "", **kwargs):
        """Add a message to the tenant-scoped message board."""
        text = text.strip()
        if text:
            text = text[:self.MAX_TEXT_LENGTH]
            store = self._store()
            store["msg_counter"] += 1
            store["messages"].append({
                "id": store["msg_counter"],
                "text": text,
                "time": time.strftime("%H:%M:%S"),
                "tenant": self._tenant_id(),
            })
            # Cap message list — drop oldest on overflow
            del store["messages"][:-self.MAX_MESSAGES]
            self.messages = list(store["messages"])

    @event_handler
    def clear_messages(self, **kwargs):
        """Clear all messages for the current tenant."""
        self._store()["messages"] = []
        self.messages = []

    def get_context_data(self, **kwargs):
        from djust_shared.components.ui import HeroSection, BackButton

        context = super().get_context_data(**kwargs)

        # Render components to HTML strings for reliable VDOM serialization
        context["hero_html"] = HeroSection(
            title="Multi-Tenant Demo",
            subtitle="Tenant isolation and context injection with djust",
            icon="🏢",
        ).render()
        context["back_btn_html"] = BackButton(href="/demos/").render()
        context["messages_count"] = len(self.messages)

        # ``tenant`` is TenantMixin's own render value (the resolved TenantInfo);
        # the template reads its id and name.
        return context
