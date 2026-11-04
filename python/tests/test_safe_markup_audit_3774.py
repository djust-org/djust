"""The audit of every ``mark_safe`` the taint guard flags outside ``djust.theming`` (#3774).

``scripts/lib/safe_markup_taint.py`` (the guard of #3766) flagged 125 sites in ``python/djust``
outside tests and theming. Each one was read: the value traced to where it is escaped, validated or
coerced. The record is ``scripts/lib/safe_markup_audit.py`` (what makes each site safe, plus
evidence that must still exist verbatim) and ``docs/internal/mark-safe-audit-3774.md`` (the same
table with line numbers, generated).

What each class proves:

* ``TestEverySiteIsAudited`` runs the guard over the whole tree: a flagged site with no entry, a
  function with MORE sinks than audited, an entry with no site (or a different number), an entry
  without a reason or evidence, and evidence that no longer exists in its file all fail.
* ``TestTheAuditRulesCanFail`` feeds ``reconcile`` / ``missing_evidence`` a synthetic baseline for
  each rule: an empty real result proves nothing unless every rule is shown to be able to fire
  (#1468).
* ``TestTheScanReadsTheRightFiles`` shows the scan reads a dirty file anywhere in the tree and skips
  tests and theming.
* ``TestTheEscaperIsRecognisedByOrigin`` pins why ``_advanced`` / ``_charts`` / ``_forms`` import
  ``conditional_escape`` from ``django.utils.html``: the guard trusts an escaper by ORIGIN, so the
  same call through ``._registry`` re-flags every site it escapes.
* ``TestPayloadSweep`` is the empirical backstop for "safe": every component tag is rendered with a
  hostile payload in every argument it reads, in four shapes, and the output is PARSED. A payload
  that became an attribute, an event handler or an element fails. Its own checker is shown to catch
  a deliberately unescaped tag.
* ``TestTheDocMatchesTheAudit`` keeps the generated table honest.

Refs #3774, #3766, #3775, #1859, #1468, #1197.
"""

from __future__ import annotations

import importlib
import inspect
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Node, NodeList
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from scripts.lib import safe_markup_audit as audit
from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/internal/mark-safe-audit-3774.md"


def _entry(sites: int = 1, reason: str = "why", evidence=(("f.py", "needle"),)) -> audit.Audit:
    return audit.Audit(sites, reason, tuple(evidence))


class TestEverySiteIsAudited:
    def test_the_tree_has_files_to_scan(self):
        names = {p.name for p in audit.package_files()}
        assert {"djust_components.py", "live_tags.py", "icons.py", "forms.py"} <= names

    def test_every_flagged_site_is_audited_and_every_entry_is_current(self):
        problems = audit.reconcile(audit.scan(), audit.AUDITED)
        assert problems["unaudited"] == [], (
            "markup built from a raw value and marked safe, not in the audit (escape it, or build "
            "it with format_html; CLAUDE.md Security Rules 1; if it is safe, add an entry to "
            f"scripts/lib/safe_markup_audit.py with the reason and evidence): {problems['unaudited']}"
        )
        assert problems["more_sites"] == [], (
            f"a sink was added to an audited function (audit it): {problems['more_sites']}"
        )
        assert problems["stale"] == [], (
            f"audited but no longer flagged: delete: {problems['stale']}"
        )
        assert problems["fewer_sites"] == [], (
            f"audited more sinks than are flagged now: update the count: {problems['fewer_sites']}"
        )
        assert problems["no_reason"] == [], f"an entry needs a reason: {problems['no_reason']}"
        assert problems["no_evidence"] == [], f"an entry needs evidence: {problems['no_evidence']}"

    def test_the_evidence_still_exists(self):
        missing = audit.missing_evidence(audit.AUDITED)
        assert missing == [], f"evidence no longer in its file (re-read the site): {missing}"

    def test_every_audited_function_exists(self):
        gone = [k for k in audit.AUDITED if audit.function_line(k[0], k[1]) is None]
        assert gone == []


class TestTheAuditRulesCanFail:
    FOUND = {
        ("a.py", "f"): ["f:3 mark_safe() <- x"],
        ("b.py", "g"): ["g:9 mark_safe() <- y", "g:12 mark_safe() <- z"],
    }

    def _audited(self):
        return {("a.py", "f"): _entry(1), ("b.py", "g"): _entry(2)}

    def test_a_clean_baseline_has_no_problems(self):
        problems = audit.reconcile(self.FOUND, self._audited())
        assert not any(problems.values())

    def test_a_flagged_site_with_no_entry_is_unaudited(self):
        audited = self._audited()
        del audited[("b.py", "g")]
        assert [k for k, _ in audit.reconcile(self.FOUND, audited)["unaudited"]] == [("b.py", "g")]

    def test_a_sink_added_to_an_audited_function_is_caught(self):
        # The old allowlist was keyed (path, function) only: a THIRD sink in ``g`` would have been
        # waved through by the entry written for two.
        found = {**self.FOUND, ("b.py", "g"): [*self.FOUND[("b.py", "g")], "g:20 mark_safe() <- w"]}
        problems = audit.reconcile(found, self._audited())
        assert [(k, n) for k, n, _ in problems["more_sites"]] == [(("b.py", "g"), 2)]

    def test_an_entry_with_no_site_is_stale(self):
        problems = audit.reconcile(self.FOUND, {**self._audited(), ("c.py", "h"): _entry()})
        assert problems["stale"] == [("c.py", "h")]

    def test_an_entry_with_more_sinks_than_are_flagged_is_caught(self):
        problems = audit.reconcile(self.FOUND, {**self._audited(), ("b.py", "g"): _entry(3)})
        assert problems["fewer_sites"] == [(("b.py", "g"), 3, 2)]

    def test_an_entry_needs_a_reason_and_evidence(self):
        audited = {**self._audited(), ("a.py", "f"): _entry(1, reason="  ", evidence=())}
        problems = audit.reconcile(self.FOUND, audited)
        assert problems["no_reason"] == [("a.py", "f")]
        assert problems["no_evidence"] == [("a.py", "f")]

    def test_evidence_that_is_not_in_the_file_is_reported(self, tmp_path):
        (tmp_path / "f.py").write_text("x = needle\n")
        ok = {("a.py", "f"): _entry(1, evidence=[("f.py", "needle")])}
        gone = {("a.py", "f"): _entry(1, evidence=[("f.py", "needle"), ("f.py", "absent")])}
        nofile = {("a.py", "f"): _entry(1, evidence=[("nope.py", "needle")])}
        assert audit.missing_evidence(ok, tmp_path) == []
        assert audit.missing_evidence(gone, tmp_path) == [(("a.py", "f"), "f.py", "absent")]
        assert audit.missing_evidence(nofile, tmp_path) == [(("a.py", "f"), "nope.py", "needle")]


class TestTheScanReadsTheRightFiles:
    DIRTY = (
        "from django.utils.safestring import mark_safe\n"
        'def f(x):\n    return mark_safe(f"<a {x}>")\n'
    )

    def _tree(self, tmp_path):
        base = tmp_path / "python/djust"
        for rel in (
            "components/templatetags/deep.py",
            "top.py",
            "tests/test_dirty.py",
            "theming/t.py",
            "components/test_inline.py",
            "conftest.py",
        ):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(self.DIRTY)
        return tmp_path

    def test_a_dirty_file_anywhere_is_found_and_tests_and_theming_are_skipped(self, tmp_path):
        found = audit.scan(self._tree(tmp_path))
        assert sorted(found) == [
            ("python/djust/components/templatetags/deep.py", "f"),
            ("python/djust/top.py", "f"),
        ]


class TestTheEscaperIsRecognisedByOrigin:
    DIRECT = (
        "from django.utils.html import conditional_escape\n"
        "from django.utils.safestring import mark_safe\n"
        'def f(x):\n    e = conditional_escape(x)\n    return mark_safe(f"<a>{e}</a>")\n'
    )
    REEXPORTED = DIRECT.replace("from django.utils.html import", "from ._registry import")

    def test_the_same_escape_is_clean_from_django_and_flagged_through_a_reexport(self):
        assert find_unsafe_markup(self.DIRECT) == []
        assert [f.function for f in find_unsafe_markup(self.REEXPORTED)] == ["f"]

    @pytest.mark.parametrize("module", ["_advanced", "_charts", "_forms"])
    def test_the_tag_modules_import_the_escaper_from_django(self, module):
        text = (ROOT / f"python/djust/components/templatetags/{module}.py").read_text()
        assert "from django.utils.html import conditional_escape" in text
        registry_import = re.search(
            r"from \._registry import \((.*?)\)|from \._registry import ([^\n]*)", text, re.S
        )
        assert registry_import is not None
        assert "conditional_escape" not in (registry_import.group(1) or registry_import.group(2))


# ---------------------------------------------------------------------------
# The empirical backstop: render every component tag with a hostile payload and parse the output.
# ---------------------------------------------------------------------------

TAG = "ZQ"
PAYLOAD = (
    TAG + '"onmouseover=' + TAG + "<b id=" + TAG + ">'" + TAG + "</script><svg onload=" + TAG + ">"
)
TAG_MODULES = (
    "djust.components.templatetags.djust_components",
    "djust.components.templatetags._forms",
    "djust.components.templatetags._advanced",
    "djust.components.templatetags._charts",
    "djust.components.templatetags._dev_tools",
)
SHAPES = ("str", "list_str", "list_dict", "dict")
#: Parameters that are, by contract, already-rendered (escaped) markup rather than data.
RENDERED_PARAMS = frozenset({"content", "body", "children"})


class _Injection(HTMLParser):
    """Records what the payload turned into: an event-handler attribute or an element it opened."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("onmouseover", "onload"):
                self.hits.append(f"<{tag} {name}=...>")
        if tag == "b" and dict(attrs).get("id") == TAG:
            self.hits.append("<b id=ZQ>")

    handle_startendtag = handle_starttag


#: What an UNESCAPED payload looks like as raw text. Escaped, each starts with ``&quot;`` / ``&lt;``
#: / ``&#x27;`` instead, so output without one of these cannot hold an injection and is not parsed
#: (parsing every output is the slow part of the sweep).
_RAW = ('"onmouseover=' + TAG, "<b id=" + TAG, "<svg onload=" + TAG, "'" + TAG + "<")


def injections(markup) -> list[str]:
    text = str(markup)
    if not any(raw in text for raw in _RAW):
        return []
    parser = _Injection()
    parser.feed(text)
    parser.close()
    return parser.hits


def _keys() -> tuple[set[str], set[str]]:
    """Every kwarg name the tags read, and the ones they ``int()`` straight away."""
    keys: set[str] = set()
    numeric: set[str] = set()
    for name in TAG_MODULES:
        src = inspect.getsource(importlib.import_module(name))
        keys |= set(re.findall(r'kw\.get\(\s*"(\w+)"', src))
        keys |= set(re.findall(r'nkw\.get\(\s*"(\w+)"', src))
        numeric |= set(re.findall(r'int\(\s*kw\.get\(\s*"(\w+)"', src))
    return keys, numeric


def _shape(shape: str, keys: set[str], numeric: set[str]):
    row = {k: (3 if k in numeric else PAYLOAD) for k in keys}
    return {
        "str": PAYLOAD,
        "list_str": [PAYLOAD, PAYLOAD],
        "list_dict": [dict(row), dict(row, children=[dict(row)])],
        "dict": dict(row),
    }[shape]


def render_node(cls, shape: str, keys: set[str], numeric: set[str]):
    """Instantiate a tag ``Node`` with the payload in every kwarg, render it; ``None`` if it cannot."""
    kwargs = {k: (3 if k in numeric else _shape(shape, keys, numeric)) for k in keys}
    args = []
    for name in (p for p in inspect.signature(cls.__init__).parameters if p != "self"):
        args.append(kwargs if name == "kwargs" else NodeList() if name == "nodelist" else PAYLOAD)
    try:
        return cls(*args).render(Context({}))
    except Exception:  # noqa: BLE001 - a tag that rejects the payload is not rendering it
        return None


def render_function(func, shape: str, keys: set[str], numeric: set[str]):
    params = {}
    for name, p in inspect.signature(func).parameters.items():
        if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        if name == "context":
            params[name] = Context({})
        elif name in RENDERED_PARAMS:
            params[name] = conditional_escape(PAYLOAD)
        else:
            params[name] = _shape(shape, keys, numeric)
    try:
        return func(**params)
    except Exception:  # noqa: BLE001
        return None


def sweep() -> tuple[list[tuple], int, int]:
    """``(injections found, nodes rendered, nodes discovered)`` over every tag module."""
    keys, numeric = _keys()
    found: list[tuple] = []
    rendered = discovered = 0
    for module_name in TAG_MODULES:
        module = importlib.import_module(module_name)
        for name, obj in inspect.getmembers(module):
            if getattr(obj, "__module__", None) != module_name:
                continue
            if inspect.isclass(obj) and issubclass(obj, Node):
                discovered += 1
                ok = False
                for shape in SHAPES:
                    out = render_node(obj, shape, keys, numeric)
                    if out is None:
                        continue
                    ok = True
                    found += [(module_name, name, shape, h) for h in injections(out)]
                rendered += ok
            elif inspect.isfunction(obj) and not name.startswith(("_", "do_")):
                for shape in SHAPES:
                    out = render_function(obj, shape, keys, numeric)
                    if isinstance(out, str):
                        found += [(module_name, name, shape, h) for h in injections(out)]
    return found, rendered, discovered


class TestPayloadSweep:
    def test_the_checker_catches_an_unescaped_attribute_and_an_unescaped_element(self):
        class Vulnerable(Node):
            def __init__(self, kwargs):
                self.kwargs = kwargs

            def render(self, context):
                return mark_safe(f'<a title="{self.kwargs["x"]}">{self.kwargs["x"]}</a>')

        class Escaped(Vulnerable):
            def render(self, context):
                value = conditional_escape(self.kwargs["x"])
                return mark_safe(f'<a title="{value}">{value}</a>')

        assert {h for h in injections(Vulnerable({"x": PAYLOAD}).render(None))} == {
            "<a onmouseover=...>",
            "<b id=ZQ>",
            "<svg onload=...>",
        }
        assert injections(Escaped({"x": PAYLOAD}).render(None)) == []

    def test_the_checker_does_not_flag_a_quote_in_text(self):
        assert injections(f'<p>{TAG}"onmouseover={TAG}</p>') == []

    def test_no_component_tag_turns_the_payload_into_markup(self):
        found, rendered, discovered = sweep()
        assert found == [], f"a payload became markup: {found}"
        # A sweep that renders almost nothing proves nothing: most of the tags must have rendered.
        assert rendered >= 0.85 * discovered, (rendered, discovered)

    def test_the_sweep_reads_a_real_tag_with_the_payload(self):
        keys, numeric = _keys()
        from djust.components.templatetags._advanced import RibbonNode

        out = str(render_node(RibbonNode, "str", keys, numeric))
        assert "dj-ribbon" in out and TAG in out and "&quot;onmouseover" in out


# ---------------------------------------------------------------------------
# The generated table
# ---------------------------------------------------------------------------

_LINE = re.compile(r"(?<=\.py):\d+")


def _without_lines(text: str) -> str:
    return _LINE.sub("", text)


class TestTheDocMatchesTheAudit:
    BEGIN = "<!-- audit-table:begin -->"
    END = "<!-- audit-table:end -->"

    def _doc_table(self) -> str:
        text = DOC.read_text()
        assert self.BEGIN in text and self.END in text, "the generated table markers are missing"
        return text.split(self.BEGIN, 1)[1].split(self.END, 1)[0].strip()

    def test_the_table_lists_exactly_the_audited_functions(self):
        # Line numbers move with every unrelated edit to a 10,000-line file; what must not drift
        # is WHICH sites are audited, how many sinks, and why. ``--table`` regenerates the lines.
        assert _without_lines(self._doc_table()) == _without_lines(audit.table())
