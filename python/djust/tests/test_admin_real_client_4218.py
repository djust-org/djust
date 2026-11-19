"""#4218 with the real client: the built ``client.js`` in jsdom, against the admin pages.

``_auto_recover_bridge`` runs the client in jsdom and relays every frame it
sends to the real server. Here its fetches are answered by the admin route
the URL names, as the person signed in, so what the page sends is exactly
what a browser on the HTTP fallback would send.
"""

from __future__ import annotations

import json
from typing import Any, Tuple
from urllib.parse import urlsplit

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory
from django.urls import resolve

from djust.tests._auto_recover_bridge import Browser
from djust.tests.test_admin_change_form_4218 import (  # noqa: F401  (fixture)
    LIST,
    RADIO_LIST,
    REQUIRED,
    _seed,
    site_urls,
)
from djust_rentals.models import Property

pytestmark = pytest.mark.admin

TYPE = """
(function (values) {
  for (const [name, value] of Object.entries(values)) {
    const el = document.querySelector('[name="' + name + '"]');
    if (el.type === 'checkbox') { if (el.checked !== value) el.click(); continue; }
    if (el.type === 'radio') {
      document.querySelector('[name="' + name + '"][value="' + value + '"]').click();
      continue;
    }
    el.value = value;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  return null;
})(%s)
"""

INVALID = (
    "JSON.stringify(Array.from(document.querySelectorAll('form[dj-submit] :invalid'), e => e.name))"
)

PRESS = """
(function (name) {
  const form = document.querySelector('form[dj-submit]');
  const button = name ? form.querySelector('button[type="submit"][name="' + name + '"]')
                      : form.querySelector('button[type="submit"]:not([name])');
  button.click();
  return null;
})(%s)
"""


class Admin:
    """The signed-in person: their browser session and the admin routes."""

    def __init__(self, user: Any, key: str) -> None:
        self.user, self.key = user, key

    def page(self, url: str) -> str:
        browser = Client()
        browser.cookies[settings.SESSION_COOKIE_NAME] = self.key
        response = browser.get(url)
        assert response.status_code == 200, response.status_code
        return response.content.decode()

    def post(self, url: str, fetch: dict) -> Tuple[int, str]:
        """The page POST, answered by the admin route for ``url`` (the page the client is on)."""
        headers = {
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in (fetch.get("headers") or {}).items()
            if name.lower() != "content-type"
        }
        request = RequestFactory().post(
            url, data=fetch["body"] or "{}", content_type="application/json", **headers
        )
        request.user, request.session, request.tenant = self.user, SessionStore(self.key), None
        match = resolve(urlsplit(url).path)
        response = match.func(request, *match.args, **match.kwargs)
        return response.status_code, response.content.decode()


async def _admin(django_user_model) -> Admin:
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)
    return Admin(user, browser.cookies[settings.SESSION_COOKIE_NAME].value)


async def _answer(browser: Browser, admin: Admin, url: str) -> dict:
    """Answer the page's next POST; returns the frame the server answered."""
    fetch = await browser.expect(lambda m: m["op"] == "fetch", "the page POST")
    status, body = await sync_to_async(admin.post)(url, fetch)
    await browser.tell(op="fetch_reply", id=fetch["id"], status=status, body=body)
    return {
        "event": fetch["headers"].get("X-Djust-Event"),
        "params": json.loads(fetch["body"]),
        "status": status,
        "answer": json.loads(body),
    }


async def _save_on(admin: Admin, url: str, typed: dict, button: str | None) -> list:
    """Open ``url`` in the real client over the HTTP fallback, type, press ``button``."""
    browser = await Browser.open(await sync_to_async(admin.page)(url), "http")
    try:
        await browser.eval(TYPE % json.dumps(typed))
        invalid = await browser.eval(INVALID)
        assert invalid == "[]", "the form would not submit: %s" % invalid
        answers = []
        # Each typed field may send a validate_field before the Save; answer them all.
        await browser.eval(PRESS % json.dumps(button))
        while True:
            answer = await _answer(browser, admin, url)
            answers.append(answer)
            if answer["event"] != "validate_field":
                return answers
    finally:
        await browser.close()


def _navigation(answer: dict) -> list:
    return [
        n["path"] for n in answer["answer"].get("_navigation", []) if n["action"] == "live_redirect"
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("site_urls")
async def test_add_then_continue_twice_makes_one_row(django_user_model):
    """Save and continue on the add page goes to the new row's change page, so the next Save
    edits that row (Django's behaviour): two saves, one row."""
    admin = await _admin(django_user_model)
    answers = await _save_on(
        admin, LIST + "add/", {**REQUIRED, "name": "First"}, "save_and_continue"
    )
    row = await sync_to_async(Property.objects.get)()
    assert _navigation(answers[-1]) == ["%s%d/change/" % (LIST, row.pk)], answers[-1]
    # The browser follows the navigation; the next Save is on the change page.
    await _save_on(admin, _navigation(answers[-1])[0], {"name": "Second"}, "save_and_continue")
    names = await sync_to_async(lambda: list(Property.objects.values_list("name", flat=True)))()
    assert names == ["Second"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("site_urls")
async def test_a_radio_choice_form_submits_and_saves(django_user_model):
    """A required choice field drawn as radios, and a stored zero: the form is valid as
    rendered (a lone unchecked required radio, or an emptied ``0``, blocked every Save)."""
    row = await _seed()
    await sync_to_async(Property.objects.filter(pk=row.pk).update)(bedrooms=0)
    admin = await _admin(django_user_model)
    url = "%s%d/change/" % (RADIO_LIST, row.pk)
    answers = await _save_on(admin, url, {"property_type": "condo"}, "save_and_continue")
    assert answers[-1]["event"] == "save" and answers[-1]["status"] == 200, answers[-1]
    saved = await sync_to_async(
        lambda: Property.objects.values_list("property_type", "bedrooms").get(pk=row.pk)
    )()
    assert saved == ("condo", 0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("site_urls")
async def test_delete_with_the_real_client(django_user_model):
    row = await _seed()
    admin = await _admin(django_user_model)
    url = "%s%d/delete/" % (LIST, row.pk)
    browser = await Browser.open(await sync_to_async(admin.page)(url), "http")
    try:
        await browser.eval("document.querySelector('[dj-click^=confirm_delete]').click(); null")
        answer = await _answer(browser, admin, url)
    finally:
        await browser.close()
    assert answer["status"] == 200, answer
    assert _navigation(answer) == [LIST]
    assert not await sync_to_async(Property.objects.filter(pk=row.pk).exists)()
