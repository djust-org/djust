"""
Mixins to make LiveView more DRY and Python-friendly.
"""

from typing import Any, Dict
from djust.components.base import Component


class AutoRenderMixin:
    """
    Automatically renders Component instances in context to HTML strings.

    This eliminates the need to call .render() manually on every component.

    Usage:
        class MyView(AutoRenderMixin, LiveView):
            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                # No need to call .render()!
                context['hero'] = HeroSection(title="Demo")
                context['button'] = Button(text="Click", event="click")
                return context
    """

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)

        # Auto-render any Component instances
        for key, value in context.items():
            if isinstance(value, Component):
                context[key] = value.render()

        return context


class CommonComponentsMixin:
    """
    Automatically provides common components (navbar, back button) to context.

    Usage:
        class MyView(CommonComponentsMixin, LiveView):
            back_button_href = "/demos/"  # Override default
            back_button_text = "Back to Demos"  # Override default
    """

    # Override these in your view
    back_button_href = "/demos/"
    back_button_text = "Back to Demos"
    show_back_button = True

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        from demo_app.components import BackButton

        context = super().get_context_data(**kwargs)

        # Add back button if enabled
        if self.show_back_button:
            context['back_btn'] = BackButton(
                href=self.back_button_href,
                text=self.back_button_text
            ).render()

        return context


class DemoViewBase(AutoRenderMixin, CommonComponentsMixin):
    """
    Combines all common demo view patterns.

    Usage:
        class MyDemoView(DemoViewBase, LiveView):
            template_name = "demos/my_demo.html"

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                # Components auto-render, back button auto-added!
                context['hero'] = HeroSection(title="My Demo")
                return context
    """
    pass
