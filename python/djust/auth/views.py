from typing import Any

from django.conf import settings
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from django.contrib.auth import views as auth_views
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView

from .forms import SignupForm


class SignupView(CreateView):
    form_class = SignupForm
    template_name = "djust_auth/signup.html"
    extra_context = {"auth_step": "signup"}

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.user.is_authenticated:
            return redirect(getattr(settings, "LOGIN_REDIRECT_URL", "/"))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: Any) -> HttpResponse:
        from .accounts import get_account_backend
        from .signals import user_signed_up

        backend = get_account_backend()
        try:
            backend.run_signup_validators(self.request, form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        user = form.save()
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        user_signed_up.send(sender=type(backend), request=self.request, user=user)
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        default_url: str = getattr(settings, "LOGIN_REDIRECT_URL", "/")
        next_url: str = self.request.POST.get("next", "")
        if not next_url:
            return default_url
        if not url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return default_url
        return next_url


class DjustLoginView(auth_views.LoginView):
    template_name = "djust_auth/login.html"
    redirect_authenticated_user = True
    extra_context = {"auth_step": "login"}


def logout_view(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    url = getattr(settings, "LOGOUT_REDIRECT_URL", "/")
    return redirect(url)
