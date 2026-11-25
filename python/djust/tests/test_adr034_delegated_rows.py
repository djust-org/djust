"""ADR-034 D5: the delegated-row example, run as written in the ADR.

Repeated rows that only choose an operation on a record pass the record id to
an ordinary view handler: no component per row, no stateful collection. The
ADR's ``RowActionsView`` block is extracted with the doc-snippet checker's own
extractor, and its HTML block is the template. It is driven through the HTTP
fallback with the ids the page renders, before and after a reorder, and with
forged ids and actions.
"""

import importlib.util
import pathlib
import re
import sys
import types

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path

from djust.testing import open_live_page

ROOT = pathlib.Path(__file__).resolve().parents[3]
ADR = ROOT / "docs/adr/034-component-scoped-events-and-bindings.md"


def _example():
    spec = importlib.util.spec_from_file_location(
        "_doc_snippets", ROOT / "scripts/check-doc-snippets.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    code = next(
        code
        for _line, code, _m in checker.extract_python_blocks(ADR)
        if "class RowActionsView" in code
    )
    text = ADR.read_text()
    start = text.index("class RowActionsView")
    html = re.search(r"```html\n(.*?)\n```", text[start:], re.S).group(1)
    module = types.ModuleType("djust_adr034_rows")
    sys.modules[module.__name__] = module
    exec(compile(code, "<adr034-rows>", "exec"), module.__dict__)
    view = module.RowActionsView
    view.exposure_policy = "explicit"
    view.template_name = None
    view.template = "<div dj-root>" + html + "</div>"

    # The ADR's mount() keeps its rows in plain attributes; an explicit view
    # renders only what ``get_context_data()`` supplies from them.
    def get_context_data(self, **kwargs):
        context = super(view, self).get_context_data(**kwargs)
        context.update(
            projects=self.projects,
            selected_project=self.selected_project,
            selected_action=self.selected_action,
        )
        return context

    view.get_context_data = get_context_data
    return view


RowActionsView = _example()


class ReorderedRowsView(RowActionsView):
    """The ADR's view after its list was reordered."""

    exposure_policy = "explicit"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["projects"] = list(reversed(context["projects"]))
        return context


urlpatterns = [
    path("rows/", RowActionsView.as_view()),
    path("rows-reordered/", ReorderedRowsView.as_view()),
]

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.urls(__name__),
]


@pytest.fixture(autouse=True)
def _allowed():
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust_adr034_rows", __name__], ALLOWED_HOSTS=["*"]
    ):
        yield


async def _post(page, params, event="project_action"):
    return await sync_to_async(page.post_event)(event, params, full=True)


async def test_rows_carry_their_record_id_and_forged_ids_are_refused():
    page = await sync_to_async(open_live_page)("/rows/")
    # Each row's buttons carry that row's id, typed; no component per row.
    assert page.html.count('dj-value-project-id:int="42"') == 2
    assert page.html.count('dj-value-project-id:int="87"') == 2
    assert "data-component-id" not in page.html

    response = await _post(page, {"project_id": "87", "action": "activity"})
    assert response.status_code == 200, response.content
    assert "87: activity</p>" in response.json()["html"]

    for params in (
        {"project_id": "99", "action": "details"},
        {"project_id": "42", "action": "drop"},
    ):
        response = await _post(page, params)
        assert response.status_code >= 400, params
        # The refused POST's handler never ran: nothing was selected.
        assert b"details</p>" not in response.content and b"drop</p>" not in response.content


async def test_a_reordered_list_still_sends_each_rows_own_id():
    page = await sync_to_async(open_live_page)("/rows-reordered/")
    html = page.html
    beta, alpha = html.index("Beta"), html.index("Alpha")
    assert beta < alpha
    assert html.index('dj-value-project-id:int="87"', beta) < alpha
    assert html.index('dj-value-project-id:int="42"', alpha) > alpha
