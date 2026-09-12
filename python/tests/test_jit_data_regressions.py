"""Model-backed template regressions reported against 1.2.0rc4 (#2802–#2805)."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import models
from django.template import Context, Engine

from djust._rust import extract_template_variables, serialize_queryset, render_template
from djust.mixins.jit import JITMixin
from djust.optimization.codegen import compile_serializer, generate_serializer_code
from djust.optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset


class DataOwner(models.Model):
    code = models.CharField(max_length=20, unique=True)

    @property
    def avatar(self):
        return {"layers": ["body", "eyes"], "settings": {"enabled": True, "price": Decimal("1.20")}}

    class Meta:
        app_label = "jit_data_regressions"


class DataEntry(models.Model):
    owner = models.ForeignKey(DataOwner, null=True, on_delete=models.CASCADE)
    custom_owner = models.ForeignKey(
        DataOwner,
        null=True,
        to_field="code",
        db_column="owner_code",
        on_delete=models.CASCADE,
        related_name="+",
    )
    image = models.ImageField(blank=True)
    document = models.FileField(blank=True)

    @property
    def tags(self):
        return ["django", "python"]

    class Meta:
        app_label = "jit_data_regressions"


def python_serializer(paths):
    return compile_serializer(
        generate_serializer_code("DataEntry", paths, "serialize_entry"), "serialize_entry"
    )


@pytest.mark.parametrize("path", ["owner_id", "custom_owner_id", "owner_id.real"])
def test_fk_attname_does_not_join(path):
    plan = analyze_queryset_optimization(DataEntry, [path])
    assert not plan.select_related
    assert not plan.prefetch_related
    str(optimize_queryset(DataEntry.objects.all(), plan).query)


def test_fk_names_still_join():
    plan = analyze_queryset_optimization(DataEntry, ["owner.code", "custom_owner.code"])
    assert plan.select_related == {"owner", "custom_owner"}
    str(optimize_queryset(DataEntry.objects.all(), plan).query)


@pytest.mark.parametrize("paths", [["owner.avatar", "tags"], ["owner.avatar.layers", "tags"]])
def test_rust_preserves_property_containers(paths):
    entry = DataEntry(pk=1, owner=DataOwner(pk=2))
    result = serialize_queryset([entry], paths)[0]
    assert result["tags"] == ["django", "python"]
    assert result["owner"]["avatar"]["layers"] == ["body", "eyes"]
    if "owner.avatar" in paths:
        assert result["owner"]["avatar"]["settings"] == {"enabled": True, "price": "1.20"}


@pytest.mark.parametrize("field", ["image", "document"])
@pytest.mark.parametrize("filename", ["", "uploads/example.png"])
@pytest.mark.parametrize("backend", ["rust", "python"])
def test_optional_file_guard_renders(field, filename, backend):
    entry = DataEntry(pk=1, **{field: filename})
    paths = [field, field + ".url"]
    result = (
        serialize_queryset([entry], paths)[0]
        if backend == "rust"
        else python_serializer(paths)(entry)
    )
    source = "{% if entry." + field + " %}{{ entry." + field + ".url }}{% else %}no-file{% endif %}"
    expected = Engine().from_string(source).render(Context({"entry": entry}))
    assert render_template(source, {"entry": result}) == expected


def test_codegen_does_not_suppress_unrelated_property_errors():
    class Broken:
        @property
        def url(self):
            raise ValueError("application bug")

    with pytest.raises(ValueError, match="application bug"):
        python_serializer(["image.url"])(SimpleNamespace(image=Broken()))


@pytest.mark.parametrize("only", ["", " only"])
def test_include_alias_nested_loop_paths(tmp_path, only):
    (tmp_path / "author.html").write_text(
        "{% for layer in author.avatar.layers %}{{ layer }}{% endfor %}"
    )
    source = (
        '{% for entry in entries %}{% include "author.html" with author=entry.owner'
        + only
        + " %}{% endfor %}"
    )
    inlined = JITMixin._inline_includes(source, [str(tmp_path)])
    paths = extract_template_variables(inlined)
    assert "owner.avatar.layers" in paths["entries"]


def test_include_alias_shadowing_and_only(tmp_path):
    (tmp_path / "inner.html").write_text("{{ author.avatar.layers }}{{ outside.secret }}")
    (tmp_path / "outer.html").write_text(
        '{% include "inner.html" with author=author.owner only %}{{ author.tags }}'
    )
    source = '{% include "outer.html" with author=entry %}{{ author.original }}'
    paths = extract_template_variables(JITMixin._inline_includes(source, [str(tmp_path)]))
    assert "owner.avatar.layers" in paths["entry"]
    assert "tags" in paths["entry"]
    assert paths["author"] == ["original"]
    assert "outside" not in paths


@pytest.mark.parametrize("paths", [["image", "image.url"], ["image.url", "image"]])
def test_field_tree_keeps_nested_paths_in_either_order(paths):
    assert serialize_queryset([DataEntry(image="photo.png")], paths)[0]["image"]["url"].endswith(
        "photo.png"
    )


def test_recursive_property_is_bounded():
    value = []
    value.append(value)
    with pytest.raises(RecursionError, match="nesting limit"):
        serialize_queryset([SimpleNamespace(tags=value)], ["tags"])


def test_container_snapshot_handles_conversion_side_effect():
    value = {"first": None, "second": [1, None, False]}

    class Mutating:
        def __str__(self):
            value.clear()
            return "safe"

    value["first"] = Mutating()
    assert serialize_queryset([SimpleNamespace(tags=value)], ["tags"]) == [
        {"tags": {"first": "safe", "second": [1, None, False]}}
    ]


def test_include_assignments_are_simultaneous(tmp_path):
    (tmp_path / "partial.html").write_text("{{ a.title }}{{ b.name }}")
    source = '{% include "partial.html" with a=entry b=a %}'
    paths = extract_template_variables(JITMixin._inline_includes(source, [str(tmp_path)]))
    assert paths["entry"] == ["title"]
    assert paths["a"] == ["name"]


from djust import LiveView  # noqa: E402


class DataListView(LiveView):
    login_required = False
    template = """<div dj-view="test_jit_data_regressions.DataListView">
    {% for entry in entries %}<section>
    {% if entry.owner_id %}<b>has-owner</b>{% endif %}
    {% for layer in entry.owner.avatar.layers %}<i>{{ layer }}</i>{% endfor %}
    {% for tag in entry.tags %}<em>{{ tag }}</em>{% endfor %}
    {% if entry.image %}<img src="{{ entry.image.url }}">{% else %}<b>no-image</b>{% endif %}
    {% if entry.document %}<a href="{{ entry.document.url }}">file</a>{% else %}<b>no-document</b>{% endif %}
    </section>{% endfor %}</div>"""

    def mount(self, request, **kwargs):
        self.entries = DataEntry.objects.all().order_by("pk")


@pytest.fixture
def data_rows(transactional_db):
    from django.db import connection

    with connection.schema_editor() as editor:
        editor.create_model(DataOwner)
        editor.create_model(DataEntry)
    owner = DataOwner.objects.create(code="owner")
    DataEntry.objects.create(owner=owner)
    DataEntry.objects.create(owner=owner, image="photo.png", document="report.pdf")
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(DataEntry)
            editor.delete_model(DataOwner)


def assert_list_html(html):
    import re

    html = re.sub(r' dj-id="[^"]*"', "", html)
    assert html.count("<i>body</i>") == 2
    assert html.count("<i>eyes</i>") == 2
    assert html.count("<em>django</em>") == 2
    assert html.count("<em>python</em>") == 2
    assert html.count("has-owner") == 2
    assert "no-image" in html and "photo.png" in html
    assert "no-document" in html and "report.pdf" in html


@pytest.mark.parametrize("force_codegen", [False, True])
def test_http_model_list(data_rows, force_codegen, monkeypatch):
    if force_codegen:
        monkeypatch.setattr(
            "djust._rust.serialize_queryset", lambda rows, paths: [{} for _ in rows]
        )
    from django.test import RequestFactory
    from django.contrib.sessions.middleware import SessionMiddleware

    request = RequestFactory().get("/jit-data/")
    SessionMiddleware(lambda request: None).process_request(request)
    response = DataListView.as_view()(request)
    assert response.status_code == 200
    assert_list_html(response.content.decode())


@pytest.mark.asyncio
async def test_websocket_model_list(data_rows):
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings
    from djust.websocket import LiveViewConsumer

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from(timeout=5)
            await communicator.send_json_to({"type": "mount", "view": f"{__name__}.DataListView"})
            message = await communicator.receive_json_from(timeout=5)
            assert message["type"] == "mount", message
            assert_list_html(message["html"])
        finally:
            await communicator.disconnect()
