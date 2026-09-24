from django.urls import include, path

urlpatterns = [
    path("accounts/", include("djust.auth.accounts.urls")),
    path("a2/", include("allauth.urls")),
]
