"""Generator output executed as documentation examples (ADR-037 D2)."""

from djust.tests import _doc_examples as H

from . import scenario
from .adr035 import PACKAGE, Article, drive_model_form


def _schema_examples():
    from djust.schema import BEST_PRACTICES

    forms = BEST_PRACTICES["forms"]
    return [
        H.Example(
            "schema-form-create",
            "form-create",
            None,
            "python/djust/schema.py",
            0,
            forms["example"],
            None,
            (forms["example"],),
        ),
        H.Example(
            "schema-form-edit",
            "generated-model-form-edit",
            None,
            "python/djust/schema.py",
            0,
            forms["edit_example"],
            None,
            (forms["edit_example"],),
        ),
    ]


EXAMPLES = _schema_examples()

FORM_TEMPLATE = (
    '<div dj-root><form dj-submit="submit_form">{% csrf_token %}'
    "{{ success_message }}{{ error_message }}"
    '<input name="email" value="{{ form_data.email }}">'
    '<textarea name="message">{{ form_data.message }}</textarea></form></div>'
)
EDIT_TEMPLATE = (
    '<div dj-root><h1>Editing {{ object.title }}</h1><form dj-submit="submit_form">'
    '{% csrf_token %}{{ success_message }}<input name="title" value="{{ form_data.title }}">'
    '<textarea name="body">{{ form_data.body }}</textarea></form></div>'
)


@scenario("form-create")
def form_create(example):
    import json

    from django.test import RequestFactory

    from djust.tests.test_exposure_runtime import make_request

    view_class = H.load(example, template=FORM_TEMPLATE)
    initial = make_request()
    assert view_class().get(initial).status_code == 200

    def submit(**params):
        request = RequestFactory().post(
            initial.path,
            data=json.dumps({"event": "submit_form", "params": params}),
            content_type="application/json",
        )
        request.user, request.session, request.tenant = initial.user, initial.session, None
        view = view_class()
        response = view.post(request)
        assert response.status_code == 200, response.content
        return view

    assert submit(email="not-an-email", message="").error_message
    assert submit(email="a@example.com", message="Hello").success_message


@scenario("generated-model-form-edit")
def generated_model_form_edit(example):
    view_class = H.load(
        example, template=EDIT_TEMPLATE, package=PACKAGE, modules={"models": {"Article": Article}}
    )
    drive_model_form(
        view_class,
        Article,
        "author",
        "articles/<int:pk>/edit/",
        {"title": "Published", "body": "Text"},
    )
