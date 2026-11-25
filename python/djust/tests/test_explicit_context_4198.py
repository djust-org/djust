"""#4198: an explicit view's template context carries no LiveView configuration.

The legacy walk (``ContextMixin.get_context_data`` over class attributes) leaked
``login_required``, ``sticky``, ``tick_interval`` and the other framework
defaults into the template context (#2960); its tests are gone with the legacy
subject. An explicit view has no class-attribute walk: only ``state()`` fields
and ``get_context_data()`` reach the template. This pins that, with a
declared-state control so an empty context cannot pass.
"""

import re

from djust import LiveView
from djust.decorators import state
from djust.testing import rendered_state


class Page(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root>{{ greeting }}{{ extra }}"
        "{% if login_required %}L{% endif %}{% if sticky %}S{% endif %}</div>"
    )
    login_required = True
    sticky = True
    tick_interval = None
    plain_attribute = "not declared"
    greeting = state(default="hi")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["extra"] = "!"
        return context


def _rendered():
    view = Page()
    view.mount(None)
    view.render_full_template()
    return rendered_state(view)


def test_declared_state_and_context_data_reach_the_template():
    seen = _rendered()  # the control: the render did hand the template its data
    assert seen["greeting"] == "hi"
    assert seen["extra"] == "!"
    # ... and the template that names login_required / sticky renders neither.
    view = Page()
    view.mount(None)
    html = view.render_full_template()
    root = re.search(r"<div dj-root>(.*?)</div>", html, re.S).group(1)
    assert root.replace("<!--dj-if-->", "") == "hi!", root


def test_framework_config_and_plain_attributes_are_not_template_context():
    seen = _rendered()
    for name in (
        "login_required",
        "sticky",
        "tick_interval",
        "abstract",
        "template",
        "template_name",
        "plain_attribute",
        "exposure_policy",
    ):
        assert name not in seen, name
