from django.urls import include, path

from . import views

app_name = "djust_theming"

urlpatterns = [
    path("theme.css", views.theme_css_view, name="theme_css"),
    path("deferred.css", views.deferred_theme_css_view, name="deferred_theme_css"),
    path("gallery/", include("djust.theming.gallery.urls")),
]
