"""
Declarative guided-tour state machine for LiveView.

ADR-002 Phase 1c. Build guided tours by describing them as a list of
``TutorialStep`` entries and mixing in ``TutorialMixin``. The mixin
runs the steps in order, pushing highlight + narrate chains via
``push_commands`` (Phase 1a) and suspending between steps via
``wait_for_event`` (Phase 1b). Zero custom JavaScript required.

Quickstart::

    from djust import LiveView
    from djust.tutorials import TutorialMixin, TutorialStep

    class OnboardingView(LiveView, TutorialMixin):
        template_name = "onboarding.html"
        tutorial_steps = [
            TutorialStep(
                target="#nav-dashboard",
                message="This is your dashboard.",
                timeout=4.0,
            ),
            TutorialStep(
                target="#btn-new-project",
                message="Click here to create your first project.",
                wait_for="create_project",
            ),
        ]

In the template::

    {% load djust_tutorials %}
    <button dj-click="start_tutorial">Take the tour</button>
    {% tutorial_bubble %}

That's the entire tour. The mixin handles step ordering, highlight
cleanup on advance, skip/cancel handling, and per-step timeout.
"""

from .mixin import TutorialMixin
from .step import TutorialStep

__all__ = ["TutorialMixin", "TutorialStep"]
