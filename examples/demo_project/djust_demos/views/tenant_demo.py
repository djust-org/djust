"""
Multi-Tenant Demo - Tenant isolation with djust LiveView

Demonstrates:
- TenantMixin with custom resolve_tenant override
- Tenant-scoped state isolation
- TenantAwareMemoryBackend for presence tracking
- Tenant switching via query parameter
"""

import time

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

    def mount(self, request, **kwargs):
        # Ensure tenant is resolved for WebSocket mount (dispatch() isn't called
        # by the WebSocket consumer, so TenantMixin._ensure_tenant() needs
        # to be called explicitly here)
        self._ensure_tenant(request)

        from djust_shared.components.ui import HeroSection, BackButton

        # Render components to HTML strings for reliable VDOM serialization
        self.hero_html = HeroSection(
            title="Multi-Tenant Demo",
            subtitle="Tenant isolation and context injection with djust",
            icon="🏢",
        ).render()
        self.back_btn_html = BackButton(href="/demos/").render()

        # Get or initialize tenant-scoped state
        tenant_id = self.tenant.id if self.tenant else "unknown"
        if tenant_id not in self._tenant_state:
            self._tenant_state[tenant_id] = {
                "counter": 0,
                "messages": [],
                "msg_counter": 0,
            }

        state = self._tenant_state[tenant_id]
        self.counter = state["counter"]
        self.messages = list(state["messages"])

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

        # Computed tenant display values (set here so ContextMixin picks them up)
        if self.tenant:
            self.tenant_theme = self.tenant.get_setting("theme_color", "#6b7280")
            self.tenant_plan = self.tenant.get_setting("plan", "Free")
            self.tenant_max_users = self.tenant.get_setting("max_users", 10)
        else:
            self.tenant_theme = "#6b7280"
            self.tenant_plan = "N/A"
            self.tenant_max_users = 0

        # Available tenants for the switcher
        self.available_tenants = [
            {"id": tid, "name": t.name, "active": tid == tenant_id}
            for tid, t in DEMO_TENANTS.items()
        ]

    @event_handler
    def increment(self, **kwargs):
        """Increment the tenant-scoped counter."""
        self.counter = min(self.counter + 1, self.COUNTER_MAX)
        self._save_state()

    @event_handler
    def decrement(self, **kwargs):
        """Decrement the tenant-scoped counter."""
        self.counter = max(self.counter - 1, self.COUNTER_MIN)
        self._save_state()

    @event_handler
    def add_message(self, text: str = "", **kwargs):
        """Add a message to the tenant-scoped message board."""
        text = text.strip()
        if text:
            text = text[:self.MAX_TEXT_LENGTH]
            tenant_id = self.tenant.id if self.tenant else "unknown"
            state = self._tenant_state.get(tenant_id, {})
            state["msg_counter"] = state.get("msg_counter", 0) + 1
            self.messages.append({
                "id": state["msg_counter"],
                "text": text,
                "time": time.strftime("%H:%M:%S"),
                "tenant": tenant_id,
            })
            # Cap message list — drop oldest on overflow
            if len(self.messages) > self.MAX_MESSAGES:
                self.messages = self.messages[-self.MAX_MESSAGES:]
            self._save_state()

    @event_handler
    def clear_messages(self, **kwargs):
        """Clear all messages for the current tenant."""
        self.messages = []
        self._save_state()

    def _save_state(self):
        """Persist state back to class-level storage."""
        tenant_id = self.tenant.id if self.tenant else "unknown"
        self._tenant_state[tenant_id] = {
            "counter": self.counter,
            "messages": list(self.messages),
            "msg_counter": self._tenant_state.get(tenant_id, {}).get("msg_counter", 0),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Convert TenantInfo to a plain dict for JSON-serializable VDOM context
        # (TenantInfo uses __slots__ and doesn't serialize for VDOM updates)
        if self.tenant:
            context["tenant"] = {
                "id": self.tenant.id,
                "name": self.tenant.name,
                "settings": self.tenant.settings,
            }
        else:
            context["tenant"] = None

        return context
