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

import re
from pathlib import Path

import payload_sweep_3774 as sweepmod
import pytest
from django.template import Node
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from payload_drivers_3774 import DRIVERS

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
        return {
            ("a.py", "f"): _entry(1, evidence=[("a.py", "needle")]),
            ("b.py", "g"): _entry(2, evidence=[("b.py", "needle")]),
        }

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
        (tmp_path / "f.py").write_text("def f():\n    x = needle\n")
        key = ("f.py", "f")
        ok = {key: _entry(1, evidence=[("f.py", "needle")])}
        gone = {key: _entry(1, evidence=[("f.py", "needle"), ("f.py", "absent")])}
        nofile = {key: _entry(1, evidence=[("nope.py", "needle")])}
        assert audit.missing_evidence(ok, tmp_path) == []
        assert audit.missing_evidence(gone, tmp_path) == [(key, "f.py", "absent", "f")]
        assert audit.missing_evidence(nofile, tmp_path) == [(key, "nope.py", "needle", "<module>")]

    def test_evidence_is_the_audited_functions_own_code_not_the_file(self, tmp_path):
        """Review of #3821: a file-wide substring check passed when the escape that mattered was
        deleted from the audited function but a twin of it survived elsewhere in the file."""
        (tmp_path / "m.py").write_text(
            "def other():\n"
            "    e_name = conditional_escape(name)\n"
            "\n"
            "def audited():\n"
            "    e_name = name\n"
        )
        key = ("m.py", "audited")
        entry = {key: _entry(1, evidence=[("m.py", "e_name = conditional_escape(name)")])}
        assert audit.missing_evidence(entry, tmp_path) == [
            (key, "m.py", "e_name = conditional_escape(name)", "audited")
        ]

    def test_a_comment_or_a_docstring_is_not_evidence(self, tmp_path):
        (tmp_path / "m.py").write_text(
            "def audited():\n"
            '    """e_name = conditional_escape(name)"""\n'
            "    # e_name = conditional_escape(name)\n"
            "    e_name = name  # e_name = conditional_escape(name)\n"
        )
        key = ("m.py", "audited")
        entry = {key: _entry(1, evidence=[("m.py", "conditional_escape(name)")])}
        assert len(audit.missing_evidence(entry, tmp_path)) == 1

    def test_code_in_the_scope_is_evidence_and_a_named_scope_is_honoured(self, tmp_path):
        (tmp_path / "m.py").write_text(
            "def helper():\n    return escape(v)\n\ndef audited():\n    return helper()\n"
        )
        key = ("m.py", "audited")
        own = [("m.py", "return helper()")]
        scoped = [("m.py", "return escape(v)", "helper")]
        wrong_scope = [("m.py", "return escape(v)")]
        assert audit.missing_evidence({key: _entry(1, evidence=own)}, tmp_path) == []
        assert audit.missing_evidence({key: _entry(1, evidence=scoped)}, tmp_path) == []
        assert len(audit.missing_evidence({key: _entry(1, evidence=wrong_scope)}, tmp_path)) == 1

    def test_an_entry_needs_one_literal_in_its_own_body(self):
        found = {("a.py", "f"): ["f:3 mark_safe() <- x"]}
        helper_only = {("a.py", "f"): _entry(1, evidence=[("a.py", "needle", "helper")])}
        other_file = {("a.py", "f"): _entry(1, evidence=[("elsewhere.py", "needle")])}
        own = {("a.py", "f"): _entry(1, evidence=[("a.py", "needle"), ("a.py", "x", "helper")])}
        assert audit.reconcile(found, helper_only)["no_own_evidence"] == [("a.py", "f")]
        assert audit.reconcile(found, other_file)["no_own_evidence"] == [("a.py", "f")]
        assert audit.reconcile(found, own)["no_own_evidence"] == []


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
# The empirical backstop: drive the audited sinks with a hostile payload, parse the output, and
# prove the sweep RAN each sink (``payload_sweep_3774`` / ``payload_drivers_3774``).
# ---------------------------------------------------------------------------

TAG = sweepmod.TAG
PAYLOAD = sweepmod.PAYLOAD
URL_PAYLOAD = sweepmod.URL_PAYLOAD
injections = sweepmod.injections


@pytest.fixture(scope="module")
def traced_sweep():
    """The whole sweep once, with the lines of every audited file recorded."""
    files = {str(audit.ROOT / key[0]) for key in audit.AUDITED}
    executed: dict[str, set[int]] = {}
    with sweepmod.line_tracer(files, executed):
        result = sweepmod.sweep(DRIVERS)
    return result, executed


class TestTheCheckerCanFail:
    class Vulnerable(Node):
        def __init__(self, kwargs):
            self.kwargs = kwargs

        def render(self, context):
            return mark_safe(
                f'<a title="{self.kwargs["x"]}">{self.kwargs["x"]}</a><a href="{self.kwargs["u"]}">go</a>'
            )

    class Escaped(Vulnerable):
        def render(self, context):
            x = conditional_escape(self.kwargs["x"])
            u = conditional_escape(self.kwargs["u"])
            return mark_safe(f'<a title="{x}">{x}</a><a href="{u}">go</a>')

    def test_it_catches_an_unescaped_attribute_an_unescaped_element_and_a_script_url(self):
        out = self.Vulnerable({"x": PAYLOAD, "u": URL_PAYLOAD}).render(None)
        assert set(injections(out)) == {
            "<a onmouseover=...>",
            "<b id=ZQ>",
            "<svg onload=...>",
            "<a href=javascript:zq(1)...>",
        }

    def test_an_escaped_twin_is_clean_even_with_a_script_url(self):
        # Escaping stops attribute breakout; it does NOT stop ``javascript:`` in an href. That is
        # what safe_url / url_attr are for, and why the url mode exists: it fails any tag that
        # prints a script URL, escaped or not.
        out = self.Escaped({"x": PAYLOAD, "u": "/safe"}).render(None)
        assert injections(out) == []
        assert injections(self.Escaped({"x": PAYLOAD, "u": URL_PAYLOAD}).render(None)) != []

    @pytest.mark.parametrize(
        "markup",
        [
            f'<p>{TAG}"onmouseover={TAG}</p>',  # a quote in text
            '<img src="data:image/png;base64,AAAA">',  # an image data URI is legitimate
            '<a href="/x?next=javascript:1">x</a>',  # not a script URL: the scheme is not first
        ],
    )
    def test_it_does_not_flag_what_is_harmless(self, markup):
        assert injections(markup) == []

    @pytest.mark.parametrize(
        "href", ["javascript:1", " JaVa\tScRiPt:1", "vbscript:1", "data:text/html,<b>"]
    )
    def test_it_flags_every_script_scheme_spelling(self, href):
        assert injections(f'<a href="{href}">x</a>') != []


class TestPayloadSweep:
    def test_no_audited_sink_turns_the_payload_into_markup_or_a_script_url(self, traced_sweep):
        result, _ = traced_sweep
        assert result["found"] == [], f"a payload became markup or a script URL: {result['found']}"
        # A sweep that renders almost nothing proves nothing: most of the tags must have rendered.
        assert result["rendered"] >= 0.85 * result["discovered"], result

    def test_the_sweep_reads_a_real_tag_with_the_payload(self):
        all_keys, numeric = sweepmod.keys()
        from djust.components.templatetags._advanced import RibbonNode

        out = str(sweepmod.render_node(RibbonNode, "str", "markup", all_keys, numeric))
        assert "dj-ribbon" in out and TAG in out and "&quot;onmouseover" in out

    def test_a_number_argument_gets_a_number_so_the_code_after_the_coercion_runs(self):
        # int(PAYLOAD) raising is why slider, number_stepper and others were never reached.
        from djust.components.templatetags._forms import slider

        out = sweepmod.render_function(slider, "numeric_none", "markup")
        assert out is not None and 'class="dj-slider' in str(out)


class TestEverySinkIsReached:
    """The claim "the sweep covers the audited sinks" is checked, not assumed (review of #3821).

    Line coverage is taken over the sweep itself; a sink whose lines never ran is either a hole in
    the sweep (add a driver) or recorded per entry as ``unreached`` with the reason.
    """

    @staticmethod
    def _reached(traced_sweep):
        _, executed = traced_sweep
        out = {}
        for key in audit.AUDITED:
            ran = executed.get(str(audit.ROOT / key[0]), set())
            out[key] = [bool(group & ran) for group in audit.sink_groups(key)]
        return out

    def test_every_audited_sink_runs_unless_the_entry_says_why_not(self, traced_sweep):
        reached = self._reached(traced_sweep)
        unexplained = [
            k for k, flags in reached.items() if not all(flags) and not audit.AUDITED[k].unreached
        ]
        assert unexplained == [], (
            "the payload sweep never executed these sinks: add a driver to "
            f"python/tests/payload_drivers_3774.py, or give the entry an `unreached` reason: {unexplained}"
        )

    def test_an_entry_marked_unreached_really_is(self, traced_sweep):
        reached = self._reached(traced_sweep)
        stale = [k for k, entry in audit.AUDITED.items() if entry.unreached and all(reached[k])]
        assert stale == [], (
            f"marked unreached but the sweep reaches every sink (drop the note): {stale}"
        )

    def test_an_unreached_note_says_something(self):
        thin = [k for k, e in audit.AUDITED.items() if e.unreached and len(e.unreached.split()) < 6]
        assert thin == []

    def test_the_tracer_tells_a_line_that_ran_from_one_that_did_not(self, tmp_path):
        source = tmp_path / "branchy.py"
        source.write_text(
            "def f(flag):\n    if flag:\n        return 'taken'\n    return 'skipped'\n"
        )
        namespace: dict = {}
        executed: dict[str, set[int]] = {}
        with sweepmod.line_tracer({str(source)}, executed):
            exec(compile(source.read_text(), str(source), "exec"), namespace)  # noqa: S102
            namespace["f"](True)
        lines = executed[str(source)]
        assert 3 in lines and 4 not in lines

    def test_sink_groups_are_one_per_flagged_call(self):
        # render_modal has two flagged sinks, RibbonNode.render one: coverage is per sink.
        modal = audit.sink_groups((audit.DC, "render_modal"))
        ribbon = audit.sink_groups((audit.ADV, "RibbonNode.render"))
        assert len(modal) == 2 and len(ribbon) == 1
        assert not modal[0] & modal[1]


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
