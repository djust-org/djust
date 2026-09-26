"""ADR-037 D1: template event bindings checked against their owners (T019-T022).

The fixture views live in a module written to a temporary directory, imported
under a top-level name and unloaded after each test, so no other test's system
check run sees them. Their template files are served from a temporary
directory through ``override_settings(TEMPLATES=...)``.
"""

from __future__ import annotations

import gc
import importlib.util
import json
import sys
import textwrap
import types
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from djust.checks.bindings import (
    _messages,
    binding_reports,
    check_event_bindings,
    coverage,
    summary_line,
)

MODULE = "binding_check_fixture"

SOURCE = '''
from __future__ import annotations

from typing import Any, Optional

import functools

from django import forms
from django.contrib.auth.models import User

from djust import LiveView
from djust.components.base import LiveComponent
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler
from djust.forms import FormMixin, ModelFormMixin


def audited(function):
    @functools.wraps(function)
    def wrapper(self, *args, **kwargs):
        return function(self, *args, **kwargs)

    return wrapper


class Decorated(LiveView):
    template = """<div dj-root><button dj-click="archive">x</button></div>"""

    @event_handler
    @audited
    def archive(self, item_id: int):
        pass


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username"]


class EditUser(ModelFormMixin[User], LiveView):
    """A managed ``self.object``: the framework owns its persistence."""

    model = User
    form_class = UserForm
    template = """<div dj-root><form dj-submit="submit_form">{% csrf_token %}
<input name="username" dj-change="validate_field"></form>{{ object.username }}</div>"""

    def get_queryset(self):
        return User.objects.filter(pk=self.request.user.pk)


class UserList(LiveView):
    """Authorized ORM values rendered from the context, never persisted."""

    template = """<div dj-root>{% for user in users %}
<button dj-click="pick" data-user-id="{{ user.pk }}">{{ user.username }}</button>{% endfor %}</div>"""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["users"] = User.objects.filter(is_active=True)
        return context

    @event_handler
    def pick(self, user_id: int = 0, **kwargs):
        pass


class Handlers:
    @event_handler
    def from_mixin(self, **kwargs):
        pass


class Names(Handlers, LiveView):
    template = """<div dj-root>
<button dj-click="nope">a</button>
<button dj-click="helper">b</button>
<button dj-click="from_mixin">c</button>
<button dj-click="ping">d</button>
<button dj-submit="save(1)">e</button>
<button dj-click="doThing">f</button>
<button dj-click="toggle">g</button>
<button dj-click="selected">h</button>
<button dj-click="on_menu_selected">i</button>
<dialog id="d"><button dj-dialog="close" popovertarget="p">x</button></dialog>
</div>"""
    menu = DropdownMenu(label="Menu", items=[{"label": "A", "value": "a"}])

    def helper(self):
        pass

    @staticmethod
    @event_handler
    def ping(**kwargs):
        pass

    @event_handler
    def save(self, **kwargs):
        pass

    @menu.on.selected
    def on_menu_selected(self, component: DropdownMenu, value: str) -> None:
        pass


class Legacy(LiveView):
    template = """<div dj-root>
<input name="q" dj-input="search">
<input name="q" dj-input="search_open">
<button dj-click="delete">no id</button>
<button dj-click="delete" data-item-id="4">ok</button>
<button dj-click="delete" data-item-id="4" dj-value-item-id="5">dup</button>
<button dj-click="count_to" data-count="abc">bad literal</button>
<button dj-click="count_to" data-count:int="5">typed</button>
<button dj-click="count_to('x')">bad positional</button>
<button dj-click="count_to(1, 2)">extra positional</button>
<button dj-click="delete" data-item-id="4" data-view-id="v">routing</button>
<button dj-click="delete" {% if a %}data-item-id="1"{% endif %}>conditional</button>
</div>"""

    @event_handler
    def search(self, value: str = ""):
        pass

    @event_handler
    def search_open(self, value: str = "", **form_data: Any):
        pass

    @event_handler
    def delete(self, item_id: int):
        pass

    @event_handler
    def count_to(self, count: int):
        pass


class Strict(LiveView):
    template = """<div dj-root>
<button dj-click="pick" data-item-id="4">data only</button>
<button dj-click="pick" dj-value-item-id:int="4">ok</button>
<button dj-click="pick" dj-value-item-id:bool="true">hint misfit</button>
<button dj-click="pick" dj-value-item-id:date="x">unknown hint</button>
<button dj-click="pick" dj-value-item-id="abc">bad literal</button>
<button dj-click="pick" dj-value-item-id:int="4" dj-value-extra="1">extra</button>
<input name="q" dj-input="typed" dj-value-value="x">
<input name="q" dj-input="typed">
<button dj-click="pick" dj-value-item-id:int="4" dj-value-component-id="c">routing</button>
<button dj-click="pick(4)" dj-value-item-id:int="4">twice</button>
</div>"""

    @event_handler(parameter_policy="strict")
    def pick(self, item_id: int) -> None:
        pass

    @event_handler(parameter_policy="strict")
    def typed(self, value: str) -> None:
        pass


class Dynamic(LiveView):
    template = """<div dj-root>
<button dj-click="{{ handler }}">computed</button>
<div data-component-id="c1"><button dj-click="inside">component</button></div>
{% include name %}
<button dj-click="[[&quot;push&quot;, {&quot;event&quot;: &quot;nope_js&quot;}]]">js</button>
</div>
<button dj-click="outside_root">outside</button>"""


class Suppressed(LiveView):
    template = """<div dj-root>
{# noqa: T019 -- rendered by a third-party widget that adds the handler #}
<button dj-click="reasoned">a</button>
<button dj-click="bare">b</button> {# noqa: T019 #}
</div>"""


class Files(LiveView):
    template_name = "fixture/page.html"

    @event_handler
    def shared(self, **kwargs):
        pass


class OtherFiles(LiveView):
    template_name = "fixture/page.html"


class ContactForm(forms.Form):
    email = forms.EmailField()


class FormView(FormMixin, LiveView):
    form_class = ContactForm
    template = """<div dj-root>
<input name="email" dj-change="validate_field">
<input name="emial" dj-change="validate_field">
</div>"""


class Counter(LiveComponent):
    template = """<div><button dj-click="bump">+</button><button dj-poll="host_only">p</button>
<button dj-click="missing_here">m</button></div>"""

    @event_handler
    def bump(self, **kwargs):
        pass


class Exploding(LiveView):
    """Nothing here may run during a check."""

    template = """<div dj-root>{% for item in items %}
<button dj-click="remove" data-item-id="{{ item.pk }}">x</button>{% endfor %}</div>"""

    class Boom:
        def __iter__(self):
            raise AssertionError("a queryset was evaluated")

        def __bool__(self):
            raise AssertionError("a queryset was evaluated")

    items = Boom()

    def mount(self, request, **kwargs):
        raise AssertionError("mount ran")

    def get_context_data(self, **kwargs):
        raise AssertionError("get_context_data ran")

    @property
    def total(self):
        raise AssertionError("a property ran")

    @event_handler
    def remove(self, item_id: int):
        raise AssertionError("a handler ran")
'''

TEMPLATES = {
    "fixture/base.html": (
        "<html><body>\n"
        '<nav><button dj-click="not_live">outside</button></nav>\n'
        "<main dj-root>{% block content %}{% endblock %}</main>\n"
        "</body></html>"
    ),
    "fixture/page.html": (
        '{% extends "fixture/base.html" %}\n'
        "{% block content %}\n"
        '{% include "fixture/part.html" %}\n'
        "{% endblock %}"
    ),
    "fixture/part.html": '<p>part</p>\n<button dj-click="shared">shared</button>\n',
}


def _load(tmp_path) -> types.ModuleType:
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


def _unload(module: types.ModuleType) -> None:
    sys.modules.pop(MODULE, None)
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == MODULE:
            value.abstract = True
    gc.collect()


@pytest.fixture
def fixture(tmp_path):
    templates = tmp_path / "templates"
    for name, text in TEMPLATES.items():
        target = templates / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    module = _load(tmp_path)
    settings = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(templates)],
            "APP_DIRS": True,
        }
    ]
    try:
        with override_settings(TEMPLATES=settings):
            yield module, templates
    finally:
        _unload(module)


def _reports():
    return [r for r in binding_reports() if r.owner.__module__ == MODULE]


def _found(owner: str) -> list[tuple[str, str, int]]:
    """(check id, message tail, line) for one fixture owner."""
    reports = [r for r in _reports() if r.owner.__qualname__ == owner]
    return [(m.id, m.msg.split(": ", 1)[1], m.line_number) for m in _messages(reports)]


def _ids(owner: str) -> list[str]:
    return [check_id for check_id, _msg, _line in _found(owner)]


def _source_line(module: types.ModuleType, text: str) -> int:
    lines = open(module.__file__, encoding="utf-8").read().splitlines()
    return next(i for i, line in enumerate(lines, 1) if text in line)


def _by_line(module, owner):
    return {line: (check_id, msg) for check_id, msg, line in _found(owner)}


# -- T019: names and ownership -------------------------------------------------


def test_names_resolve_only_to_browser_callable_handlers_on_the_owner(fixture):
    module, _ = fixture
    found = _by_line(module, "Names")

    def at(text):
        return found.pop(_source_line(module, text))

    assert at('dj-click="nope"') == (
        "djust.T019",
        "'nope' is not a handler on %s.Names." % MODULE,
    )
    assert at('dj-click="helper"')[1].startswith(
        "'helper' on %s.Names is not an @event_handler" % MODULE
    )
    assert "does not take arguments" in at('dj-submit="save(1)"')[1]
    assert "is not a valid event name" in at('dj-click="doThing"')[1]
    assert at('dj-click="toggle"')[1].startswith("'toggle' is an action of the 'menu' component")
    assert at('dj-click="selected"')[1].startswith(
        "'selected' is an output of the 'menu' component"
    )
    assert "component output callback" in at('dj-click="on_menu_selected"')[1]
    # Inherited, decorated and static handlers resolve; native controls bind nothing.
    assert found == {}


def test_a_component_template_is_checked_against_the_component(fixture):
    module, _ = fixture
    assert _found("Counter") == [
        (
            "djust.T019",
            "'missing_here' is not a handler on %s.Counter." % MODULE,
            _source_line(module, 'dj-click="missing_here"'),
        )
    ]
    (report,) = [r for r in _reports() if r.owner.__qualname__ == "Counter"]
    statuses = {r.binding.name: (r.status, r.reason) for r in report.results}
    assert statuses["host_only"] == ("dynamic", "dj-poll always reaches the host view")


# -- T020-T022 under the legacy policy ------------------------------------------


def test_legacy_arguments(fixture):
    module, _ = fixture
    found = _by_line(module, "Legacy")

    def at(text):
        return found.pop(_source_line(module, text))

    assert at('dj-input="search"') == (
        "djust.T020",
        "dj-input sends 'field', '_target', which search() does not accept.",
    )
    assert at('dj-click="delete">no id') == (
        "djust.T020",
        "delete() requires 'item_id', which this binding never sends.",
    )
    assert at('dj-value-item-id="5"') == (
        "djust.T020",
        "data-item-id and dj-value-item-id both send 'item_id'; the browser keeps only one.",
    )
    assert at('data-count="abc"') == (
        "djust.T021",
        "data-count gives count_to() 'abc' for 'count', which is not a valid int.",
    )
    assert at("count_to('x')")[0] == "djust.T021"
    assert at("count_to(1, 2)") == (
        "djust.T020",
        "dj-click passes 2 positional arguments; count_to() takes 1, and the rest are dropped.",
    )
    assert at('data-view-id="v"') == (
        "djust.T022",
        "data-view-id supplies 'view_id', which the server reads as routing context, "
        "not an argument.",
    )
    # An intentional catch-all, a sent id, a typed literal and a conditional
    # attribute set (whose missing arguments are unknown) report nothing.
    assert found == {}


def test_a_catch_all_is_named_as_unchecked_not_excused(fixture):
    module, _ = fixture
    (report,) = [r for r in _reports() if r.owner.__qualname__ == "Legacy"]
    open_binding = next(r for r in report.results if r.binding.name == "search_open")
    assert open_binding.status == "checked" and open_binding.findings == []


# -- T020-T022 under the strict policy -------------------------------------------


def test_strict_arguments(fixture):
    module, _ = fixture
    found = _by_line(module, "Strict")

    def at(text):
        return found.pop(_source_line(module, text))

    check_id, message = at('data-item-id="4">data only')
    assert check_id == "djust.T020"
    assert message == "pick() requires 'item_id', which this binding never sends."
    assert at(':bool="true"')[0] == "djust.T021"
    assert at(':date="x"')[0] == "djust.T021"
    assert at('dj-value-item-id="abc"') == (
        "djust.T021",
        "dj-value-item-id gives pick() 'abc' for 'item_id', which is not a valid int.",
    )
    assert at('dj-value-extra="1"') == (
        "djust.T020",
        "dj-value-extra sends 'extra', which pick() does not declare.",
    )
    assert at('dj-value-value="x"') == (
        "djust.T020",
        "dj-value-value reuses 'value', which dj-input generates itself; the browser "
        "rejects the event.",
    )
    assert at('dj-value-component-id="c"')[0] == "djust.T022"
    assert at('dj-click="pick(4)"') == (
        "djust.T020",
        "'item_id' is supplied both positionally and by dj-value-item-id:int.",
    )
    assert found == {}


def test_strict_missing_argument_hint_points_at_data_attributes(fixture):
    module, _ = fixture
    (message,) = [
        m
        for m in _messages([r for r in _reports() if r.owner.__qualname__ == "Strict"])
        if m.line_number == _source_line(module, "data only")
    ]
    assert "rename data-item-id to dj-value-item-id" in message.hint


# -- Coverage, locations and dynamic templates ------------------------------------


def test_dynamic_bindings_are_reported_as_coverage_not_findings(fixture):
    module, _ = fixture
    assert _found("Dynamic") == [
        (
            "djust.T019",
            "'nope_js' is not a handler on %s.Dynamic." % MODULE,
            _source_line(module, "nope_js"),
        )
    ]
    (report,) = [r for r in _reports() if r.owner.__qualname__ == "Dynamic"]
    statuses = sorted((r.binding.label.split("=")[0], r.status, r.reason) for r in report.results)
    assert statuses == [
        ("dj-click", "checked", ""),
        ("dj-click", "dynamic", "inside markup a component owns"),
        ("dj-click", "dynamic", "the event name is computed by the template"),
        ("dj-click", "unsupported", "outside the live root"),
    ]
    assert [g.reason for g in report.gaps] == ["{% include %} names a dynamic template"]


def test_includes_and_parents_are_followed_with_their_own_locations(fixture):
    module, templates = fixture
    assert _found("Files") == []
    part = str(templates / "fixture" / "part.html")
    assert _found("OtherFiles") == [
        ("djust.T019", "'shared' is not a handler on %s.OtherFiles." % MODULE, 2)
    ]
    (message,) = _messages([r for r in _reports() if r.owner.__qualname__ == "OtherFiles"])
    assert message.file_path == part
    (report,) = [r for r in _reports() if r.owner.__qualname__ == "Files"]
    nav = next(r for r in report.results if r.binding.name == "not_live")
    assert (nav.status, nav.binding.file, nav.binding.line) == (
        "unsupported",
        str(templates / "fixture" / "base.html"),
        2,
    )


def test_suppression_requires_a_reason(fixture):
    module, _ = fixture
    assert _found("Suppressed") == [
        (
            "djust.T019",
            "'bare' is not a handler on %s.Suppressed. (A noqa comment without a reason "
            "does not suppress T019; write {# noqa: T019 -- <reason> #}.)" % MODULE,
            _source_line(module, 'dj-click="bare"'),
        )
    ]


def test_form_fields_are_checked_against_a_static_form_class(fixture):
    module, _ = fixture
    assert _found("FormView") == [
        (
            "djust.T021",
            "'emial' is not a field of ContactForm, so validate_field() never sees it as one.",
            _source_line(module, 'name="emial"'),
        )
    ]


def test_decorated_handlers_are_checked_by_their_effective_signature(fixture):
    module, _ = fixture
    assert _found("Decorated") == [
        (
            "djust.T020",
            "archive() requires 'item_id', which this binding never sends.",
            _source_line(module, 'dj-click="archive"'),
        )
    ]


def test_managed_objects_and_authorized_querysets_are_not_flagged(fixture):
    """ADR-037 D5: rendering authorized ORM values is not a persistence risk."""
    from django.core.checks import run_checks

    for owner in ("EditUser", "UserList"):
        label = "%s.%s" % (MODULE, owner)
        flagged = [
            m.id
            for m in run_checks(tags=["djust"])
            if label in str(m.msg) or label == getattr(m, "owner", "")
        ]
        # Only the fixture module's placement is reported: it is not in
        # LIVEVIEW_ALLOWED_MODULES (V005), and EditUser declares no login (S005).
        assert set(flagged) <= {"djust.V005", "djust.S005", "djust.V002"}, (owner, flagged)


def test_checking_runs_no_mount_handler_property_or_queryset(fixture):
    assert _found("Exploding") == []


def test_every_message_is_a_warning_with_machine_readable_facts(fixture):
    messages = _messages(_reports())
    assert messages and all(m.level == 30 for m in messages)
    delete = next(m for m in messages if "delete() requires" in m.msg)
    assert delete.owner == "%s.Legacy" % MODULE
    assert delete.binding == 'dj-click="delete"'
    assert delete.expected == ["item_id"]
    assert delete.supplied == []


def test_coverage_object_and_summary(fixture):
    data = coverage(_reports())
    assert set(data) == {"bindings", "owners", "complete_owners", "templates"}
    dynamic = next(t for t in data["templates"] if t["owner"].endswith(".Dynamic"))
    assert dynamic["complete"] is False
    assert dynamic["counts"] == {"checked": 1, "dynamic": 2, "unsupported": 1}
    assert dynamic["gaps"][0]["reason"] == "{% include %} names a dynamic template"
    files = next(t for t in data["templates"] if t["owner"].endswith(".Files"))
    assert files["template"] == "fixture/page.html"
    assert summary_line(data).startswith("Event bindings: ")


def test_registered_check_reports_the_fixture(fixture):
    ids = {m.id for m in check_event_bindings(None) if MODULE in m.msg}
    assert ids == {"djust.T019", "djust.T020", "djust.T021", "djust.T022"}


def test_djust_check_json_carries_the_facts_and_coverage(fixture):
    out = StringIO()
    call_command("djust_check", "--format", "json", stdout=out)
    data = json.loads(out.getvalue())
    assert "coverage" in data and data["coverage"]["bindings"]["checked"] > 0
    entry = next(c for c in data["checks"] if "delete() requires" in c["message"])
    assert entry["owner"] == "%s.Legacy" % MODULE
    assert entry["binding"] == 'dj-click="delete"'
    assert entry["expected"] == ["item_id"] and entry["supplied"] == []


def test_legacy_json_output_is_unchanged(fixture):
    out = StringIO()
    call_command("djust_check", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert set(data) == {"checks", "summary"}
    entry = next(c for c in data["checks"] if "delete() requires" in c["message"])
    assert set(entry) == {"id", "severity", "category", "message", "hint"}


def test_text_output_prints_the_coverage_summary(fixture):
    out = StringIO()
    call_command("djust_check", stdout=out)
    assert "Event bindings: " in out.getvalue()


# -- The directive table against the client ---------------------------------------


def _client_sources() -> str:
    from pathlib import Path

    import djust

    src = Path(djust.__file__).parent / "static" / "djust" / "src"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(src.glob("*.js")))


def test_every_attribute_the_client_reads_is_classified():
    import re

    from djust._template_bindings import DIRECTIVES, NON_EVENT_ATTRIBUTES

    source = _client_sources()
    read = set(re.findall(r"""(?:get|has)Attribute\(\s*['"](dj-[a-z-]+)['"]""", source))
    read |= set(re.findall(r"\[(dj-[a-z-]+)\]", source))
    unclassified = sorted(read - set(DIRECTIVES) - NON_EVENT_ATTRIBUTES)
    assert unclassified == [], "classify these client attributes: %s" % unclassified
    assert not set(DIRECTIVES) & NON_EVENT_ATTRIBUTES


def test_every_directive_is_one_the_client_binds():
    from djust._template_bindings import DIRECTIVES

    source = _client_sources()
    for name in DIRECTIVES:
        family = name.rsplit("-", 1)[0] + "-"
        assert name in source or (family in ("dj-window-", "dj-document-") and family in source), (
            name
        )


def test_rendered_markup_events_use_the_same_parser():
    """The catalogue's component_events (row 14) sees key modifiers and calls."""
    from djust.theming.gallery.catalogue import component_events

    html = (
        '<input dj-keydown.enter="send"><button dj-click="pick(1)">a</button>'
        '<pre>&lt;button dj-click="escaped"&gt;</pre><button dj-click="send">b</button>'
    )
    assert component_events(html) == ["send", "pick"]


def test_the_ai_schema_event_directives_agree_with_the_directive_table():
    """ADR-037 row 21: the schema keeps its prose; its facts match the table."""
    from djust._template_bindings import DIRECTIVES
    from djust.schema import DIRECTIVES as SCHEMA

    documented = set()
    for entry in SCHEMA:
        if entry.get("category") != "event":
            continue
        name = entry["name"]
        if name.endswith("-*"):
            family = [d for d in DIRECTIVES if d.startswith(name[:-1])]
            assert family, name
            documented.update(family)
            continue
        assert name in DIRECTIVES, name
        documented.add(name)
        sent = [p for p in entry.get("params_sent") or [] if " " not in p]
        if sent:
            assert set(sent) == set(DIRECTIVES[name].generated), name
    # Directives the schema does not document yet; the D2 schema work adds them.
    assert set(DIRECTIVES) - documented == {
        "dj-auto-recover",
        "dj-copy-event",
        "dj-dialog-close-event",
        "dj-viewport-bottom",
        "dj-viewport-top",
    }
