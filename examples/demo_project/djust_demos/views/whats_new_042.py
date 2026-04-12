"""
What's New in v0.4.2 — Interactive showcase of headline features.

Demonstrates:
- TutorialMixin guided tours (Phase 1c)
- push_commands server-driven UI (Phase 1a)
- wait_for_event async primitive (Phase 1b)
- Private attribute preservation (#627/#611)
- Serialization hardening (set, Decimal, date)
"""

from datetime import date
from decimal import Decimal

from djust.decorators import background, event_handler
from djust.js import JS
from djust.tutorials import TutorialMixin, TutorialStep

from djust_shared.views import BaseViewWithNavbar


class WhatsNew042View(TutorialMixin, BaseViewWithNavbar):
    """
    Showcase of v0.4.2 features — a single LiveView that demonstrates
    each headline feature in an interactive section.

    Note: TutorialMixin comes FIRST in the base list. This is required
    because Django's View.__init__ doesn't call super().__init__(),
    so mixins listed after LiveView never get initialized. The V010
    system check (#691) catches wrong ordering at startup.
    """

    template_name = "demos/whats_new_042.html"

    # Tour steps as a class attribute. TutorialMixin.__init_subclass__
    # automatically migrates this to _tutorial_steps so the context
    # serializer never sees non-serializable TutorialStep objects (#694).
    tutorial_steps = [
        TutorialStep(
            target="#section-push-commands",
            message="push_commands lets your Python code drive the browser — "
            "no custom JavaScript needed.",
            timeout=4.0,
            position="bottom",
        ),
        TutorialStep(
            target="#btn-highlight-demo",
            message="Try clicking this button — it uses push_commands to "
            "highlight elements from the server.",
            wait_for="highlight_demo",
        ),
        TutorialStep(
            target="#section-wait-for",
            message="wait_for_event pauses a background task until the user "
            "acts. Click the button below to continue.",
            wait_for="confirm_action",
        ),
        TutorialStep(
            target="#section-state",
            message="Private attributes now survive reconnects. "
            "And set(), Decimal, date all serialize correctly.",
            timeout=4.0,
        ),
        TutorialStep(
            target="#section-push-commands",
            message="That's the v0.4.2 tour! All driven by Python — "
            "zero custom JavaScript.",
            timeout=3.0,
        ),
    ]

    def mount(self, request, **kwargs):
        from djust_shared.components.ui import (
            BackButton,
            HeroSection,
        )

        # Public state
        self.count = 0
        self.highlight_active = False
        self.confirmed = False

        # v0.4.2 fix: private attrs now survive events (#627)
        self._internal_counter = 0

        # v0.4.2 fix: these types now serialize correctly (#628, #626)
        self.example_set = {1, 2, 3}
        self.example_date = date(2026, 4, 12)
        self.example_decimal = Decimal("3.14")

        # Components
        self.hero = HeroSection(
            title="What's New in v0.4.2",
            subtitle="Backend-Driven UI, Guided Tours, and 11 Bug Fixes",
            icon="🚀",
        )
        self.back_btn = BackButton(href="/demos/")

    # -- Phase 1a: push_commands demo --

    @event_handler
    def highlight_demo(self, **kwargs):
        """Server pushes a JS command chain to highlight an element."""
        self.highlight_active = not self.highlight_active
        if self.highlight_active:
            chain = JS.add_class("demo-highlight", to="#highlight-target").dispatch(
                "demo:flash", detail={"text": "Highlighted from Python!"}
            )
        else:
            chain = JS.remove_class("demo-highlight", to="#highlight-target")
        self.push_commands(chain)

    # -- Phase 1b: wait_for_event demo --

    @event_handler
    def confirm_action(self, **kwargs):
        """User clicked the confirm button — resolves any pending waiter."""
        self.confirmed = True

    @event_handler
    @background
    async def run_wait_demo(self, **kwargs):
        """Demonstrates wait_for_event: pauses until user clicks confirm.

        This is an async @background handler — #692 fix ensures the
        coroutine is properly awaited. #693 fix ensures push_commands
        reach the client mid-task via flush_push_events().
        """
        self.confirmed = False
        self.count = 0

        # Wait for the user to click "Confirm" — no polling, no timers
        await self.wait_for_event("confirm_action", timeout=30.0)

        self.count += 1
        self.push_commands(
            JS.add_class("demo-success", to="#wait-result").dispatch(
                "demo:flash", detail={"text": "Confirmed!"}
            )
        )
        await self.flush_push_events()

    # -- State demos --

    @event_handler
    def increment_private(self, **kwargs):
        """Increments both public and private counters.
        v0.4.2 fix: _internal_counter now survives across events."""
        self.count += 1
        self._internal_counter += 1

    @event_handler
    def add_to_set(self, **kwargs):
        """Adds a value to the set. v0.4.2 fix: set() now serializes.
        Note: set round-trips through JSON as a list, so we coerce back."""
        if isinstance(self.example_set, list):
            self.example_set = set(self.example_set)
        self.example_set.add(len(self.example_set) + 1)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["internal_counter"] = self._internal_counter
        if isinstance(self.example_set, list):
            self.example_set = set(self.example_set)
        ctx["set_display"] = sorted(self.example_set)
        return ctx
