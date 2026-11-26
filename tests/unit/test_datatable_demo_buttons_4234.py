"""#4234: the /demos/datatable/ buttons dispatch handlers the view defines.

The page's "Add Sample Products", "Clear All" and "Toggle Inactive" buttons
call ``handleAction(<name>)``, which POSTs ``{"event": <name>, "params": {}}``
to the page URL. Each name must be an ``@event_handler`` on
``ProductDataTableView``; before the fix all three answered "Event rejected".
"""

import json
import re
from pathlib import Path

import pytest
from django.test import Client
from djust.tests._frames import payloads_of

URL = "/demos/datatable/"
TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "examples/demo_project/djust_demos/templates/demos/datatable.html"
)


def _button_events():
    return re.findall(r"handleAction\('([a-z_]+)'\)", TEMPLATE.read_text())


def test_template_buttons_are_the_three_named_in_the_issue():
    assert sorted(_button_events()) == ["add_sample_products", "clear_products", "toggle_inactive"]


@pytest.mark.django_db
@pytest.mark.parametrize("event", _button_events())
def test_each_button_click_is_accepted(event):
    client = Client()
    assert client.get(URL).status_code == 200
    resp = client.post(
        URL, data=json.dumps({"event": event, "params": {}}), content_type="application/json"
    )
    body = resp.content.decode()
    assert resp.status_code == 200, body
    assert "Event rejected" not in body


def _click(client, event):
    resp = client.post(
        URL, data=json.dumps({"event": event, "params": {}}), content_type="application/json"
    )
    assert resp.status_code == 200, resp.content.decode()
    pushed = [e for e in payloads_of(resp.json(), "push_event") if e["event"] == "products_updated"]
    assert len(pushed) == 1, resp.json()
    payload = pushed[0]["payload"]
    assert set(payload) == {"products", "stats"}
    return payload


@pytest.mark.django_db
def test_clicks_accumulate_and_push_products_and_stats():
    client = Client()
    assert client.get(URL).status_code == 200

    added = _click(client, "add_sample_products")
    assert added["stats"]["total_products"] == 30
    ids = [p["id"] for p in added["products"]]
    assert ids == list(range(1, 31))

    again = _click(client, "add_sample_products")
    assert again["stats"]["total_products"] == 40

    before = {p["id"]: p["active"] for p in again["products"]}
    toggled = _click(client, "toggle_inactive")
    after = {p["id"]: p["active"] for p in toggled["products"]}
    assert after.keys() == before.keys()
    assert sum(before[i] != after[i] for i in before) == 5
    assert toggled["stats"]["active_products"] == sum(after.values())

    cleared = _click(client, "clear_products")
    assert cleared["products"] == []
    assert cleared["stats"]["total_products"] == 0

    # Toggle on an empty table is a no-op, not an error.
    assert _click(client, "toggle_inactive")["products"] == []
