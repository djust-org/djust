"""Admin authentication must finish over HTTP so Django can rotate cookies."""

import re

import pytest
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.test import Client

from djust.tests.test_admin_page_render import PREFIX, _Socket, _session_cookie

pytestmark = [pytest.mark.admin, pytest.mark.django_db]
PASSWORD = "session-rotation-test-password"


@pytest.fixture(autouse=True)
def login_settings(settings):
    settings.ROOT_URLCONF = "djust.tests.test_admin_page_render"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust"]


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user("staff", password=PASSWORD, is_staff=True)


def login_form(browser, query="", secure=False):
    response = browser.get(PREFIX + "login/" + query, secure=secure)
    assert response.status_code == 200
    html = response.content.decode()
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    assert match, "login must render an ordinary CSRF-protected HTTP form"
    return match[1], html


def test_http_login_rotates_session_and_csrf_cookies(staff):
    browser = Client(enforce_csrf_checks=True)
    session = browser.session
    session["before_login"] = "retained"
    session.save()
    old_key = session.session_key
    csrf, html = login_form(browser)
    old_csrf = browser.cookies[settings.CSRF_COOKIE_NAME].value
    assert '<form method="post"' in html
    assert "dj-root" not in html
    assert "dj-input" not in html
    response = browser.post(
        PREFIX + "login/",
        {
            "username": staff.username,
            "password": PASSWORD,
            "csrfmiddlewaretoken": csrf,
        },
    )
    assert response.status_code == 302
    assert response["Location"] == PREFIX
    new_key = browser.cookies[settings.SESSION_COOKIE_NAME].value
    assert new_key != old_key
    assert not Session.objects.filter(session_key=old_key).exists()
    assert browser.session[SESSION_KEY] == str(staff.pk)
    assert browser.session["before_login"] == "retained"
    assert browser.cookies[settings.CSRF_COOKIE_NAME].value != old_csrf
    assert browser.get(PREFIX).status_code == 200
    fixed_cookie = Client()
    fixed_cookie.cookies[settings.SESSION_COOKIE_NAME] = old_key
    assert fixed_cookie.get(PREFIX).status_code == 302


@pytest.mark.parametrize("csrf", [None, "invalid-token"])
def test_login_rejects_missing_or_invalid_csrf_before_authentication(staff, csrf, settings):
    # The endpoint is protected even without project-wide CSRF middleware.
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if not m.endswith("CsrfViewMiddleware")]
    browser = Client(enforce_csrf_checks=True)
    login_form(browser)
    data = {"username": staff.username, "password": PASSWORD}
    if csrf is not None:
        data["csrfmiddlewaretoken"] = csrf
    response = browser.post(PREFIX + "login/", data)
    assert response.status_code == 403
    assert SESSION_KEY not in browser.session


@pytest.mark.parametrize(
    "active,staff_access,password",
    [
        (True, True, "wrong-password"),
        (True, False, PASSWORD),
        (False, True, PASSWORD),
    ],
)
def test_failed_login_never_authenticates_or_echoes_password(
    django_user_model, active, staff_access, password
):
    django_user_model.objects.create_user(
        "member", password=PASSWORD, is_active=active, is_staff=staff_access
    )
    browser = Client(enforce_csrf_checks=True)
    csrf, _ = login_form(browser)
    response = browser.post(
        PREFIX + "login/",
        {
            "username": "member",
            "password": password,
            "csrfmiddlewaretoken": csrf,
            "next": PREFIX + "auth/group/",
        },
    )
    assert response.status_code == 200
    assert SESSION_KEY not in browser.session
    html = response.content.decode()
    assert password not in html
    assert 'value="member"' in html
    assert 'value="' + PREFIX + 'auth/group/"' in html
    assert "staff account" in html


def test_authenticated_account_switch_flushes_previous_session(staff, django_user_model):
    other = django_user_model.objects.create_user("other", password=PASSWORD, is_staff=True)
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(other)
    session = browser.session
    session["previous_user_private_data"] = "discard"
    session.save()
    old_key = session.session_key
    csrf, _ = login_form(browser)
    response = browser.post(
        PREFIX + "login/",
        {
            "username": staff.username,
            "password": PASSWORD,
            "csrfmiddlewaretoken": csrf,
        },
    )
    assert response.status_code == 302
    assert browser.session[SESSION_KEY] == str(staff.pk)
    assert "previous_user_private_data" not in browser.session
    assert not Session.objects.filter(session_key=old_key).exists()


@pytest.mark.django_db(transaction=True)
async def test_websocket_cannot_mount_login_or_authenticate(staff):
    from asgiref.sync import sync_to_async

    browser = Client()
    await sync_to_async(lambda: browser.session.save())()
    cookie = _session_cookie(browser)
    async with _Socket(cookie) as ws:
        await ws.send(
            {"type": "mount", "view": "djust.admin_ext.views.LoginView", "url": PREFIX + "login/"}
        )
        for _ in range(3):
            frame = await ws.comm.receive_json_from(timeout=10)
            if frame["type"] != "connect":
                break
        assert frame["type"] == "error", frame
    assert SESSION_KEY not in await sync_to_async(lambda: dict(browser.session))()


@pytest.mark.parametrize(
    "destination,expected",
    [
        ("http://testserver/private/", PREFIX),
        ("https://testserver/private/", "https://testserver/private/"),
    ],
)
def test_secure_login_never_redirects_to_plain_http(staff, destination, expected):
    browser = Client(enforce_csrf_checks=True)
    csrf, _ = login_form(browser, secure=True)
    response = browser.post(
        PREFIX + "login/",
        {
            "username": staff.username,
            "password": PASSWORD,
            "csrfmiddlewaretoken": csrf,
            "next": destination,
        },
        secure=True,
        HTTP_REFERER="https://testserver" + PREFIX + "login/",
    )
    assert response.status_code == 302
    assert response["Location"] == expected


def test_login_missing_fields_returns_bound_form_without_authentication():
    browser = Client(enforce_csrf_checks=True)
    csrf, _ = login_form(browser)
    response = browser.post(PREFIX + "login/", {"csrfmiddlewaretoken": csrf})
    assert response.status_code == 200
    assert "This field is required" in response.content.decode()
    assert SESSION_KEY not in browser.session


@pytest.mark.parametrize(
    "destination", ["https://other.example/", "//other.example/", "javascript:alert(1)"]
)
def test_login_rejects_forged_post_redirect(staff, destination):
    browser = Client(enforce_csrf_checks=True)
    csrf, _ = login_form(browser)
    response = browser.post(
        PREFIX + "login/",
        {
            "username": staff.username,
            "password": PASSWORD,
            "csrfmiddlewaretoken": csrf,
            "next": destination,
        },
    )
    assert response.status_code == 302
    assert response["Location"] == PREFIX
