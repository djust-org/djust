from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
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
        return self.request.POST.get(
            "next",
            getattr(settings, "LOGIN_REDIRECT_URL", "/"),
        )


class DjustLoginView(auth_views.LoginView):
    template_name = "djust_auth/login.html"
    redirect_authenticated_user = True


def logout_view(request):
    logout(request)
    url = getattr(settings, "LOGOUT_REDIRECT_URL", "/")
    return redirect(url)
