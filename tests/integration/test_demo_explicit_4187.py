"""The demo project runs on explicit exposure and strict event parameters (#4187).

The demo stated the legacy exposure policy on its shared navbar base (and
so on every page built on it), on the navbar examples and on a deliberate
legacy twin of the exposure matrix, and one strict-parameters handler stated
``parameter_policy="legacy"``. Under ``event_parameter_policy = "strict"``
the checks reported unannotated handler parameters (V016) and ``data-*``
arguments a strict handler never receives (T020). Each page now declares its
state with ``state()``, hands render-only values over in
``get_context_data()``, types its handler parameters and passes event
arguments as ``dj-value-*``.

This file holds the tree to that, and drives each migrated page's primary
interaction over the three transports a browser can use: the WebSocket, SSE
and the HTTP fallback (``djust.testing.open_live_page``, through the demo's
own URLconf, middleware and ASGI application). After the events on each
transport a fresh socket mounts the page again in the same session, as a
reconnect does, and must show the declared state the events changed.

Gate-off: state the legacy exposure policy again on
``djust_shared.views.BaseViewWithNavbar`` and the pin and the policy tests
fail; put a ``data-*`` argument back on a strict handler's element (the
financial dashboard's period buttons) and the strict check fails; let the
counter's handler stop counting and its three transport cases fail; let the
rentals row cache trust a restored copy and the HTTP fallback cases of the
maintenance update and the portal filter fail.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "demo_project"

#: A legacy exposure or parameter policy stated anywhere in the demo's Python.
LEGACY_STATEMENT = re.compile(
    r"""(?:exposure_policy|parameter_policy|event_parameter_policy)["']?\s*[:=]\s*["']legacy["']"""
)

DEMO_APPS = (
    "demo_app",
    "demo_project",
    "djust_demos",
    "djust_docs",
    "djust_forms",
    "djust_homepage",
    "djust_rentals",
    "djust_shared",
    "djust_tests",
    "sticky_demo",
)


def _demo_python_files():
    for path in sorted(DEMO.rglob("*.py")):
        if "migrations" not in path.parts and "node_modules" not in path.parts:
            yield path


def test_the_scan_finds_what_it_claims_to_find():
    # Built, not written: tests/legacy_exposure_ledger.py counts a literal one.
    legacy = '"' + "legacy" + '"'
    assert LEGACY_STATEMENT.search("    exposure_policy = " + legacy)
    assert LEGACY_STATEMENT.search("@event_handler(parameter_policy=%s)" % legacy.replace('"', "'"))
    assert LEGACY_STATEMENT.search('LIVEVIEW_CONFIG = {"event_parameter_policy": %s}' % legacy)
    assert not LEGACY_STATEMENT.search('    exposure_policy = "explicit"')
    assert sum(1 for _ in _demo_python_files()) > 100  # the walk reached the demo apps


def test_the_demo_states_no_legacy_policy():
    found = [
        "%s:%d" % (path.relative_to(ROOT), number)
        for path in _demo_python_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if LEGACY_STATEMENT.search(line)
    ]
    assert found == []


def _demo_views():
    import importlib
    import pkgutil

    from djust.live_view import LiveView

    for app in DEMO_APPS:
        package = importlib.import_module(app)
        for info in pkgutil.walk_packages(package.__path__, app + "."):
            if ".tests" in info.name or ".migrations" in info.name:
                continue
            try:
                importlib.import_module(info.name)
            except ImportError:
                continue  # a module whose optional dependency is not installed

    def subclasses(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from subclasses(sub)

    return [
        cls
        for cls in set(subclasses(LiveView))
        if (cls.__module__ or "").split(".")[0] in DEMO_APPS
    ]


def test_every_demo_view_runs_under_explicit_exposure():
    from djust._exposure import uses_legacy_exposure

    views = _demo_views()
    assert len(views) > 100, len(views)
    legacy = sorted(
        "%s.%s" % (cls.__module__, cls.__qualname__)
        for cls in views
        if uses_legacy_exposure(cls) or cls.exposure_policy != "explicit"
    )
    assert legacy == []


def test_the_demo_is_clean_under_strict_event_parameters():
    """V016 (an invalid strict contract), T019 (an event strict mode refuses),
    T020 (an argument a strict handler never receives) and V029 (a change the
    HTTP fallback loses), with ``event_parameter_policy = "strict"``."""
    from django.conf import settings

    from djust.checks.bindings import check_event_bindings
    from djust.checks.components import check_undeclared_handler_state
    from djust.checks.parameters import check_event_parameter_contracts
    from djust.validation import get_project_parameter_policy

    _demo_views()
    config = {**getattr(settings, "LIVEVIEW_CONFIG", {}), "event_parameter_policy": "strict"}
    with override_settings(LIVEVIEW_CONFIG=config):
        assert get_project_parameter_policy() == "strict"
        found = [
            "%s %s" % (m.id, m.msg)
            for m in list(check_event_parameter_contracts(None))
            + list(check_event_bindings(None))
            + list(check_undeclared_handler_state(None))
            if m.id in ("djust.V016", "djust.T019", "djust.T020", "djust.V029")
        ]
    demo = re.compile(r"(?<![\w.])(?:%s)[./][\w./]+" % "|".join(map(re.escape, DEMO_APPS)))
    assert [line for line in found if demo.search(line)] == []


# --------------------------------------------------------------------------- #
# Each migrated page's primary interaction, over every transport
# --------------------------------------------------------------------------- #

#: (page, handler, params, how many events, a regex the page shows afterwards).
#: ``{t}`` in a param or marker is the transport's name, for state that outlives
#: one page (the tenant demo's per-tenant store). ``None``: no visible marker;
#: the event must still answer without an error on every transport.
CASES = [
    ("/demos/counter/", "increment", {}, 3, r">3</div>"),
    ("/demos/activity/", "set_profile_name", {"value": "Ada4187"}, 1, r'value="Ada4187"'),
    ("/demos/activity/", "increment", {}, 2, r'font-mono"[^>]*>2</span>'),
    ("/demos/debounce/", "search", {"value": "milk4187"}, 1, r'value="milk4187"'),
    ("/demos/cache/", "search", {"query": "MacBook"}, 1, r"Laptop - MacBook Pro"),
    ("/demos/todo/", "add_todo", {"text": "milk4187"}, 1, r"milk4187"),
    ("/demos/chat/", "send_message", {"message": "hello4187"}, 1, r"hello4187"),
    ("/demos/performance/", "add_items_10", {}, 1, r"Item 10\b"),
    ("/demos/", "increment", {}, 3, r">\s*3\s*</div>"),
    ("/demos/smart-dashboard/", "set_temperature", {"temperature": 77.5}, 1, r"77\.5"),
    ("/demos/pwa/", "add_note", {"text": "note4187"}, 1, r"note4187"),
    ("/demos/tenant/", "add_message", {"text": "msg-{t}"}, 1, r"msg-{t}"),
    ("/demos/whats-new/0.4.2/", "increment_private", {}, 2, r'class="value"[^>]*>2</div>'),
    ("/demos/kitchen-sink/", "show_modal", {}, 1, None),
    ("/demos/markdown-stream/", "reset", {}, 1, None),
    ("/rentals/", "filter_properties", {"status": "vacant"}, 1, None),
    ("/rentals/properties/", "search", {"query": "zz4187"}, 1, r'value="zz4187"'),
    ("/rentals/leases/", "search", {"value": "zz4187"}, 1, r'value="zz4187"'),
    ("/rentals/tenants/", "search", {"query": "zz4187"}, 1, r'value="zz4187"'),
    ("/rentals/maintenance/", "search", {"query": "zz4187"}, 1, r'value="zz4187"'),
    (
        "/rentals/financials/",
        "change_period",
        {"period": "year"},
        1,
        r'<button(?=[^>]*dj-value-period="year")(?=[^>]*bg-primary text-primary-foreground)',
    ),
    ("/rentals/portal/maintenance/", "filter_by_status", {"status": "open"}, 1, None),
]

TRANSPORTS = ("ws", "sse", "http")


def _fill(value, transport):
    if isinstance(value, str):
        return value.replace("{t}", transport)
    if isinstance(value, dict):
        return {key: _fill(item, transport) for key, item in value.items()}
    return value


def _assert_answer(answer, label):
    assert answer.get("type") != "error", (label, answer)
    assert "error" not in answer, (label, answer)


async def _drive(page, transport, handler, params, times):
    label = "%s %s over %s" % (page.path, handler, transport)
    if transport == "http":
        for _ in range(times):
            response = await sync_to_async(page.post_event)(handler, params)
            body = response.content.decode("utf-8")
            assert response.status_code == 200, (label, body[:500])
            _assert_answer(json.loads(body), label)
        return
    connect = page.connect if transport == "ws" else page.connect_sse
    connection = await connect()
    try:
        assert connection.mount.get("type") == "mount", (label, connection.mount)
        for _ in range(times):
            _assert_answer(await connection.send_event(handler, params), label)
    finally:
        await connection.close()


def _params():
    for case in CASES:
        for transport in TRANSPORTS:
            yield pytest.param(*case, transport, id="%s-%s-%s" % (case[0], case[1], transport))


@pytest.mark.parametrize("url, handler, params, times, marker, transport", list(_params()))
def test_the_page_interaction_works_and_survives_a_reconnect(
    url, handler, params, times, marker, transport, monkeypatch
):
    from djust.testing import close_live_pages, open_live_page

    import time

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    params, marker = _fill(params, transport), _fill(marker, transport)
    page = open_live_page(url)
    if marker is not None:
        assert not re.search(marker, page.html), "the marker is on the page before the event"

    async def run():
        await _drive(page, transport, handler, params, times)
        # A reconnect: a fresh socket mounts the page in the same session.
        socket = await page.connect()
        try:
            assert socket.mount.get("type") == "mount", socket.mount
            return socket.mount.get("html") or ""
        finally:
            await socket.close()

    try:
        html = asyncio.run(run())
    finally:
        close_live_pages()
    if marker is not None:
        assert re.search(marker, html), "%s after %s over %s: %r not shown on reconnect" % (
            url,
            handler,
            transport,
            marker,
        )


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_a_second_save_of_a_new_property_updates_it(transport):
    """The property form keeps the saved property's id (``persist="server"``), so
    a second save on the same page updates that property instead of creating
    another, over every transport."""
    from djust.testing import close_live_pages, open_live_page
    from djust_rentals.models import Property

    fields = {
        "name": "Elm %s" % transport,
        "address": "1 Elm St",
        "city": "Town",
        "state": "CA",
        "zip_code": "90000",
        "property_type": "house",
        "bedrooms": "2",
        "bathrooms": "1.0",
        "square_feet": "900",
        "monthly_rent": "1500.00",
        "security_deposit": "1500.00",
        "status": "available",
    }
    page = open_live_page("/rentals/properties/add/")

    async def run():
        await _drive(page, transport, "save_property", fields, 1)
        await _drive(page, transport, "save_property", {**fields, "city": "Village"}, 1)

    try:
        asyncio.run(run())
    finally:
        close_live_pages()
    saved = list(Property.objects.filter(name="Elm %s" % transport).values_list("city", flat=True))
    assert saved == ["Village"]


# --------------------------------------------------------------------------- #
# Pages that read rows: mounted over every transport, with one row per table
# --------------------------------------------------------------------------- #


def _seeded(django_user_model):
    from tests.integration.test_demo_project_pages_3994 import _seed

    return _seed(django_user_model)


#: (page, a regex its mount shows). ``{Model}`` is the seeded row's pk.
MOUNTS = [
    ("/demos/design1/", None),
    ("/demos/datatable/", r"Product DataTable|DataTable"),
    ("/demos/server-function/", None),
    ("/rentals/properties/{Property}/", r"Elm House"),
    ("/rentals/properties/{Property}/edit/", r'value="Elm House"'),
    ("/rentals/properties/{Property}/delete/", r"Elm House"),
    ("/rentals/tenants/{Tenant}/", r"Ada Lane"),
    ("/rentals/tenants/{Tenant}/edit/", r'value="Ada"'),
    ("/rentals/leases/{Lease}/", r"Elm House"),
    ("/rentals/leases/{Lease}/edit/", r"Elm House"),
    ("/rentals/maintenance/{MaintenanceRequest}/", r"Leaky tap"),
    ("/rentals/maintenance/{MaintenanceRequest}/update/", r"Leaky tap"),
    ("/rentals/financials/income/", r"Income Report"),
    ("/rentals/financials/expenses/", r"Expense Report"),
    ("/rentals/financials/profit-loss/", r"Profit &amp; Loss|Profit & Loss"),
    ("/rentals/portal/", r"Ada Lane"),
    ("/rentals/portal/payments/", r"Payment History"),
    ("/rentals/portal/maintenance/create/", r"Elm House"),
]


@pytest.mark.parametrize("transport", ("ws", "sse"))
@pytest.mark.parametrize("url, marker", MOUNTS, ids=[m[0] for m in MOUNTS])
def test_a_page_that_reads_rows_mounts(url, marker, transport, django_user_model):
    from djust.testing import close_live_pages, open_live_page

    url = url.format(**_seeded(django_user_model))
    page = open_live_page(url)

    async def run():
        connect = page.connect if transport == "ws" else page.connect_sse
        connection = await connect()
        try:
            assert connection.mount.get("type") == "mount", connection.mount
            return connection.mount.get("html") or ""
        finally:
            await connection.close()

    try:
        html = asyncio.run(run())
    finally:
        close_live_pages()
    if marker is not None:
        assert re.search(marker, page.html), (url, "page")
        assert re.search(marker, html), (url, transport)


def _lease_fields(pks):
    import datetime

    today = datetime.date.today()
    return {
        "property_id": str(pks["Property"]),
        "tenant_id": str(pks["Tenant"]),
        "start_date": str(today),
        "end_date": str(today + datetime.timedelta(days=300)),
        "monthly_rent": "1500.00",
        "security_deposit": "1500.00",
        "rent_due_day": "1",
        "late_fee": "0",
        "status": "active",
        "terms": "terms-4187",
    }


def _check_lease(pks):
    from djust_rentals.models import Lease

    return Lease.objects.get(pk=pks["Lease"]).terms == "terms-4187"


def _check_status(pks):
    from djust_rentals.models import MaintenanceRequest

    return MaintenanceRequest.objects.get(pk=pks["MaintenanceRequest"]).status == "in_progress"


def _check_tenant(pks):
    from djust_rentals.models import Tenant

    return Tenant.objects.get(pk=pks["Tenant"]).user.first_name == "Zed"


def _check_request(pks):
    from djust_rentals.models import MaintenanceRequest

    return MaintenanceRequest.objects.filter(title="Leak-4187").count() == 1


#: (page, handler, params (a callable of the seeded pks), a check of the database).
SAVES = [
    ("/rentals/leases/{Lease}/edit/", "save_lease", _lease_fields, _check_lease),
    (
        "/rentals/maintenance/{MaintenanceRequest}/update/",
        "update_status",
        lambda pks: {"status": "in_progress"},
        _check_status,
    ),
    (
        "/rentals/tenants/{Tenant}/edit/",
        "save_tenant",
        lambda pks: {"first_name": "Zed", "last_name": "Lane", "email": "t@example.com"},
        _check_tenant,
    ),
    (
        "/rentals/portal/maintenance/create/",
        "submit_request",
        lambda pks: {"title": "Leak-4187", "description": "Drips", "priority": "high"},
        _check_request,
    ),
]


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("url, handler, params, check", SAVES, ids=[s[1] for s in SAVES])
def test_a_form_page_saves_over_every_transport(
    url, handler, params, check, transport, django_user_model
):
    from djust.testing import close_live_pages, open_live_page

    pks = _seeded(django_user_model)
    page = open_live_page(url.format(**pks))
    try:
        asyncio.run(_drive(page, transport, handler, params(pks), 1))
    finally:
        close_live_pages()
    assert check(pks), (url, handler, transport)


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_the_portal_filter_reads_its_tenant_on_every_turn(transport, django_user_model):
    """The portal list keeps its tenant as an id: the HTTP fallback restores a
    saved model as its field dict, and a filter over that dict failed. The
    seeded request is open, so filtering by "completed" hides it, on every
    transport and on the reconnect after it."""
    from djust.testing import close_live_pages, open_live_page

    _seeded(django_user_model)
    page = open_live_page("/rentals/portal/maintenance/")
    assert "Leaky tap" in page.html

    async def run():
        await _drive(page, transport, "filter_by_status", {"status": "completed"}, 2)
        socket = await page.connect()
        try:
            return socket.mount.get("html") or ""
        finally:
            await socket.close()

    try:
        html = asyncio.run(run())
    finally:
        close_live_pages()
    assert "Leaky tap" not in html
    assert re.search(r"<[^>]*bg-primary text-primary-foreground", html)
