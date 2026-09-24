"""A minimal custom account backend (test-only): email a sign-in link, nothing else.

It declares only ``login`` and ``logout``, so the kit must hide sign-up and
password-reset links. The accounts guide's "Writing a backend" section is
this file, cleaned up.
"""

from django import forms
from django.shortcuts import render
from django.urls import path

from djust.auth.accounts import AccountBackend


class MagicLinkForm(forms.Form):
    email = forms.EmailField(label="Email")


def magic_login(request):
    form = MagicLinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # A real backend would email a signed, expiring link here.
        return render(
            request,
            "djust_auth/pages/verify_sent.html",
            {"auth_step": "verify_sent", "email": form.cleaned_data["email"]},
        )
    return render(request, "djust_auth/pages/login.html", {"form": form, "auth_step": "login"})


class MagicLinkBackend(AccountBackend):
    name = "magic-link"
    features = frozenset({"login", "logout"})

    def urlpatterns(self) -> list:
        from djust.auth.views import logout_view

        return [
            path("login/", magic_login, name="login"),
            path("logout/", logout_view, name="logout"),
        ]
