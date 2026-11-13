"""
Homepage LiveView Demo Components

These are embedded demo components shown on the homepage to showcase
djust's reactive capabilities in action.

The demos are the explicit-exposure views in ``demo_app.views.homepage`` and
``demo_app.views_old`` (#4086): this module used to keep legacy copies of them.
"""

from djust import state

from demo_app.views.homepage import (  # noqa: F401 - routed from djust_homepage.urls
    HomeCounterDemo,
    HomeLiveDataDemo,
    HomeSearchDemo,
    HomeTodoDemo,
)
from demo_app.views_old import IndexView as _IndexView
from demo_app.views_old import NavbarBadgeDemo  # noqa: F401 - routed from djust_homepage.urls


class IndexView(_IndexView):
    """The landing page: ``demo_app.views_old.IndexView`` on this app's template.

    A subclass redeclares the persisted fields it inherits (explicit exposure
    does not inherit a persistence grant silently).
    """

    notification_count = state(0, persist="server")
    demo_counter = state(0, persist="server")
    search_query = state("", persist="server")
    demo_todos = state(default_factory=list, persist="server")
    template_name = "homepage/index.html"
