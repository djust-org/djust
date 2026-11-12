"""ADR-038 Step R, PR 0b: the LiveViews djust ships use explicit exposure.

Three nets:

1. A structural pin of the SET of LiveView classes under ``python/djust`` and
   of each one's policy. A new shipped view has to be added here, and it has
   to be explicit unless it is listed, with its reason, in ``LEGACY``.
2. Each converted group mounts under explicit exposure over a real socket
   (session and auth middleware, as an application runs it) and answers one
   of its events; the gallery and catalogue also answer one over the HTTP
   POST fallback.
3. What ``djust new`` and ``djust_gen_live`` generate is explicit and runs.

It also pins the route-map fix this conversion needed: an explicit view's
``as_view()`` returns djust's own error-handling wrapper, which the client
route map used to read as an access gate (so every public explicit route was
dropped from an anonymous visitor's map).
"""

from __future__ import annotations

import ast
import functools
import importlib
import json
import re
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser, Group
from django.test import Client, RequestFactory
from django.urls import include, path

from djust import LiveView, event_handler
from djust.admin_ext import AdminPage, AdminPlugin, DjustAdminSite, DjustModelAdmin
from djust.auth.admin_views import OAuthProvidersView, SocialAccountsView
from djust.components.ttyd.terminal import TtydTerminalView
from djust.decorators import state
from djust.live_view import is_protected_http_entry
from djust.tests.test_admin_page_render import _Socket, _session_cookie

PACKAGE = Path(__file__).resolve().parents[1]
MODULE = __name__

# --------------------------------------------------------------------------
# 1. The set of shipped LiveViews, and each one's policy
# --------------------------------------------------------------------------

#: Every LiveView class djust ships, as ``path/under/python/djust.py:Class``.
#: ``test_the_set_of_shipped_liveviews_is_pinned`` fails when this and the
#: source disagree, in either direction.
EXPLICIT = {
    "admin_ext/progress.py:BulkActionProgressView",
    "admin_ext/progress.py:BulkActionProgressWidget",
    "admin_ext/views.py:AdminIndexView",
    "admin_ext/views.py:LogoutView",
    "admin_ext/views.py:ModelDeleteView",
    "auth/admin_views.py:OAuthProvidersView",
    "auth/admin_views.py:SocialAccountsView",
    "components/gallery/live_views.py:DataGalleryView",
    "components/gallery/live_views.py:FeedbackGalleryView",
    "components/gallery/live_views.py:FormGalleryView",
    "components/gallery/live_views.py:GalleryIndexView",
    "components/gallery/live_views.py:IndicatorGalleryView",
    "components/gallery/live_views.py:LayoutGalleryView",
    "components/gallery/live_views.py:MiscGalleryView",
    "components/gallery/live_views.py:NavGalleryView",
    "components/gallery/live_views.py:OverlayGalleryView",
    "components/gallery/live_views.py:TypographyGalleryView",
    "theming/gallery/live_views.py:ComponentsCategoryView",
    "theming/gallery/live_views.py:ComponentsDetailView",
    "theming/gallery/live_views.py:ComponentsIndexView",
}

#: Shipped views pinned to the legacy policy (``exposure_policy = "legacy"`` in
#: the class body, so a changed default does not convert them), each with what
#: it relies on that explicit exposure does not offer yet. Not a place for
#: "not converted yet".
LEGACY = {
    "components/ttyd/terminal.py:TtydTerminalView": (
        "A documented base class. A subclass renders its own public attributes "
        "(session_log in the docstring example) through the attribute walk, so making "
        "the base explicit would empty existing subclasses' templates without an error."
    ),
    "live_view.py:DynamicLiveView": (
        "The class the function-based @live_view decorator builds per request. The "
        "decorated function returns its locals and they are set as view attributes "
        "for the attribute walk to render; there is no declared context to give it."
    ),
    "admin_ext/views.py:ModelListView": (
        "Embeds the ModelAdmin's change_list_widgets with {% live_render %}. Those are "
        "application views, legacy by default, and an explicit root cannot hold a "
        "legacy child: _exposure_child_persistence._capture_children asks every "
        "registered child for an explicit binding on the HTTP GET."
    ),
    "admin_ext/views.py:ModelDetailView": (
        "Embeds the ModelAdmin's change_form_widgets with {% live_render %}; see ModelListView."
    ),
    "admin_ext/views.py:ModelCreateView": "A ModelDetailView; see ModelListView.",
    "components/interactive_examples.py:DropdownMenuExample": (
        "Its menus are interactive components addressed by a per-instance id. Only "
        "the legacy HTTP POST fallback carries the component registry from one "
        "request to the next, so the executable documentation scenario "
        "(catalogue-dropdown) cannot reach a menu on an explicit view."
    ),
    "theming/gallery/live_views.py:InteractiveDropdownCatalogueView": (
        "A DropdownMenuExample inside the catalogue chrome; see DropdownMenuExample."
    ),
}


#: Built inside a function on each call, so there is no class to import.
_FUNCTION_LOCAL = {"live_view.py:DynamicLiveView"}


def _base_names(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Subscript):  # Generic[...] / Mixin[Model]
            base = base.value
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _declared_policy(node: ast.ClassDef) -> str | None:
    for statement in node.body:
        targets = []
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "exposure_policy":
                value = statement.value
                return value.value if isinstance(value, ast.Constant) else "<dynamic>"
    return None


def _shipped_classes() -> dict[str, tuple[str, ast.ClassDef]]:
    """Every class statement under python/djust outside the test tree, by name.

    ``ast.walk`` so that a class built inside a function
    (``BulkActionProgressView``) is seen; a class in a docstring is not code.
    """
    classes: dict[str, list[tuple[str, ast.ClassDef]]] = {}
    for source in sorted(PACKAGE.rglob("*.py")):
        relative = source.relative_to(PACKAGE).as_posix()
        if relative.startswith("tests/") or "/tests/" in relative:
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, []).append((relative, node))
    return classes


def _liveviews() -> dict[str, str | None]:
    """``path:Class`` -> the policy the class statement resolves to.

    ``None`` when neither the class nor a base it names states one: the view
    would take whatever ``LiveView``'s default is. No default is assumed here.
    """
    classes = _shipped_classes()

    def resolve(name: str, seen: frozenset = frozenset()) -> tuple[bool, str | None]:
        """(is a LiveView, declared policy) for a class name, through its bases."""
        if name == "LiveView":
            return True, None
        if name in seen or name not in classes:
            return False, None
        is_view, policy = False, None
        for _relative, node in classes[name]:
            declared = _declared_policy(node)
            for base in _base_names(node):
                base_is_view, inherited = resolve(base, seen | {name})
                is_view = is_view or base_is_view
                if declared is None and policy is None:
                    policy = inherited
            policy = declared if declared is not None else policy
        return is_view, policy

    found = {}
    for name, definitions in classes.items():
        if name == "LiveView":
            continue
        is_view, policy = resolve(name)
        if is_view:
            for relative, _node in definitions:
                found["%s:%s" % (relative, name)] = policy
    return found


def test_the_set_of_shipped_liveviews_is_pinned():
    found = set(_liveviews())
    expected = EXPLICIT | set(LEGACY)
    assert found - expected == set(), (
        "a LiveView djust ships is not listed here. Give it "
        'exposure_policy = "explicit" and add it to EXPLICIT'
    )
    assert expected - found == set(), "a listed view no longer exists; drop it from the list"
    assert not EXPLICIT & set(LEGACY)


def test_every_shipped_liveview_is_explicit_unless_it_is_listed_with_a_reason():
    policies = _liveviews()
    assert {key: policies[key] for key in EXPLICIT} == dict.fromkeys(EXPLICIT, "explicit")
    # The exception list cannot go stale either: a view that became explicit
    # has to leave it. And each exception STATES "legacy": a view that merely
    # inherits today's default would be converted by a changed default.
    assert {key: policies[key] for key in LEGACY} == dict.fromkeys(LEGACY, "legacy")
    assert None not in policies.values(), "a shipped view states no policy"
    assert all(len(reason) > 30 for reason in LEGACY.values())


def test_the_pinned_policies_are_what_the_classes_resolve_to():
    """The same pin, read off the imported classes (the AST walk above is by name)."""
    for key, expected in [(k, "explicit") for k in EXPLICIT] + [(k, "legacy") for k in LEGACY]:
        if key in _FUNCTION_LOCAL:
            continue
        relative, name = key.split(":")
        module = importlib.import_module("djust." + relative[:-3].replace("/", "."))
        view_class = getattr(module, name)
        assert issubclass(view_class, LiveView), key
        assert view_class.exposure_policy == expected, key


def test_the_default_is_explicit_and_no_shipped_view_relies_on_it():
    """The default is explicit (exposure-flip PR 1). Every shipped view above
    still states its own policy in its class body, so the views pinned
    ``"legacy"`` were not converted by the flip and the explicit ones would not
    be converted back by a revert."""
    assert LiveView.__dict__["exposure_policy"] == "explicit"


# --------------------------------------------------------------------------
# The route map: an explicit view's own wrapper is not an access gate
# --------------------------------------------------------------------------


class PublicExplicitPage(LiveView):
    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root>public</div>"


class PublicLegacyPage(LiveView):
    exposure_policy = "legacy"
    login_required = False
    template = "<div dj-root>public</div>"


class GatedExplicitPage(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root>gated</div>"


class DecoratedExplicitPage(LiveView):
    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root>decorated</div>"


def _passthrough(view):
    """A decorator djust cannot read: it may or may not be a gate."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        return view(request, *args, **kwargs)

    return wrapper


def _anonymous_route_map() -> dict:
    from djust.routing import _reset_route_map_cache, get_route_map_script

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    _reset_route_map_cache()
    try:
        script = get_route_map_script(request)
    finally:
        _reset_route_map_cache()
    if not script:
        return {}
    return json.loads(script.split("_routeMap=", 1)[1].rsplit(";</script>", 1)[0])


def test_a_public_explicit_route_is_in_an_anonymous_visitors_route_map(settings):
    settings.ROOT_URLCONF = MODULE
    routes = _anonymous_route_map()
    assert routes.get("/routes/public-explicit/") == MODULE + ".PublicExplicitPage"
    # The legacy control: the two policies agree.
    assert routes.get("/routes/public-legacy/") == MODULE + ".PublicLegacyPage"


def test_a_gated_explicit_route_stays_out_of_an_anonymous_route_map(settings):
    settings.ROOT_URLCONF = MODULE
    routes = _anonymous_route_map()
    # The class attribute, a real auth decorator, and a decorator djust cannot
    # read (fail closed) each still gate an explicit view's route.
    assert "/routes/gated-explicit/" not in routes
    assert "/routes/login-decorated-explicit/" not in routes
    assert "/routes/wrapped-explicit/" not in routes


def test_only_djusts_own_wrapper_object_is_looked_through():
    entry = PublicExplicitPage.as_view()
    assert is_protected_http_entry(entry)
    # functools.wraps copies the wrapped callable's __dict__, so an attribute
    # marker would be inherited by any decorator around it. Identity is not.
    assert not is_protected_http_entry(_passthrough(entry))
    assert not is_protected_http_entry(login_required(entry))
    assert not is_protected_http_entry(PublicLegacyPage.as_view())
    assert not is_protected_http_entry(None) and not is_protected_http_entry(3)


# --------------------------------------------------------------------------
# 2. Each converted group mounts and answers an event under explicit exposure
# --------------------------------------------------------------------------


class GroupAdmin(DjustModelAdmin):
    pass


class _ProvidersPage(OAuthProvidersView):
    # The two auth admin pages name templates djust does not ship, so they are
    # exercised here through a template of their context.
    template = "<div dj-root><h1>{{ title }}</h1><i>{{ total_users }}</i></div>"


class _AccountsPage(SocialAccountsView):
    template = (
        "<div dj-root><h1>{{ title }}</h1><i>[{{ ordering }}]</i><b>({{ search_query }})</b></div>"
    )
    # A grant is not inherited (ADR-038): a subclass states its own.
    search_query = state(default="", persist="server")
    current_page = state(default=1, persist="server")
    ordering = state(default="-date_joined", persist="server")
    filter_provider = state(default="", persist="server")


class _AuthPages(AdminPlugin):
    name = "explicit_views_auth"

    def get_pages(self):
        return [
            AdminPage(url_path="auth/providers", url_name="x_providers", view_class=_ProvidersPage),
            AdminPage(url_path="auth/accounts", url_name="x_accounts", view_class=_AccountsPage),
        ]


class LoggingTerminal(TtydTerminalView):
    """The docstring's subclass, as an application wrote it before this change."""

    template = (
        '<div dj-root><i data-rows="{{ rows }}">{{ ttyd_url }}</i>'
        "<b>[{{ terminal_connected }}]</b><u>{{ session_log|length }}</u></div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.session_log = []

    def on_ttyd_connect(self, timestamp="", user_agent="", **kwargs):
        super().on_ttyd_connect(timestamp=timestamp, user_agent=user_agent, **kwargs)
        self.session_log.append("Connected at %s" % timestamp)

    on_ttyd_connect = event_handler()(on_ttyd_connect)


class ExplicitTerminal(LoggingTerminal):
    """The docstring's explicit subclass: it lists what its own template reads."""

    exposure_policy = "explicit"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            session_log=self.session_log,
            terminal_connected=self.terminal_connected,
            **kwargs,
        )


SITE = DjustAdminSite(name="explicit_views_admin")
SITE.site_header = "Explicit-views administration"
SITE.register(Group, GroupAdmin)
SITE.register_plugin(_AuthPages)

urlpatterns = [
    path("x-admin/", SITE.urls),
    path("theme/", include("djust.theming.urls")),
    path("ttyd/", TtydTerminalView.as_view()),
    path("ttyd-logging/", LoggingTerminal.as_view()),
    path("ttyd-explicit/", ExplicitTerminal.as_view()),
    path("routes/public-explicit/", PublicExplicitPage.as_view()),
    path("routes/public-legacy/", PublicLegacyPage.as_view()),
    path("routes/gated-explicit/", GatedExplicitPage.as_view()),
    path("routes/login-decorated-explicit/", login_required(DecoratedExplicitPage.as_view())),
    path("routes/wrapped-explicit/", _passthrough(DecoratedExplicitPage.as_view())),
]

ADMIN = "/x-admin/"
GALLERY = "/theme/components-legacy/"
CATALOGUE = "/theme/components/"


@pytest.fixture
def site_urls(settings):
    from djust.routing import _reset_route_map_cache

    settings.ROOT_URLCONF = MODULE
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MODULE]
    settings.DJUST_THEMING_GALLERY_PUBLIC = True
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


async def _staff_browser(django_user_model) -> Client:
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)
    return browser


async def _visitor(url: str) -> Client:
    """An anonymous browser that has loaded ``url`` (the GET sets its session)."""
    browser = Client()
    response = await sync_to_async(browser.get)(url)
    assert response.status_code == 200, response.content[:300]
    return browser


def _text_patches(frame) -> list[str]:
    return [str(patch.get("text", "")) for patch in frame.get("patches", ())]


def _event(event: str, /, **params) -> dict:
    return {"type": "event", "event": event, "params": params}


@pytest.mark.django_db(transaction=True)
async def test_admin_index_mounts(site_urls, django_user_model):
    browser = await _staff_browser(django_user_model)
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount("djust.admin_ext.views.AdminIndexView", ADMIN)
    assert "Explicit-views administration" in mounted["html"]


@pytest.mark.django_db(transaction=True)
async def test_admin_delete_view_mounts_and_deletes(site_urls, django_user_model):
    browser = await _staff_browser(django_user_model)
    group = await sync_to_async(Group.objects.create)(name="to-delete")
    url = f"{ADMIN}auth/group/{group.pk}/delete/"
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount("djust.admin_ext.views.ModelDeleteView", url)
        assert "to-delete" in mounted["html"]
        await ws.send(_event("confirm_delete"))
        await ws.until("patch", "html_update", "noop")
    assert not await sync_to_async(Group.objects.filter(pk=group.pk).exists)()


@pytest.mark.django_db(transaction=True)
async def test_bulk_progress_view_mounts_and_cancels(site_urls, django_user_model, monkeypatch):
    from djust.admin_ext.progress import _JOBS, BulkActionProgressWidget, Job

    # The polling loop sleeps in a worker thread; the event under test does
    # not need it.
    monkeypatch.setattr(BulkActionProgressWidget, "_poll", lambda self: None)
    browser = await _staff_browser(django_user_model)
    owner = await sync_to_async(django_user_model.objects.get)(username="root")
    job = Job(
        job_id="explicit-views-job",
        action_label="Export everything",
        user_id=owner.pk,
        admin_site_name=SITE.name,
        redirect_url=ADMIN,
        total=10,
    )
    _JOBS[job.job_id] = job
    try:
        url = f"{ADMIN}djust-progress/{job.job_id}/"
        async with _Socket(_session_cookie(browser)) as ws:
            mounted = await ws.mount("djust.admin_ext.progress.BulkActionProgressView", url)
            assert "Export everything" in mounted["html"]
            await ws.send(_event("cancel"))
            await ws.until("patch", "html_update", "noop")
        assert job.cancelled and job.done
    finally:
        _JOBS.pop(job.job_id, None)


@pytest.mark.django_db(transaction=True)
async def test_auth_admin_pages_mount_and_sort(site_urls, django_user_model):
    pytest.importorskip("allauth.socialaccount")
    browser = await _staff_browser(django_user_model)
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(MODULE + "._ProvidersPage", ADMIN + "auth/providers/")
        assert "OAuth Providers" in mounted["html"] and ">1</i>" in mounted["html"]
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(MODULE + "._AccountsPage", ADMIN + "auth/accounts/")
        assert "[-date_joined]" in mounted["html"]
        await ws.send(_event("sort_by", field="provider"))
        frame = await ws.until("patch", "html_update")
    assert "[provider]" in _text_patches(frame), frame


@pytest.mark.django_db(transaction=True)
async def test_component_gallery_index_mounts_and_searches(site_urls):
    browser = await _visitor(GALLERY)
    view = "djust.components.gallery.live_views.GalleryIndexView"
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, GALLERY)
        # The landing grid is rendered from category_cards and total_count.
        assert "lv/layout/" in mounted["html"]
        await ws.send(_event("search", value="accordion"))
        frame = await ws.until("patch", "html_update")
    assert "ccordion" in json.dumps(frame), frame


@pytest.mark.django_db(transaction=True)
async def test_component_gallery_category_mounts_and_toggles(site_urls):
    url = GALLERY + "lv/layout/"
    browser = await _visitor(url)
    view = "djust.components.gallery.live_views.LayoutGalleryView"
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, url)
        # category_label and the sidebar's category_cards.
        assert re.search(r"<h2[^>]*>Layout</h2>", mounted["html"])
        assert "lv/form/" in mounted["html"]
        await ws.send(_event("accordion_toggle", value="1"))
        await ws.until("patch", "html_update", "noop")


@pytest.mark.django_db(transaction=True)
async def test_catalogue_index_mounts_and_filters(site_urls):
    browser = await _visitor(CATALOGUE)
    view = "djust.theming.gallery.live_views.ComponentsIndexView"
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, CATALOGUE)
        # total_count, and the sidebar count read off sidebar_components.
        assert re.search(r"[1-9]\d* components across", mounted["html"])
        assert re.search(r"Components \([1-9]\d*\)", mounted["html"])
        await ws.send(_event("search", value="accordion"))
        frame = await ws.until("patch", "html_update")
    # The sidebar count is rendered from sidebar_components, which the walk
    # used to supply.
    assert frame.get("patches") or frame.get("html"), frame


@pytest.mark.django_db(transaction=True)
async def test_catalogue_category_mounts_and_searches(site_urls):
    from djust.theming.gallery.live_views import _all_catalogue_components

    category = _all_catalogue_components()[0]["category"]
    url = f"{CATALOGUE}category/{category}/"
    browser = await _visitor(url)
    view = "djust.theming.gallery.live_views.ComponentsCategoryView"
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, url, {"category": category})
        # category and category_components.
        assert re.search(r"[1-9]\d* components? in this category", mounted["html"])
        assert re.search(r"<h1[^>]*>%s</h1>" % re.escape(category), mounted["html"])
        await ws.send(_event("toggle_category", value=category))
        await ws.until("patch", "html_update", "noop")


@pytest.mark.django_db(transaction=True)
async def test_terminal_view_mounts_and_records_a_connection(site_urls):
    url = "/ttyd/?rows=30"
    browser = await _visitor(url)
    view = "djust.components.ttyd.terminal.TtydTerminalView"
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, url)
        html = mounted["html"]
        assert 'data-rows="30"' in html and 'data-cols="80"' in html
        assert 'data-ttyd-url="ws://localhost:7681"' in html
        await ws.send(_event("on_ttyd_connect", timestamp="now"))
        await ws.until("patch", "html_update", "noop")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name", ["LoggingTerminal", "ExplicitTerminal"])
async def test_a_terminal_subclass_renders_its_own_attributes(site_urls, name):
    """The base stays legacy, so an existing subclass is unchanged; an explicit
    subclass gets the terminal's names from ``super()`` and lists its own."""
    url = "/ttyd-logging/" if name == "LoggingTerminal" else "/ttyd-explicit/"
    browser = await _visitor(url)
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(MODULE + "." + name, url)
        html = mounted["html"]
        assert 'data-rows="24"' in html and "ws://localhost:7681" in html, html
        assert re.search(r"<b[^>]*>\[False\]</b>", html) and re.search(r"<u[^>]*>0</u>", html)
        await ws.send(_event("on_ttyd_connect", timestamp="now"))
        frame = await ws.until("patch", "html_update")
    assert {"[True]", "1"} <= set(_text_patches(frame)), frame


# --------------------------------------------------------------------------
# Which principal a view sees, per ASGI stack, at mount and at an event
# --------------------------------------------------------------------------


def _who(request) -> str:
    user = getattr(request, "user", None)
    if user is None:
        return "none"
    return user.get_username() if user.is_authenticated else "anonymous"


class WhoExplicitPage(LiveView):
    """Renders the principal ``mount()`` saw and the one the last event saw."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root><i>mount={{ mount_who }}</i><b>event={{ event_who }}</b></div>"

    def mount(self, request, **kwargs):
        self.mount_who = _who(request)
        self.event_who = "-"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            mount_who=self.mount_who, event_who=self.event_who, **kwargs
        )

    @event_handler()
    def ask(self, **kwargs):
        self.event_who = _who(self.request)


class WhoLegacyPage(WhoExplicitPage):
    exposure_policy = "legacy"


class WhoGatedExplicitPage(WhoExplicitPage):
    login_required = True


class WhoGatedLegacyPage(WhoLegacyPage):
    login_required = True


def _stack(name: str):
    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter

    from djust.routing import DjustMiddlewareStack
    from djust.websocket import LiveViewConsumer

    router = URLRouter([path("ws/live/", LiveViewConsumer.as_asgi())])
    return {
        "session-only": lambda: DjustMiddlewareStack(router),
        "djust(auth)": lambda: DjustMiddlewareStack(AuthMiddlewareStack(router)),
        "auth(djust)": lambda: AuthMiddlewareStack(DjustMiddlewareStack(router)),
    }[name]()


class _StackSocket(_Socket):
    """A socket behind one of the documented ASGI stacks, origin check on."""

    def __init__(self, stack: str, cookie_header: str = ""):
        from channels.testing import WebsocketCommunicator

        headers = [(b"host", b"testserver"), (b"origin", b"http://testserver")]
        if cookie_header:
            headers.append((b"cookie", cookie_header.encode()))
        self.comm = WebsocketCommunicator(_stack(stack), "/ws/live/", headers=headers)

    async def answer(self, frame, *types):
        await self.send(frame)
        for _ in range(10):
            received = await self.comm.receive_json_from(timeout=10)
            if received.get("type") in types:
                return received
        raise AssertionError("no %s frame" % (types,))


_MOUNT_ANSWERS = ("mount", "error", "navigate", "permission_denied")
_EVENT_ANSWERS = ("patch", "html_update", "noop", "error", "permission_denied", "navigate")

#: (stack, the browser's session is logged in) -> the principal a PUBLIC view
#: sees at mount and at an event, and whether a ``login_required`` view mounts.
#: The legacy column is ``main``'s behaviour and must not move. "none" is a
#: request with no ``user`` attribute at all.
_PRINCIPALS = {
    ("session-only", False): {"explicit": "anonymous", "legacy": "none", "gated": False},
    ("session-only", True): {"explicit": "root", "legacy": "none", "gated": False},
    ("djust(auth)", True): {"explicit": "root", "legacy": "root", "gated": True},
    ("auth(djust)", True): {"explicit": "root", "legacy": "root", "gated": True},
}


async def _browser(django_user_model, logged_in: bool) -> Client:
    if logged_in:
        return await _staff_browser(django_user_model)
    return await _visitor("/routes/public-explicit/")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("stack, logged_in", sorted(_PRINCIPALS))
async def test_a_public_view_has_one_principal_for_mount_and_event(
    site_urls, django_user_model, stack, logged_in, policy
):
    expected = _PRINCIPALS[(stack, logged_in)][policy]
    view = MODULE + (".WhoExplicitPage" if policy == "explicit" else ".WhoLegacyPage")
    browser = await _browser(django_user_model, logged_in)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        mounted = await ws.answer(
            {"type": "mount", "view": view, "url": "/routes/public-explicit/", "params": {}},
            *_MOUNT_ANSWERS,
        )
        assert mounted.get("type") == "mount", mounted
        assert ">mount=%s</i>" % expected in mounted["html"], mounted["html"]
        answered = await ws.answer(_event("ask"), *_EVENT_ANSWERS)
    # The event is accepted, and its request names the principal the mount did.
    assert answered.get("type") == "patch", answered
    assert _text_patches(answered) == ["event=%s" % expected], answered


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("stack, logged_in", sorted(_PRINCIPALS))
async def test_a_login_required_view_is_admitted_exactly_where_it_was(
    site_urls, django_user_model, stack, logged_in, policy
):
    admitted = _PRINCIPALS[(stack, logged_in)]["gated"]
    view = MODULE + (".WhoGatedExplicitPage" if policy == "explicit" else ".WhoGatedLegacyPage")
    browser = await _browser(django_user_model, logged_in)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        mounted = await ws.answer(
            {"type": "mount", "view": view, "url": "/routes/public-explicit/", "params": {}},
            *_MOUNT_ANSWERS,
        )
        if not admitted:
            # A stack that authenticates nobody admits no gated view, even for
            # a browser whose session is logged in.
            assert mounted.get("type") != "mount", mounted
            return
        assert mounted.get("type") == "mount" and ">mount=root</i>" in mounted["html"], mounted
        answered = await ws.answer(_event("ask"), *_EVENT_ANSWERS)
    assert _text_patches(answered) == ["event=root"], answered


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "view, url",
    [
        ("djust.components.gallery.live_views.GalleryIndexView", GALLERY),
        ("djust.theming.gallery.live_views.ComponentsIndexView", CATALOGUE),
    ],
)
async def test_a_shipped_public_page_works_behind_the_session_only_stack(site_urls, view, url):
    browser = await _visitor(url)
    async with _StackSocket("session-only", _session_cookie(browser)) as ws:
        mounted = await ws.answer(
            {"type": "mount", "view": view, "url": url, "params": {}}, *_MOUNT_ANSWERS
        )
        assert mounted.get("type") == "mount", mounted
        answered = await ws.answer(_event("search", value="accordion"), *_EVENT_ANSWERS)
    assert answered.get("type") in ("patch", "html_update"), answered


# --------------------------------------------------------------------------
# Without a socket: a later request keeps what an earlier one set
# --------------------------------------------------------------------------


def _post_event(browser, url, event, **params):
    response = browser.post(
        url, data=json.dumps(params), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )
    assert response.status_code == 200, response.content[:300]
    return response.content.decode()


@pytest.mark.django_db
def test_the_catalogue_renders_without_a_user_or_a_server_side_session(site_urls):
    """Why the public catalogue declares no persisted field.

    A persisted field binds the page's state to a server-side session and to
    ``request.user``, so the page would stop rendering for a project that has
    neither. It has to keep rendering there.
    """
    from djust.theming.gallery.live_views import ComponentsIndexView

    from django.contrib.sessions.backends.signed_cookies import SessionStore

    request = RequestFactory().get(CATALOGUE)
    request.session = SessionStore()  # a cookie session, and no request.user
    response = ComponentsIndexView.as_view()(request)
    assert response.status_code == 200
    assert re.search(r"[1-9]\d* components across", response.content.decode())


@pytest.mark.django_db
def test_the_accounts_page_keeps_its_sort_across_http_fallback_requests(
    site_urls, django_user_model
):
    pytest.importorskip("allauth.socialaccount")
    django_user_model.objects.create_superuser("root", password="pw")
    browser = Client()
    browser.force_login(django_user_model.objects.get(username="root"))
    url = ADMIN + "auth/accounts/"
    assert browser.get(url).status_code == 200
    assert "[provider]" in _post_event(browser, url, "sort_by", field="provider")
    answer = _post_event(browser, url, "search", value="alice")
    assert "[provider]" in answer and "(alice)" in answer


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url, event, params, expected",
    [
        (GALLERY, "search", {"value": "accordion"}, "ccordion"),
        (CATALOGUE, "search", {"value": "accordion"}, "ccordion"),
        ("/ttyd/", "on_ttyd_connect", {"timestamp": "now"}, ""),
    ],
)
def test_public_views_answer_an_event_over_the_http_fallback(
    site_urls, url, event, params, expected
):
    browser = Client()
    assert browser.get(url).status_code == 200
    response = browser.post(
        url, data=json.dumps(params), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )
    assert response.status_code == 200, response.content[:300]
    assert expected in response.content.decode()


# --------------------------------------------------------------------------
# 3. Scaffolder output
# --------------------------------------------------------------------------


def _generated_view_sources() -> dict[str, str]:
    from djust.scaffolding import gen_live_templates as G
    from djust.scaffolding import generator
    from djust.scaffolding import templates as T

    sources = {}
    for flags in ({}, {"with_presence": True, "with_streaming": True}, {"with_db": True}):
        ctx = generator._build_context("child", **flags)
        label = "new:" + ("+".join(sorted(flags)) or "default")
        if flags.get("with_db"):
            written = {}
            original = generator._write
            generator._write = lambda target, content: written.__setitem__(target.name, content)
            try:
                generator._create_db_views(Path("pkg"), Path("tpl"), "child", ctx)
            finally:
                generator._write = original
            sources[label] = written["views.py"]
        else:
            sources[label] = T.VIEWS_PY_BASE % ctx
    sources["new:bare"] = T.BARE_VIEWS_PY % {"app_name": "child", "view_class": "ChildView"}
    sources["new:schema"] = T.SCHEMA_VIEW_CLASS % {
        "view_class": "PostView",
        "app_name": "child",
        "template_name": "post_list.html",
        "mount_body": '        self.search_query = ""',
        "compute_body": "        self.items = []",
        "handler_methods": "",
        "context_body": '            "items": self.items,\n',
    }
    gen_live = {
        "app_name": "child",
        "model_name": "Post",
        "model_slug": "post",
        "view_class": "PostLiveView",
        "q_import": "",
        "search_filter": "            pass\n",
        "create_params": "",
        "create_body": "        pass\n",
        "update_params": "",
        "update_body": "        pass\n",
    }
    sources["gen_live:html"] = G.VIEWS_PY_TEMPLATE % gen_live
    sources["gen_live:api"] = G.VIEWS_PY_API_TEMPLATE % gen_live
    return sources


@pytest.mark.parametrize("label", sorted(_generated_view_sources()))
def test_every_generated_liveview_declares_explicit_exposure(label):
    tree = ast.parse(_generated_view_sources()[label])
    views = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    assert views, label
    assert [_declared_policy(node) for node in views] == ["explicit"] * len(views), label


def _load_generated(source: str, name: str, template: str):
    namespace = {"__name__": MODULE}
    exec(compile(source, "<generated %s>" % name, "exec"), namespace)  # noqa: S102
    view_class = namespace[name]
    view_class.template = template
    view_class.template_name = None
    globals()[name] = view_class  # mountable as MODULE.<name>
    return view_class


async def _runtime_mount(view_class):
    from django.test import override_settings

    from djust.runtime import ViewRuntime
    from djust.tests.test_exposure_runtime import make_request
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": MODULE + "." + view_class.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    return runtime, transport


@pytest.mark.django_db(transaction=True)
async def test_the_generated_bare_view_runs_under_explicit_exposure():
    from djust.scaffolding import templates as T

    view_class = _load_generated(
        T.BARE_VIEWS_PY % {"app_name": "child", "view_class": "GeneratedBareView"},
        "GeneratedBareView",
        "<div dj-root><b>{{ pings }}</b></div>",
    )
    assert view_class.exposure_policy == "explicit"
    runtime, transport = await _runtime_mount(view_class)
    mount = next(frame for frame in transport.sent if frame.get("type") == "mount")
    assert ">0</b>" in mount["html"]
    transport.sent.clear()
    await runtime.dispatch_event(_event("ping"))
    assert not transport.errors, transport.errors
    assert ["1"] == [t for frame in transport.sent for t in _text_patches(frame)]


@pytest.mark.django_db(transaction=True)
async def test_the_generated_starter_view_runs_under_explicit_exposure():
    from djust.scaffolding import generator
    from djust.scaffolding import templates as T

    ctx = generator._build_context("child", with_streaming=True)
    ctx["view_class"] = "GeneratedStarterView"
    view_class = _load_generated(
        T.VIEWS_PY_BASE % ctx,
        "GeneratedStarterView",
        "<div dj-root><b>{{ total_count }}</b>"
        "<ul>{% for item in items %}<li>{{ item.name }}</li>{% endfor %}</ul>"
        '<ol dj-stream="feed">{% for entry in streams.feed %}<li>{{ entry }}</li>{% endfor %}</ol>'
        "</div>",
    )
    assert view_class.exposure_policy == "explicit"
    runtime, transport = await _runtime_mount(view_class)
    mount = next(frame for frame in transport.sent if frame.get("type") == "mount")
    assert ">3</b>" in mount["html"] and "First item" in mount["html"]
    transport.sent.clear()
    await runtime.dispatch_event(_event("add_item", name="Fourth item"))
    assert not transport.errors, transport.errors
    assert "Fourth item" in json.dumps(transport.sent)
    assert runtime.view_instance.get_state() == {}


# The real generated project: its own templates, its own settings.

_PROJECT_PROBE = """
import io, json, os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "child.settings"
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
from django.core.management import call_command
call_command("migrate", run_syncdb=True, verbosity=0)

from child.views import ChildView
from djust.checks.templates import _get_context_data_is_too_dynamic

out = io.StringIO()
call_command("djust_typecheck", "--json", stdout=out)
reports = json.loads(out.getvalue())["reports"]
missing = sorted({m["name"] for r in reports for m in r.get("missing", [])})

from django.test import Client
client = Client()
page = client.get("/")
html = page.content.decode()
answer = client.post(
    "/",
    data=json.dumps({"name": "Fourth item"}),
    content_type="application/json",
    HTTP_X_DJUST_EVENT="add_item",
)
said = client.post(
    "/",
    data=json.dumps({"message": "hello feed"}),
    content_type="application/json",
    HTTP_X_DJUST_EVENT="send_message",
)
print("PROBE_JSON", json.dumps({
    "policy": ChildView.exposure_policy,
    "stream_entry_rendered": "hello feed" in said.content.decode(),
    "typecheck_missing": missing,
    "t018_skips_the_view": _get_context_data_is_too_dynamic(ChildView),
    "status": page.status_code,
    "items_rendered": "First item" in html and "Third item" in html,
    "stream_container": 'dj-stream="feed"' in html,
    "presence_rendered": "0 users online" in html,
    "event_status": answer.status_code,
    "event_added": "Fourth item" in answer.content.decode(),
}))
"""


def test_a_generated_project_renders_typechecks_and_answers_an_event(tmp_path):
    """``djust new --with-presence --with-streaming``, run as generated.

    The page is the scaffold's own template under explicit exposure, an event
    arrives over the HTTP fallback, and the two static tools still read the
    view: ``djust_typecheck`` finds the names ``get_context_data()`` supplies
    (it reads a literal returned dict) and T018 does not skip the view.
    """
    import os
    import subprocess
    import sys

    from djust.scaffolding import generator

    project = generator.generate_project(
        "child",
        target_dir=str(tmp_path),
        auto_setup=False,
        with_presence=True,
        with_streaming=True,
    )
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="child.settings")
    env["PYTHONPATH"] = os.pathsep.join([str(project), env.get("PYTHONPATH", "")])
    probe = subprocess.run(
        [sys.executable, "-c", _PROJECT_PROBE],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    line = next((text for text in probe.stdout.splitlines() if text.startswith("PROBE_JSON")), None)
    assert line is not None, probe.stdout + probe.stderr
    result = json.loads(line.split(" ", 1)[1])
    supplied = {
        "items",
        "search_query",
        "total_count",
        "done_count",
        "theme_packs",
        "theme_pack_count",
        "djust_version",
        "presence_list",
        "presence_count",
    }
    assert supplied.isdisjoint(result.pop("typecheck_missing")), result
    assert result == {
        "policy": "explicit",
        "t018_skips_the_view": False,
        "status": 200,
        "items_rendered": True,
        "stream_container": True,
        "stream_entry_rendered": True,
        "presence_rendered": True,
        "event_status": 200,
        "event_added": True,
    }
