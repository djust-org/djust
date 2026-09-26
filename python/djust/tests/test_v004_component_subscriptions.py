"""V004 knows component subscriptions (ADR-034 / ADR-037, #3134 CI).

A ``@menu.on.selected`` callback is dispatched by its component, not the
browser. V004 ("looks like an event handler but is missing @event_handler")
reported every documented ``on_<menu>_selected`` callback, and its advice
(add ``@event_handler``) is refused by ``subscribe()`` itself.
"""

from djust import LiveView
from djust.checks.components import check_liveviews
from djust.components.interactive import DropdownMenu


class SubscriptionView(LiveView):
    template = "<div dj-root>{{ project_menu }}</div>"
    login_required = False
    project_menu = DropdownMenu(label="Project", items=[{"label": "Edit", "value": "edit"}])

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        pass

    def on_plain_click(self, **kwargs):  # still flagged: undecorated, not a subscription
        pass


def _findings(check_id, cls):
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    return [m.msg for m in check_liveviews(None) if m.id == check_id and label in m.msg]


def test_a_subscription_callback_is_not_reported_as_a_missing_handler():
    found = _findings("djust.V004", SubscriptionView)
    assert not [m for m in found if "on_project_menu_selected" in m], found
    assert [m for m in found if "on_plain_click" in m], found


def test_the_shipped_interactive_example_passes_its_own_checks():
    # A scaffolded project's `manage.py check` must stay warning-free
    # (tests/integration/test_scaffold_boot_1787.py); djust's example modules
    # are checked like user code (checks/utils._is_framework_internal_class).
    from djust.checks.configuration import check_configuration
    from djust.components.interactive_examples import DropdownMenuExample

    label = "djust.components.interactive_examples.DropdownMenuExample"
    assert not _findings("djust.V004", DropdownMenuExample)
    assert not [m.msg for m in check_configuration(None) if m.id == "djust.S005" and label in m.msg]
