"""Django named partials used directly as LiveView templates."""

import django
import pytest
from django.template import TemplateDoesNotExist
from django.test import override_settings, RequestFactory
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from djust import LiveView
from djust.decorators import event_handler

pytestmark = pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 named partials")


def settings_for(path, debug, backend="django.template.backends.django.DjangoTemplates"):
    return [
        {
            "BACKEND": backend,
            "DIRS": [str(path)],
            "APP_DIRS": False,
            "OPTIONS": {"debug": debug},
        }
    ]


def files(path, text="FIRST"):
    folder = path / "sub"
    folder.mkdir(exist_ok=True)
    (folder / "part.html").write_text("RELATIVE={{ value }}")
    (folder / "page.html").write_text(
        "OUTSIDE {% partialdef sibling %}<main dj-root>SIBLING</main>{% endpartialdef %}"
        '{% partialdef card %}<main dj-root><input dj-model="value"><b>' + text + ":{{ value }}</b>"
        '{% include "./part.html" %}</main>{% endpartialdef %} AFTER'
    )


def request():
    req = RequestFactory().get("/named-partial/")
    req.user = AnonymousUser()
    req.session = SessionStore()
    return req


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize(
    "backend",
    [
        "django.template.backends.django.DjangoTemplates",
        "djust.template.backend.DjustTemplateBackend",
    ],
)
def test_named_partial_http_and_diff(debug, backend, tmp_path):
    files(tmp_path)

    class NamedView(LiveView):
        exposure_policy = "legacy"
        template_name = "sub/page.html#card"

        def mount(self, request, **kwargs):
            self.value = "one"

        def get_context_data(self, **kwargs):
            return {"value": self.value}

    with override_settings(TEMPLATES=settings_for(tmp_path, debug, backend)):
        response = NamedView.as_view()(request())
        assert response.status_code == 200
        page = response.content.decode()
        assert "FIRST:one" in page and "RELATIVE=one" in page
        assert "SIBLING" not in page and "OUTSIDE" not in page and "AFTER" not in page
        view = NamedView()
        view.mount(None)
        html, _, _ = view.render_with_diff(None)
        assert "FIRST:one" in html and "RELATIVE=one" in html
        assert "value" in view._dj_model_fields
        view.value = "two"
        html, patches, _ = view.render_with_diff(None)
        assert "FIRST:two" in html and "RELATIVE=two" in html and patches


@pytest.mark.parametrize("debug", [False, True])
def test_named_partial_missing_name_raises(debug, tmp_path):
    files(tmp_path)

    class MissingView(LiveView):
        template_name = "sub/page.html#missing"

    with override_settings(TEMPLATES=settings_for(tmp_path, debug)):
        with pytest.raises(TemplateDoesNotExist):
            MissingView().get_template()


@pytest.mark.parametrize("debug", [False, True])
def test_named_partial_template_hash_tracks_source_change(debug, tmp_path):
    files(tmp_path)

    class Before(LiveView):
        template_name = "sub/page.html#card"

    class After(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, debug)):
        first = Before()._get_cached_template_hash_slot()
    files(tmp_path, "SECOND")
    with override_settings(TEMPLATES=settings_for(tmp_path, debug)):
        second = After()._get_cached_template_hash_slot()
    assert first != second


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
async def test_named_partial_websocket_mount_and_event(debug, tmp_path):
    import json
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    files(tmp_path)
    with override_settings(
        TEMPLATES=settings_for(tmp_path, debug),
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        DEBUG=False,
        DJUST_ALLOW_MISSING_ORIGIN=True,
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            assert (await comm.connect())[0]
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.SocketNamedView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") == "mount", mount
            assert "FIRST:one" in json.dumps(mount) and "RELATIVE=one" in json.dumps(mount)
            assert "SIBLING" not in json.dumps(mount)
            await comm.send_json_to({"type": "event", "event": "change", "params": {}, "ref": 1})
            event = await comm.receive_json_from(timeout=5)
            assert "two" in json.dumps(event)
        finally:
            await comm.disconnect()


class SocketNamedView(LiveView):
    template_name = "sub/page.html#card"

    def mount(self, request, **kwargs):
        self.value = "one"

    @event_handler()
    def change(self):
        self.value = "two"


def test_named_partial_jit_scopes_fields_to_selected_and_called_partials(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "piece.html").write_text("{{ person.first_name }}")
    (folder / "page.html").write_text(
        "{% partialdef sibling %}{{ person.email }}{% endpartialdef %}"
        "{% partialdef helper %}{{ user.last_name }}{% endpartialdef %}"
        "{% partialdef unused %}{{ person.password }}{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{{ person.username }}"
        '{% include "./piece.html" %}{% with user=person %}{% partial helper %}{% endwith %}</main>{% endpartialdef %}'
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        paths = set(view.template_paths().get("person", []))
    assert {"username", "first_name", "last_name"} <= paths
    assert not {"email", "password"} & paths


@pytest.mark.django_db
def test_named_partial_queryset_jit_omits_sibling_fields(tmp_path):
    from django.contrib.auth import get_user_model

    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "piece.html").write_text("{{ person.first_name }}")
    (folder / "page.html").write_text(
        "{% partialdef sibling %}{{ person.email }}{% endpartialdef %}"
        "{% partialdef helper %}{{ user.last_name }}{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{% for person in people %}"
        "{{ person.username }}{% with user=person %}{% partial helper %}{% endwith %}"
        '{% include "./piece.html" %}{% endfor %}</main>{% endpartialdef %}'
    )
    User = get_user_model()
    User.objects.create(
        username="selected", first_name="First", last_name="Last", email="secret@example.test"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        template = view._get_template_content()
        rows = view._jit_serialize_queryset(
            User.objects.filter(username="selected"), template, "people"
        )
    assert rows == [{"username": "selected", "first_name": "First", "last_name": "Last"}]


def test_named_partial_hash_tracks_base_changes_on_same_view_class(tmp_path):
    files(tmp_path)

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        first = NamedView()._get_cached_template_hash_slot()
    files(tmp_path, "SECOND")
    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        second = NamedView()._get_cached_template_hash_slot()
    assert first != second


def test_named_partial_jit_ignores_uninvoked_nested_verbatim_and_comments(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        "🍰 {% partialdef secret %}{{ person.email }}{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{{ person.username }}"
        "{% partialdef hidden %}{{ person.last_name }}{% endpartialdef %}"
        "{% comment %}{% partial secret %}{% endcomment %}"
        "{% verbatim %}{% partial secret %}{{ person.email }}{% endverbatim %}"
        "</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        paths = set(view.template_paths().get("person", []))
        view.person = {"username": "visible", "email": "private", "last_name": "unused"}
        html, _, _ = view.render_with_diff(None)
    assert paths == {"username"}
    assert "visible" in html
    assert "private" not in html and "unused" not in html


def test_named_partial_recursive_calls_render_with_caller_context(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        "🍰 {% partialdef sibling %}{{ node.secret }}{% endpartialdef %}"
        "{% partialdef item %}{{ node.label }}"
        "{% if node.child %}{% with node=node.child %}{% partial item %}{% endwith %}{% endif %}"
        "{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{% partial item %}</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        view.node = {"label": "first", "child": {"label": "second", "child": None}}
        html, _, _ = view.render_with_diff(None)
        paths = set(view.template_paths().get("node", []))
    assert "first" in html and "second" in html
    assert "secret" not in paths


def test_named_partial_nested_inline_and_called_definitions(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        "{% partialdef card %}<main dj-root>"
        "{% partialdef inline_piece inline %}{{ person.first_name }}{% endpartialdef %}"
        "{% partialdef called %}{{ person.last_name }}{% endpartialdef %}"
        "{% partialdef unused %}{{ person.email }}{% endpartialdef %}"
        "{% partial called %}</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        view.person = {"first_name": "First", "last_name": "Last", "email": "secret"}
        html, _, _ = view.render_with_diff(None)
        paths = set(view.template_paths().get("person", []))
    assert "First" in html and "Last" in html and "secret" not in html
    assert paths == {"first_name", "last_name"}


@pytest.mark.django_db
def test_named_partial_queryset_nested_relation_is_scoped(tmp_path):
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        "{% partialdef sibling %}{{ permission.content_type.model }}{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{% for permission in permissions %}"
        "{{ permission.content_type.app_label }}{% endfor %}</main>{% endpartialdef %}"
    )
    content_type = ContentType.objects.create(app_label="visible_app", model="private_model")
    Permission.objects.create(
        name="selection", codename="named_partial_selection", content_type=content_type
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        template = view._get_template_content()
        rows = view._jit_serialize_queryset(
            Permission.objects.filter(name="selection"), template, "permissions"
        )
    assert rows == [{"content_type": {"app_label": "visible_app"}}]


def test_named_partial_jit_cache_tracks_selected_source_changes(tmp_path):
    from djust.mixins.jit import _jit_template_content

    folder = tmp_path / "sub"
    folder.mkdir()
    page = folder / "page.html"
    page.write_text(
        "{% partialdef card %}<main dj-root>{{ person.username }}</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False), DEBUG=False):
        from django.template import engines

        first = NamedView()
        original = _jit_template_content(first)
        page.write_text(
            "{% partialdef card %}<main dj-root>{{ person.first_name }}</main>{% endpartialdef %}"
        )
        for loader in engines.all()[0].engine.template_loaders:
            loader.reset()
        second = NamedView()
        updated = _jit_template_content(second)
    assert "person.username" in original
    assert "person.first_name" in updated and "person.username" not in updated


def test_named_partial_jit_does_not_expand_calls_in_raw_blocks(tmp_path):
    from djust._rust import register_raw_block_tag_handler, unregister_raw_block_tag_handler

    class Raw:
        def render(self, args, body, context):
            return ""

    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        "{% partialdef secret %}{{ person.email }}{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{{ person.username }}"
        "{% named_partial_raw %}{% partial secret %}{% finish_named_raw %}"
        "</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    register_raw_block_tag_handler("named_partial_raw", "finish_named_raw", Raw())
    try:
        with override_settings(
            TEMPLATES=settings_for(tmp_path, False, "djust.template.backend.DjustTemplateBackend")
        ):
            paths = set(NamedView().template_paths().get("person", []))
    finally:
        unregister_raw_block_tag_handler("named_partial_raw")
    assert paths == {"username"}


@pytest.mark.django_db(transaction=True)
def test_named_partial_recursion_with_model_relations(tmp_path):
    from django.db import connection, models

    class PartialNode(models.Model):
        label = models.CharField(max_length=30)
        secret = models.CharField(max_length=30)
        child = models.ForeignKey("self", null=True, on_delete=models.CASCADE)

        class Meta:
            app_label = "demo_app"
            db_table = "djust_named_partial_node_test"

    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        "{% partialdef sibling %}{{ node.secret }}{% endpartialdef %}"
        "{% partialdef item %}{{ node.label }}"
        "{% if node.child %}{% with node=node.child %}{% partial item %}{% endwith %}{% endif %}"
        "{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{% partial item %}</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with connection.schema_editor() as schema:
        schema.create_model(PartialNode)
    try:
        leaf = PartialNode.objects.create(label="third", secret="no", child=None)
        middle = PartialNode.objects.create(label="second", secret="no", child=leaf)
        root = PartialNode.objects.create(label="first", secret="no", child=middle)
        with override_settings(TEMPLATES=settings_for(tmp_path, False)):
            view = NamedView()
            view.node = root
            html, _, _ = view.render_with_diff(None)
            payload = view.get_context_data()["node"]
            assert payload["child"]["child"]["label"] == "third"
            assert "secret" not in payload["child"]
            # Recursive root alongside ordinary objects must not claim them.
            view.other = leaf
            view.others = PartialNode.objects.filter(pk=leaf.pk)
            view.get_context_data()
            (folder / "aliased.html").write_text(
                "{% partialdef item %}[{{ node.label }}]"
                "{% if node.child %}{% with node=node.child %}{% partial item %}"
                "{% endwith %}{% endif %}{% endpartialdef %}"
                "{% partialdef card %}<main dj-root>{% with node=root %}"
                "{% partial item %}{% endwith %}</main>{% endpartialdef %}"
            )
            aliased = NamedView()
            aliased.template_name = "sub/aliased.html#card"
            aliased.root = root
            aliased.other = leaf
            aliased.others = PartialNode.objects.filter(pk=leaf.pk)
            assert "[third]" in aliased.render_with_diff(None)[0]
            assert "secret" not in aliased.get_context_data()["root"]["child"]
            paths = set(view.template_paths().get("node", []))
        assert all(label in html for label in ("first", "second", "third"))
        assert "secret" not in paths
    finally:
        with connection.schema_editor() as schema:
            schema.delete_model(PartialNode)


@pytest.mark.django_db
def test_named_partial_jit_scopes_named_include_fields(tmp_path):
    from django.contrib.auth import get_user_model

    (tmp_path / "page.html").write_text(
        "{% partialdef card %}<main dj-root>{% for person in people %}"
        '{{ person.username }}{% include "piece.html#inner" %}'
        "{% endfor %}</main>{% endpartialdef %}"
    )
    (tmp_path / "piece.html").write_text(
        "{% partialdef inner %}{{ person.first_name }}{% endpartialdef %}"
        "{% partialdef sibling %}{{ person.email }}{% endpartialdef %}"
    )
    User = get_user_model()
    User.objects.create(username="selected", first_name="First", email="secret@example.test")

    class NamedView(LiveView):
        template_name = "page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = NamedView()
        template = view._get_template_content()
        rows = view._jit_serialize_queryset(
            User.objects.filter(username="selected"), template, "people"
        )
    assert rows == [{"username": "selected", "first_name": "First"}]


def test_named_include_nesting_scope_raw_text_and_cycles(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "page.html").write_text(
        '{% partialdef card %}<main dj-root>{% include "./piece.html#inner" with user=person only %}'
        '{% verbatim %}{% include "./piece.html#secret" %}{% endverbatim %}'
        "</main>{% endpartialdef %}"
    )
    (folder / "piece.html").write_text(
        '{% partialdef inner %}{{ user.first_name }}{% include "./leaf.html#leaf" %}{% endpartialdef %}'
        "{% partialdef secret %}{{ person.email }}{% endpartialdef %}"
    )
    (folder / "leaf.html").write_text(
        '{% partialdef leaf %}{{ user.last_name }}{% include "./piece.html#inner" %}{% endpartialdef %}'
        "{% partialdef unused %}{{ user.password }}{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "sub/page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        paths = set(NamedView().template_paths().get("person", []))
    assert paths == {"first_name", "last_name"}


def test_named_partial_jit_cache_tracks_included_partial_source_changes(tmp_path):
    from django.template import engines
    from djust.mixins.jit import _jit_template_content, clear_template_source_caches

    (tmp_path / "page.html").write_text(
        '{% partialdef card %}<main dj-root>{% include "piece.html#inner" %}'
        "</main>{% endpartialdef %}"
    )
    piece = tmp_path / "piece.html"
    piece.write_text("{% partialdef inner %}{{ person.username }}{% endpartialdef %}")

    class NamedView(LiveView):
        template_name = "page.html#card"

    with override_settings(TEMPLATES=settings_for(tmp_path, False), DEBUG=False):
        clear_template_source_caches()
        first = NamedView()
        assert "person.username" in _jit_template_content(first)
        # The second call populates the cache with the resolved include.
        assert "person.username" in _jit_template_content(first)
        piece.write_text("{% partialdef inner %}{{ person.first_name }}{% endpartialdef %}")
        for template_loader in engines.all()[0].engine.template_loaders:
            template_loader.reset()
        updated = NamedView()
        assert "person.first_name" in _jit_template_content(updated)
        cached = _jit_template_content(updated)
        assert "person.first_name" in cached
        assert "person.username" not in cached


@pytest.mark.django_db(transaction=True)
def test_named_partial_recursive_queryset_preserves_descendant_fields(tmp_path, monkeypatch):
    from django.db import connection, models

    class QuerysetPartialNode(models.Model):
        label = models.CharField(max_length=30)
        password = models.CharField(max_length=30, default="sensitive")
        unused = models.CharField(max_length=30, default="unreferenced")
        child = models.ForeignKey("self", null=True, on_delete=models.CASCADE)

        @property
        def display(self):
            return self.label.upper()

        class Meta:
            app_label = "demo_app"
            db_table = "djust_named_partial_queryset_node_test"

    (tmp_path / "page.html").write_text(
        "{% partialdef item %}[{{ node.label }}]{{ node.password }}({{ node.display }})"
        "{% if node.child %}{% with node=node.child %}{% partial item %}{% endwith %}{% endif %}"
        "{% endpartialdef %}"
        "{% partialdef card %}<main dj-root>{% for node in nodes %}"
        "{% partial item %}{% endfor %}</main>{% endpartialdef %}"
    )

    class NamedView(LiveView):
        template_name = "page.html#card"

    with connection.schema_editor() as schema:
        schema.create_model(QuerysetPartialNode)
    try:
        leaf = QuerysetPartialNode.objects.create(label="third")
        middle = QuerysetPartialNode.objects.create(label="second", child=leaf)
        root = QuerysetPartialNode.objects.create(label="first", child=middle)
        with override_settings(TEMPLATES=settings_for(tmp_path, False)):
            view = NamedView()
            view.nodes = QuerysetPartialNode.objects.filter(pk=root.pk)
            html, _, _ = view.render_with_diff(None)
            assert "[first]" in html
            assert "[second]" in html
            assert "[third]" in html
            assert "sensitive" not in html
            assert "(FIRST)" in html and "(SECOND)" in html and "(THIRD)" in html

            # Shared descendants and preloaded paths retain the QuerySet's
            # database behavior, and unrelated or denied fields never escape.
            from django.test.utils import CaptureQueriesContext

            roots = (
                QuerysetPartialNode.objects.filter(pk__in=[root.pk, middle.pk])
                .select_related("child__child")
                .order_by("-pk")
            )
            with CaptureQueriesContext(connection) as queries:
                rows = view.serialize_for_template(roots, "nodes")
            assert len(queries) == 1
            assert [row["label"] for row in rows] == ["first", "second"]
            assert rows[0]["child"]["child"]["label"] == "third"
            for row in (rows[0], rows[0]["child"], rows[0]["child"]["child"]):
                assert "password" not in row
                assert "unused" not in row
            list_rows = view.serialize_for_template(list(roots), "nodes")
            assert list_rows == rows
            list_view = NamedView()
            list_view.nodes = list(roots)
            list_context = list_view.get_context_data()["nodes"]
            assert list_context == rows
            assert "unused" not in list_context[0]["child"]
            assert "[third]" in list_view.render_with_diff(None)[0]
            deferred = view.serialize_for_template(
                QuerysetPartialNode.objects.only("label").filter(pk=root.pk), "nodes"
            )
            assert deferred[0]["child"]["child"]["display"] == "THIRD"

            # Do not introduce a small fixed recursion depth cutoff.
            deep = QuerysetPartialNode(label="leaf")
            for _ in range(70):
                deep = QuerysetPartialNode(label="parent", child=deep)
            branch = view.serialize_for_template([deep], "nodes")[0]
            for _ in range(70):
                branch = branch["child"]
            assert branch["label"] == "leaf"
            # Distinct in-memory unsaved objects may share an assigned key.
            unsaved_leaf = QuerysetPartialNode(pk=99, label="leaf")
            unsaved_root = QuerysetPartialNode(pk=99, label="root", child=unsaved_leaf)
            unsaved = view.serialize_for_template([unsaved_root], "nodes")
            assert unsaved[0]["child"]["label"] == "leaf"

            # Context shapes which bypass scoped JIT fail explicitly rather
            # than serializing all model fields and truncating descendants.
            (tmp_path / "nested.html").write_text(
                "{% partialdef item %}[{{ node.label }}]{% if node.child %}"
                "{% with node=node.child %}{% partial item %}{% endwith %}"
                "{% endif %}{% endpartialdef %}{% partialdef card %}"
                "<main dj-root>{% for node in group.nodes %}{% partial item %}"
                "{% endfor %}</main>{% endpartialdef %}"
            )
            nested = NamedView()
            nested.template_name = "nested.html#card"
            nested.group = {"nodes": [root]}
            with pytest.raises(RuntimeError, match="require scoped JIT"):
                nested.render_with_diff(None)

            # Before get_template(), a plain file name can hide a named
            # recursive include; the guard must resolve it on demand.
            (tmp_path / "ordinary.html").write_text(
                '<main dj-root>{% include "nested.html#card" %}</main>'
            )
            ordinary = NamedView()
            ordinary.template_name = "ordinary.html"
            ordinary.group = {"nodes": [root]}
            with pytest.raises(RuntimeError, match="require scoped JIT"):
                ordinary.get_context_data()

            # ORM values unrelated to the recursive root remain available.
            unrelated = NamedView()
            unrelated.nodes = [{"label": "safe", "child": None}]
            unrelated.extra = {"owner": root}
            assert "[safe]" in unrelated.render_with_diff(None)[0]

            class ExplicitView(NamedView):
                exposure_policy = "explicit"

                def get_render_only_context(self):
                    return {"nodes": [root]}

            with pytest.raises(RuntimeError, match="require scoped JIT"):
                ExplicitView().render_with_diff(None)

            class UnrelatedExplicitView(ExplicitView):
                def get_render_only_context(self):
                    return {"nodes": [{"label": "safe", "child": None}], "extra": {"owner": root}}

            assert "[safe]" in UnrelatedExplicitView().render_with_diff(None)[0]
            with monkeypatch.context() as patch:
                patch.setattr("djust.mixins.context._jit_serialization_enabled", lambda: False)
                disabled = NamedView()
                disabled.nodes = [root]
                with pytest.raises(RuntimeError, match="require scoped JIT"):
                    disabled.render_with_diff(None)

            # A denied relation is never followed or exposed.
            QuerysetPartialNode.djust_exclude_fields = ["child"]
            try:
                denied = view.serialize_for_template(list(roots), "nodes")
                assert all("child" not in row for row in denied)
            finally:
                del QuerysetPartialNode.djust_exclude_fields

            # An unbounded model cycle fails explicitly instead of truncating.
            leaf.child = root
            leaf.save(update_fields=["child"])
            with CaptureQueriesContext(connection) as cycle_queries:
                with pytest.raises(RuntimeError) as error:
                    view.serialize_for_template(
                        QuerysetPartialNode.objects.filter(pk=root.pk), "nodes"
                    )
            assert isinstance(error.value.__cause__, RecursionError)
            assert len(cycle_queries) < 12
            leaf.child = None
            leaf.save(update_fields=["child"])

            # Context-dependent stop conditions require lazy rendering and
            # cannot safely be approximated by eager traversal.
            (tmp_path / "unsupported.html").write_text(
                "{% partialdef item %}{{ node.label }}"
                "{% if node.child and node.label %}{% with node=node.child %}"
                "{% partial item %}{% endwith %}{% endif %}{% endpartialdef %}"
                "{% partialdef card %}<main dj-root>{% for node in nodes %}"
                "{% partial item %}{% endfor %}</main>{% endpartialdef %}"
            )
            unsupported = NamedView()
            unsupported.template_name = "unsupported.html#card"
            with pytest.raises(RuntimeError) as error:
                unsupported.serialize_for_template(
                    QuerysetPartialNode.objects.filter(pk=root.pk), "nodes"
                )
            assert "Unsupported recursive partial" in str(error.value.__cause__)
            unsupported.nodes = QuerysetPartialNode.objects.filter(pk=root.pk)
            with pytest.raises(RuntimeError) as error:
                unsupported.render_with_diff(None)
            assert "Unsupported recursive partial" in str(error.value.__cause__)

            # Nested loops and aliases with filters must fail at the real
            # LiveView entry point, not fall back to unscoped serialization.
            item = (
                "{% partialdef item %}{{ node.label }}{% if node.child %}"
                "{% with node=node.child %}{% partial item %}{% endwith %}"
                "{% endif %}{% endpartialdef %}"
            )
            bodies = [
                "{% for node in nodes %}{% for x in extras %}{% partial item %}"
                "{% endfor %}{% endfor %}",
                "{% with selected=nodes|slice:':1' %}{% for node in selected %}"
                "{% partial item %}{% endfor %}{% endwith %}",
            ]
            for index, body in enumerate(bodies):
                name = f"unsupported_{index}.html"
                (tmp_path / name).write_text(
                    item
                    + "{% partialdef card %}<main dj-root>"
                    + body
                    + "</main>{% endpartialdef %}"
                )
                candidate = NamedView()
                candidate.template_name = name + "#card"
                candidate.nodes = QuerysetPartialNode.objects.filter(pk=root.pk)
                candidate.extras = [1]
                with pytest.raises(RuntimeError) as error:
                    candidate.render_with_diff(None)
                assert "Unsupported recursive partial" in str(error.value.__cause__)
    finally:
        with connection.schema_editor() as schema:
            schema.delete_model(QuerysetPartialNode)


def test_recursive_model_collection_rejects_unsupported_loop_shapes():
    from djust.mixins.jit import _recursive_queryset_relation

    item = (
        "{% if node.child %}{% with node=node.child %}"
        "{# djust:recursive-partial #}{% endwith %}{% endif %}"
    )
    with pytest.raises(ValueError, match="Unsupported recursive partial"):
        _recursive_queryset_relation(
            "{% for node in nodes|slice:':1' %}" + item + "{% endfor %}", "nodes"
        )
    with pytest.raises(ValueError, match="Unsupported recursive partial"):
        _recursive_queryset_relation(
            "{% with alias=nodes %}{% for node in alias %}" + item + "{% endfor %}{% endwith %}",
            "nodes",
        )
    assert (
        _recursive_queryset_relation("{% for node in nodes %}" + item + "{% endfor %}", "other")
        is None
    )


def test_recursive_partial_rejects_ifchanged():
    from djust.mixins.jit import _recursive_queryset_relation

    source = (
        "{% for node in nodes %}{% ifchanged node.label %}"
        "{% if node.child %}{% with node=node.child %}"
        "{# djust:recursive-partial #}{% endwith %}{% endif %}"
        "{% endifchanged %}{% endfor %}"
    )
    with pytest.raises(ValueError, match="Unsupported recursive partial"):
        _recursive_queryset_relation(source, "nodes")


def test_recursive_single_model_inside_unrelated_loop_rejected():
    from djust.mixins.jit import _recursive_queryset_relation

    source = (
        "{% for x in entries %}{% if root.child %}{% with root=root.child %}"
        "{# djust:recursive-partial #}{% endwith %}{% endif %}{% endfor %}"
    )
    with pytest.raises(ValueError, match="Unsupported recursive single model"):
        _recursive_queryset_relation(source, "root", single=True)


def test_recursive_model_collection_rejects_outer_conditional():
    from djust.mixins.jit import _recursive_queryset_relation

    source = (
        "{% if enabled %}{% for node in nodes %}"
        "{% if node.child %}{% with node=node.child %}"
        "{# djust:recursive-partial #}{% endwith %}{% endif %}"
        "{% endfor %}{% endif %}"
    )
    with pytest.raises(ValueError, match="Unsupported recursive partial"):
        _recursive_queryset_relation(source, "nodes")


def dynamic_include_files(path):
    (path / "dynamic_outer.html").write_text("<main dj-root>{% include target %}</main>")
    (path / "dynamic_filter.html").write_text(
        "<main dj-root>{% filter lower %}{% include target %}{% endfilter %}</main>"
    )
    (path / "dynamic_base.html").write_text(
        "{% partialdef item %}[{{ node.label }}]{% if node.child %}"
        "{% with node=node.child %}{% partial item %}{% endwith %}{% endif %}"
        "{% endpartialdef %}{% for node in nodes %}{% partial item %}{% endfor %}"
    )
    (path / "dynamic_child.html").write_text('{% extends "dynamic_base.html" %}')
    (path / "dynamic_grandchild.html").write_text('{% extends "dynamic_child.html" %}')
    (path / "dynamic_parts.html").write_text(
        "{% partialdef safe %}<b>SAFE {{ value }}</b>{% endpartialdef %}"
        "{% partialdef item %}[{{ node.label }}]{% if node.child %}"
        "{% with node=node.child %}{% partial item %}{% endwith %}{% endif %}"
        "{% endpartialdef %}"
        "{% partialdef recursive %}{% for node in nodes %}"
        "{% partial item %}{% endfor %}{% endpartialdef %}"
        "{% partialdef wrapper %}{% include 'dynamic_parts.html#recursive' %}"
        "{% endpartialdef %}"
    )


def test_dynamic_recursive_partial_http_fails_but_nonrecursive_succeeds(tmp_path):
    dynamic_include_files(tmp_path)

    class DynamicHttpView(LiveView):
        template_name = "dynamic_outer.html"
        selected = "dynamic_parts.html#safe"

        def mount(self, request, **kwargs):
            self.target = self.selected
            self.value = "OK"
            self.nodes = [{"label": "one", "child": {"label": "two"}}]

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        response = DynamicHttpView.as_view()(request())
        assert response.status_code == 200
        assert "SAFE OK" in response.content.decode()
        for selected in (
            "dynamic_parts.html#recursive",
            "dynamic_parts.html#wrapper",
            "dynamic_child.html",
            "dynamic_grandchild.html",
        ):
            DynamicHttpView.selected = selected
            with pytest.raises(RuntimeError, match="Recursive named partials selected by dynamic"):
                DynamicHttpView.as_view()(request())
        DynamicHttpView.template_name = "dynamic_filter.html"
        DynamicHttpView.selected = "dynamic_parts.html#recursive"
        with pytest.raises(RuntimeError, match="Recursive named partials selected by dynamic"):
            DynamicHttpView.as_view()(request())


def test_dynamic_template_object_is_checked_and_plain_backend_is_unaffected(tmp_path):
    from django.template import Template, engines

    dynamic_include_files(tmp_path)

    class ObjectView(LiveView):
        template_name = "dynamic_outer.html"

        def get_context_data(self, **kwargs):
            return {
                "target": self.target,
                "value": "OK",
                "nodes": [{"label": "one", "child": {"label": "two"}}],
            }

    with override_settings(TEMPLATES=settings_for(tmp_path, False)):
        view = ObjectView()
        view.target = Template("<b>OBJECT {{ value }}</b>")
        assert "OBJECT OK" in view.render_with_diff(None)[0]
        view = ObjectView()
        view.target = engines.all()[0].get_template("dynamic_parts.html#recursive")
        with pytest.raises(RuntimeError, match="Dynamic template object cannot be verified"):
            view.render_with_diff(None)

        class OverriddenTemplate(Template):
            def render(self, context):
                return "[unsafe]"

        view = ObjectView()
        view.target = OverriddenTemplate("safe source")
        with pytest.raises(RuntimeError, match="Dynamic template object cannot be verified"):
            view.render_with_diff(None)
        view = ObjectView()
        view.target = Template("{{ value|lower }}")
        with pytest.raises(RuntimeError, match="Dynamic template object cannot be verified"):
            view.render_with_diff(None)

    with override_settings(
        TEMPLATES=settings_for(tmp_path, False, "djust.template.backend.DjustTemplateBackend")
    ):
        plain = engines.all()[0].from_string("{% include target %}")
        result = plain.render(
            {
                "target": "dynamic_parts.html#recursive",
                "nodes": [{"label": "one", "child": {"label": "two"}}],
            }
        )
        assert "[one]" in result and "[two]" in result


class DynamicPartialSocketView(LiveView):
    template_name = "dynamic_outer.html"

    def mount(self, request, **kwargs):
        self.target = "dynamic_parts.html#safe"
        self.value = "OK"
        self.nodes = [{"label": "one", "child": {"label": "two"}}]

    @event_handler()
    def select_recursive(self):
        self.target = "dynamic_parts.html#recursive"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_dynamic_recursive_partial_websocket_fails_explicitly(tmp_path):
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    dynamic_include_files(tmp_path)
    with override_settings(
        TEMPLATES=settings_for(tmp_path, False),
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        DEBUG=False,
        DJUST_ALLOW_MISSING_ORIGIN=True,
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            assert (await comm.connect())[0]
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.DynamicPartialSocketView", "url": "/"}
            )
            mount = await comm.receive_json_from(timeout=5)
            assert mount.get("type") == "mount", mount
            assert "SAFE OK" in str(mount)
            await comm.send_json_to(
                {"type": "event", "event": "select_recursive", "params": {}, "ref": 1}
            )
            event = await comm.receive_json_from(timeout=5)
            assert event.get("type") == "error", event
            assert "[one]" not in str(event)
        finally:
            await comm.disconnect()


def test_dynamic_template_object_checks_actual_execution_methods():
    from django.template import Template
    from djust.template.operands import is_safe_dynamic_template_object

    assert is_safe_dynamic_template_object(Template("Hello {{ name }}"))
    assert not is_safe_dynamic_template_object(Template("Hello {{ name|lower }}"))
    changed = Template("safe")
    changed._render = lambda context: "unsafe"
    assert not is_safe_dynamic_template_object(changed)
    changed = Template("safe")
    changed.nodelist[0].render_annotated = lambda context: "unsafe"
    assert not is_safe_dynamic_template_object(changed)
    changed = Template("{{ name }}")
    changed.nodelist[0].render_annotated = lambda context: "unsafe"
    assert not is_safe_dynamic_template_object(changed)
    changed = Template("{{ name }}")
    changed.nodelist.render = lambda context: "unsafe"
    assert not is_safe_dynamic_template_object(changed)
