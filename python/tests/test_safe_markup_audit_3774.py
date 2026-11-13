"""The audit of every ``mark_safe`` the taint guard flags outside ``djust.theming`` (#3774).

``scripts/lib/safe_markup_taint.py`` (the guard of #3766) flagged 125 sites in ``python/djust``
outside tests and theming. Each one was read: the value traced to where it is escaped, validated or
coerced. The record is ``scripts/lib/safe_markup_audit.py`` (what makes each site safe, plus
evidence that must still be live code in its scope) and ``docs/internal/mark-safe-audit-3774.md`` (the same
table with line numbers, generated). Since #3820 the guard also sees the ``_safe`` / ``safe_html``
wrappers (199 sinks with them) and proves most of them itself, so what is audited here is the 54 sinks in
50 functions it still cannot (``test_safe_markup_guard_3820.py`` holds the guard's own rules).

What each class proves:

* ``TestEverySiteIsAudited`` runs the guard over the whole tree: a flagged site with no entry, a
  function with MORE sinks than audited, an entry with no site (or a different number), an entry
  without a reason or evidence, and evidence that no longer exists in its file all fail.
* ``TestTheAuditRulesCanFail`` feeds ``reconcile`` / ``missing_evidence`` a synthetic baseline for
  each rule: an empty real result proves nothing unless every rule is shown to be able to fire
  (#1468).
* ``TestTheScanReadsTheRightFiles`` shows the scan reads a dirty file anywhere in the tree and skips
  tests and theming.
* ``TestTheEscaperIsRecognisedByOrigin`` pins why ``_charts`` / ``_forms`` import
  ``conditional_escape`` from ``django.utils.html``: the guard trusts an escaper by ORIGIN, so the
  same call through ``._registry`` re-flags every site it escapes.
* ``TestPayloadSweep`` is the empirical backstop for "safe": every component tag AND every Rust-native
  handler (#3820) is rendered with a hostile payload in every argument it reads, in seven shapes
  (``payload_sweep_3774.SHAPES``), and the output is PARSED. It covers what the guard now proves too,
  so a clean verdict from the guard is still run. A payload that became an attribute, an event handler or an element fails,
  including one interpolated raw inside ``<textarea>`` or an SVG ``<title>`` (the payload closes
  both first, #3827). Its own checker is shown to catch a deliberately unescaped tag.
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
from tests import sweep_sample

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/internal/mark-safe-audit-3774.md"


def _entry(sites: int = 1, reason: str = "why", evidence=(("f.py", "needle"),)) -> audit.Audit:
    return audit.Audit(sites, reason, tuple(evidence))


# A whole-tree pin: runs on PRs and in the merge queue whatever its recorded
# duration (#4051, tests/slow_tier.py).
@pytest.mark.pr_tier
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

    # The ways of satisfying a textual evidence literal without the escape being there (#3827).
    # Each body keeps ``e = escape(v)`` where it cannot run; the literal is present, the escape is not.
    DEAD = {
        "a string dropped into the body": "    'e = escape(v)'\n    e = v\n",
        "an f-string dropped into the body": "    f'e = escape(v)'\n    e = v\n",
        "an if False branch": "    if False:\n        e = escape(v)\n    e = v\n",
        "an if 0 branch": "    if 0:\n        e = escape(v)\n    e = v\n",
        "an if not True branch": "    if not True:\n        e = escape(v)\n    e = v\n",
        "a while False body": "    while False:\n        e = escape(v)\n    e = v\n",
        "the else of if True": "    if True:\n        e = v\n    else:\n        e = escape(v)\n",
        "code after a return": "    return v\n    e = escape(v)\n",
        "code after a raise": "    raise ValueError\n    e = escape(v)\n",
        "a nested def nothing calls": "    def never():\n        return escape(v)\n    e = v\n",
        "a nested def called only from a dead branch": (
            "    def never():\n        return escape(v)\n    if False:\n        never()\n    e = v\n"
        ),
        "a nested def called only by another nested def nothing calls": (
            "    def inner():\n        return escape(v)\n"
            "    def outer():\n        return inner()\n    e = v\n"
        ),
        "a nested def that only calls itself": (
            "    def loop(n):\n        return loop(n - 1) or escape(v)\n    e = v\n"
        ),
    }

    @pytest.mark.parametrize("what", sorted(DEAD))
    def test_a_literal_that_cannot_run_is_not_evidence(self, tmp_path, what):
        (tmp_path / "m.py").write_text("def audited(v):\n" + self.DEAD[what])
        key = ("m.py", "audited")
        entry = {key: _entry(1, evidence=[("m.py", "escape(v)")])}
        assert audit.missing_evidence(entry, tmp_path) == [(key, "m.py", "escape(v)", "audited")]

    #: The same literal where it CAN run: each of these must still count, or the rule above would
    #: be satisfied by deleting every entry's evidence.
    LIVE = {
        "plain code": "    e = escape(v)\n",
        "an if on a name": "    if flag:\n        e = escape(v)\n    else:\n        e = v\n",
        "the body of if True": "    if True:\n        e = escape(v)\n",
        "the else of if False": "    if False:\n        e = v\n    else:\n        e = escape(v)\n",
        "the body of a loop": "    for _ in v:\n        e = escape(v)\n",
        "code before a return": "    e = escape(v)\n    return e\n",
        "a nested def that is called": "    def go():\n        return escape(v)\n    e = go()\n",
        "a nested def passed along": "    def go(x):\n        return escape(v)\n    e = list(map(go, v))\n",
        "a nested def that is returned": "    def go():\n        return escape(v)\n    return go\n",
        "a decorated nested def": "    @register\n    def go():\n        return escape(v)\n",
        "a method of a nested class": (
            "    class N:\n        def render(self):\n            return escape(v)\n    e = N()\n"
        ),
        "a nested def called from a nested def that is called": (
            "    def inner():\n        return escape(v)\n"
            "    def outer():\n        return inner()\n    e = outer()\n"
        ),
        "an except handler": "    try:\n        e = v\n    except KeyError:\n        e = escape(v)\n",
    }

    @pytest.mark.parametrize("what", sorted(LIVE))
    def test_the_same_literal_where_it_can_run_still_counts(self, tmp_path, what):
        (tmp_path / "m.py").write_text("def audited(v):\n" + self.LIVE[what])
        key = ("m.py", "audited")
        entry = {key: _entry(1, evidence=[("m.py", "escape(v)")])}
        assert audit.missing_evidence(entry, tmp_path) == []

    def test_what_the_ast_cannot_see_is_still_counted_and_the_sweep_is_the_check(self, tmp_path):
        """DOCUMENTED LIMIT (#3827): whether a referenced branch, callback or lambda ever runs is a
        runtime fact. These satisfy the literal check although the escape never executes."""
        (tmp_path / "m.py").write_text(
            "def audited(v, flag=False):\n"
            "    if flag and not flag:\n"
            "        e = escape(v)\n"
            "    never = lambda: escape(v)\n"
            "    e = v\n"
        )
        key = ("m.py", "audited")
        entry = {key: _entry(1, evidence=[("m.py", "escape(v)")])}
        assert audit.missing_evidence(entry, tmp_path) == []

    def test_dead_code_keeps_its_line_structure(self, tmp_path):
        # Blanking must not move a line: the generated table resolves every literal to a line.
        (tmp_path / "m.py").write_text(
            "def audited(v):\n    if False:\n        x = 1\n        y = 2\n    return escape(v)\n"
        )
        key = ("m.py", "audited")
        assert audit.evidence_line(key, ("m.py", "escape(v)"), tmp_path) == 5

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

    @pytest.mark.parametrize("module", ["_charts", "_forms"])
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


#: Tiers (#4074): the payload sweep is ``@pytest.mark.sweep``, so before merge it drives a seeded
#: sample of these units (every unit nightly); the two tests that ask whether EVERY audited sink
#: ran need the whole sweep and are ``nightly``. The site audit, the doc table and the rule
#: tests run in full on every pull request.
SWEEP_POPULATIONS = {"payload-sweep/units": lambda: sweepmod.units(DRIVERS)}
#: Units a sampled run draws beyond the ones a change forces (about a minute).
SWEEP_BUDGET = 60
#: The sweep's own code: a change to it drives every unit even in a sampled run.
SHARED_PATHS = (
    "python/tests/payload_sweep_3774.py",
    "python/tests/payload_drivers_3774.py",
    "python/tests/test_safe_markup_audit_3774.py",
    "tests/sweep_sample.py",
)


def _unit_paths(unit: str) -> list[str]:
    """``kind:package.module.Name`` -> the module's file."""
    kind, _, dotted = unit.partition(":")
    if kind == "driver":
        return []
    module = dotted.rsplit(".", 1)[0]
    return ["python/" + module.replace(".", "/") + ".py"]


def _sampled_units() -> set[str] | None:
    all_units = sweepmod.units(DRIVERS)
    chosen = sweep_sample.select(
        "payload-sweep/units",
        all_units,
        budget=SWEEP_BUDGET,
        # the named drivers each pin a sink the generic drivers could not reach: always run
        always=[u for u in all_units if u.startswith("driver:")],
        paths=_unit_paths,
        shared=SHARED_PATHS,
    )
    return None if len(chosen) == len(all_units) else set(chosen)


@pytest.fixture(scope="module")
def traced_sweep():
    """The whole sweep once (a sample of it before merge), with the lines of every audited file
    recorded."""
    files = {str(audit.ROOT / key[0]) for key in audit.AUDITED}
    executed: dict[str, set[int]] = {}
    only = _sampled_units()
    with sweepmod.line_tracer(files, executed):
        result = sweepmod.sweep(DRIVERS, only=only)
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

    class VulnerableHandler:
        """A Rust-native handler that prints an operand raw: the sweep must see it."""

        def render(self, args, context):
            from djust.components.rust_handlers import _parse_args

            kw = _parse_args(args, context)
            return mark_safe(f'<a title="{kw["x"]}">{kw["x"]}</a>')

    def test_it_catches_an_unescaped_native_handler(self):
        all_keys, numeric = sweepmod.keys()
        out = sweepmod.render_handler(
            self.VulnerableHandler(), False, "str", "markup", all_keys | {"x"}, numeric
        )
        assert set(injections(out)) >= {"<a onmouseover=...>", "<b id=ZQ>", "<svg onload=...>"}

    def test_an_escaped_twin_is_clean_even_with_a_script_url(self):
        # Escaping stops attribute breakout; it does NOT stop ``javascript:`` in an href. That is
        # what safe_url / url_attr are for, and why the url mode exists: it fails any tag that
        # prints a script URL, escaped or not.
        out = self.Escaped({"x": PAYLOAD, "u": "/safe"}).render(None)
        assert injections(out) == []
        assert injections(self.Escaped({"x": PAYLOAD, "u": URL_PAYLOAD}).render(None)) != []

    # A raw value INSIDE an element whose contents the parser keeps as text (RCDATA, #3827). A
    # browser ends <textarea> / <title> at the first matching end tag in that text, so the
    # payload has to close them before its own element or it is invisible here.
    RCDATA = {
        "a textarea": "<textarea name='n'>{x}</textarea>",
        "an svg title": "<svg><rect><title>{x}</title></rect></svg>",
        "a title and then a textarea": "<svg><title>{x}</title></svg><textarea>{x}</textarea>",
    }

    @pytest.mark.parametrize("where", sorted(RCDATA))
    def test_it_catches_a_raw_value_inside_a_textarea_or_an_svg_title(self, where):
        assert "<svg onload=...>" in injections(self.RCDATA[where].format(x=PAYLOAD))

    @pytest.mark.parametrize("where", sorted(RCDATA))
    def test_the_escaped_value_inside_them_is_clean(self, where):
        assert injections(self.RCDATA[where].format(x=conditional_escape(PAYLOAD))) == []

    @pytest.mark.parametrize("where", sorted(RCDATA))
    def test_without_the_closers_the_same_raw_value_is_invisible(self, where):
        """The blind spot the closers fix, shown rather than asserted: the payload as it was before
        #3827 is inert text in these elements, so a raw interpolation went unreported."""
        assert sweepmod.RCDATA_CLOSERS in PAYLOAD
        old_payload = PAYLOAD.replace(sweepmod.RCDATA_CLOSERS, "")
        assert injections(self.RCDATA[where].format(x=old_payload)) == []

    def test_the_closers_come_before_the_element_they_let_through(self):
        assert (
            PAYLOAD.index("</textarea>") < PAYLOAD.index("</title>") < PAYLOAD.index("<svg onload")
        )

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


# Mark each reader explicitly: once they share a shard, only the first
# reader's recorded duration includes the expensive fixture setup.
class TestPayloadSweep:
    @pytest.mark.sweep("payload-sweep/units")
    def test_no_audited_sink_turns_the_payload_into_markup_or_a_script_url(self, traced_sweep):
        result, _ = traced_sweep
        assert result["found"] == [], f"a payload became markup or a script URL: {result['found']}"
        # A sweep that renders almost nothing proves nothing: most of the tags must have rendered
        # (half of a sample: a small draw can land on the quiet ones).
        share = 0.85 if not sweep_sample.is_sampled() else 0.5
        assert result["rendered"] >= share * result["sampled"], result
        # ... and the same for the Rust-native handlers (#3820). Since 2.0 only the kept native
        # twins remain (``NATIVE_INLINE_HANDLERS`` / ``NATIVE_BLOCK_HANDLERS``).
        from djust.components import rust_handlers as rh

        kept = len(rh.NATIVE_INLINE_HANDLERS) + len(rh.NATIVE_BLOCK_HANDLERS)
        assert result["handlers_discovered"] == kept, result
        assert result["handlers_rendered"] >= share * result["handlers_sampled"], result

    def test_the_sweep_reads_a_real_handler_with_the_payload(self):
        from djust.components.rust_handlers import AppShellHandler

        all_keys, numeric = sweepmod.keys()
        out = str(
            sweepmod.render_handler(AppShellHandler(), True, "str", "markup", all_keys, numeric)
        )
        assert "dj-app-shell" in out and TAG in out and "&quot;onmouseover" in out

    def test_a_block_handler_is_given_the_body_the_engine_gives_it(self):
        from djust.components.rust_handlers import AppContentHandler

        all_keys, numeric = sweepmod.keys()
        out = sweepmod.render_handler(AppContentHandler(), True, "str", "markup", all_keys, numeric)
        assert out is not None and TAG in str(out)

    def test_the_sweep_reads_a_real_tag_with_the_payload(self):
        all_keys, numeric = sweepmod.keys()
        # (RibbonNode until #4002 removed it.)
        from djust.components.templatetags.djust_components import CalloutNode

        out = str(sweepmod.render_node(CalloutNode, "str", "markup", all_keys, numeric))
        assert "dj-callout" in out and TAG in out and "&quot;onmouseover" in out

    def test_a_number_argument_gets_a_number_so_the_code_after_the_coercion_runs(self):
        # int(PAYLOAD) raising is why slider, number_stepper and others were never reached.
        from djust.components.templatetags._forms import slider

        out = sweepmod.render_function(slider, "numeric_none", "markup")
        assert out is not None and 'class="dj-slider' in str(out)


def _needs_every_unit() -> None:
    if _sampled_units() is not None:
        pytest.skip("asks whether EVERY audited sink ran, so it needs the whole sweep (#4074)")


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

    @pytest.mark.nightly
    def test_every_audited_sink_runs_unless_the_entry_says_why_not(self, traced_sweep):
        _needs_every_unit()
        reached = self._reached(traced_sweep)
        unexplained = [
            k for k, flags in reached.items() if not all(flags) and not audit.AUDITED[k].unreached
        ]
        assert unexplained == [], (
            "the payload sweep never executed these sinks: add a driver to "
            f"python/tests/payload_drivers_3774.py, or give the entry an `unreached` reason: {unexplained}"
        )

    @pytest.mark.nightly
    def test_an_entry_marked_unreached_really_is(self, traced_sweep):
        _needs_every_unit()
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
        # render_modal has two flagged sinks, render_icon one: coverage is per sink.
        # (RibbonNode.render, then OrgChartNode.render, were the one-sink example until #3846
        # proved the first and #4002 removed the second.)
        modal = audit.sink_groups((audit.DC, "render_modal"))
        org = audit.sink_groups(("python/djust/components/icons.py", "render_icon"))
        assert len(modal) == 2 and len(org) == 1
        assert not modal[0] & modal[1]


# ---------------------------------------------------------------------------
# The generated table
# ---------------------------------------------------------------------------

_LINE = re.compile(r"(?<=\.py):\d+")


def _without_lines(text: str) -> str:
    return _LINE.sub("", text)


# A whole-tree pin: runs on PRs and in the merge queue whatever its recorded
# duration (#4051, tests/slow_tier.py).
@pytest.mark.pr_tier
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
