"""ADR-034 D7: the published interactive-component examples run as documented.

The Python blocks of ``guides/interactive-components.md`` and of the AI
components reference's interactive section are extracted with the
doc-snippet checker's own extractor. Each view block runs as a module, and its
next ``html`` block is the template. The documented behavior is then driven
through a real GET and the real HTTP fallback, using the component identities
the page rendered.
"""

import importlib.util
import json
import pathlib
import re
import sys
import types

import pytest
from django.test import RequestFactory

ROOT = pathlib.Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/website/guides/interactive-components.md"
AI = ROOT / "docs/ai/components.md"
AI_SECTION = "## Interactive components (djust 1.3+)"


def _extractor():
    spec = importlib.util.spec_from_file_location(
        "_doc_snippets", ROOT / "scripts/check-doc-snippets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_python_blocks


def _pairs(path, heading=None):
    text = path.read_text()
    lines = text.splitlines()
    first, last = 0, len(lines) + 1
    if heading:
        start = text.index(heading)
        end = text.find("\n## ", start + len(heading))
        first = text[:start].count("\n") + 1
        last = text[:end].count("\n") + 1 if end != -1 else last
    found = []
    for fence, code, _marker in _extractor()(path):
        if not first <= fence <= last or "(LiveView)" not in code:
            continue
        i = fence + code.count("\n") + 1
        while i < len(lines) and lines[i].strip() != "```html":
            i += 1
        body = []
        for line in lines[i + 1 :]:
            if line.strip() == "```":
                break
            body.append(line)
        found.append((code, "\n".join(body)))
    return found


def _load(code, html, name):
    from djust import LiveView

    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(code, "<%s>" % name, "exec"), module.__dict__)
    views = [
        v
        for v in vars(module).values()
        if isinstance(v, type) and issubclass(v, LiveView) and v.__module__ == name
    ]
    assert len(views) == 1, views
    view = views[0]
    view.template_name = None
    view.template = html
    return view


EXAMPLES = [
    *[(code, html, "guide") for code, html in _pairs(GUIDE)],
    *[(code, html, "ai") for code, html in _pairs(AI, AI_SECTION)],
]
NAMES = [re.search(r"class (\w+)\(LiveView\)", code).group(1) for code, *_ in EXAMPLES]


def test_all_documented_views_are_found():
    assert NAMES == [
        "ProjectView",
        "ToolbarView",
        "HelpView",
        "RowActionsView",
        "ProjectListView",
        "ProjectView",
    ]


class Page:
    """A GET, then HTTP-fallback events on the same session."""

    def __init__(self, view_class):
        from djust.tests.test_exposure_runtime import make_request

        self.view_class = view_class
        self.initial = make_request()
        response = view_class().get(self.initial)
        assert response.status_code == 200
        self.html = response.content.decode()
        self.view = None

    def ids(self):
        return re.findall(r'data-component-id="([^"]+)"', self.html)

    def event(self, name, **params):
        request = RequestFactory().post(
            self.initial.path,
            data=json.dumps(params),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=name,
        )
        request.user, request.session, request.tenant = (
            self.initial.user,
            self.initial.session,
            None,
        )
        self.view = self.view_class()
        response = self.view.post(request)
        body = json.loads(response.content) if response.content else {}
        if "html" in body:
            self.html = body["html"]
        return response.status_code


@pytest.mark.django_db
@pytest.mark.parametrize(
    "index", range(len(EXAMPLES)), ids=lambda i: "%s-%s" % (EXAMPLES[i][2], NAMES[i])
)
def test_documented_example_behaves_as_described(index):
    code, html, source = EXAMPLES[index]
    name = NAMES[index]
    view_class = _load(code, html, "djust_adr034_doc_%s_%s" % (source, index))
    page = Page(view_class)

    if name == "ProjectView":
        (menu,) = page.ids()
        assert page.event("toggle", component_id=menu) == 200
        # The disabled item and an unknown value never reach the callback.
        if source == "guide":
            assert page.event("select", component_id=menu, value="delete") >= 400
        assert page.event("select", component_id=menu, value="nope") >= 400
        assert page.event("select", component_id=menu, value="edit") == 200
        assert page.view.selected_action == "edit"
    elif name == "ToolbarView":
        project, account = page.ids()
        assert page.event("select", component_id=account, value="settings") == 200
        assert page.view.show_account_settings and page.view.selected_action == ""
        assert not page.view.project_menu.open
        assert page.event("select", component_id=project, value="archive") == 200
        assert page.view.selected_action == "archive"
    elif name == "HelpView":
        (menu,) = page.ids()
        lifetime = re.search(r'data-dj-observe-lifetime="([^"]+)"', page.html).group(1)
        report = {"component_id": menu, "lifetime": lifetime}
        assert page.event("observe_toggle", open=True, sequence=1, **report) == 200
        assert page.view.help_opened == 1
        assert page.event("observe_toggle", open=True, sequence=1, **report) == 200  # duplicate
        assert page.event("observe_toggle", open=False, sequence=2, **report) == 200
        assert page.view.help_opened == 1
    elif name == "RowActionsView":
        assert page.ids() == []
        assert page.html.count('dj-value-project-id:int="42"') == 1
        assert page.event("project_action", project_id="87", action="details") == 200
        assert (page.view.selected_project, page.view.selected_action) == (87, "details")
        assert page.event("project_action", project_id="99", action="details") >= 400
    elif name == "ProjectListView":
        alpha, beta = page.ids()
        assert page.event("select", component_id=beta, value="details") == 200
        assert page.view.selected == "87:details"
        assert page.event("select", component_id=alpha, value="archive") == 200
        assert page.view.selected == "42:archive"
        assert [m.key for m in page.view.row_menus] == ["87"]
        assert page.view.row_menus.get("42") is None
        # The archived row's identity is refused from now on.
        assert page.event("select", component_id=alpha, value="details") >= 400
    else:  # pragma: no cover - the name list above is exhaustive
        raise AssertionError(name)
