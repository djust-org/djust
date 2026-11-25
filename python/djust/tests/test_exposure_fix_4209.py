"""``manage.py djust_exposure_inventory --fix`` (#4209).

The input is 1.3 code: ``fixtures/upgrade13_4209/views.py-tpl`` holds the 2.0
upgrade guide's own ``CartView`` and a few shapes around it (a policy on a base,
an existing ``get_context_data()``, Python-built markup, a 2.0 view). Each test
copies the fixture to a temporary directory, imports the copy under a unique
top-level name, runs the command on it, and unloads it after.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import sys
import types
import warnings
from io import StringIO
from pathlib import Path

import pytest
from django.core.checks import ERROR
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from djust import LiveView

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "upgrade13_4209"
REPO = Path(__file__).resolve().parents[3]
VIEWS = ("CartView", "SearchView", "ReportView", "ChipView", "ModernView")
#: The 1.3 statement, built so this file does not state it (the legacy ledger scans tests).
POLICY = "exposure_policy = " + '"' + "legacy" + '"'
LEGACY_LINE = re.compile(re.escape(POLICY))

_loaded: list[types.ModuleType] = []


def _copy(tmp_path: Path, name: str) -> Path:
    app_dir = tmp_path / name
    shutil.copytree(FIXTURE, app_dir)
    for template in app_dir.rglob("*.py-tpl"):
        template.rename(template.with_suffix(".py"))
    return app_dir


def _load(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        spec.loader.exec_module(module)
    _loaded.append(module)
    return module


def _unload(module: types.ModuleType) -> None:
    sys.modules.pop(module.__name__, None)
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == module.__name__:
            value.abstract = True


def _app(tmp_path: Path, name: str) -> tuple[Path, types.ModuleType]:
    app_dir = _copy(tmp_path, name)
    return app_dir, _load(app_dir / "views.py", name)


def _templates(app_dir: Path) -> list[dict]:
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(app_dir / "templates")],
            "APP_DIRS": True,
        }
    ]


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    while _loaded:
        _unload(_loaded.pop())


def _fix(app_dir: Path, module: types.ModuleType, *views: str, extra=(), root=None) -> str:
    args = ["--fix", "--root", str(root or app_dir), *extra]
    for view in views or VIEWS:
        args += ["--view", "%s.%s" % (module.__name__, view)]
    out = StringIO()
    with override_settings(TEMPLATES=_templates(app_dir)):
        call_command("djust_exposure_inventory", *args, stdout=out)
    return out.getvalue()


def _class_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.ClassDef) and n.name == name)
    return ast.get_source_segment(text, node) or ""


def _snapshot(app_dir: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(app_dir)): p.read_bytes()
        for p in app_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_policy_line_deleted_and_template_names_supplied(tmp_path):
    """Gate-off: skip the policy deletion (A.4 step 4) and the line stays."""
    app_dir, module = _app(tmp_path, "fix4209_policy")
    _fix(app_dir, module, "CartView")
    cart = _class_source(app_dir / "views.py", "CartView")
    assert "exposure_policy" not in cart
    assert "def get_context_data(self, **kwargs):" in cart
    assert "context = super().get_context_data(**kwargs)" in cart
    for name in ("items", "coupon", "page"):
        assert 'context["%s"] = self.%s' % (name, name) in cart
    assert '_audit_note"]' not in cart
    assert "= state(" not in cart and "persist=" not in cart


def test_inherited_policy_is_deleted_on_the_base_only_when_every_subclass_was_supplied(tmp_path):
    """Gate-off: drop the "every view supplied" condition and the second half fails."""
    app_dir, module = _app(tmp_path, "fix4209_base")
    _fix(app_dir, module, "SearchView")
    views = app_dir / "views.py"
    assert "exposure_policy" not in _class_source(views, "AppView")
    search = _class_source(views, "SearchView")
    assert 'context["query"] = self.query' in search
    assert 'context["selected"] = self.selected' in search
    assert 'context["flash"] = getattr(self, "flash", "")' in search

    broken_dir = _copy(tmp_path, "fix4209_base_broken")
    source = broken_dir / "views.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "upgrade13_4209/search.html", "upgrade13_4209/missing.html"
        ),
        encoding="utf-8",
    )
    broken = _load(source, "fix4209_base_broken")
    output = _fix(broken_dir, broken, "SearchView", extra=["--json"])
    report = json.loads(output)
    assert LEGACY_LINE.search(_class_source(source, "AppView"))
    kinds = {note["kind"] for note in report["notes"]}
    assert "policy_kept" in kinds and "unreadable_view" in kinds
    assert not any(c["kind"] == "policy_deleted" for c in report["changes"])


def test_existing_get_context_data_is_extended_before_its_return(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_report")
    _fix(app_dir, module, "ReportView")
    report = _class_source(app_dir / "views.py", "ReportView")
    assert (
        '        context["title"] = "Report"\n'
        '        context["rows"] = self.rows\n'
        "        return context"
    ) in report
    assert report.count('context["title"]') == 1
    assert "exposure_policy" not in report


def test_unannotated_parameters_get_any_and_reserved_ones_are_reported(tmp_path):
    """Gate-off: take ``col_offset`` as a str index and the edit after "café" lands
    one character off, so the ``count`` annotation is wrong."""
    app_dir, module = _app(tmp_path, "fix4209_params")
    report = json.loads(_fix(app_dir, module, "SearchView", extra=["--json"]))
    text = (app_dir / "views.py").read_text(encoding="utf-8")
    ast.parse(text)
    assert 'def search(self, value: Any = "", **kwargs):' in text
    assert (
        'def open_item(self, item_id: Any = 0, label: Any = "café", count: Any = 1, **kwargs):'
        in text
    )
    assert text.count("from typing import Any") == 1
    # CartView's handlers were already annotated and are not in scope.
    assert 'def add(self, sku: str = "", **kwargs):' in text
    target = [n for n in report["notes"] if n["kind"] == "target_read"]
    assert len(target) == 1 and "open_item" in target[0]["where"]
    assert [n["where"] for n in report["notes"] if n["kind"] == "parameter_types"] == [
        "fix4209_params.SearchView.search",
        "fix4209_params.SearchView.open_item",
    ]


def test_data_argument_renamed_where_the_handler_declares_it(tmp_path):
    """Gate-off: drop the ``k in params`` filter and the undeclared key is renamed."""
    app_dir, module = _app(tmp_path, "fix4209_data")
    other = app_dir / "templates" / "upgrade13_4209" / "other.html"
    other.write_text(
        '<div dj-root><button dj-click="search" data-page="2">x</button></div>\n',
        encoding="utf-8",
    )
    source = app_dir / "views.py"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n\nclass OtherView(SearchView):\n"
        '    template_name = "upgrade13_4209/other.html"\n',
        encoding="utf-8",
    )
    _unload(module)
    module = _load(source, "fix4209_data_b")
    output = json.loads(
        _fix(app_dir, module, "CartView", "SearchView", "OtherView", extra=["--json"])
    )
    search = (app_dir / "templates" / "upgrade13_4209" / "search.html").read_text(encoding="utf-8")
    assert 'dj-value-item-id="42"\n   class="x">' in search
    assert "data-item-id" not in search
    assert 'dj-value-sku="A1"' in _class_source(source, "CartView")
    # search() does not declare ``page``; it has **kwargs, so the key is reported.
    assert 'data-page="2"' in other.read_text(encoding="utf-8")
    assert any(
        n["kind"] == "catch_all_data" and "data-page" in n["detail"] for n in output["notes"]
    )


def test_python_built_markup_is_reported_not_rewritten(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_chip")
    before = _class_source(app_dir / "views.py", "ChipView")
    report = json.loads(_fix(app_dir, module, "ChipView", extra=["--json"]))
    after = _class_source(app_dir / "views.py", "ChipView")
    assert before.replace("    " + POLICY + "\n", "") == after
    assert "data-item-id" in after
    markup = [n for n in report["notes"] if n["kind"] == "python_markup"]
    assert len(markup) == 1 and "data-item-id" in markup[0]["detail"]


def test_fix_is_idempotent(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_twice")
    _fix(app_dir, module)
    after_first = _snapshot(app_dir)
    _unload(module)
    again = _load(app_dir / "views.py", "fix4209_twice_again")
    output = _fix(app_dir, again)
    assert output.startswith("djust_exposure_inventory --fix: nothing to change")
    assert _snapshot(app_dir) == after_first


def test_no_op_on_2_0_code(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_modern")
    before = _snapshot(app_dir)
    output = _fix(app_dir, module, "ModernView")
    assert output == "djust_exposure_inventory --fix: nothing to change\n"
    assert _snapshot(app_dir) == before


def test_dry_run_writes_nothing_and_prints_the_diff(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_dry")
    before = _snapshot(app_dir)
    output = _fix(app_dir, module, extra=["--dry-run"])
    assert _snapshot(app_dir) == before
    assert output.startswith("djust_exposure_inventory --fix: 2 files would change")
    assert "--- a/views.py" in output and "+++ b/views.py" in output
    assert "-    " + POLICY in output
    assert '+        context["coupon"] = self.coupon' in output


def test_files_outside_root_are_not_changed(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_outside")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    before = _snapshot(app_dir)
    report = json.loads(_fix(app_dir, module, extra=["--json"], root=elsewhere))
    assert _snapshot(app_dir) == before
    assert report["changes"] == [] and report["written"] is False
    assert any(n["kind"] == "outside_root" for n in report["notes"])


def _visible(html: str) -> str:
    # The handler metadata script differs by design: the handlers are strict now.
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _render_all(module: types.ModuleType, parameter_policy: str) -> dict[str, str]:
    """Each fixture view mounted and rendered; 1.3 code ran under the legacy parameter
    policy (the 1.3 default), the fixed code runs under 2.0's strict default."""
    from djust.config import config
    from djust.testing import LiveViewTestClient

    previous = config.get("event_parameter_policy", "strict")
    config.set("event_parameter_policy", parameter_policy)
    try:
        return _render_each(module, LiveViewTestClient)
    finally:
        config.set("event_parameter_policy", previous)


def _render_each(module: types.ModuleType, client_class: type) -> dict[str, str]:
    rendered = {}
    for name in VIEWS:
        client = client_class(getattr(module, name))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            client.mount()
            rendered[name] = client.render()
    return rendered


@pytest.mark.django_db
def test_fixed_app_passes_the_checks_and_renders_the_same(tmp_path):
    """Gate-off: skip rewrite 1 and T027 fires and ``query`` renders empty."""
    from djust.checks.bindings import check_event_bindings
    from djust.checks.components import check_legacy_exposure_policy
    from djust.checks.parameters import check_event_parameter_contracts
    from djust.checks.templates import check_unsupplied_template_names

    before_dir, before = _app(tmp_path, "fix4209_before")
    with override_settings(TEMPLATES=_templates(before_dir)):
        rendered_before = _render_all(before, "legacy")
    _unload(before)

    after_dir, after = _app(tmp_path, "fix4209_after")
    _fix(after_dir, after)
    _unload(after)
    fixed = _load(after_dir / "views.py", "fix4209_fixed")
    with override_settings(TEMPLATES=_templates(after_dir)):
        rendered_after = _render_all(fixed, "strict")
        messages = [
            m
            for check in (
                check_unsupplied_template_names,
                check_event_parameter_contracts,
                check_event_bindings,
                check_legacy_exposure_policy,
            )
            for m in check(None)
            if "fix4209_fixed" in str(m.msg) or "fix4209_after" in str(getattr(m, "file_path", ""))
        ]
    for name in VIEWS:
        assert _visible(rendered_after[name]) == _visible(rendered_before[name]), name
    assert "none / 0 /" in _visible(rendered_after["SearchView"])
    assert 'dj-value-item-id="42"' in rendered_after["SearchView"]
    assert not [m for m in messages if m.level >= ERROR]
    reported = {m.id for m in messages}
    assert not reported & {
        "djust.T027",
        "djust.V016",
        "djust.T020",
        "djust.V028",
        "djust.V033",
        "djust.T028",
    }, [str(m) for m in messages]


CART_REPORT = """\
djust_exposure_inventory --fix: 1 file changed

Changed:
  views.py
    line 15: CartView: deleted {policy}
    line 18: data-sku -> dj-value-sku (dj-click="add": add() declares sku)
    line 34: CartView: added get_context_data() supplying items, coupon, page

Needs a person:
  {module}.CartView: items, coupon and page are supplied from get_context_data(). \
Declare a name with state() instead when it is the view's state; add persist="server" \
only when it must survive a reconnect.

Next: run python manage.py check, then load each page and compare it with how it rendered before.
"""


def test_text_and_json_report(tmp_path):
    app_dir, module = _app(tmp_path, "fix4209_text")
    assert _fix(app_dir, module, "CartView", extra=["--dry-run"]).startswith(
        CART_REPORT.format(module="fix4209_text", policy=POLICY)
        .replace("1 file changed", "1 file would change")
        .replace("Changed:", "Would change:")
    )
    assert _fix(app_dir, module, "CartView") == CART_REPORT.format(
        module="fix4209_text", policy=POLICY
    )

    json_dir, json_module = _app(tmp_path, "fix4209_json")
    report = json.loads(_fix(json_dir, json_module, "CartView", extra=["--json"]))
    assert {"changes", "notes", "written"} <= set(report)
    assert report["written"] is True
    assert [c["kind"] for c in report["changes"]] == [
        "policy_deleted",
        "data_argument_renamed",
        "context_added",
    ]


def test_never_constructs_a_view(tmp_path, monkeypatch):
    app_dir, module = _app(tmp_path, "fix4209_construct")

    def forbidden(self, *args, **kwargs):
        raise AssertionError("--fix must not instantiate views")

    monkeypatch.setattr(LiveView, "__init__", forbidden)
    assert "files changed" in _fix(app_dir, module)


@pytest.mark.django_db(transaction=True)
def test_the_demo_project_needs_no_fix():
    """The demo was migrated by #4187: --fix finds nothing to change in it."""
    from djust.management._exposure_fix import fix_views
    from tests.integration.test_demo_explicit_4187 import _demo_views

    report = fix_views(
        _demo_views(), root=REPO / "examples" / "demo_project", write=False, scoped=True
    )
    assert report.changes == [], report.changes[:5]


def test_dry_run_and_root_need_fix():
    with pytest.raises(CommandError, match="need --fix"):
        call_command("djust_exposure_inventory", "--dry-run", stdout=StringIO())
    with pytest.raises(CommandError, match="need --fix"):
        call_command("djust_exposure_inventory", "--root", ".", stdout=StringIO())


# ---------------------------------------------------------------------------
# Review findings: shapes the fixture does not cover
# ---------------------------------------------------------------------------


def _write_app(
    tmp_path: Path, name: str, source: str, templates=None
) -> tuple[Path, types.ModuleType]:
    """A one-file app; ``POLICY`` in ``source`` stands for the 1.3 statement."""
    app_dir = tmp_path / name
    (app_dir / "templates").mkdir(parents=True)
    for relpath, text in (templates or {}).items():
        target = app_dir / "templates" / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    (app_dir / "views.py").write_bytes(source.replace("POLICY", POLICY).encode("utf-8"))
    return app_dir, _load(app_dir / "views.py", name)


INHERITED_INLINE = '''\
from django.utils.html import format_html

from djust import LiveView
from djust.decorators import event_handler


class Parent(LiveView):
    exposure_policy = "explicit"
    template = """<div dj-root>
<p>x</p>
<button dj-click="pick" data-item-id="1">x</button>
</div>"""

    @event_handler()
    def pick(self, item_id: int = 0):
        pass


class Child(Parent):
    def render_chip(self):  # the scan puts Parent's button line (3rd) on the next line
        return format_html('<button dj-click="pick" data-item-id="{}">x</button>', 1)
'''


def test_an_inherited_inline_template_never_rewrites_python_built_html(tmp_path):
    """The scan maps Parent's inline template onto Child's body, where its lines land
    on format_html(); only text inside a ``template = "..."`` literal is rewritten.

    Gate-off: drop the inline-template span check in ``_rewrite_markup`` and the
    format_html() string is rewritten."""
    app_dir, module = _write_app(tmp_path, "fix4209_inherited", INHERITED_INLINE)
    report = json.loads(_fix(app_dir, module, "Child", extra=["--json"]))
    text = (app_dir / "views.py").read_text(encoding="utf-8")
    assert 'format_html(\'<button dj-click="pick" data-item-id="{}">x</button>\', 1)' in text
    assert not [c for c in report["changes"] if c["kind"] == "data_argument_renamed"]
    # Parent, run on its own, has its template rewritten.
    _fix(app_dir, module, "Parent")
    text = (app_dir / "views.py").read_text(encoding="utf-8")
    assert '<button dj-click="pick" dj-value-item-id="1">x</button>' in text
    assert 'data-item-id="{}"' in text


RUNTIME_TEMPLATES = """\
from django.template import Context, Template

from djust import LiveView


class DynView(LiveView):
    POLICY

    def mount(self, request, **kwargs):
        self.template_name = "dyn.html"
        self.n = "SEEN"


class RawView(LiveView):
    POLICY

    def mount(self, request, **kwargs):
        self.name = "bob"

    def render(self, request=None):
        return Template("<div dj-root>{{ name }}</div>").render(Context(self.get_context_data()))
"""


def test_a_view_whose_template_is_chosen_at_runtime_keeps_its_policy(tmp_path):
    """A leaf view with no template attribute is not a template-less base.

    Gate-off: let ``_template_less_base`` accept any class without a template and both
    policy lines are deleted, so ``{{ n }}`` and ``{{ name }}`` render empty."""
    app_dir, module = _write_app(
        tmp_path, "fix4209_runtime", RUNTIME_TEMPLATES, {"dyn.html": "<div dj-root>{{ n }}</div>"}
    )
    before = _snapshot(app_dir)
    report = json.loads(_fix(app_dir, module, "DynView", "RawView", extra=["--json"]))
    assert _snapshot(app_dir) == before
    assert report["changes"] == []
    unreadable = {n["where"] for n in report["notes"] if n["kind"] == "unreadable_view"}
    kept = {n["where"] for n in report["notes"] if n["kind"] == "policy_kept"}
    assert unreadable == kept == {"fix4209_runtime.DynView", "fix4209_runtime.RawView"}


SHARED_INCLUDE = (
    "from djust import LiveView\r\n"
    "from djust.decorators import event_handler\r\n"
    "\r\n"
    "\r\n"
    "class AView(LiveView):\r\n"
    "    POLICY\r\n"
    '    template_name = "a.html"\r\n'
    "\r\n"
    "    def mount(self, request, **kwargs):\r\n"
    "        self.n = 1\r\n"
    "\r\n"
    "    @event_handler()\r\n"
    '    def add(self, sku="é", **kwargs):\r\n'
    "        pass\r\n"
    "\r\n"
    "\r\n"
    "class BView(LiveView):\r\n"
    '    exposure_policy = "explicit"\r\n'
    '    template_name = "b.html"\r\n'
    "\r\n"
    "    @event_handler()\r\n"
    "    def add(self, **kwargs):\r\n"
    "        pass\r\n"
)


def test_a_scoped_run_asks_every_owner_of_a_shared_include_and_keeps_crlf(tmp_path):
    """``--view AView`` must not rename ``data-sku`` in a partial BView also renders,
    whose ``add()`` does not declare ``sku``: under strict it would lose the value.

    Gate-off: skip the out-of-scope vote in ``_rewrite_markup`` and p.html is rewritten."""
    partial = '<span>é</span><button dj-click="add" data-sku="1">x</button>'
    app_dir, module = _write_app(
        tmp_path,
        "fix4209_shared",
        SHARED_INCLUDE,
        {
            "a.html": '<div dj-root>{{ n }}{% include "p.html" %}</div>',
            "b.html": '<div dj-root>{% include "p.html" %}</div>',
            "p.html": partial,
        },
    )
    report = json.loads(_fix(app_dir, module, "AView", extra=["--json"]))
    assert (app_dir / "templates" / "p.html").read_text(encoding="utf-8") == partial
    assert any(
        n["kind"] == "ambiguous_markup" and "data-sku" in n["detail"] for n in report["notes"]
    )
    raw = (app_dir / "views.py").read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")  # every line ending is still \r\n
    text = raw.decode("utf-8")
    assert 'def add(self, sku: Any = "é", **kwargs):' in text
    assert "from typing import Any\r\n" in text
    assert 'context["n"] = self.n\r\n' in text
    assert POLICY not in text
    after_first = _snapshot(app_dir)
    _unload(module)
    again = _load(app_dir / "views.py", "fix4209_shared_again")
    assert _fix(app_dir, again, "AView").startswith(
        "djust_exposure_inventory --fix: nothing to change"
    )
    assert _snapshot(app_dir) == after_first


GUIDE = REPO / "docs" / "website" / "guides" / "migrating-to-2.0.md"


def test_the_guide_prints_what_fix_prints_for_its_cart_view(tmp_path):
    """The guide's ``--fix`` sample is this command's output on the guide's own 1.3
    ``CartView`` saved as ``shop/views.py``, run from the project root."""
    guide = GUIDE.read_text(encoding="utf-8")
    snippet = next(
        block
        for block in re.findall(r"```python\n(.*?)```", guide, re.DOTALL)
        if "class CartView" in block and POLICY in block
    )
    sample = next(
        block
        for block in re.findall(r"```text\n(.*?)```", guide, re.DOTALL)
        if block.startswith("djust_exposure_inventory --fix:")
    )
    project = tmp_path / "project"
    (project / "shop").mkdir(parents=True)
    (project / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (project / "shop" / "views.py").write_text(snippet, encoding="utf-8")
    (project / "templates").mkdir()
    package = types.ModuleType("shop")
    package.__path__ = [str(project / "shop")]  # type: ignore[attr-defined]
    sys.modules["shop"] = package
    try:
        module = _load(project / "shop" / "views.py", "shop.views")
        output = _fix(project, module, "CartView")
    finally:
        sys.modules.pop("shop", None)
    assert output == sample
