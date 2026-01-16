"""
Django app configuration for djust_rentals
"""

from django.apps import AppConfig


class DjustRentalsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'djust_rentals'
    verbose_name = 'Rental Property Management'

    def ready(self):
        """
        Perform app initialization
        """
        # Import signals here if needed
        pass
