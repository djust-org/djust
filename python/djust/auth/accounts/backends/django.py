"""The ``django`` account backend: ``django.contrib.auth`` rendered by the djust kit (ADR-039).

The backward-compatible default. It offers log in, log out, sign up and
Django's password reset. For email verification, social sign-in and
reset-by-code, use the ``allauth`` backend.
"""

from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from ..base import AccountBackend


class DjangoBackend(AccountBackend):
    name = "django"
    features = frozenset({"login", "logout", "signup", "password_reset"})

    def urlpatterns(self) -> list:
        from djust.auth.views import SignupView, logout_view

        pages = "djust_auth/pages/"
        return [
            path(
                "login/",
                auth_views.LoginView.as_view(
                    template_name=pages + "login.html",
                    redirect_authenticated_user=True,
                    extra_context={"auth_step": "login"},
                ),
                name="login",
            ),
            path("logout/", logout_view, name="logout"),
            path(
                "signup/",
                SignupView.as_view(
                    template_name=pages + "signup.html", extra_context={"auth_step": "signup"}
                ),
                name="signup",
            ),
            path(
                "password/reset/",
                auth_views.PasswordResetView.as_view(
                    template_name=pages + "password_reset.html",
                    success_url=reverse_lazy("djust_auth:password_reset_done"),
                    extra_context={"auth_step": "password_reset"},
                ),
                name="password_reset",
            ),
            path(
                "password/reset/sent/",
                auth_views.PasswordResetDoneView.as_view(
                    template_name=pages + "password_reset_sent.html"
                ),
                name="password_reset_done",
            ),
            path(
                "password/reset/<uidb64>/<token>/",
                auth_views.PasswordResetConfirmView.as_view(
                    template_name=pages + "password_reset_confirm.html",
                    success_url=reverse_lazy("djust_auth:password_reset_complete"),
                    extra_context={"auth_step": "password_reset_confirm"},
                ),
                name="password_reset_confirm",
            ),
            path(
                "password/reset/done/",
                auth_views.PasswordResetCompleteView.as_view(
                    template_name=pages + "password_reset_done.html"
                ),
                name="password_reset_complete",
            ),
        ]
