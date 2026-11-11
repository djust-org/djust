"""Executed Django partials can own a LiveView root without widening its authority."""

import json

import django
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust import LiveView

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(django.VERSION < (6, 0), reason="Django partials require Django 6+"),
]


def templates_for(directory):
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(directory)],
            "APP_DIRS": False,
        }
    ]


def write(directory, name, source):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def view_class(name="page.html"):
    class PartialView(LiveView):
        template_name = name

    return PartialView


def test_with_caller_context_is_shared_by_initial_page_and_diff(tmp_path):
    write(
        tmp_path,
        "page.html",
        "<header>HEADER</header>"
        "{% partialdef page %}<main dj-root><b>{{ selected }}</b></main>{% endpartialdef %}"
        "{% with selected=value %}{% partial page %}{% endwith %}<footer>FOOTER</footer>",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.value = "first"
        view.get_template()
        full = view.render_full_template(None)
        assert full.count("HEADER") == 1 and full.count("FOOTER") == 1
        assert ">first</b>" in full
        html, _, _ = view.render_with_diff(None)
        assert ">first</b>" in html
        assert "HEADER" not in html and "FOOTER" not in html
        view.value = "second"
        html, patches, _ = view.render_with_diff(None)
        assert ">second</b>" in html
        assert patches and "second" in str(patches)


def test_uncalled_partial_does_not_override_actual_authored_root(tmp_path):
    write(
        tmp_path,
        "page.html",
        "{% partialdef unused %}<main dj-root>HIDDEN</main>{% endpartialdef %}"
        "<main dj-root><b>{{ value }}</b></main>",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.value = "actual"
        html, _, _ = view.render_with_diff(None)
        assert ">actual</b>" in html
        assert "HIDDEN" not in html


def test_inherited_partial_and_relative_include_preserve_context(tmp_path):
    write(
        tmp_path,
        "base.html",
        "<html><body><header>HEADER</header>{% block body %}{% endblock %}<footer>FOOTER</footer></body></html>",
    )
    write(tmp_path, "nested/piece.html", "<b>{{ selected }}</b>")
    write(
        tmp_path,
        "page.html",
        '{% extends "base.html" %}'
        '{% partialdef page %}<main dj-root>{% include "./nested/piece.html" %}</main>{% endpartialdef %}'
        "{% block body %}{% with selected=value %}{% partial page %}{% endwith %}{% endblock %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.value = "inherited"
        view.get_template()
        page = view.render_full_template(None)
        assert "HEADER" in page and "FOOTER" in page and ">inherited</b>" in page
        html, _, _ = view.render_with_diff(None)
        assert ">inherited</b>" in html and "HEADER" not in html and "FOOTER" not in html


def test_outer_safe_markup_and_unrelated_bindings_have_no_root_authority(tmp_path):
    write(
        tmp_path,
        "page.html",
        '<header>{{ untrusted|safe }}<input dj-model="is_admin"></header>'
        '{% partialdef page %}<main dj-root><input dj-model="query"><b>{{ query }}</b></main>{% endpartialdef %}'
        "{% partial page %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.query = "real"
        view.is_admin = False
        view.untrusted = '<span><input dj-model="is_admin">SHELL_SAFE</span>'
        view.get_template()
        page = view.render_full_template(None)
        assert "SHELL_SAFE" in page
        html, _, _ = view.render_with_diff(None)
        assert ">real</b>" in html and "SHELL_SAFE" not in html
        assert "query" in view._dj_model_fields and "is_admin" not in view._dj_model_fields
        view.update_model(field="query", value="updated")
        view.update_model(field="is_admin", value=True)
        assert view.query == "updated" and view.is_admin is False


@pytest.mark.parametrize("invalid_rows", [[], ["one", "two"]], ids=["absent", "multiple"])
def test_root_count_failure_preserves_last_successful_diff_baseline(tmp_path, invalid_rows):
    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root><b>{{ row }} {{ value }}</b></main>{% endpartialdef %}"
        "{% for row in rows %}{% partial page %}{% endfor %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.rows, view.value = ["one"], "before"
        first, _, version = view.render_with_diff(None)
        assert ">one before</b>" in first
        view.rows = invalid_rows
        with pytest.raises(ImproperlyConfigured):
            view.render_with_diff(None)
        view.rows, view.value = ["one"], "after"
        html, patches, after_version = view.render_with_diff(None)
        assert ">one after</b>" in html
        assert patches and "after" in str(patches)
        assert after_version == version + 1


def test_conditional_invocation_renders_when_true_without_losing_with_scope(tmp_path):
    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root>{{ selected }}</main>{% endpartialdef %}"
        "{% if show %}{% with selected=value %}{% partial page %}{% endwith %}{% endif %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.show, view.value = True, "visible"
        html, _, _ = view.render_with_diff(None)
        assert ">visible</main>" in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_actual_websocket_mount_and_event_use_partial_root(tmp_path):
    from channels.testing import WebsocketCommunicator
    from djust.decorators import event_handler
    from djust.websocket import LiveViewConsumer

    write(
        tmp_path,
        "page.html",
        "<header>HEADER</header>"
        "{% partialdef page %}<main dj-root><b>{{ selected }}</b></main>{% endpartialdef %}"
        "{% with selected=value %}{% partial page %}{% endwith %}<footer>FOOTER</footer>",
    )

    class PartialRootSocketView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "first"

        @event_handler()
        def change(self):
            self.value = "second"

    globals()["PartialRootSocketView"] = PartialRootSocketView
    with override_settings(
        TEMPLATES=templates_for(tmp_path), LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False
    ):
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
            assert "first" in json.dumps(mount)
            assert "HEADER" not in json.dumps(mount) and "FOOTER" not in json.dumps(mount)
            await comm.send_json_to({"type": "event", "event": "change", "params": {}, "ref": 1})
            frame = await comm.receive_json_from(timeout=5)
            assert "second" in json.dumps(frame), frame
        finally:
            await comm.disconnect()
            globals().pop("PartialRootSocketView", None)


def test_server_patches_apply_to_browser_parsed_http_page(tmp_path):
    """The real client must patch the root the browser created from HTTP."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the jsdom client replay")
    repo = Path(__file__).resolve().parents[3]
    if not (repo / "node_modules" / "jsdom").is_dir():
        pytest.skip("jsdom is not installed")
    write(
        tmp_path,
        "page.html",
        "<!doctype html><html><body><header>shell</header>"
        "{% partialdef page %}<main dj-root><strong>{{ selected }}</strong> "
        "{% if show %}<i>stable</i>{% endif %}<b>end</b></main>{% endpartialdef %}"
        "{% with selected=value %}{% partial page %}{% endwith %}</body></html>",
    )
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    class BrowserPartialView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "before"
            self.show = True

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = BrowserPartialView.as_view()(request)
        assert response.status_code == 200
        page = response.content.decode()
        # An independently mounted WS view computes the actual patch baseline.
        view = BrowserPartialView()
        view.mount(request)
        mount_html, _, _ = view.render_with_diff(request)
        mount_html = view._extract_liveview_content(view._strip_comments_and_whitespace(mount_html))
        view.value = "after"
        view.show = False
        expected, patches, _ = view.render_with_diff(request)
    assert patches
    result = subprocess.run(
        [node, "tests/js/partial_root_browser_helper.mjs"],
        cwd=repo,
        input=json.dumps(
            {
                "page": page,
                "mount": mount_html,
                "expected": expected,
                "patches": json.loads(patches),
            }
        ),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "shell,root",
    [
        ("<html><frameset>{call}</frameset></html>", "<main dj-root>root</main>"),
        ("<html><body><form>{call}</form></body></html>", "<form dj-root>root</form>"),
        ("<html><body><b>shell</b>{call}</body></html>", "<body dj-root>root</body>"),
    ],
    ids=["frameset", "nested-form", "nested-body"],
)
def test_html5_parser_context_cannot_create_a_phantom_root(tmp_path, shell, root):
    source = (
        "{% partialdef page %}"
        + root
        + "{% endpartialdef %}"
        + shell.replace("{call}", "{% partial page %}")
    )
    write(tmp_path, "page.html", source)
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        with pytest.raises(ImproperlyConfigured):
            view.get_template()
            view.render_full_template(None)


def test_real_http_get_mounts_root_from_executed_partial(tmp_path):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    write(
        tmp_path,
        "page.html",
        "<!doctype html><html><body><header>shell</header>"
        "{% partialdef page %}<main dj-root><b>{{ selected }}</b></main>{% endpartialdef %}"
        "{% with selected=value %}{% partial page %}{% endwith %}</body></html>",
    )

    class HTTPPartialView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "http"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = HTTPPartialView.as_view()(request)
    assert response.status_code == 200
    html = response.content.decode()
    assert "<header>shell</header>" in html and ">http</b>" in html
    assert html.count("dj-root") == 1


def test_http_and_websocket_mount_ids_match_for_partial_root(tmp_path):
    from djust.testing import LiveViewTestClient

    write(
        tmp_path,
        "page.html",
        "<header>shell</header>"
        "{% partialdef page %}<main dj-root><b>{{ selected }}</b></main>{% endpartialdef %}"
        "{% with selected=value %}{% partial page %}{% endwith %}",
    )

    class ParityPartialView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "visible"

    with override_settings(TEMPLATES=templates_for(tmp_path)):
        LiveViewTestClient(ParityPartialView).assert_http_ws_djid_parity()


def test_partial_root_tracks_dynamic_template_changes_and_binding_authority(tmp_path):
    class DynamicPartialView(LiveView):
        which = "first"
        first = "first-value"
        second = "second-value"
        is_admin = False

        @property
        def template(self):
            field = self.which
            return (
                '<header><input dj-model="is_admin">SHELL</header>'
                '{% partialdef page %}<main dj-root><input dj-model="'
                + field
                + '"><b>{{ '
                + field
                + " }}</b></main>{% endpartialdef %}{% partial page %}"
            )

    view = DynamicPartialView()
    html, _, _ = view.render_with_diff(None)
    assert "first-value" in html and "first" in view._dj_model_fields
    assert "SHELL" not in html
    assert "second" not in view._dj_model_fields and "is_admin" not in view._dj_model_fields
    view.which = "second"
    html, _, _ = view.render_with_diff(None)
    assert "second-value" in html and "second" in view._dj_model_fields
    assert "first" not in view._dj_model_fields
    view.update_model(field="first", value="forged")
    assert view.first == "first-value"


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_partial_root_restoration_respects_exposure_cache_isolation(
    tmp_path, rf, monkeypatch, policy
):
    from types import SimpleNamespace

    from djust.decorators import state
    from djust.state_backends.memory import InMemoryStateBackend

    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root><b>{{ value }}</b></main>{% endpartialdef %}{% partial page %}",
    )
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)

    class LegacyPartialView(LiveView):
        template_name = "page.html"
        value = "default"

    class ExplicitPartialView(LiveView):
        template_name = "page.html"
        exposure_policy = "explicit"
        value = state("default", persist="server")

        def get_context_data(self, **kwargs):
            return super().get_context_data(value=self.value, **kwargs)

    cls = LegacyPartialView if policy == "legacy" else ExplicitPartialView
    request = rf.get("/same-page/")
    request.session = SimpleNamespace(session_key="partial-root-session")
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        original = cls()
        original.value = "original"
        first, _, _ = original.render_with_diff(request)
        assert "original" in first
        if policy == "legacy":
            assert backend._cache, "legacy test must exercise real snapshot storage"
        else:
            assert not backend._cache, "explicit render context must not enter the legacy backend"
        restored = cls()
        restored._initialize_rust_view(request)
        if policy == "legacy":
            assert "original" in restored._rust_view.render()
        else:
            assert "original" not in restored._rust_view.render()
            html, _, _ = restored.render_with_diff(request)
            assert "default" in html
            assert not backend._cache


@pytest.mark.parametrize("transport", ["http", "diff"])
def test_competing_forged_shell_root_is_rejected(tmp_path, transport):
    write(
        tmp_path,
        "page.html",
        "<header>{{ untrusted|safe }}</header>"
        "{% partialdef page %}<main dj-root><b>{{ value }}</b></main>{% endpartialdef %}"
        "{% partial page %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.value = "legitimate"
        view.untrusted = '<main dj-root dj-view="attacker.View">FORGED</main>'
        with pytest.raises(ImproperlyConfigured):
            if transport == "http":
                view.get_template()
                view.render_full_template(None)
            else:
                view.render_with_diff(None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_competing_forged_shell_root_rejects_websocket_mount(tmp_path):
    from channels.testing import WebsocketCommunicator
    from djust.decorators import event_handler
    from djust.websocket import LiveViewConsumer

    write(
        tmp_path,
        "page.html",
        "<header>{{ untrusted|safe }}</header>"
        "{% partialdef page %}<main dj-root><b>legitimate</b></main>{% endpartialdef %}"
        "{% partial page %}",
    )
    events = []

    class ForgedRootSocketView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.untrusted = '<main dj-root dj-view="attacker.View">FORGED</main>'

        @event_handler()
        def change(self):
            events.append("called")

    globals()["ForgedRootSocketView"] = ForgedRootSocketView
    with override_settings(
        TEMPLATES=templates_for(tmp_path), LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.ForgedRootSocketView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") != "mount", mount
            await comm.send_json_to({"type": "event", "event": "change", "params": {}, "ref": 1})
            reply = await comm.receive_json_from(timeout=5)
            assert reply.get("type") in {"error", "view_refused", "close"}, reply
            assert not events
        finally:
            await comm.disconnect()
            globals().pop("ForgedRootSocketView", None)


def test_selected_partial_root_retains_only_its_authored_lazy_children(tmp_path):
    from djust._rust import authored_lazy_elements

    write(
        tmp_path,
        "page.html",
        '<header><div dj-view="demo.Shell" dj-lazy></div></header>'
        '{% partialdef page %}<main dj-root><div dj-view="demo.Child" dj-lazy></div>'
        "{{ untrusted|safe }}</main>{% endpartialdef %}{% partial page %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view._initialize_rust_view(None)
        view._rust_view.update_state({"untrusted": '<div dj-view="attacker.Forged" dj-lazy></div>'})
        html, spans, _ = view._rust_view.render_lazy_html()
        authorized = authored_lazy_elements(html, spans)
        assert len(authorized) == 1
        assert 'dj-view="demo.Child"' in html
        assert 'dj-view="demo.Shell"' not in html


def wrapper_context_processor(request):
    return {"wrapper_content": getattr(request, "wrapper_content", "")}


@pytest.mark.parametrize("forged", [False, True], ids=["benign-wrapper", "forged-wrapper"])
def test_postprocessed_wrapper_rejects_forged_root_and_lazy_slot(tmp_path, forged):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    write(
        tmp_path,
        "page.html",
        '{% partialdef page %}<main dj-root><b>legitimate</b><div dj-view="demo.Child" dj-lazy></div>'
        "</main>{% endpartialdef %}{% partial page %}",
    )
    write(
        tmp_path,
        "wrapper.html",
        "<html><body>{{ wrapper_content|safe }}{{ liveview_content|safe }}</body></html>",
    )
    templates = templates_for(tmp_path)
    templates[0]["OPTIONS"] = {"context_processors": [f"{__name__}.wrapper_context_processor"]}

    class WrappedPartialView(LiveView):
        template_name = "page.html"
        wrapper_template = "wrapper.html"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.wrapper_content = (
        '<main dj-root dj-view="attacker.View">FORGED</main>'
        '<div dj-view="attacker.Child" dj-lazy></div>'
        if forged
        else '<header>WRAPPER</header><div dj-view="attacker.Child" dj-lazy></div>'
    )
    with override_settings(TEMPLATES=templates):
        if forged:
            with pytest.raises(ImproperlyConfigured):
                WrappedPartialView.as_view()(request)
            return
        response = WrappedPartialView.as_view()(request)
    assert response.status_code == 200
    html = response.content.decode()
    assert "WRAPPER" in html and ">legitimate</b>" in html
    assert html.count("dj-root") == 1
    assert "dj-view=" in html and "WrappedPartialView" in html
    # Wrapper reinjection does not grant lazy authority to injected elements.
    assert "data-djust-lazy-id" not in html


def test_fragment_with_dynamic_include_and_no_root_keeps_rendering(tmp_path):
    write(tmp_path, "first.html", "<b>FIRST</b>")
    write(tmp_path, "second.html", "<i>SECOND</i>")

    class DynamicIncludeFragment(LiveView):
        template = "<section>{% include selected %}</section>"

    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = DynamicIncludeFragment()
        view.selected = "first.html"
        html, _, _ = view.render_with_diff(None)
        assert "FIRST" in html
        view.selected = "second.html"
        html, _, _ = view.render_with_diff(None)
        assert "SECOND" in html


def test_forged_wrapper_failure_does_not_advance_cached_baseline(tmp_path, monkeypatch):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from djust.state_backends.memory import InMemoryStateBackend

    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root><b>{{ value }}</b></main>{% endpartialdef %}{% partial page %}",
    )
    write(
        tmp_path,
        "wrapper.html",
        "<html><body>{{ wrapper_content|safe }}{{ liveview_content|safe }}</body></html>",
    )
    templates = templates_for(tmp_path)
    templates[0]["OPTIONS"] = {"context_processors": [f"{__name__}.wrapper_context_processor"]}
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)

    class CachedWrappedView(LiveView):
        template_name = "page.html"
        wrapper_template = "wrapper.html"

        def mount(self, request, **kwargs):
            self.value = "before"

    session = SessionStore()
    session.create()

    def request(content):
        result = RequestFactory().get("/cached-partial/")
        result.user = AnonymousUser()
        result.session = session
        result.wrapper_content = content
        return result

    with override_settings(TEMPLATES=templates):
        response = CachedWrappedView.as_view()(request("<header>WRAPPER</header>"))
        assert response.status_code == 200
        html = response.content.decode()
        assert "dj-view=" in html and "CachedWrappedView" in html
        assert backend._cache, "the test must exercise an existing cached baseline"
        baseline = {key: value[0] for key, value in backend._cache.items()}
        with pytest.raises(ImproperlyConfigured):
            CachedWrappedView.as_view()(
                request('<main dj-root dj-view="attacker.View">FORGED</main>')
            )
        # `serialize_msgpack` writes a fresh timestamp on every cache refresh.
        # Compare all persisted state, VDOM, version and authority fields while
        # excluding only that timestamp (the fifth field in SerializableViewState).
        import msgpack

        current = {key: value[0] for key, value in backend._cache.items()}
        assert current.keys() == baseline.keys()
        for key in baseline:
            before = msgpack.unpackb(baseline[key], raw=False, strict_map_key=False)
            after = msgpack.unpackb(current[key], raw=False, strict_map_key=False)
            assert before[:4] == after[:4]
            assert before[5:] == after[5:]
        response = CachedWrappedView.as_view()(request("<header>WRAPPER</header>"))
        assert response.status_code == 200 and b"before" in response.content
        restored = CachedWrappedView()
        restored._initialize_rust_view(request(""))
        restored.value = "after"
        html, patches, _ = restored.render_with_diff(request(""))
        assert "after" in html and patches and "after" in str(patches)


def test_named_partial_root_resolves_relative_include_for_http_and_ws(tmp_path):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    write(tmp_path, "widgets/piece.html", "<b>{{ selected }}</b>")
    write(
        tmp_path,
        "widgets/page.html",
        "<header>HEADER</header>"
        '{% partialdef page %}<main dj-root>{% include "./piece.html" %}</main>{% endpartialdef %}'
        "{% with selected=value %}{% partial page %}{% endwith %}",
    )

    class RelativePartialView(LiveView):
        template_name = "widgets/page.html"

        def mount(self, request, **kwargs):
            self.value = "relative"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = RelativePartialView.as_view()(request)
        assert response.status_code == 200
        assert (
            "HEADER" in response.content.decode() and ">relative</b>" in response.content.decode()
        )
        ws = RelativePartialView()
        ws.mount(request)
        html, _, _ = ws.render_with_diff(request)
        assert ">relative</b>" in html and "HEADER" not in html


@pytest.mark.parametrize("dynamic_include", [False, True], ids=["static", "dynamic"])
def test_included_partial_root_cannot_precede_another_http_root(tmp_path, dynamic_include):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    include = "{% include target %}" if dynamic_include else '{% include "child.html" %}'
    write(tmp_path, "page.html", include + "<main dj-root>OUTER</main>")
    write(
        tmp_path,
        "child.html",
        "{% partialdef p %}<main dj-root>INNER</main>{% endpartialdef %}{% partial p %}",
    )

    class CompetingIncludedRoots(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.target = "child.html"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        with pytest.raises(ImproperlyConfigured):
            CompetingIncludedRoots.as_view()(request)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("dynamic_include", [False, True], ids=["static", "dynamic"])
async def test_included_partial_root_cannot_precede_another_websocket_root(
    tmp_path, dynamic_include
):
    from channels.testing import WebsocketCommunicator
    from djust.decorators import event_handler
    from djust.websocket import LiveViewConsumer

    include = "{% include target %}" if dynamic_include else '{% include "child.html" %}'
    write(tmp_path, "page.html", include + "<main dj-root>OUTER</main>")
    write(
        tmp_path,
        "child.html",
        "{% partialdef p %}<main dj-root>INNER</main>{% endpartialdef %}{% partial p %}",
    )
    events = []

    class IncludedRootsSocketView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.target = "child.html"

        @event_handler()
        def change(self):
            events.append("called")

    globals()["IncludedRootsSocketView"] = IncludedRootsSocketView
    with override_settings(
        TEMPLATES=templates_for(tmp_path), LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.IncludedRootsSocketView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") != "mount", mount
            await comm.send_json_to({"type": "event", "event": "change", "params": {}, "ref": 1})
            reply = await comm.receive_json_from(timeout=5)
            assert reply.get("type") in {"error", "view_refused", "close"}, reply
            assert not events
        finally:
            await comm.disconnect()
            globals().pop("IncludedRootsSocketView", None)


def test_dynamic_root_attribute_cannot_authorize_injected_model_binding(tmp_path):
    write(
        tmp_path,
        "page.html",
        """{% partialdef page %}<main dj-root title="{{ left|safe }} dj-model='is_admin' {{ right|safe }}">"""
        "legitimate</main>{% endpartialdef %}{% partial page %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.left = '"'
        view.right = 'x="'
        view.is_admin = False
        html, _, _ = view.render_with_diff(None)
        assert "legitimate" in html
        assert "is_admin" not in view._dj_model_fields
        view.update_model(field="is_admin", value=True)
        assert view.is_admin is False


def test_stateful_include_is_evaluated_once_for_http_and_cached_baseline(tmp_path, monkeypatch):
    """The page and diff baseline must come from the same selected template."""
    import msgpack

    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory
    from djust.state_backends.memory import InMemoryStateBackend

    write(tmp_path, "page.html", "{% include chooser.selected %}")
    for name, label in (("first.html", "FIRST"), ("second.html", "SECOND")):
        write(
            tmp_path,
            name,
            "{% partialdef page %}<main dj-root><b>"
            + label
            + "</b></main>{% endpartialdef %}{% partial page %}",
        )

    class Chooser:
        def __init__(self):
            self.reads = 0

        @property
        def selected(self):
            self.reads += 1
            return "first.html" if self.reads % 2 else "second.html"

    chooser = Chooser()
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)

    class StatefulIncludeView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.chooser = chooser

    request = RequestFactory().get("/stateful-include/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = StatefulIncludeView.as_view()(request)
    assert response.status_code == 200
    page = response.content.decode()
    assert "FIRST" in page and "SECOND" not in page
    assert chooser.reads == 1, "HTTP page and its baseline must share one template evaluation"
    assert backend._cache, "the test must inspect the persisted WebSocket baseline"
    snapshots = [
        msgpack.unpackb(value[0], raw=False, strict_map_key=False)
        for value in backend._cache.values()
    ]
    assert any(
        "FIRST" in str(snapshot[2]) and "SECOND" not in str(snapshot[2]) for snapshot in snapshots
    )


def test_injected_duplicate_model_binding_does_not_borrow_literal_binding_authority(tmp_path):
    write(
        tmp_path,
        "page.html",
        """{% partialdef page %}<main dj-root title="{{ left|safe }} dj-model='is_admin' {{ right|safe }}" dj-model="query">"""
        "legitimate</main>{% endpartialdef %}{% partial page %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.left = '"'
        view.right = 'x="'
        view.is_admin = False
        view.query = "query-value"
        html, _, _ = view.render_with_diff(None)
        assert "legitimate" in html
        assert "is_admin" not in view._dj_model_fields
        view.update_model(field="is_admin", value=True)
        assert view.is_admin is False


def test_injected_view_path_cannot_borrow_root_authority(tmp_path):
    write(
        tmp_path,
        "page.html",
        """{% partialdef page %}<main dj-root title="{{ left|safe }} dj-view='attacker.Forged' {{ right|safe }}">"""
        "legitimate</main>{% endpartialdef %}{% partial page %}",
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.left = '"'
        view.right = 'x="'
        with pytest.raises(ImproperlyConfigured):
            view.render_with_diff(None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_cannot_update_field_from_forged_model_attribute(tmp_path):
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    write(
        tmp_path,
        "page.html",
        """{% partialdef page %}<main dj-root title="{{ left|safe }} dj-model='is_admin' {{ right|safe }}">"""
        "legitimate</main>{% endpartialdef %}{% partial page %}",
    )
    mounted = []

    class ForgedModelSocketView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.left = '"'
            self.right = 'x="'
            self.is_admin = False
            mounted.append(self)

    globals()["ForgedModelSocketView"] = ForgedModelSocketView
    with override_settings(
        TEMPLATES=templates_for(tmp_path), LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.ForgedModelSocketView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") == "mount", mount
            assert mounted and "is_admin" not in mounted[-1]._dj_model_fields
            await comm.send_json_to(
                {
                    "type": "event",
                    "event": "update_model",
                    "params": {"field": "is_admin", "value": True},
                    "ref": 1,
                }
            )
            await comm.receive_json_from(timeout=5)
            assert mounted[-1].is_admin is False
        finally:
            await comm.disconnect()
            globals().pop("ForgedModelSocketView", None)


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_partial_root_framework_state_is_not_application_private_state(tmp_path, policy):
    from djust._exposure import explicit_state_projection
    from djust.decorators import state

    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root>{{ value }}</main>{% endpartialdef %}{% partial page %}",
    )

    class LegacyRootView(LiveView):
        template_name = "page.html"
        value = "visible"

    class ExplicitRootView(LiveView):
        template_name = "page.html"
        exposure_policy = "explicit"
        value = state("visible", persist="server")

        def get_context_data(self, **kwargs):
            return super().get_context_data(value=self.value, **kwargs)

    cls = LegacyRootView if policy == "legacy" else ExplicitRootView
    framework_keys = {
        "_authenticated_partial_root",
        "_authenticated_partial_root_origin",
        "_authenticated_template_source",
        "_prepared_authenticated_partial_root",
        "_pending_authenticated_page",
    }
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = cls()
        view._user_secret = "application-private"
        view.render_with_diff(None)
        # Exercise a pending transaction as well as the planned named root.
        view._prepared_authenticated_partial_root = True
        view._pending_authenticated_page = True
        view._snapshot_user_private_attrs()
        assert framework_keys <= view._framework_attrs
        assert not framework_keys & view._user_private_keys
        if policy == "legacy":
            private = view._get_private_state()
            assert private["_user_secret"] == "application-private"
            assert not framework_keys & private.keys()
        else:
            projection = explicit_state_projection(view, "server")
            assert projection == {"value": "visible"}
            assert not framework_keys & projection.keys()


def test_http_dynamic_include_transitions_between_fragment_and_partial_root(tmp_path, monkeypatch):
    """A shared session can move between a fragment and an authenticated root."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory
    from djust.state_backends.memory import InMemoryStateBackend

    write(tmp_path, "fragment.html", "<section>PLAIN</section>")
    write(
        tmp_path,
        "root.html",
        '<header><input dj-model="is_admin">SHELL</header>'
        '{% partialdef page %}<main dj-root><input dj-model="query"><b>{{ query }}</b></main>'
        "{% endpartialdef %}{% partial page %}",
    )
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)
    mounted = []

    class DynamicIncludeView(LiveView):
        template = "{% include selected %}"

        def mount(self, request, **kwargs):
            self.selected = request.selected_template
            self.query = "SEARCH"
            self.is_admin = False
            mounted.append(self)

    session = SessionStore()
    session.create()

    def request(selected):
        result = RequestFactory().get("/dynamic-partial/")
        result.user = AnonymousUser()
        result.session = session
        result.selected_template = selected
        return result

    with override_settings(TEMPLATES=templates_for(tmp_path)):
        first = DynamicIncludeView.as_view()(request("fragment.html"))
        assert first.status_code == 200 and b"PLAIN" in first.content
        assert not mounted[-1]._rust_view.authenticated_partial_root()

        second = DynamicIncludeView.as_view()(request("root.html"))
        assert second.status_code == 200
        page = second.content.decode()
        assert "SHELL" in page and "SEARCH" in page and "dj-root" in page
        assert "dj-view=" in page and "DynamicIncludeView" in page
        assert mounted[-1]._rust_view.authenticated_partial_root()
        assert "query" in mounted[-1]._dj_model_fields
        assert "is_admin" not in mounted[-1]._dj_model_fields

        # The saved baseline must be the root sent to the browser.
        restored = DynamicIncludeView()
        restored._initialize_rust_view(request("root.html"))
        restored.selected = "root.html"
        restored.query = "CHANGED"
        html, patches, _ = restored.render_with_diff(request("root.html"))
        assert "CHANGED" in html and patches and "CHANGED" in str(patches)

        third = DynamicIncludeView.as_view()(request("fragment.html"))
        assert third.status_code == 200 and b"PLAIN" in third.content
        assert not mounted[-1]._rust_view.authenticated_partial_root()


def test_react_hydration_and_lazy_child_survive_partial_root_page_and_patch(tmp_path):
    """The browser can mount and patch a partial root with postprocessed children."""
    import shutil
    import subprocess
    from pathlib import Path

    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory
    from djust.react import react_components

    node = shutil.which("node")
    repo = Path(__file__).resolve().parents[3]
    if not node or not (repo / "node_modules" / "jsdom").is_dir():
        pytest.skip("node and jsdom are needed for the client replay")

    class HydrationLazyChild(LiveView):
        template = "<div><p>lazy child</p></div>"

    globals()["HydrationLazyChild"] = HydrationLazyChild
    write(
        tmp_path,
        "page.html",
        "<!doctype html><html><body>"
        "{% partialdef page %}<main dj-root><strong>{{ value }}</strong>"
        '<Greeting who="friend" />'
        f'<div dj-view="{__name__}.HydrationLazyChild" dj-lazy></div>'
        "</main>{% endpartialdef %}{% partial page %}</body></html>",
    )

    class HydrationPartialView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "before"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    react_components.register("Greeting")(lambda props, children: "<span>HELLO_REACT</span>")
    try:
        with override_settings(
            TEMPLATES=templates_for(tmp_path),
            LIVEVIEW_ALLOWED_MODULES=[__name__],
        ):
            response = HydrationPartialView.as_view()(request)
            assert response.status_code == 200
            page = response.content.decode()
            assert "HELLO_REACT" in page
            assert 'data-djust-lazy-id="lazy_' in page
            view = HydrationPartialView()
            view.mount(request)
            mount_html, _, _ = view.render_with_diff(request)
            mount_html = view._extract_liveview_content(
                view._strip_comments_and_whitespace(mount_html)
            )
            view.value = "after"
            expected, patches, _ = view.render_with_diff(request)
        assert patches
        result = subprocess.run(
            [node, "tests/js/partial_root_browser_helper.mjs"],
            cwd=repo,
            input=json.dumps(
                {
                    "page": page,
                    "mount": mount_html,
                    "expected": expected,
                    "patches": json.loads(patches),
                }
            ),
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
    finally:
        react_components._components.pop("Greeting", None)
        globals().pop("HydrationLazyChild", None)


def test_partial_root_with_literal_angle_brackets_in_quoted_attribute(tmp_path):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    write(
        tmp_path,
        "page.html",
        '{% partialdef page %}<main dj-root title="<em>literal</em>"><b>{{ value }}</b></main>'
        "{% endpartialdef %}{% partial page %}",
    )

    class LiteralAngleRootView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "before"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = LiteralAngleRootView.as_view()(request)
        assert response.status_code == 200
        page = response.content.decode()
        assert 'title="<em>literal</em>"' in page and ">before</b>" in page
        view = LiteralAngleRootView()
        view.mount(request)
        view.render_with_diff(request)
        view.value = "after"
        html, patches, _ = view.render_with_diff(request)
        assert ">after</b>" in html and patches and "after" in str(patches)


def test_named_partial_root_registers_lazy_child_from_relative_include(tmp_path):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    class RelativeLazyChild(LiveView):
        template = "<div><p>lazy child</p></div>"

    globals()["RelativeLazyChild"] = RelativeLazyChild
    write(
        tmp_path,
        "widgets/piece.html",
        f'<div dj-view="{__name__}.RelativeLazyChild" dj-lazy></div>',
    )
    write(
        tmp_path,
        "widgets/page.html",
        '{% partialdef page %}<main dj-root>{% include "./piece.html" %}</main>'
        "{% endpartialdef %}{% partial page %}",
    )

    class RelativeLazyPage(LiveView):
        template_name = "widgets/page.html"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    try:
        with override_settings(
            TEMPLATES=templates_for(tmp_path), LIVEVIEW_ALLOWED_MODULES=[__name__]
        ):
            response = RelativeLazyPage.as_view()(request)
        assert response.status_code == 200
        page = response.content.decode()
        assert 'data-djust-lazy-id="lazy_' in page
        assert page.count('data-djust-lazy-id="lazy_') == 1
    finally:
        globals().pop("RelativeLazyChild", None)


@pytest.mark.parametrize("authored_view", [False, True], ids=["stamped", "authored"])
def test_browser_events_survive_conditional_partial_root_tag_replacement(tmp_path, authored_view):
    """Replacing the root must preserve browser mount identity and event routing."""
    import shutil
    import subprocess
    from pathlib import Path

    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    node = shutil.which("node")
    repo = Path(__file__).resolve().parents[3]
    if not node or not (repo / "node_modules" / "jsdom").is_dir():
        pytest.skip("node and jsdom are needed for the client replay")
    view_attr = ' dj-view="named.AuthoredView"' if authored_view else ""
    write(
        tmp_path,
        "page.html",
        "<!doctype html><html><body>"
        "{% partialdef first %}<main dj-root"
        + view_attr
        + '><button dj-click="advance">{{ value }}</button></main>'
        "{% endpartialdef %}"
        "{% partialdef second %}<section dj-root"
        + view_attr
        + '><button dj-click="advance">{{ value }}</button></section>'
        "{% endpartialdef %}"
        "{% if alternate %}{% partial second %}{% else %}{% partial first %}{% endif %}"
        "</body></html>",
    )
    from djust.decorators import event_handler

    class SwitchingPartialView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "before"
            self.alternate = False

        @event_handler()
        def advance(self):
            self.alternate = True
            self.value = "after" if self.value == "before" else "final"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = SwitchingPartialView.as_view()(request)
        assert response.status_code == 200
        page = response.content.decode()
        view = SwitchingPartialView()
        view.mount(request)
        mount_html, _, _ = view.render_with_diff(request)
        mount_html = view._extract_liveview_content(view._strip_comments_and_whitespace(mount_html))
        view.advance()
        expected, patches, _ = view.render_with_diff(request)
        view.advance()
        next_expected, next_patches, _ = view.render_with_diff(request)
    assert patches and next_patches
    result = subprocess.run(
        [node, "tests/js/partial_root_browser_helper.mjs"],
        cwd=repo,
        input=json.dumps(
            {
                "page": page,
                "mount": mount_html,
                "expected": expected,
                "patches": json.loads(patches),
                "requireReplacement": True,
                "nextExpected": next_expected,
                "nextPatches": json.loads(next_patches),
            }
        ),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_safe_markup_cannot_move_shell_binding_into_partial_root_authority(tmp_path):
    """A binding must belong to the root in both author-only HTML5 projections."""
    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root>{{ a|safe }}</main>{{ b|safe }}"
        '<input dj-model="is_admin"></main>{% endpartialdef %}{% partial page %}',
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.a, view.b = "<textarea>", "</textarea>"
        view.is_admin = False
        try:
            html, _, _ = view.render_with_diff(None)
        except ImproperlyConfigured:
            return  # The HTML5/source-range equivalence check safely refused it.
        assert "is_admin" in html  # The browser really did parse the moved input.
        assert "is_admin" not in view._dj_model_fields
        view.update_model(field="is_admin", value=True)
        assert view.is_admin is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_events_continue_after_conditional_partial_root_switch(tmp_path):
    from channels.testing import WebsocketCommunicator
    from djust.decorators import event_handler
    from djust.websocket import LiveViewConsumer

    write(
        tmp_path,
        "page.html",
        '{% partialdef first %}<main dj-root><button dj-click="advance">{{ value }}</button></main>'
        "{% endpartialdef %}"
        '{% partialdef second %}<section dj-root><button dj-click="advance">{{ value }}</button></section>'
        "{% endpartialdef %}"
        "{% if alternate %}{% partial second %}{% else %}{% partial first %}{% endif %}",
    )

    class SwitchingSocketView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "before"
            self.alternate = False

        @event_handler()
        def advance(self):
            self.alternate = True
            self.value = "after" if self.value == "before" else "final"

    globals()["SwitchingSocketView"] = SwitchingSocketView
    with override_settings(
        TEMPLATES=templates_for(tmp_path), LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.SwitchingSocketView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") == "mount", mount
            assert "before" in json.dumps(mount)
            for ref, expected in ((1, "after"), (2, "final")):
                await comm.send_json_to(
                    {"type": "event", "event": "advance", "params": {}, "ref": ref}
                )
                frame = await comm.receive_json_from(timeout=5)
                assert expected in json.dumps(frame), frame
        finally:
            await comm.disconnect()
            globals().pop("SwitchingSocketView", None)


def test_safe_markup_cannot_move_child_binding_into_parent_partial_root(tmp_path):
    """Removing a child wrapper in browser parsing must not grant its field to the parent."""
    write(
        tmp_path,
        "page.html",
        "{% partialdef page %}<main dj-root>{{ a|safe }}"
        '<div dj-view="demo.Child" data-djust-embedded>{{ b|safe }}'
        '<input dj-model="is_admin"></div></main>{% endpartialdef %}{% partial page %}',
    )
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = view_class()()
        view.a, view.b = "<textarea>", "</textarea>"
        view.is_admin = False
        try:
            html, _, _ = view.render_with_diff(None)
        except ImproperlyConfigured:
            return  # Refusing ambiguous HTML is also safe.
        assert "is_admin" in html
        assert "is_admin" not in view._dj_model_fields
        view.update_model(field="is_admin", value=True)
        assert view.is_admin is False


def test_untrusted_comment_cannot_downgrade_authored_page_root_to_fragment(tmp_path):
    """Opaque markup hiding the page root must not grant shell model fields."""
    write(tmp_path, "blank.html", "")

    class ConditionalRootPage(LiveView):
        template = (
            "{{ a|safe }}<main dj-root>real</main>{{ b|safe }}"
            '<input dj-model="is_admin">{% include selected %}'
        )

    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = ConditionalRootPage()
        view.a, view.b = "<!--", "-->"
        view.selected = "blank.html"
        view.is_admin = False
        with pytest.raises(ImproperlyConfigured):
            view.render_with_diff(None)
        assert "is_admin" not in view._dj_model_fields


def test_ordinary_table_root_cannot_downgrade_to_sticky_view(tmp_path):
    """A guarded ordinary render must not choose a different live root."""
    write(tmp_path, "blank.html", "")

    class StickyAndTableView(LiveView):
        template = (
            '<div dj-root dj-view="demo.Child" dj-sticky-root>STICKY_CHILD</div>'
            "<table dj-root><tbody><tr><td>{{ value }}</td></tr></tbody></table>"
            '{% include "blank.html" %}'
        )

    with override_settings(TEMPLATES=templates_for(tmp_path)):
        view = StickyAndTableView()
        view.value = "TABLE_VALUE"
        try:
            html, _, _ = view.render_with_diff(None)
        except ImproperlyConfigured:
            return
        assert "TABLE_VALUE" in html and "STICKY_CHILD" not in html
        assert "<table" in html


def test_partial_body_root_mounts_and_updates_through_http_and_diff(tmp_path):
    """A genuine authored body root remains the page root in both transports."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    write(
        tmp_path,
        "page.html",
        "<!doctype html><html><head><title>Body root</title></head>"
        "{% partialdef page %}<body dj-root><p>{{ value }}</p></body>{% endpartialdef %}"
        "{% partial page %}</html>",
    )

    class PartialBodyView(LiveView):
        template_name = "page.html"

        def mount(self, request, **kwargs):
            self.value = "before"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(TEMPLATES=templates_for(tmp_path)):
        response = PartialBodyView.as_view()(request)
        assert response.status_code == 200
        page = response.content.decode()
        assert "<title>Body root</title>" in page
        assert "before" in page and "<body" in page and "dj-root" in page
        view = PartialBodyView()
        view.mount(request)
        view.render_with_diff(request)
        view.value = "after"
        html, patches, _ = view.render_with_diff(request)
        assert ">after</p>" in html and patches and "after" in str(patches)
