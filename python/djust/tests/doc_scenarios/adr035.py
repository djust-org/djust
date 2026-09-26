"""ADR-035 scenarios: a ModelFormMixin view edits only an authorized record."""

import json
import re
import sys
import types
import uuid

from django.db import connection, models
from django.test import RequestFactory, override_settings
from django.urls import path

from djust.tests import _doc_examples as H

from . import scenario

PACKAGE = "djust_doc_examples"
DENIED = b"Access denied for this object."
DEFAULT_ROUTE = "articles/<int:pk>/edit/"


class Article(models.Model):
    """The model the documented examples import from ``.models``."""

    title = models.CharField(max_length=100)
    body = models.TextField(blank=True)
    author = models.ForeignKey("auth.User", on_delete=models.CASCADE)

    class Meta:
        app_label = "demo_app"
        db_table = "adr035_doc_article"


def ensure_table(model):
    """``demo_app`` is unmigrated, so the stand-in table is created on demand."""
    if model._meta.db_table not in connection.introspection.table_names():
        with connection.schema_editor() as editor:
            editor.create_model(model)


def drive_model_form(view_class, model, owner_field, route, edit):
    """Author edits, another user and a missing record are denied alike."""
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.backends.db import SessionStore

    from djust.checks.security import check_model_form_object_policy

    ensure_table(model)
    urls = types.ModuleType(PACKAGE + ".urls")
    urls.urlpatterns = [path(route, view_class.as_view())]
    sys.modules[urls.__name__] = urls

    tag = uuid.uuid4().hex[:8]
    users = get_user_model().objects
    author = users.create_user(username="author-" + tag)
    other = users.create_user(username="other-" + tag)
    record = model.objects.create(title="First draft", body="Text", **{owner_field: author})
    url = "/" + route.replace("<int:pk>", str(record.pk))
    sessions: dict = {}

    def call(user, method="get", pk=record.pk, **params):
        target = url.replace(str(record.pk), str(pk))
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

    with override_settings(ROOT_URLCONF=urls.__name__):
        page = call(author)
        assert page.status_code == 200
        assert "First draft" in page.content.decode()
        # Another user and a missing record: the same denial, no content.
        for user, pk in ((other, record.pk), (author, record.pk + 1000)):
            denied = call(user, pk=pk)
            assert (denied.status_code, denied.content) == (403, DENIED)
        call(other)
        assert call(other, "post", **dict(edit, title="Hijacked")).status_code == 403
        saved = call(author, "post", **edit)
        assert saved.status_code == 200, saved.content
        assert "Saved!" in saved.content.decode()

    record.refresh_from_db()
    assert (record.title, getattr(record, owner_field + "_id")) == (edit["title"], author.pk)
    # The view scopes its records, so S013 has nothing to say.
    assert not [
        message
        for message in check_model_form_object_policy(None)
        if view_class.__qualname__ in message.msg
    ]


@scenario("model-form-edit")
def model_form_edit(example):
    view_class = H.load(example, package=PACKAGE, modules={"models": {"Article": Article}})
    routes = [
        m.group(1) for code in example.section_code for m in re.finditer(r'path\("([^"]+)"', code)
    ]
    # The guide documents the route; the AI reference relies on the same one.
    assert routes in ([], [DEFAULT_ROUTE]), routes
    drive_model_form(
        view_class, Article, "author", DEFAULT_ROUTE, {"title": "Published", "body": "Text"}
    )
