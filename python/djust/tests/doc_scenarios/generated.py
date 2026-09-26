"""Generator output executed as documentation examples (ADR-037 D2)."""

from django.db import models

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


class Item(models.Model):
    """What the scaffold's ``from .models import Item`` resolves to."""

    title = models.CharField(max_length=100)
    body = models.TextField(blank=True)
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)

    class Meta:
        app_label = "demo_app"
        db_table = "adr037_scaffold_item"


def _scaffold_examples():
    from djust.mcp.server import create_server

    server = create_server()
    (tool,) = [t for t in server._tool_manager._tools.values() if t.name == "scaffold_view"]
    found = []
    for features in ("form_edit", "form_edit,auth"):
        code = tool.fn(name="ItemEditView", features=features)
        found.append(
            H.Example(
                "scaffold-%s" % features.replace(",", "-").replace("_", "-"),
                "scaffold-model-form-edit",
                None,
                "python/djust/mcp/server.py",
                0,
                code,
                None,
                (code,),
            )
        )
    return found


def _catalogue_examples():
    import inspect

    from django.template.loader import get_template

    from djust.components import interactive_examples

    markup = get_template(
        "djust_theming/catalogue/examples/interactive_dropdown_menu.html"
    ).template.source
    code = inspect.getsource(interactive_examples)
    return [
        H.Example(
            "catalogue-dropdown-menu",
            "catalogue-dropdown",
            None,
            "python/djust/components/interactive_examples.py",
            0,
            code,
            "<div dj-root>%s</div>" % markup,
            (code,),
        )
    ]


EXAMPLES = _schema_examples() + _scaffold_examples() + _catalogue_examples()


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


@scenario("scaffold-model-form-edit")
def scaffold_model_form_edit(example):
    view_class = H.load(
        example, template=EDIT_TEMPLATE, package=PACKAGE, modules={"models": {"Item": Item}}
    )
    drive_model_form(
        view_class, Item, "owner", "items/<int:pk>/edit/", {"title": "Published", "body": "Text"}
    )


@scenario("catalogue-dropdown")
def catalogue_dropdown(example):
    page = H.Page(H.load(example))
    project, alpha, beta = page.ids()
    # Each menu's choice reaches only its own callback.
    assert page.event("select", component_id=beta, value="details") == 200
    assert (page.view.row_selection, page.view.selected_action) == ("87:details", "")
    assert page.event("select", component_id=project, value="edit") == 200
    assert (page.view.selected_action, page.view.row_selection) == ("edit", "87:details")
    # The disabled item and an unknown value are refused before any callback.
    assert page.event("select", component_id=project, value="delete") >= 400
    assert page.event("select", component_id=alpha, value="nope") >= 400
