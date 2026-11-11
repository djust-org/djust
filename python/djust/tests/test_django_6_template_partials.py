"""Django 6.x template partial compatibility through the djust backend."""

import django
import pytest
from django.template import Context, Engine, TemplateDoesNotExist, TemplateSyntaxError

from djust.template.backend import DjustTemplateBackend

pytestmark = pytest.mark.skipif(
    django.VERSION < (6, 0), reason="Django template partials require 6.0+"
)


def _backends(tmp_path):
    return (
        Engine(dirs=[str(tmp_path)]),
        DjustTemplateBackend(
            {"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
        ),
    )


@pytest.mark.parametrize(
    "source",
    [
        "{% partialdef greeting inline %}Hello {{ name }}{% endpartialdef %}",
        "{% partial greeting %}|{% partialdef greeting %}Hello {{ name }}{% endpartialdef %}|{% partial greeting %}",
        "{% partialdef item %}[{{ value }}]{% endpartialdef item %}{% for value in values %}{% partial item %}{% endfor %}",
        "{% if show %}{% partialdef inner %}{{ name }}{% endpartialdef %}{% endif %}{% partial inner %}",
    ],
)
def test_partial_definitions_and_references_match_django(tmp_path, source):
    django_engine, djust_engine = _backends(tmp_path)
    values = {"name": "Ada <L>", "values": [1, 2], "show": False}
    expected = django_engine.from_string(source).render(Context(values))
    assert djust_engine.from_string(source).render(values) == expected


def test_include_and_select_named_partial_match_django(tmp_path):
    (tmp_path / "card.html").write_text(
        "header{% partialdef card %}<b>{{ name }}</b>{% endpartialdef %}footer", encoding="utf-8"
    )
    django_engine, djust_engine = _backends(tmp_path)
    values = {"name": "Ada"}
    assert djust_engine.get_template("card.html#card").render(values) == django_engine.get_template(
        "card.html#card"
    ).render(Context(values))
    source = '{% include "card.html#card" %}'
    assert djust_engine.from_string(source).render(values) == django_engine.from_string(
        source
    ).render(Context(values))


def test_missing_named_partial_raises_template_does_not_exist(tmp_path):
    (tmp_path / "card.html").write_text("plain", encoding="utf-8")
    _, djust_engine = _backends(tmp_path)
    with pytest.raises(TemplateDoesNotExist):
        djust_engine.get_template("card.html#missing")


def test_duplicate_partial_definition_is_rejected(tmp_path):
    _, djust_engine = _backends(tmp_path)
    with pytest.raises(TemplateSyntaxError):
        djust_engine.from_string(
            "{% partialdef a %}x{% endpartialdef %}{% partialdef a %}y{% endpartialdef %}"
        )


def test_undefined_partial_fails_at_render_time(tmp_path):
    _, djust_engine = _backends(tmp_path)
    template = djust_engine.from_string("{% partial missing %}")
    with pytest.raises(TemplateSyntaxError):
        template.render({})


def test_each_partial_invocation_has_its_own_render_state(tmp_path):
    django_engine, djust_engine = _backends(tmp_path)
    source = "{% partialdef cell %}{% cycle 'a' 'b' %}{% endpartialdef %}{% partial cell %}{% partial cell %}"
    assert djust_engine.from_string(source).render({}) == django_engine.from_string(source).render(
        Context({})
    )


def test_recursive_partial_renders_finite_tree(tmp_path):
    django_engine, djust_engine = _backends(tmp_path)
    source = "{% partialdef tree %}{{ node.name }}{% if node.child %}{% with node=node.child %}{% partial tree %}{% endwith %}{% endif %}{% endpartialdef %}{% partial tree %}"
    values = {"node": {"name": "A", "child": {"name": "B", "child": None}}}
    assert djust_engine.from_string(source).render(values) == django_engine.from_string(
        source
    ).render(Context(values))


def test_partial_recursion_failure_does_not_poison_following_render():
    from djust._rust import render_template

    recursive = "{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}"
    for _ in range(2):
        with pytest.raises(RecursionError):
            render_template(recursive, {})
        assert render_template("{% partialdef p %}OK{% endpartialdef %}{% partial p %}", {}) == "OK"


def test_many_sequential_partial_calls_do_not_accumulate_depth():
    from djust._rust import render_template

    source = "{% partialdef p %}X{% endpartialdef %}" + "{% partial p %}" * 128
    assert render_template(source, {}) == "X" * 128


def test_forward_partial_loop_dependency_extraction():
    from djust._rust import extract_template_variables

    source = "{% for row in rows %}{% partial cell %}{% endfor %}{% partialdef cell %}{{ row.author.name }}{% endpartialdef %}"
    assert "author.name" in extract_template_variables(source)["rows"]


def test_partial_calls_have_unique_stable_liveview_markers():
    import re

    from djust._rust import RustLiveView

    source = "<div dj-root>{% partialdef p %}{% if visible %}<b>{{ value }}</b>{% endif %}{% endpartialdef %}{% partial p %}{% partial p %}</div>"
    view = RustLiveView(source)
    view.update_state({"visible": True, "value": "one"})
    html, _, _ = view.render_with_diff()
    ids = re.findall(r'<!--dj-if id="([^"]+)"-->', html)
    assert len(ids) == 2
    assert len(set(ids)) == 2
    view.update_state({"visible": True, "value": "two"})
    view.set_changed_keys(["value"])
    updated, patches, _ = view.render_with_diff()
    assert re.findall(r'<!--dj-if id="([^"]+)"-->', updated) == ids
    assert updated.count("two") == 2
    assert patches


def test_unbounded_partial_recursion_raises_without_crashing_process():
    import subprocess
    import sys

    program = (
        "import sys\nsys.path.extend("
        + repr(sys.path)
        + ")\n"
        + """
from djust._rust import render_template
source = '{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}'
try:
    render_template(source, {})
except Exception as exc:
    assert 'recursion' in str(exc).lower()
else:
    raise AssertionError('Unbounded partial recursion did not fail')
"""
    )
    subprocess.run([sys.executable, "-c", program], check=True, timeout=15)


def test_partial_body_uses_defining_template_relative_include(tmp_path):
    directory = tmp_path / "subdir"
    directory.mkdir()
    (directory / "child.html").write_text("hello {{ name }}", encoding="utf-8")
    (directory / "parent.html").write_text(
        '{% partialdef greeting %}{% include "./child.html" %}{% endpartialdef %}{% partial greeting %}',
        encoding="utf-8",
    )
    django_engine, djust_engine = _backends(tmp_path)
    values = {"name": "Ada"}
    assert djust_engine.get_template("subdir/parent.html").render(
        values
    ) == django_engine.get_template("subdir/parent.html").render(Context(values))


def test_partial_ifchanged_has_fresh_render_context(tmp_path):
    django_engine, djust_engine = _backends(tmp_path)
    source = "{% partialdef p %}{% ifchanged %}x{% endifchanged %}{% endpartialdef %}{% partial p %}{% partial p %}"
    assert djust_engine.from_string(source).render({}) == django_engine.from_string(source).render(
        Context({})
    )


@pytest.mark.django_db
def test_forward_partial_queryset_loop_survives_liveview_update():
    from django.contrib.auth.models import Permission

    from djust import LiveView
    from djust.decorators import event_handler

    permission = Permission.objects.select_related("content_type").first()
    assert permission is not None
    expected = permission.content_type.app_label

    class PartialRowsView(LiveView):
        template = (
            "<div dj-root><span>{{ label }}</span>{% for row in rows %}"
            "{% partial cell %}{% endfor %}"
            "{% partialdef cell %}<b>{{ row.content_type.app_label }}</b>{% endpartialdef %}</div>"
        )

        def mount(self, request, **kwargs):
            self.rows = Permission.objects.filter(pk=permission.pk)
            self.label = "before"

        @event_handler()
        def change(self):
            self.label = "after"

    view = PartialRowsView()
    view.mount(None)
    before, _, _ = view.render_with_diff(None)
    assert f">{expected}</b>" in before
    view.change()
    after, _, _ = view.render_with_diff(None)
    assert f">{expected}</b>" in after
    assert "after" in after


def test_partial_metadata_exposes_nested_source_and_renders(tmp_path):
    _, backend = _backends(tmp_path)
    backend.debug = True
    source = (
        "{% comment %}{% partialdef fake %}ignored{% endpartialdef %}{% endcomment %}"
        "{% partialdef outer %}A{% partialdef inner %}{{ name }}{% endpartialdef inner %}"
        "B{% endpartialdef outer %}"
    )
    template = backend.from_string(source)
    partials = template.extra_data["partials"]
    assert set(partials) == {"outer", "inner"}
    assert (
        partials["inner"].find_partial_source(source)
        == "{% partialdef inner %}{{ name }}{% endpartialdef inner %}"
    )
    assert partials["inner"].render(Context({"name": "Ada"})) == "Ada"


def test_partial_metadata_ignores_opaque_block_tag_bodies(tmp_path):
    from djust._rust import register_raw_block_tag_handler, unregister_raw_block_tag_handler

    class Opaque:
        def render(self, args, body, context):
            return ""

    register_raw_block_tag_handler("opaque_for_partials", "endopaque_for_partials", Opaque())
    try:
        _, backend = _backends(tmp_path)
        source = (
            "{% opaque_for_partials %}{% partialdef fake %}no{% endpartialdef %}{% endopaque_for_partials %}"
            "{% partialdef real %}yes{% endpartialdef %}"
        )
        assert set(backend.from_string(source).extra_data["partials"]) == {"real"}
    finally:
        unregister_raw_block_tag_handler("opaque_for_partials")


def test_partial_source_without_debug_warns_and_is_empty(tmp_path):
    backend = DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {"debug": False}}
    )
    partial = backend.from_string("{% partialdef p %}hello{% endpartialdef %}").extra_data[
        "partials"
    ]["p"]
    with pytest.warns(RuntimeWarning, match="only available when template debugging is enabled"):
        assert partial.source == ""


@pytest.mark.parametrize(
    "body",
    [
        "{% partialdef p %}<div dj-view='demo.View' dj-lazy></div>{{ bad|safe }}{% endpartialdef %}{% partial p %}{% partial p %}",
        "{% partialdef p inline %}<div dj-view='demo.View' dj-lazy></div>{{ bad|safe }}{% endpartialdef %}{% partial p %}",
        "{% partialdef p %}<div dj-view='demo.View' dj-lazy></div>{{ bad|safe }}{% endpartialdef %}{% for n in nums %}{% partial p %}{% endfor %}",
    ],
)
def test_repeated_partials_have_stable_distinct_lazy_authority(body):
    from djust._render_provenance import RenderedHTML
    from djust._rust import RustLiveView, authored_lazy_elements

    view = RustLiveView("<main dj-root>" + body + "</main>", [])
    view.update_state({"bad": '<div dj-view="evil.Forged" dj-lazy></div>', "nums": [1, 2]})
    addresses = []
    for _ in range(2):
        html, spans, origins = view.render_lazy_html()
        tracked = RenderedHTML(html, tuple(spans), origins=tuple(origins))
        found = authored_lazy_elements(html, spans)
        assert len(found) == 2
        current = [tracked.authored_identity(a, b) for a, b, _, _ in found]
        assert len(set(current)) == 2
        addresses.append(current)
    assert addresses[0] == addresses[1]


def test_inherited_partial_lazy_content_requires_provenance(tmp_path):
    from djust._rust import RustLiveView, authored_lazy_elements, template_needs_provenance

    (tmp_path / "base.html").write_text(
        "<main dj-root>{% block body %}{% endblock %}</main>", encoding="utf-8"
    )
    source = (
        '{% extends "base.html" %}{% partialdef p %}<div dj-view="demo.View" dj-lazy></div>'
        "{% endpartialdef %}{% block body %}{% partial p %}{% endblock %}"
    )
    dirs = [str(tmp_path)]
    assert template_needs_provenance(source, dirs)
    html, spans, _ = RustLiveView(source, dirs).render_lazy_html()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_partial_names_are_isolated_across_template_inheritance(tmp_path):
    (tmp_path / "base.html").write_text(
        "{% partialdef p %}BASE{% endpartialdef %}{% partial p %}{% block body %}{% endblock %}",
        encoding="utf-8",
    )
    source = (
        '{% extends "base.html" %}{% partialdef p %}CHILD{% endpartialdef %}'
        "{% block body %}{% partial p %}{% endblock %}"
    )
    django_engine, djust_engine = _backends(tmp_path)
    assert djust_engine.from_string(source).render({}) == django_engine.from_string(source).render(
        Context({})
    )


def test_partial_model_bindings_are_collected_from_inherited_body(tmp_path):
    from djust._rust import dj_model_fields_from_template

    (tmp_path / "base.html").write_text("{% block body %}{% endblock %}", encoding="utf-8")
    source = (
        '{% extends "base.html" %}{% partialdef p %}<input dj-model="title">{% endpartialdef %}'
        "{% block body %}{% partial p %}{% endblock %}"
    )
    assert "title" in dj_model_fields_from_template(source, [str(tmp_path)])


def test_inherited_partial_relative_include_tracks_new_lazy_content(tmp_path):
    from djust._rust import RustLiveView, authored_lazy_elements, template_needs_provenance

    (tmp_path / "base.html").write_text(
        "<main dj-root>{% block body %}{% endblock %}</main>", encoding="utf-8"
    )
    included = tmp_path / "piece.html"
    included.write_text("ordinary", encoding="utf-8")
    source = (
        '{% extends "base.html" %}{% partialdef p %}{% include "./piece.html" %}{% endpartialdef %}'
        "{% block body %}{% partial p %}{% endblock %}"
    )
    dirs = [str(tmp_path)]
    # The planner may conservatively track even before the include has lazy markup.
    template_needs_provenance(source, dirs)
    included.write_text('<div dj-view="demo.View" dj-lazy></div>', encoding="utf-8")
    assert template_needs_provenance(source, dirs)
    html, spans, _ = RustLiveView(source, dirs).render_lazy_html()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_flattened_inheritance_preserves_partial_definition_ownership(tmp_path):
    from djust._rust import render_template, resolve_template_inheritance

    (tmp_path / "base.html").write_text(
        "<main>{% partialdef p %}BASE{% endpartialdef %}P[{% partial p %}]"
        "{% block body %}{% endblock %}</main>",
        encoding="utf-8",
    )
    (tmp_path / "child.html").write_text(
        '{% extends "base.html" %}{% partialdef p %}CHILD{% endpartialdef %}'
        "{% block body %}C[{% partial p %}]{% endblock %}",
        encoding="utf-8",
    )
    resolved = resolve_template_inheritance("child.html", [str(tmp_path)])
    assert render_template(resolved, {}) == "<main>P[BASE]C[CHILD]</main>"


@pytest.mark.django_db
@pytest.mark.parametrize("root_in_child", [False, True])
def test_liveview_root_extraction_keeps_partial_definitions(tmp_path, root_in_child):
    from django.test import override_settings

    from djust import LiveView

    base_body = "{% block body %}{% endblock %}"
    base = base_body if root_in_child else "<main dj-root>" + base_body + "</main>"
    (tmp_path / "base.html").write_text(base, encoding="utf-8")
    inner = "{% partial cell %}"
    if root_in_child:
        inner = "<main dj-root>" + inner + "</main>"
    (tmp_path / "child.html").write_text(
        '{% extends "base.html" %}{% partialdef cell %}<b>{{ value }}</b>{% endpartialdef %}'
        "{% block body %}" + inner + "{% endblock %}",
        encoding="utf-8",
    )
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]

    class NamedPartialView(LiveView):
        template_name = "child.html"

        def mount(self, request, **kwargs):
            self.value = "works"

    with override_settings(TEMPLATES=templates):
        view = NamedPartialView()
        view.mount(None)
        html, _, _ = view.render_with_diff(None)
        assert ">works</b>" in html


def test_root_extraction_preserves_only_authored_lazy_authority():
    from djust import LiveView
    from djust._rust import RustLiveView, authored_lazy_elements

    source = (
        '{% partialdef item inline %}<div dj-view="demo.View" dj-lazy></div>'
        "{{ evil|safe }}{% endpartialdef %}<main dj-root>{% partial item %}</main>"
    )
    view = LiveView()
    extracted = view._extract_liveview_root_with_wrapper(source)
    rust = RustLiveView(extracted, [])
    rust.update_state({"evil": '<div dj-view="evil.Forged" dj-lazy></div>'})
    html, spans, _ = rust.render_lazy_html()
    assert len(authored_lazy_elements(html, spans)) == 1
    assert html.count('dj-view="demo.View"') == 1


@pytest.mark.django_db
@pytest.mark.parametrize("invoke", [False, True])
@pytest.mark.parametrize("separate_root", [False, True])
def test_liveview_uses_only_one_executed_authored_root(tmp_path, invoke, separate_root):
    from django.core.exceptions import ImproperlyConfigured
    from django.test import override_settings

    from djust import LiveView

    source = (
        "<header>outer-header</header>"
        "{% partialdef page %}<main dj-root><b>hidden-or-called</b></main>{% endpartialdef %}"
        + ("{% partial page %}" if invoke else "")
        + ("<main dj-root>legitimate</main>" if separate_root else "")
        + "<footer>outer-footer</footer>"
    )
    (tmp_path / "page.html").write_text(source, encoding="utf-8")
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]

    class PartialRootView(LiveView):
        template_name = "page.html"

    with override_settings(TEMPLATES=templates):
        view = PartialRootView()
        if invoke == separate_root:  # neither, or two competing roots
            with pytest.raises(ImproperlyConfigured):
                view.render_with_diff(None)
        else:
            html, _, _ = view.render_with_diff(None)
            assert "outer-header" not in html and "outer-footer" not in html
            if invoke:
                assert "hidden-or-called" in html and "legitimate" not in html
            else:
                assert "legitimate" in html and "hidden-or-called" not in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_uncalled_partial_does_not_hide_websocket_root_or_events(tmp_path):
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings

    from djust import LiveView
    from djust.decorators import event_handler
    from djust.websocket import LiveViewConsumer

    (tmp_path / "page.html").write_text(
        "{% partialdef page %}<main dj-root>HIDDEN</main>{% endpartialdef %}"
        "<main dj-root>LEGITIMATE {{ changed }}</main>",
        encoding="utf-8",
    )
    events = []

    class PartialRootSocketView(LiveView):
        template_name = "page.html"
        changed = False

        @event_handler()
        def change(self):
            self.changed = True
            events.append("called")

    globals()["PartialRootSocketView"] = PartialRootSocketView
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]
    with override_settings(TEMPLATES=templates, LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.PartialRootSocketView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") == "mount", mount
            assert "LEGITIMATE" in str(mount) and "HIDDEN" not in str(mount)
            assert not events
            await comm.send_json_to({"type": "event", "event": "change", "params": {}, "ref": 1})
            reply = await comm.receive_json_from(timeout=5)
            assert reply.get("type") not in {"error", "view_refused", "close"}, reply
            assert events == ["called"]
        finally:
            await comm.disconnect()
            globals().pop("PartialRootSocketView", None)


@pytest.mark.django_db
def test_uncalled_partial_does_not_hide_http_root(tmp_path):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory, override_settings

    from djust import LiveView

    (tmp_path / "page.html").write_text(
        "{% partialdef page %}<main dj-root>HIDDEN</main>{% endpartialdef %}"
        "<main dj-root>LEGITIMATE</main>",
        encoding="utf-8",
    )
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]

    class PartialRootHttpView(LiveView):
        template_name = "page.html"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates):
        response = PartialRootHttpView.as_view()(request)
    assert response.status_code == 200
    html = response.content.decode()
    assert "LEGITIMATE" in html and "HIDDEN" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("explicit_root", [False, True])
def test_named_liveview_partial_include_uses_defining_template_directory(
    tmp_path, inherited, explicit_root
):
    from django.template import loader
    from django.test import override_settings
    from djust import LiveView

    parent = tmp_path / "parent"
    parent.mkdir()
    child = tmp_path / "child"
    child.mkdir()
    (parent / "piece.html").write_text("PARENT-PIECE", encoding="utf-8")
    (child / "piece.html").write_text("CHILD-PIECE", encoding="utf-8")
    prefix = '{% partialdef cell %}{% include "./piece.html" %}{% endpartialdef %}'
    opening = "<main dj-root>" if explicit_root else "<main>"
    if inherited:
        (parent / "base.html").write_text(
            prefix + opening + "P[{% partial cell %}]{% block body %}{% endblock %}</main>",
            encoding="utf-8",
        )
        (child / "page.html").write_text(
            '{% extends "parent/base.html" %}{% block body %}CHILD{% endblock %}', encoding="utf-8"
        )
        name, expected = "child/page.html", "PARENT-PIECE"
    else:
        (child / "page.html").write_text(
            prefix + opening + "P[{% partial cell %}]</main>", encoding="utf-8"
        )
        name, expected = "child/page.html", "CHILD-PIECE"
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]

    class PartialIncludeView(LiveView):
        template_name = name

    with override_settings(TEMPLATES=templates):
        expected_html = loader.get_template(name).render({})
        html, _, _ = PartialIncludeView().render_with_diff(None)
    assert expected in expected_html and expected in html
    assert ("CHILD-PIECE" if inherited else "PARENT-PIECE") not in html


def test_relative_include_resolution_does_not_rewrite_dynamic_or_opaque_content():
    from djust._rust import (
        register_raw_block_tag_handler,
        resolve_relative_template_includes,
        unregister_raw_block_tag_handler,
    )

    class Opaque:
        def render(self, args, body, context):
            return ""

    register_raw_block_tag_handler("opaque_include", "endopaque_include", Opaque())
    source = (
        '{% include "./piece.html" with key="./piece.html" %}'
        "{% include variable %}"
        '{% opaque_include %}{% include "./opaque.html" %}{% endopaque_include %}'
    )
    try:
        result = resolve_relative_template_includes(source, "child/page.html")
    finally:
        unregister_raw_block_tag_handler("opaque_include")
    assert '{% include "child/piece.html" with key="./piece.html" %}' in result
    assert "{% include variable %}" in result
    assert '{% include "./opaque.html" %}' in result
