"""Every demo-project page renders, and its first-paint handlers exist (#3994).

GETs every URL the demo project routes (``examples/demo_project``), the
``<int:pk>`` ones included, twice: on an empty database (the not-found and
no-data paths) and with one row in each rentals table. A page answers below
500. The pages that 500'd before #3994:

* ``/demos/embedded-directives/``: the shared base declared the
  ``persist="server"`` field, and an exposure grant is not inherited;
* ``/demos/no-template/``: an override of ``render_full_template`` dropped
  the ``serialized_context`` argument the page GET passes;
* ``/rentals/portal/``, ``/rentals/portal/maintenance/``,
  ``/rentals/portal/payments/``: ``.first()`` returns ``None`` on an empty
  table, and the bare ``except`` meant for it never ran;
* ``/rentals/portal/`` and ``/rentals/tenants/<pk>/``: a status count
  filtered a sliced queryset;
* ``/rentals/portal/payments/``: an unused ``dateutil`` import;
* ``/rentals/properties/<pk>/``: ``Property.lease_set`` (the relation is
  ``leases``);
* ``/rentals/tenants/<pk>/``: links to the tenant-portal pages with a pk
  they do not take;
* ``/rentals/maintenance/<pk>/`` and ``.../update/``: the context key
  ``request`` is the ``HttpRequest`` in every template, so
  ``request.property.pk`` was empty; the update page also rendered its form
  for a request that does not exist;
* the rentals edit/delete pages for a pk that does not exist: the not-found
  return skipped attributes ``get_context_data`` reads;
* ``/demos/markdown-stream/``: ``{% djust_markdown %}`` was registered for
  djust's own engine only, and the page renders through Django's backend
  (``live_tags`` registers it too now).

``test_first_paint_handlers_exist`` reads each page's first paint and checks
that every event name a root-view element names is an ``@event_handler`` on
that view (strict handlers: anything else is an error on the first click).
Elements of a component or of an embedded child belong to another handler
owner and are skipped.

``KNOWN_FAILURES`` is strict, as in ``tests/test_doc_snippets_execute.py``:
remove an entry when the page is fixed.
"""

from __future__ import annotations

import datetime
import importlib
import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.test import Client
from django.urls import URLResolver, get_resolver

pytestmark = pytest.mark.django_db

KNOWN_FAILURES: dict[str, str] = {}

# Routes that are not demo pages: framework endpoints mounted by the demo urlconf.
_NOT_PAGES = (
    "admin/",
    "accounts/",
    "djust/",
    "_djust/",
    "djust-admin-demo/",
    "sw.js",
    "manifest.json",
)

# The rentals pages a pk addresses, by URL prefix.
_PK_MODELS = {
    "/rentals/properties/": "Property",
    "/rentals/tenants/": "Tenant",
    "/rentals/leases/": "Lease",
    "/rentals/maintenance/": "MaintenanceRequest",
}

_EVENT_ATTR = re.compile(
    r"dj-(?:click|submit|change|input|blur|focus|keydown|keyup|click-away|paste|shortcut)"
)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}  # fmt: skip


def _walk(resolver, prefix=""):
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern), pattern


def _page_routes() -> list[str]:
    routes = []
    for route, _pattern in _walk(get_resolver()):
        if route.startswith(_NOT_PAGES) or route.startswith("^") or "(?P" in route:
            continue
        routes.append("/" + route)
    assert len(routes) > 50, routes  # the walk reached the demo apps
    return sorted(set(routes))


ROUTES = _page_routes()


def _params():
    for route in ROUTES:
        for seeded in (False, True):
            marks = []
            plain = route.replace("<int:pk>", "")
            if plain in KNOWN_FAILURES:
                marks.append(pytest.mark.xfail(reason=KNOWN_FAILURES[plain], strict=True))
            yield pytest.param(
                route, seeded, marks=marks, id=f"{route}-{'seeded' if seeded else 'empty'}"
            )


@pytest.fixture
def admin_client(django_user_model):
    user = django_user_model.objects.create_superuser("demo-admin", "a@example.com", "x")
    client = Client(raise_request_exception=True)
    client.force_login(user)
    return client


def _seed(django_user_model) -> dict[str, int]:
    """One row in each rentals table, linked the way the pages expect."""
    from djust_rentals.models import Lease, MaintenanceRequest, Payment, Property, Tenant

    today = datetime.date.today()
    prop = Property.objects.create(
        name="Elm House", address="1 Elm St", city="Town", state="CA", zip_code="90000",
        property_type="house", bedrooms=2, bathrooms=Decimal("1.0"), square_feet=900,
        monthly_rent=Decimal("1500.00"), security_deposit=Decimal("1500.00"), status="occupied",
    )  # fmt: skip
    user = django_user_model.objects.create_user(
        "tenant", "t@example.com", "x", first_name="Ada", last_name="Lane"
    )
    tenant = Tenant.objects.create(
        user=user, phone="555", emergency_contact_name="Bo", emergency_contact_phone="556"
    )
    lease = Lease.objects.create(
        property=prop, tenant=tenant, start_date=today - datetime.timedelta(days=60),
        end_date=today + datetime.timedelta(days=300), monthly_rent=Decimal("1500.00"),
        security_deposit=Decimal("1500.00"), rent_due_day=1, status="active",
    )  # fmt: skip
    Payment.objects.create(
        lease=lease, amount=Decimal("1500.00"), payment_date=today, payment_method="check"
    )
    request = MaintenanceRequest.objects.create(
        property=prop, tenant=tenant, title="Leaky tap", description="Drips", status="open"
    )
    return {
        "Property": prop.pk,
        "Tenant": tenant.pk,
        "Lease": lease.pk,
        "MaintenanceRequest": request.pk,
    }


def _url(route: str, pks: dict[str, int]) -> str:
    if "<int:pk>" not in route:
        return route
    model = next((m for prefix, m in _PK_MODELS.items() if route.startswith(prefix)), None)
    # An unseeded table, or a page with no seeded model, takes the not-found path.
    return route.replace("<int:pk>", str(pks.get(model, 999_999)))


@pytest.mark.parametrize("route, seeded", list(_params()))
def test_demo_page_renders(route, seeded, admin_client, django_user_model):
    pks = _seed(django_user_model) if seeded else {}
    url = _url(route, pks)
    response = admin_client.get(url, HTTP_HOST="localhost")
    assert response.status_code < 500, (url, response.status_code)


class _Events(HTMLParser):
    """(view path or None for the page's root view, event name) per root-owned element."""

    def __init__(self):
        super().__init__()
        self.stack: list[str | None | bool] = []
        self.found: list[tuple[str | None, str]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        owner = self.stack[-1] if self.stack else None
        if attributes.get("dj-view"):
            owner = attributes["dj-view"]
        if (
            "data-djust-embedded" in attributes
            or attributes.get("data-component-id")
            or "dj-component" in attributes
            or "dj-target" in attributes
        ):
            owner = False  # a component's or an embedded child's handler
        if owner is not False:
            for key, value in attributes.items():
                if key and _EVENT_ATTR.fullmatch(key) and value:
                    self.found.append((owner, value))
        if tag not in _VOID:
            self.stack.append(owner)

    def handle_endtag(self, tag):
        if tag not in _VOID and self.stack:
            self.stack.pop()


def _handler_name(value: str) -> str | None:
    value = value.strip()
    if "{" in value:
        return None
    value = value.split(":")[-1].split("(")[0].strip()  # dj-shortcut="ctrl+k:name"
    return value if _NAME.fullmatch(value) else None


def _first_paint_handler_errors(client, route: str) -> list[str]:
    from djust.decorators import is_event_handler
    from django.urls import resolve

    response = client.get(route, HTTP_HOST="localhost")
    if response.status_code != 200 or getattr(response, "streaming", False):
        return []
    view_class = getattr(resolve(route).func, "view_class", None)
    if view_class is None or not hasattr(view_class, "mount"):
        return []
    parser = _Events()
    parser.feed(response.content.decode("utf-8", "replace"))
    errors = []
    for view_path, value in parser.found:
        name = _handler_name(value)
        if name is None:
            continue
        owner = view_class
        if view_path:
            module, _, attr = view_path.rpartition(".")
            owner = getattr(importlib.import_module(module), attr)
        handler = getattr(owner, name, None)
        if handler is None or not is_event_handler(handler):
            errors.append(f"{route}: {owner.__name__}.{name}")
    return errors


def test_first_paint_handlers_exist(admin_client):
    errors = []
    for route in ROUTES:
        if "<" in route or route in KNOWN_FAILURES:
            continue
        errors.extend(_first_paint_handler_errors(admin_client, route))
    assert errors == []


def _liveview_routes() -> list[str]:
    from django.urls import resolve

    from djust import LiveView

    routes = []
    for route in ROUTES:
        if "<" in route or route in KNOWN_FAILURES:
            continue
        view_class = getattr(resolve(route).func, "view_class", None)
        if isinstance(view_class, type) and issubclass(view_class, LiveView):
            routes.append(route)
    return routes


@pytest.mark.parametrize("route", _liveview_routes())
def test_liveview_page_has_a_root_the_client_connects_to(route, admin_client):
    """Sixteen pages had no ``dj-root`` / ``dj-view`` element.

    The client looks for ``[dj-view]``; djust stamps it onto the ``dj-root``
    element. With neither, the page rendered, the console said "No containers
    found" and no click ever reached the server.
    """
    from django.urls import resolve

    response = admin_client.get(route, HTTP_HOST="localhost")
    if response.status_code != 200 or getattr(response, "streaming", False):
        pytest.skip(f"{route} answered {response.status_code}")
    view_class = resolve(route).func.view_class
    body = response.content.decode("utf-8", "replace")
    assert re.search(r"<[a-z][^>]*\sdj-view(?:=|\s|>)", body), f"{route}: no [dj-view] element"
    if view_class.__module__ and "sticky" not in route:
        assert f'dj-view="{view_class.__module__}.{view_class.__name__}"' in body, route


def test_every_routed_demo_view_can_mount_over_the_websocket():
    """Fourteen routed demo views sat outside ``LIVEVIEW_ALLOWED_MODULES``.

    The homepage, every ``/forms/`` page, ``/docs/components/`` and the sticky
    demo: their pages rendered, the WebSocket mount refused them and every
    click fell back to HTTP. The helper asks the mount gate itself.
    """
    from djust.testing import assert_all_routed_liveviews_allowlisted

    assert_all_routed_liveviews_allowlisted()


def test_the_unrouted_legacy_views_cannot_mount():
    """``demo_app.views_old`` holds unrouted legacy views; the one routed view it held,
    ``NavbarBadgeDemo``, moved to ``demo_app.views.homepage`` so the module stays off the
    allowlist."""
    from djust.security.mount import is_view_path_allowed

    assert not is_view_path_allowed("demo_app.views_old.CounterView")
    assert is_view_path_allowed("demo_app.views.homepage.NavbarBadgeDemo")


def test_portal_create_page_says_there_is_no_tenant(admin_client):
    """With no tenant row the create page says so instead of offering a form.

    ``.first()`` returns ``None`` rather than raising, so the old bare
    ``except`` never ran: this page rendered an empty form whose submit then
    answered "No active lease found".
    """
    response = admin_client.get("/rentals/portal/maintenance/create/", HTTP_HOST="localhost")
    assert response.status_code == 200
    assert b"No tenant profile found" in response.content


def test_kitchen_sink_split_dropdown_names_handlers(admin_client):
    """The split dropdown's two items named handlers the view did not have."""
    errors = _first_paint_handler_errors(admin_client, "/demos/kitchen-sink/")
    assert errors == []
    from djust_demos.views.kitchen_sink import KitchenSinkView
    from djust.decorators import is_event_handler

    assert is_event_handler(KitchenSinkView.action1)
    assert is_event_handler(KitchenSinkView.action2)
