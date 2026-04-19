"""Test URL configuration that includes djust_theming URLs with namespace."""

from django.urls import include, path

urlpatterns = [
    path("theming/", include("djust.theming.urls")),
]
