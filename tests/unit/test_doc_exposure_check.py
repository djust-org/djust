"""Documented views must work under explicit exposure, the default (#4048).

``scripts/check-doc-exposure.py`` reads every documented ``LiveView`` example
(fenced ``python`` blocks in the docs, strings in ``python/djust``) and reports
the ones that still rely on the 1.x attribute walk: a public ``self.x`` the
template reads and ``get_context_data()`` never supplies. Such an example
parses, imports and mounts; it only renders the name empty, so no other check
notices.

The first test is the repo gate. The rest pin what the scanner calls a finding,
on synthetic documents: each finding has a control that differs only by the
line that fixes it, so a scanner that reported nothing would fail them.

No view class is defined here; the examples are text.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check-doc-exposure.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_doc_exposure_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scanner = _load()

FENCE = "```"


def _classify(tmp_path, monkeypatch, markdown: str) -> list[tuple[str, str, set[str]]]:
    """``(class, kind, names)`` for each view example in ``markdown``."""
    monkeypatch.setattr(scanner, "REPO_ROOT", tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text(textwrap.dedent(markdown).replace("~~~", FENCE), encoding="utf-8")
    return [
        (example.cls, *scanner.classify(example, reads))
        for example, reads in scanner.scan_markdown(doc)
    ]


# --------------------------------------------------------------------------
# The repo gate
# --------------------------------------------------------------------------


def test_no_documented_view_relies_on_the_attribute_walk():
    kinds = scanner.by_kind(scanner.collect())
    found = [
        f"{example.path}:{example.line} {example.cls}: {', '.join(sorted(names))}"
        for example, names in scanner.failures(kinds)
    ]
    assert not found, (
        "These documented views set a public attribute that a template reads and "
        "get_context_data() does not supply (or assign a component in a method). Under "
        "explicit exposure, the default, the name renders empty. Supply it in "
        "get_context_data(); see docs/website/state/explicit-exposure.md.\n  " + "\n  ".join(found)
    )


def test_the_scan_reads_the_documents_it_claims_to():
    """A gate over zero examples would pass; this one reads hundreds."""
    results = scanner.collect()
    paths = {example.path for example, _ in results}
    assert len(results) > 300
    for expected in (
        "README.md",
        "CLAUDE.md",
        "docs/ai/lifecycle.md",
        "docs/website/guides/streaming.md",
        "docs/website/core-concepts/liveview.md",
        "python/djust/schema.py",
    ):
        assert expected in paths, expected


def test_every_backlog_entry_still_has_a_finding():
    """``_COMPONENT_BACKLOG`` excuses known findings; an entry with none left is stale."""
    kinds = scanner.by_kind(scanner.collect())
    component_paths = [example.path for example, _ in kinds.get("component", [])]
    for prefix in scanner._COMPONENT_BACKLOG:
        assert any(path.startswith(prefix) for path in component_paths), (
            f"{prefix} has no component finding left; remove it from _COMPONENT_BACKLOG"
        )


# --------------------------------------------------------------------------
# What is a finding
# --------------------------------------------------------------------------

_COUNTER = """
    ~~~python
    class CounterView(LiveView):
        template_name = "counter.html"

        def mount(self, request, **kwargs):
            self.count = 0
    {supply}
    ~~~

    ~~~html
    <div dj-root>{{{{ count }}}}</div>
    ~~~
"""
_SUPPLY = """
        def get_context_data(self, **kwargs):
            return {**super().get_context_data(**kwargs), "count": self.count}
"""


def test_an_attribute_the_template_reads_and_nothing_supplies_is_reported(tmp_path, monkeypatch):
    assert _classify(tmp_path, monkeypatch, _COUNTER.format(supply="")) == [
        ("CounterView", "blank", {"count"})
    ]
    assert _classify(tmp_path, monkeypatch, _COUNTER.format(supply=_SUPPLY)) == [
        ("CounterView", "ok", set())
    ]


@pytest.mark.parametrize(
    "supply",
    [
        'context = super().get_context_data(**kwargs)\n            context["count"] = self.count\n            return context',
        "context = super().get_context_data(**kwargs)\n            context.update(count=self.count)\n            return context",
        "return super().get_context_data(count=self.count, **kwargs)",
    ],
    ids=["subscript", "update", "super-kwarg"],
)
def test_each_way_of_supplying_a_name_counts(tmp_path, monkeypatch, supply):
    method = f"\n        def get_context_data(self, **kwargs):\n            {supply}\n"
    assert _classify(tmp_path, monkeypatch, _COUNTER.format(supply=method)) == [
        ("CounterView", "ok", set())
    ]


def test_the_inline_template_is_the_one_that_counts(tmp_path, monkeypatch):
    """A view with its own template is not judged by another example's."""
    markdown = """
        ~~~python
        class A(LiveView):
            template = "<div dj-root>{{ label }}</div>"

            def mount(self, request, **kwargs):
                self.label = "a"
                self.rows = []

            def get_context_data(self, **kwargs):
                return {**super().get_context_data(**kwargs), "label": self.label}
        ~~~

        ~~~html
        {% for row in rows %}{{ row }}{% endfor %}
        ~~~
    """
    assert _classify(tmp_path, monkeypatch, markdown) == [("A", "ok", set())]


def test_a_view_is_judged_by_the_template_that_follows_it(tmp_path, monkeypatch):
    markdown = """
        ~~~python
        class First(LiveView):
            def mount(self, request, **kwargs):
                self.projects = []
                self.selected = ""

            def get_context_data(self, **kwargs):
                return {**super().get_context_data(**kwargs), "selected": self.selected}
        ~~~

        ~~~html
        <p>{{ selected }}</p>
        ~~~

        ~~~python
        class Second(LiveView):
            def mount(self, request, **kwargs):
                self.projects = []

            def get_context_data(self, **kwargs):
                return super().get_context_data(**kwargs)
        ~~~

        ~~~html
        {% for project in projects %}{{ project }}{% endfor %}
        ~~~
    """
    assert _classify(tmp_path, monkeypatch, markdown) == [
        ("First", "ok", set()),
        ("Second", "blank", {"projects"}),
    ]


def test_the_1x_publishing_idiom_is_reported_with_no_template_shown(tmp_path, monkeypatch):
    idiom = """
        ~~~python
        class ItemsView(LiveView):
            def mount(self, request, **kwargs):
                self._items = []

            def get_context_data(self, **kwargs):
                {body}
        ~~~
    """
    old = "self.items = self._items\n                return super().get_context_data(**kwargs)"
    new = 'return {**super().get_context_data(**kwargs), "items": self._items}'
    assert _classify(tmp_path, monkeypatch, idiom.format(body=old)) == [
        ("ItemsView", "blank", {"items"})
    ]
    assert _classify(tmp_path, monkeypatch, idiom.format(body=new)) == [("ItemsView", "ok", set())]


def test_a_computed_value_is_an_attribute_like_any_other(tmp_path, monkeypatch):
    markdown = """
        ~~~python
        class TotalsView(LiveView):
            rows = state(default=[])

            @computed
            def total(self):
                return len(self.rows)
        ~~~

        ~~~html
        <p>{{ total }}</p>
        ~~~
    """
    assert _classify(tmp_path, monkeypatch, markdown) == [("TotalsView", "blank", {"total"})]


def test_a_component_assigned_in_a_method_is_reported(tmp_path, monkeypatch):
    markdown = """
        ~~~python
        from djust.components.components import Badge

        class BadgeView(LiveView):
            def mount(self, request, **kwargs):
                {line}

            def get_context_data(self, **kwargs):
                return {{**super().get_context_data(**kwargs), "badge": {value}}}
        ~~~
    """
    wrong = markdown.format(line='self.badge = Badge("New")', value="self.badge")
    right = markdown.format(line='self._badge = Badge("New")', value="self._badge")
    assert _classify(tmp_path, monkeypatch, wrong) == [("BadgeView", "component", {"badge"})]
    assert _classify(tmp_path, monkeypatch, right) == [("BadgeView", "ok", set())]


def test_what_is_not_a_finding(tmp_path, monkeypatch):
    markdown = """
        ~~~python
        class Before(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.count = 0

        class HandlerOnly(LiveView):
            @event_handler
            def increment(self, **kwargs):
                self.count += 1

        class Contact(FormMixin, LiveView):
            form_class = ContactForm

            def form_invalid(self, form):
                self.error_message = "Fix the errors below"

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)

        class Suite(LiveViewSmokeTest):
            count = 3
        ~~~

        ~~~html
        <div dj-root>{{ count }} {{ error_message }}</div>
        ~~~
    """
    assert _classify(tmp_path, monkeypatch, markdown) == [
        ("Before", "legacy", set()),
        ("HandlerOnly", "fragment", set()),
        ("Contact", "ok", set()),
    ]


def test_a_marked_block_is_not_read(tmp_path, monkeypatch):
    assert _classify(tmp_path, monkeypatch, textwrap.dedent(_COUNTER.format(supply=""))) != []
    assert (
        _classify(
            tmp_path,
            monkeypatch,
            "<!-- doc-exposure-check: legacy -->\n"
            + textwrap.dedent(_COUNTER.format(supply="")).lstrip("\n"),
        )
        == []
    )


def test_a_class_in_a_python_string_is_read(tmp_path, monkeypatch):
    """Docstrings and generated text (``schema.py``, the MCP scaffold) teach too."""
    monkeypatch.setattr(scanner, "REPO_ROOT", tmp_path)
    module = tmp_path / "taught.py"
    template = """
EXAMPLE = (
    "class ItemListView(LiveView):\\n"
    "    def mount(self, request, **kwargs):\\n"
    "        self._items = []\\n"
    "\\n"
    "    def get_context_data(self, **kwargs):\\n"
    "{body}"
)
"""
    old = (
        '        self.items = self._items\\n"\n'
        '    "        return super().get_context_data(**kwargs)\\n'
    )
    new = "        return {**super().get_context_data(**kwargs), 'items': self._items}\\n"

    def kinds(body: str) -> list[str]:
        module.write_text(template.format(body=body), encoding="utf-8")
        examples, reads = scanner.scan_python(module)
        return [scanner.classify(example, reads)[0] for example in examples]

    assert kinds(old) == ["blank"]
    assert kinds(new) == ["ok"]
