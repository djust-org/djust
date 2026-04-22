from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView

from .forms import SignupForm


class SignupView(CreateView):
    form_class = SignupForm
    template_name = "djust_auth/signup.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(getattr(settings, "LOGIN_REDIRECT_URL", "/"))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect(self.get_success_url())

    def get_success_url(self):
        next_url = self.request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return getattr(settings, "LOGIN_REDIRECT_URL", "/")


class DjustLoginView(auth_views.LoginView):
    template_name = "djust_auth/login.html"
    redirect_authenticated_user = True


def logout_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    url = getattr(settings, "LOGOUT_REDIRECT_URL", "/")
    return redirect(url)
