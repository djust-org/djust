"""ADR-035: the published ModelFormMixin examples execute as documented.

The Python blocks of the form guide's "Editing one record with
``ModelFormMixin``" section and of the AI form reference are extracted with the
doc-snippet checker's own extractor. Each view block runs as a module of a
stand-in app package whose ``models`` holds the ``Article`` the examples
import. The guide's route block supplies the URL pattern. The paired HTML
block is the template. The view then goes through a real GET and the real HTTP
fallback, as its author, as another user and for a missing record.
"""

import importlib.util
import json
import pathlib
import re
import sys
import types
import uuid

import pytest
from django.db import connection, models
from django.test import RequestFactory, override_settings
from django.urls import path

ROOT = pathlib.Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/website/guides/forms.md"
AI_GUIDE = ROOT / "docs/ai/forms.md"
GUIDE_SECTION = "## Editing one record with `ModelFormMixin`"
AI_SECTION = "## ModelFormMixin: edit one record (djust 1.3+)"
PACKAGE = "djust_doc_examples"
DENIED = b"Access denied for this object."


class Article(models.Model):
    """The model the documented examples import from ``.models``."""

    title = models.CharField(max_length=100)
    body = models.TextField(blank=True)
    author = models.ForeignKey("auth.User", on_delete=models.CASCADE)

    class Meta:
        app_label = "demo_app"
        db_table = "adr035_doc_article"


def _snippet_checker():
    spec = importlib.util.spec_from_file_location(
        "_doc_snippets", ROOT / "scripts/check-doc-snippets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section_blocks(path, heading):
    """The section's Python blocks, and its ```html blocks in order."""
    text = path.read_text()
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    first = text[:start].count("\n") + 1
    last = text[:end].count("\n") + 1 if end != -1 else len(text.splitlines()) + 1
    python = [
        code
        for fence, code, _marker in _snippet_checker().extract_python_blocks(path)
        if first <= fence <= last
    ]
    html = re.findall(r"```html\n(.*?)\n```", text[start : end if end != -1 else None], re.S)
    return python, html


def _examples():
    found = []
    for source, document, heading in (
        ("guide", GUIDE, GUIDE_SECTION),
        ("ai", AI_GUIDE, AI_SECTION),
    ):
        python, html = _section_blocks(document, heading)
        views = [code for code in python if "(ModelFormMixin[" in code]
        routes = [
            match.group(1) for code in python for match in re.finditer(r'path\("([^"]+)"', code)
        ]
        assert len(views) == 1 and len(html) == 1, (source, len(views), len(html))
        found.append(
            (source, views[0], html[0], routes[0] if routes else "articles/<int:pk>/edit/")
        )
    return found


EXAMPLES = _examples()


def _load(source, code, html):
    """Run a documented block as a module of the stand-in app package."""
    from djust import LiveView

    package = types.ModuleType(PACKAGE)
    package.__path__ = []
    models_module = types.ModuleType(PACKAGE + ".models")
    models_module.Article = Article
    sys.modules[PACKAGE] = package
    sys.modules[PACKAGE + ".models"] = models_module
    name = "%s.%s_example" % (PACKAGE, source)
    module = types.ModuleType(name)
    module.__package__ = PACKAGE
    sys.modules[name] = module
    exec(compile(code, "<%s>" % name, "exec"), module.__dict__)
    views = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, LiveView) and value.__module__ == name
    ]
    assert len(views) == 1, views
    view = views[0]
    view.template_name = None
    view.template = html
    return view


@pytest.fixture
def article_table(django_db_blocker):
    """The test database normally creates it (``demo_app`` is unmigrated)."""
    with django_db_blocker.unblock():
        if Article._meta.db_table not in connection.introspection.table_names():
            with connection.schema_editor() as editor:
                editor.create_model(Article)
    yield


def test_both_documents_are_found():
    assert [source for source, *_ in EXAMPLES] == ["guide", "ai"]
    assert EXAMPLES[0][3] == "articles/<int:pk>/edit/"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("index", range(len(EXAMPLES)))
def test_documented_example_edits_only_the_authors_record(article_table, index):
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.backends.db import SessionStore

    from djust.checks.security import check_model_form_object_policy

    source, code, html, route = EXAMPLES[index]
    view_class = _load(source, code, html)
    urls = types.ModuleType(PACKAGE + ".urls")
    urls.urlpatterns = [path(route, view_class.as_view())]
    sys.modules[urls.__name__] = urls

    tag = uuid.uuid4().hex[:8]
    users = get_user_model().objects
    author = users.create_user(username="author-" + tag)
    other = users.create_user(username="other-" + tag)
    article = Article.objects.create(title="First draft", body="Text", author=author)
    url = "/" + route.replace("<int:pk>", str(article.pk))

    def call(user, method="get", pk=article.pk, **params):
        target = url.replace(str(article.pk), str(pk))
        factory = RequestFactory()
        if method == "get":
            request = factory.get(target)
        else:
            request = factory.post(
                target,
                data=json.dumps({"event": "submit_form", "params": params}),
                content_type="application/json",
            )
        request.user, request.tenant = user, None
        request.session = sessions.setdefault(user.pk, SessionStore())
        return view_class.as_view()(request, pk=pk)

    sessions: dict = {}
    with override_settings(ROOT_URLCONF=urls.__name__):
        page = call(author)
        assert page.status_code == 200
        assert "First draft" in page.content.decode()

        # Another user and a missing record: the same denial, no content.
        for user, pk in ((other, article.pk), (author, article.pk + 1000)):
            denied = call(user, pk=pk)
            assert (denied.status_code, denied.content) == (403, DENIED)

        call(other)
        denied = call(other, "post", title="Hijacked", body="")
        assert denied.status_code == 403

        saved = call(author, "post", title="Published", body="Text")
        assert saved.status_code == 200, saved.content
        assert "Saved!" in saved.content.decode()

    article.refresh_from_db()
    assert (article.title, article.author_id) == ("Published", author.pk)
    # The documented view scopes its records, so S013 has nothing to say.
    assert not [
        message
        for message in check_model_form_object_policy(None)
        if view_class.__qualname__ in message.msg
    ]
