"""The incremental Django workflow example (#3659): ``examples/demo_project/djust_workflow``.

Covers what the guide ``docs/website/guides/incremental-django-workflow.md`` claims:
URL-backed filter and pagination, server validation, permissions, the visible error
state, and that a reconnect (a second mount at the same URL) shows the same page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import Client

from djust.testing import LiveViewTestClient, MountRedirect
from djust_workflow import views
from djust_workflow.models import Ticket
from djust_workflow.views import (
    PAGE_SIZE,
    NotificationError,
    TicketCreateView,
    TicketDetailView,
    TicketEditView,
    TicketListView,
)

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/website/guides/incremental-django-workflow.md"
EXAMPLE = ROOT / "examples/demo_project/djust_workflow"


def make_user(name, *codenames):
    user = User.objects.create_user(name, password="pw")
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return User.objects.get(pk=user.pk)  # drop the permission cache


@pytest.fixture
def staff():
    return make_user("staff", "view_ticket", "add_ticket", "change_ticket")


@pytest.fixture
def tickets(staff):
    # 7 open "Printer N" tickets and 3 closed "VPN N" tickets: 2 pages of open ones.
    for i in range(7):
        Ticket.objects.create(title="Printer %d" % i, created_by=staff)
    for i in range(3):
        Ticket.objects.create(title="VPN %d" % i, status="closed", created_by=staff)


def mount_list(user, query=None):
    return LiveViewTestClient(TicketListView, user=user).mount(query_params=query)


# --- list: URL-backed filter and pagination --------------------------------


def test_filter_and_page_come_from_the_url(staff, tickets):
    client = mount_list(staff, "status=open&page=2")
    state = client.get_state()
    assert (state["status"], state["page"]) == ("open", 2)
    html = client.render()
    assert "page 2 of 2" in html
    assert html.count("Printer") == 7 - PAGE_SIZE  # the second page holds the rest


def test_search_and_status_narrow_the_list(staff, tickets):
    html = mount_list(staff, "status=closed&q=vpn").render()
    assert "3 ticket(s)" in html and "Printer" not in html


def test_apply_filter_patches_the_url_and_resets_the_page(staff, tickets):
    client = mount_list(staff, "page=2")
    client.send_event("apply_filter", status="open", q="  printer ")
    client.assert_patch(params={"status": "open", "q": "printer", "page": 1})


def test_url_change_is_back_forward(staff, tickets):
    client = mount_list(staff, "status=open&page=2")
    client.url_change("status=closed")  # what back/forward or a live_patch sends
    assert client.get_state()["status"] == "closed"
    assert client.get_state()["page"] == 1
    client.url_change("status=open&page=2")
    assert client.get_state()["page"] == 2


def test_a_reconnect_at_the_same_url_shows_the_same_page(staff, tickets):
    before = mount_list(staff, "status=open&q=printer&page=2").render()
    after = mount_list(staff, "status=open&q=printer&page=2").render()  # a new mount
    assert re.sub(r"\s+", " ", before) == re.sub(r"\s+", " ", after)


def test_a_bad_query_string_is_a_message_not_a_500(staff, tickets):
    client = Client()
    client.force_login(staff)
    response = client.get("/workflow/?page=abc&status=open")
    assert response.status_code == 200
    body = response.content.decode()
    assert "defaults are shown" in body  # params_errors reached the page
    assert "Printer" in body


# --- permissions -----------------------------------------------------------


def test_anonymous_is_sent_to_login():
    with pytest.raises(MountRedirect):
        LiveViewTestClient(TicketListView).mount()


def test_a_user_without_view_ticket_is_denied():
    with pytest.raises(PermissionDenied):
        LiveViewTestClient(TicketListView, user=make_user("nobody")).mount()


def test_a_user_sees_only_their_own_tickets(staff, tickets):
    mine = make_user("mine", "view_ticket")
    Ticket.objects.create(title="Mine only", created_by=mine)
    html = mount_list(mine).render()
    assert "Mine only" in html and "Printer" not in html


def test_detail_hides_somebody_elses_ticket(staff, tickets):
    other = make_user("other", "view_ticket")
    ticket = Ticket.objects.filter(created_by=staff).first()
    client = LiveViewTestClient(TicketDetailView, user=other)
    client.mount(pk=ticket.pk)
    with pytest.raises(Http404):
        client.render()


def test_missing_and_not_visible_tickets_are_both_404(staff, tickets):
    other = make_user("other2", "view_ticket")
    theirs = Ticket.objects.filter(created_by=staff).first()
    client = Client()
    client.force_login(other)
    missing = client.get("/workflow/999999/")
    hidden = client.get("/workflow/%d/" % theirs.pk)
    assert missing.status_code == hidden.status_code == 404
    client.force_login(staff)
    assert client.get("/workflow/%d/" % theirs.pk).status_code == 200


def test_create_needs_add_ticket():
    viewer = make_user("viewer", "view_ticket")
    with pytest.raises(PermissionDenied):
        LiveViewTestClient(TicketCreateView, user=viewer).mount()


def test_closing_needs_change_ticket(tickets, staff):
    owner = make_user("owner", "view_ticket")
    ticket = Ticket.objects.create(title="Owner's ticket", created_by=owner)
    client = LiveViewTestClient(TicketDetailView, user=owner)
    client.mount(pk=ticket.pk)
    result = client.send_event("close_ticket", raise_on_missing=False)
    assert not result["success"]
    ticket.refresh_from_db()
    assert ticket.status == "open"


# --- forms: server validation ----------------------------------------------


def submit(client, **fields):
    data = {"title": "", "body": "", "priority": "normal"}
    data.update(fields)
    return client.send_event("submit_form", **data)


def test_create_validates_on_the_server(staff):
    client = LiveViewTestClient(TicketCreateView, user=staff).mount()
    submit(client, title="Hi")
    assert client.get_state()["field_errors"]["title"] == ["Use at least 5 characters."]
    assert Ticket.objects.count() == 0
    assert "Please correct the errors below." in client.render()


def test_a_cross_field_rule_and_a_database_rule(staff):
    client = LiveViewTestClient(TicketCreateView, user=staff).mount()
    submit(client, title="Server down", priority="urgent", body="short")
    assert "body" in client.get_state()["field_errors"]
    Ticket.objects.create(title="Server down", created_by=staff)
    submit(client, title="server DOWN")
    assert client.get_state()["field_errors"]["title"] == ["An open ticket already has this title."]


def test_a_valid_create_saves_as_the_user_and_redirects(staff):
    client = LiveViewTestClient(TicketCreateView, user=staff).mount()
    submit(client, title="Broken laptop", body="Screen is cracked")
    ticket = Ticket.objects.get()
    assert ticket.created_by == staff and ticket.status == "open"
    client.assert_redirect(path="/workflow/%d/" % ticket.pk)


def test_edit_validates_and_saves(staff, tickets):
    ticket = Ticket.objects.filter(status="open").first()
    client = LiveViewTestClient(TicketEditView, user=staff)
    client.mount(route_kwargs={"pk": ticket.pk})
    submit(client, title="no")
    assert "title" in client.get_state()["field_errors"]
    submit(client, title="Renamed ticket")
    ticket.refresh_from_db()
    assert ticket.title == "Renamed ticket"


def test_a_form_cannot_set_the_owner_or_status(staff):
    client = LiveViewTestClient(TicketCreateView, user=staff).mount()
    submit(client, title="Sneaky ticket", status="closed", created_by=999)
    assert Ticket.objects.get().status == "open"


# --- loading and error states ----------------------------------------------


def test_loading_directives_are_in_the_markup(staff, tickets):
    assert "dj-loading.disable" in mount_list(staff).render()


def test_closing_a_ticket_shows_success(staff, tickets):
    ticket = Ticket.objects.filter(status="open").first()
    client = LiveViewTestClient(TicketDetailView, user=staff)
    client.mount(pk=ticket.pk)
    client.send_event("close_ticket")
    ticket.refresh_from_db()
    assert ticket.status == "closed"
    assert "Ticket closed." in client.render()


def test_a_failing_notification_rolls_back_and_shows_the_error(staff, tickets, monkeypatch):
    def fail(ticket):
        raise NotificationError("smtp down")

    monkeypatch.setattr(views, "notify_requester", fail)
    ticket = Ticket.objects.filter(status="open").first()
    client = LiveViewTestClient(TicketDetailView, user=staff)
    client.mount(pk=ticket.pk)
    client.send_event("close_ticket")
    ticket.refresh_from_db()
    assert ticket.status == "open"  # rolled back
    assert "could not be notified" in client.render()


def test_closing_twice_shows_the_stale_state_error(staff, tickets):
    ticket = Ticket.objects.filter(status="open").first()
    client = LiveViewTestClient(TicketDetailView, user=staff)
    client.mount(pk=ticket.pk)
    Ticket.objects.filter(pk=ticket.pk).update(status="closed")  # closed in another tab
    client.send_event("close_ticket")
    assert "already closed" in client.render()


# --- the guide matches the example -----------------------------------------


def _normal(text):
    return re.sub(r"\s+", " ", text).strip()


def test_every_guide_snippet_is_in_the_example_source():
    """A fenced block marked ``<!-- example: <file> -->`` must be a verbatim excerpt of it."""
    guide = GUIDE.read_text()
    pattern = re.compile(r"<!-- example: (\S+) -->\n```\w+\n(.*?)```", re.S)
    found = pattern.findall(guide)
    assert len(found) >= 8, "the guide lost its example-backed snippets"
    for name, code in found:
        source = (EXAMPLE / name).read_text()
        for chunk in re.split(r"\n\s*# \.\.\.\n", code):
            assert _normal(chunk) in _normal(source), "guide snippet not in %s:\n%s" % (name, chunk)
