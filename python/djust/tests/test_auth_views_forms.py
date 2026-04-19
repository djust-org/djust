"""Tests for djust.auth views, forms, and mixins (from djust-auth package)."""

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.views import View

from djust.auth.forms import SignupForm
from djust.auth.mixins import (
    LoginRequiredLiveViewMixin,
    PermissionRequiredLiveViewMixin,
)

pytestmark = pytest.mark.auth


# ---- Forms ----


class SignupFormTest(TestCase):
    def test_valid_form(self):
        form = SignupForm(
            data={
                "username": "testuser",
                "email": "test@example.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            }
        )
        self.assertTrue(form.is_valid())

    def test_email_required(self):
        form = SignupForm(
            data={
                "username": "testuser",
                "email": "",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_passwords_must_match(self):
        form = SignupForm(
            data={
                "username": "testuser",
                "email": "test@example.com",
                "password1": "SecurePass123!",
                "password2": "DifferentPass456!",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)


# ---- Mixins ----


class StubView(LoginRequiredLiveViewMixin, View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if isinstance(response, HttpResponse) and response.status_code == 302:
            return response
        return HttpResponse("OK")


class StubPermView(PermissionRequiredLiveViewMixin, View):
    permission_required = "auth.view_user"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if isinstance(response, HttpResponse) and response.status_code in (302, 403):
            return response
        return HttpResponse("OK")


class LoginRequiredMixinTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser", password="testpass123")

    def test_anonymous_user_redirected(self):
        request = self.factory.get("/protected/")
        request.user = AnonymousUser()
        response = StubView.as_view()(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_next_param_preserved(self):
        request = self.factory.get("/protected/page/")
        request.user = AnonymousUser()
        response = StubView.as_view()(request)
        self.assertIn("next=%2Fprotected%2Fpage%2F", response.url)

    def test_authenticated_user_passes(self):
        request = self.factory.get("/protected/")
        request.user = self.user
        response = StubView.as_view()(request)
        self.assertEqual(response.status_code, 200)

    def test_custom_login_url(self):
        request = self.factory.get("/protected/")
        request.user = AnonymousUser()
        response = StubView.as_view(login_url="/custom/login/")(request)
        self.assertIn("/custom/login/", response.url)


class PermissionRequiredMixinTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser", password="testpass123")

    def test_user_without_perm_denied(self):
        request = self.factory.get("/protected/")
        request.user = self.user
        with self.assertRaises(Exception):
            StubPermView.as_view()(request)

    def test_user_with_perm_passes(self):
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(User)
        perm = Permission.objects.get(codename="view_user", content_type=ct)
        self.user.user_permissions.add(perm)
        self.user = User.objects.get(pk=self.user.pk)  # Refresh cache

        request = self.factory.get("/protected/")
        request.user = self.user
        response = StubPermView.as_view()(request)
        self.assertEqual(response.status_code, 200)


# ---- Views ----


@override_settings(
    ROOT_URLCONF="djust.auth.urls",
    LOGIN_REDIRECT_URL="/dashboard/",
    LOGOUT_REDIRECT_URL="/",
)
class SignupViewTest(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        client = Client()
        response = client.post(
            "/signup/",
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser").exists())
        user = User.objects.get(username="newuser")
        self.assertEqual(user.email, "new@example.com")

    def test_signup_redirects_to_login_redirect_url(self):
        client = Client()
        response = client.post(
            "/signup/",
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            },
        )
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)


@override_settings(
    ROOT_URLCONF="djust.auth.urls",
    LOGOUT_REDIRECT_URL="/",
)
class LogoutViewTest(TestCase):
    def test_logout_clears_session(self):
        User.objects.create_user(username="testuser", password="testpass123")
        client = Client()
        client.login(username="testuser", password="testpass123")
        response = client.post("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_logout_rejects_get(self):
        User.objects.create_user(username="testuser", password="testpass123")
        client = Client()
        client.login(username="testuser", password="testpass123")
        response = client.get("/logout/")
        self.assertEqual(response.status_code, 405)
