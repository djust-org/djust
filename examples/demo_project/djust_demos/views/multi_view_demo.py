"""#3252 live surface: several LiveViews on one WebSocket, each independently live.

The page holds an eager view, two ``dj-lazy`` views of one class that hydrate
together (one ``mount_batch``) and a third that hydrates on its own click (one
``mount``). ``tests/playwright/test_multi_view.py`` clicks in each, pushes to
each from a handler and checks that every view keeps answering, and that no
click or push lands on another view.
"""

from djust import LiveView
from djust.decorators import event_handler
from djust.push import push_to_view
from djust.uploads import UploadMixin

WIDGET = "djust_demos.views.multi_view_demo.MultiViewWidget"
LATE = "djust_demos.views.multi_view_demo.MultiViewLateWidget"
UPLOADER = "djust_demos.views.multi_view_demo.MultiViewUploader"


class MultiViewPage(LiveView):
    """The page's own view, mounted by the stock client's ``autoMount``."""

    template_name = "demos/multi_view.html"

    def mount(self, request, **kwargs):
        self.clicks = 0
        self.pushes = 0

    @event_handler
    def click(self, **kwargs):
        self.clicks += 1

    @event_handler
    def bump(self, **kwargs):
        self.pushes += 1

    @event_handler
    def push_widgets(self, **kwargs):
        """Push to the lazy views from a handler of the page view."""
        push_to_view(WIDGET, handler="bump")

    @event_handler
    def push_late(self, **kwargs):
        push_to_view(LATE, handler="bump")

    @event_handler
    def push_page(self, **kwargs):
        """A push to the page's own view: the page already answers the click
        that sent it, so it is not applied twice (#1677)."""
        push_to_view("djust_demos.views.multi_view_demo.MultiViewPage", handler="bump")


class MultiViewWidget(LiveView):
    """A lazily hydrated view; the page holds two of them."""

    template = (
        '<div dj-root><p>clicks <b data-role="clicks">{{ clicks }}</b> '
        'pushes <b data-role="pushes">{{ pushes }}</b></p>'
        '<button data-role="click" dj-click="click">click</button> '
        # A hook inside the view: its pushEvent runs on this view, not the page's
        # (which has a handler of the same name).
        '<button data-role="hook-click" dj-hook="MultiViewPing">hook click</button></div>'
    )

    def mount(self, request, **kwargs):
        self.clicks = 0
        self.pushes = 0

    @event_handler
    def click(self, **kwargs):
        self.clicks += 1

    @event_handler
    def bump(self, **kwargs):
        self.pushes += 1


class MultiViewLateWidget(MultiViewWidget):
    """Hydrates alone, on the user's first click (a single ``mount``)."""


class MultiViewUploader(UploadMixin, LiveView):
    """A lazy view with a file input: the upload belongs to this view."""

    template = (
        '<div dj-root><input type="file" data-role="file" dj-upload="doc">'
        '<button data-role="save" dj-click="save">save</button>'
        '<ul data-role="saved">{% for name in saved %}<li>{{ name }}</li>{% endfor %}</ul></div>'
    )

    def mount(self, request, **kwargs):
        self.saved = []
        self.allow_upload("doc", accept=".txt", max_entries=2)

    @event_handler
    def save(self, **kwargs):
        for entry in self.consume_uploaded_entries("doc"):
            self.saved.append("%s (%d bytes)" % (entry.client_name, entry.client_size))
