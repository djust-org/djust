"""#3252 authored byte provenance, HTML5 survival and isolated HTTP events."""

import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.urls import path as url_path

from djust import LiveView
from djust._rust import RustLiveView, authored_lazy_elements
from djust.decorators import event_handler
from djust.tenants.mixin import TenantMixin

MOD = __name__
TAG = '<div dj-view="' + MOD + '.Child" dj-lazy></div>'


def test_moduleless_parent_registers_without_crashing():
    from djust._lazy_containers import register_lazy_containers
    from djust._render_provenance import RenderedHTML

    # Match #2488's names-only eval namespace: type() cannot copy __name__.
    cls = eval('type("Moduleless", (), {"exposure_policy": "legacy"})', {})  # noqa: S307
    assert not hasattr(cls, "__module__")
    parent = cls()
    html, spans, _ = render(TAG)
    registered = register_lazy_containers(
        parent, request("get", {}), RenderedHTML(html, tuple(spans))
    )
    assert len(parent._http_lazy_containers) == 1
    assert 'data-djust-lazy-id="lazy_' in registered


@pytest.mark.django_db
def test_lazy_child_decimal_public_and_private_state_survives_session_json():
    from decimal import Decimal

    from djust._lazy_containers import LazyContainer, mount_http_lazy, save_http_lazy

    session = SessionStore()
    session.create()
    parent = Page()
    registry = {"lazy_decimal": LazyContainer(Child, "liveview_decimal", "")}
    parent._http_lazy_containers = registry
    req = request("get", session)
    child = mount_http_lazy(parent, req, "lazy_decimal")
    exact = Decimal("12345678901234567890.123456789")
    child.count = {"nested": [exact]}
    child._private_count = exact
    save_http_lazy(parent, req, "lazy_decimal", child)
    session.save()  # Django's encoder-less JSON boundary, then a fresh read.
    fresh_parent = Page()
    fresh_parent._http_lazy_containers = registry
    restored = mount_http_lazy(
        fresh_parent, request("get", SessionStore(session.session_key)), "lazy_decimal"
    )
    assert restored.count == {"nested": [exact]}
    assert isinstance(restored.count["nested"][0], Decimal)
    assert restored._private_count == exact and isinstance(restored._private_count, Decimal)


class Child(LiveView):
    exposure_policy = "legacy"
    template = '<div><span class="count">{{ count }}</span><button dj-click="inc">+</button></div>'
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.count = 0
        self._private_count = 0

    @event_handler
    def inc(self, **kwargs):
        self.count += 1
        self._private_count += 1
        assert self.count == self._private_count


class Page(LiveView):
    exposure_policy = "legacy"
    template = (
        '<div dj-root><section id="one">'
        + TAG
        + '</section><section id="two">'
        + TAG
        + "</section></div>"
    )

    def mount(self, request, **kwargs):
        self.extra = "é"


urlpatterns = [url_path("lazy/", Page.as_view())]


@pytest.mark.django_db
def test_real_http_middleware_signed_cookie_session_keeps_stable_addresses(settings):
    from django.test import Client

    settings.ROOT_URLCONF = __name__
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    client = Client(enforce_csrf_checks=True)
    response = client.get("/lazy/")
    ids = re.findall(r'data-djust-lazy-id="([^"]+)"', response.content.decode())
    assert len(ids) == 2
    for view_id, expected in [(ids[0], 1), (ids[1], 1), (ids[0], 2), (ids[1], 2)]:
        response = client.post(
            "/lazy/",
            json.dumps({"view_id": view_id}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="inc",
            HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert response.status_code == 200, response.content
        assert re.search(
            r'class="count"[^>]*>' + str(expected) + "</span>", response.json()["html"]
        )
    assert re.findall(r'data-djust-lazy-id="([^"]+)"', client.get("/lazy/").content.decode()) == ids
    other = Client(enforce_csrf_checks=True)
    other.get("/lazy/")
    assert (
        other.post(
            "/lazy/",
            json.dumps({"view_id": ids[0]}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="djust_lazy_mount",
            HTTP_X_CSRFTOKEN=other.cookies[settings.CSRF_COOKIE_NAME].value,
        ).status_code
        == 400
    )


class DeniedChild(Child):
    permission_required = "auth.change_user"


class ObjectDeniedChild(Child):
    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


def render(source, state=None, dirs=None):
    rust = RustLiveView(source, dirs or [])
    rust.update_state(state or {})
    html, spans = rust.render_with_provenance()
    return html, spans, authored_lazy_elements(html, spans)


@pytest.mark.parametrize(
    "source,state,count",
    [
        (TAG, {}, 1),
        (TAG.replace("dj-view", "DJ-VIEW").replace("dj-lazy", "DJ-LAZY"), {}, 1),
        ("{{ forged|safe }}" + TAG, {"forged": TAG}, 1),
        (TAG + "{{ forged|safe }}", {"forged": TAG}, 1),
        (TAG + "{{ forged|safe }}" + TAG, {"forged": TAG}, 2),
        ("{{ forged|safe }}", {"forged": TAG}, 0),
        ("{% autoescape off %}{{ forged }}{% endautoescape %}", {"forged": TAG}, 0),
        ("<!--" + TAG + "-->", {}, 0),
        ('<script>let s="' + TAG + '";</script>', {}, 0),
        ("<style>" + TAG + "</style>", {}, 0),
        ("<textarea>" + TAG + "</textarea>", {}, 0),
        ("<template>" + TAG + "</template>", {}, 0),
        ("<div title='" + TAG + "'></div>", {}, 0),
        (
            "<div title='" + TAG + '\' dj-view="{{ path }}" dj-lazy></div>',
            {"path": MOD + ".Child"},
            0,
        ),
        ("{% filter force_escape %}" + TAG + "{% endfilter %}", {}, 0),
        ("{% filter striptags %}" + TAG + "{% endfilter %}", {}, 0),
        ("{% filter lower %}" + TAG + "{% endfilter %}", {}, 0),
        ("{% if yes %}" + TAG + "{% endif %}", {"yes": True}, 1),
        ("{% if yes %}" + TAG + "{% endif %}", {"yes": False}, 0),
        ("{% for x in xs %}" + TAG + "{% endfor %}", {"xs": [1, 2, 3]}, 3),
        (
            'é<div dj-view="' + MOD + '.Child" class="{{ extra }}" dj-lazy="hover"></div>',
            {"extra": "é"},
            1,
        ),
        ('<div dj-view="{{ path }}" dj-lazy></div>', {"path": MOD + ".Child"}, 0),
        ('<div dj-view="' + MOD + '.Child" dj-lazy="{{ mode }}"></div>', {"mode": "hover"}, 0),
        (
            '<div {{ attrs|safe }} dj-view="' + MOD + '.Child" dj-lazy></div>',
            {"attrs": 'dj-view="forged.Child"'},
            0,
        ),
        (
            '<div dj-view="' + MOD + '.Child" dj-lazy {{ attrs|safe }}></div>',
            {"attrs": 'dj-view="forged.Child"'},
            1,
        ),
    ],
)
def test_contract_matrix(source, state, count):
    html, spans, found = render(source, state)
    assert len(found) == count, (html, spans, found)
    for start, end, path, trigger in found:
        assert html.encode()[start:end].startswith(b"<div")
        assert path == MOD + ".Child"


def test_includes_and_blocks_extends(tmp_path):
    (tmp_path / "child.html").write_text(TAG)
    (tmp_path / "base.html").write_text("<main>{% block body %}{% endblock %}</main>")
    source = '{% extends "base.html" %}{% block body %}{% for x in xs %}{% if x %}{% include "child.html" %}{% endif %}{% endfor %}{% endblock %}'
    assert len(render(source, {"xs": [1, 0, 2]}, [str(tmp_path)])[2]) == 2


@pytest.mark.parametrize(
    "expr,count", [("block.super", 1), ("block.super|safe", 0), ("block.super|lower", 0)]
)
def test_super_is_composed_but_filtered_super_is_a_value(tmp_path, expr, count):
    (tmp_path / "base.html").write_text("<main>{% block body %}" + TAG + "{% endblock %}</main>")
    source = '{% extends "base.html" %}{% block body %}{{ ' + expr + " }}{% endblock %}"
    assert len(render(source, dirs=[str(tmp_path)])[2]) == count


def test_reentrant_and_parallel_results_do_not_share_metadata():
    first = render(TAG)
    second = render("plain")
    assert len(first[2]) == 1 and second[2] == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: render(TAG if i % 2 else "plain"), range(20)))
    assert [len(r[2]) for r in results] == [i % 2 for i in range(20)]


def test_normalizer_restores_only_its_owned_placeholder_not_safe_payload_text():
    from djust._render_provenance import RenderedHTML

    html, spans, _ = render(
        "{{ forged|safe }}<pre>" + TAG + "</pre>{{ forged|safe }}",
        {"forged": "__PRESERVED_BLOCK_0__"},
    )
    normalized = Page()._strip_comments_and_whitespace(RenderedHTML(html, tuple(spans)))
    assert len(authored_lazy_elements(normalized, list(normalized.spans))) == 1
    assert normalized.count("__PRESERVED_BLOCK_0__") == 2


def request(method, session, body=None, user=None, tenant=None):
    factory = RequestFactory()
    req = (
        factory.get("/lazy/")
        if method == "get"
        else factory.post("/lazy/", json.dumps(body), content_type="application/json")
    )
    req.META["HTTP_X_TENANT"] = tenant
    req.session = session
    req.user = user or AnonymousUser()
    return req


def get_ids(session, cls=Page, user=None, tenant=None):
    response = cls.as_view()(request("get", session, user=user, tenant=tenant))
    assert response.status_code == 200, response.content
    session.save()
    return re.findall(r'data-djust-lazy-id="([^"]+)"', response.content.decode())


def post(session, view_id, event="inc", cls=Page, user=None, tenant=None):
    response = cls.as_view()(
        request("post", session, {"event": event, "params": {"view_id": view_id}}, user, tenant)
    )
    session.save()
    return response


@pytest.mark.django_db
def test_http_two_instances_retain_public_and_private_state_and_mount_on_trigger():
    session = SessionStore()
    session.create()
    Child.mounts = 0
    ids = get_ids(session)
    assert len(ids) == 2 and len(set(ids)) == 2
    assert Child.mounts == 0
    assert get_ids(SessionStore(session.session_key)) == ids
    for view_id, event, expected in [
        (ids[0], "djust_lazy_mount", 0),
        (ids[1], "djust_lazy_mount", 0),
        (ids[0], "inc", 1),
        (ids[1], "inc", 1),
        (ids[0], "inc", 2),
    ]:
        response = post(SessionStore(session.session_key), view_id, event)
        assert response.status_code == 200, response.content
        data = json.loads(response.content)
        assert data["view_id"] == view_id and data["type"] == "embedded_update"
        assert re.search(r'class="count"[^>]*>' + str(expected) + "</span>", data["html"]), data


@pytest.mark.django_db
def test_other_session_and_forged_ids_are_refused():
    a, b = SessionStore(), SessionStore()
    a.create()
    b.create()
    ids = get_ids(a)
    assert not set(ids) & set(get_ids(b))
    for value in (ids[0], "lazy_000000000000", MOD + ".Child"):
        assert post(b, value).status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("view", [DeniedChild, ObjectDeniedChild])
def test_registration_applies_child_auth_and_object_permission(view):
    class DeniedPage(Page):
        template = (
            '<div dj-root><div dj-view="' + MOD + "." + view.__name__ + '" dj-lazy></div></div>'
        )

    session = SessionStore()
    session.create()
    ids = get_ids(session, DeniedPage)
    if view is DeniedChild:
        assert ids == []
    else:
        assert len(ids) == 1
        assert post(session, ids[0], "djust_lazy_mount", DeniedPage).status_code == 403


class TenantChild(Child):
    def get_state_key_prefix(self):
        return self.request.META.get("HTTP_X_TENANT")


class TenantPage(Page):
    template = '<div dj-root><div dj-view="' + MOD + '.TenantChild" dj-lazy></div></div>'

    def get_state_key_prefix(self):
        return self.request.META.get("HTTP_X_TENANT")


class ResolvedTenantChild(TenantMixin, Child):
    tenant_required = True

    def resolve_tenant(self, request):
        from types import SimpleNamespace

        value = request.META.get("HTTP_X_TENANT")
        return SimpleNamespace(id=value) if value else None


@pytest.mark.django_db
def test_child_tenant_resolves_before_mount_and_unresolved_child_is_refused():
    class ChildTenantPage(Page):
        template = (
            '<div dj-root><div dj-view="' + MOD + '.ResolvedTenantChild" dj-lazy></div></div>'
        )

    session = SessionStore()
    session.create()
    assert get_ids(session, ChildTenantPage) == []
    a = get_ids(session, ChildTenantPage, tenant="A")
    b = get_ids(session, ChildTenantPage, tenant="B")
    assert a and b and a != b
    assert post(session, a[0], cls=ChildTenantPage, tenant="B").status_code == 400
    for ids, tenant, count in [(a, "A", 1), (b, "B", 1), (a, "A", 2)]:
        response = post(session, ids[0], cls=ChildTenantPage, tenant=tenant)
        assert response.status_code == 200, response.content
        assert re.search(
            r'class="count"[^>]*>' + str(count) + "</span>", json.loads(response.content)["html"]
        )


@pytest.mark.django_db
def test_same_session_user_and_tenant_bindings_do_not_share_state(django_user_model):
    session = SessionStore()
    session.create()
    first = django_user_model.objects.create_user(username="lazy-first")
    second = django_user_model.objects.create_user(username="lazy-second")
    a = get_ids(session, user=first)
    b = get_ids(session, user=second)
    assert not set(a) & set(b)
    assert post(session, a[0], user=second).status_code == 400
    ta = get_ids(session, TenantPage, first, "tenant:A")
    tb = get_ids(session, TenantPage, first, "tenant:B")
    assert ta and tb and ta != tb
    assert post(session, ta[0], cls=TenantPage, user=first, tenant="tenant:B").status_code == 400
    assert get_ids(session, TenantPage, first) == []
    for ids, tenant, count in [(ta, "tenant:A", 1), (tb, "tenant:B", 1), (ta, "tenant:A", 2)]:
        response = post(session, ids[0], cls=TenantPage, user=first, tenant=tenant)
        assert response.status_code == 200, response.content
        assert re.search(
            r'class="count"[^>]*>' + str(count) + "</span>", json.loads(response.content)["html"]
        )


def test_nested_renderer_callback_cannot_grant_its_output_authority():
    from djust._rust import register_tag_handler, unregister_tag_handler
    from django.utils.safestring import mark_safe

    class Nested:
        def render(self, args, context):
            inner = render(TAG)
            assert len(inner[2]) == 1
            assert render("plain")[2] == []
            return mark_safe(inner[0])

    register_tag_handler("lazy_nested_3252", Nested())
    try:
        result = render(TAG + "{% lazy_nested_3252 %}" + TAG)
        assert len(result[2]) == 2
    finally:
        unregister_tag_handler("lazy_nested_3252")


@pytest.mark.django_db
def test_rendered_page_loop_keys_and_safe_forgery_counts():
    class LoopPage(Page):
        template = (
            "<div dj-root>{{ forged|safe }}{% for n in numbers %}"
            + TAG
            + "{% endfor %}{{ forged|safe }}</div>"
        )

        def mount(self, request, **kwargs):
            self.forged = TAG
            self.numbers = [1, 2, 3]

    session = SessionStore()
    session.create()
    ids = get_ids(session, LoopPage)
    assert len(ids) == 3 and len(set(ids)) == 3
    assert get_ids(session, LoopPage) == ids


@pytest.mark.django_db
def test_http_registration_from_inherited_page_shell_and_included_root(tmp_path, settings):
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "base3252.html").write_text(
        "<html><head></head><body>" + TAG + "{% block body %}{% endblock %}</body></html>"
    )
    (tmp_path / "included3252.html").write_text(
        '<div dj-view="' + MOD + '.Child" class="{{ extra }}" dj-lazy="click"></div>'
    )
    (tmp_path / "page3252.html").write_text(
        '{% extends "base3252.html" %}{% block body %}<div dj-root>{% for n in numbers %}{% if n %}{% include "included3252.html" %}{% endif %}{% endfor %}</div>{% endblock %}'
    )

    class FilePage(Page):
        template = None
        template_name = "page3252.html"

        def mount(self, request, **kwargs):
            self.numbers = [1, 0, 2]
            self.extra = "é"

    session = SessionStore()
    session.create()
    ids = get_ids(session, FilePage)
    assert len(ids) == 3 and len(set(ids)) == 3
    for view_id in ids:
        response = post(session, view_id, cls=FilePage)
        assert response.status_code == 200, response.content
        assert re.search(r'class="count"[^>]*>1</span>', json.loads(response.content)["html"])
