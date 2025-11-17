"""
URL configuration for demo_project.
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),

    # New organized apps
    path('', include('djust_homepage.urls')),       # Homepage and embedded demos
    path('demos/', include('djust_demos.urls')),     # Feature demonstrations
    path('forms/', include('djust_forms.urls')),     # Forms demonstrations
    path('tests/', include('djust_tests.urls')),     # Test views
    path('docs/', include('djust_docs.urls')),       # Documentation
    path('rentals/', include('djust_rentals.urls')), # Rental property management

    # Old app (for backwards compatibility - will be phased out)
    # path('', include('demo_app.urls')),
]
