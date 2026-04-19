from django.urls import path

from . import views

app_name = "djust_auth"

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("login/", views.DjustLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
]

# NOTE: allauth.urls must be included at the project level (not inside
# this namespaced app) so allauth's internal reverse() calls work.
# In your project urls.py, add:
#   path("accounts/", include("allauth.urls"))
