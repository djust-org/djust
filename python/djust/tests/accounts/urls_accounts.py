from django.urls import include, path

urlpatterns = [path("accounts/", include("djust.auth.accounts.urls"))]
