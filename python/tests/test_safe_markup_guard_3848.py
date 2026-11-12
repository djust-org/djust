"""What the independent review of #3848 found, pinned (#3820, #3848).

* ``TestTheReviewCorpus``: the unsafe functions of the review's corpus (57, plus the misses it pinned
  that the guard catches since the follow-up of #3850, and their data twins), RUN with the payload ``<x>``
  (the execution is the oracle: it must come out raw) and checked against the guard. 27 of them are
  regressions: the first guard flagged them and the head of #3848 did not (a class attribute that
  ``setattr`` replaces, a table changed through ``self.X`` / an alias / ``setdefault`` / ``update``, a
  helper rebound by ``global`` or hidden by a method of the same name, ...).
* ``TestDirectedRows``: ``safe_markup_rows_3848.ROWS``, each with its clean twin.
* ``TestDynamicAttributes``: ``Component`` writes every constructor keyword with ``setattr``, so a class
  attribute of a ``Component`` subclass is a default the caller replaces; the guard learns that from the
  package, not from a list.
* ``TestWrappers``: a function that marks one of its parameters safe is a sink, found by data flow, in the
  guard (same file) and over the package (every wrapper is named).
* ``TestTheGuardsEdges``: what is now said honestly about the limits.

Refs #3820, #3848, #3774.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest
import safe_markup_corpus_3848 as corpus
import safe_markup_known_misses_3848 as misses
import safe_markup_rows_3848 as rows

from scripts.lib import safe_markup_audit as audit
from scripts.lib import safe_markup_taint as taint
from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]
PRELUDE = (
    "from django.utils.safestring import mark_safe, SafeString\n"
    "from django.utils.html import escape, format_html, conditional_escape\n"
)


def flagged(source: str, **kwargs) -> list[str]:
    return [f.function for f in find_unsafe_markup(PRELUDE + source, **kwargs)]


def run(source: str, args: tuple):
    namespace: dict = {"__name__": "corpus_case"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # optimize=1: an ``assert`` is gone, as it is under ``python -O``
        exec(compile(PRELUDE + source, "<case>", "exec", optimize=1), namespace)  # noqa: S102
        return str(namespace["f"](*args))


class TestTheReviewCorpus:
    @pytest.mark.parametrize("case", sorted(corpus.CASES))
    def test_the_case_really_emits_the_payload_raw(self, case):
        source, args = corpus.CASES[case]
        assert "<x>" in run(source, args), "the oracle: this case is not unsafe, fix the case"

    @pytest.mark.parametrize("case", sorted(corpus.CASES))
    def test_the_guard_flags_it(self, case):
        source, _ = corpus.CASES[case]
        assert flagged(source) != [], case

    def test_there_are_27_regressions_and_they_are_in_the_corpus(self):
        assert len(corpus.REGRESSIONS) == 27
        assert set(corpus.REGRESSIONS) <= set(corpus.CASES)


class TestDirectedRows:
    @pytest.mark.parametrize("label", sorted(rows.ROWS))
    def test_the_verdict(self, label):
        source, expect = rows.ROWS[label]
        assert bool(flagged(source)) is expect, label


class TestDynamicAttributes:
    BADGE = """
    class Badge(Component):
        color = "blue"
        def _render_custom(self):
            return mark_safe(f'<span class="badge-{self.color}">x</span>')
    """

    @staticmethod
    def _dedent(text: str) -> str:
        import textwrap

        return textwrap.dedent(text)

    def test_the_component_base_writes_attributes_under_names_the_source_does_not_show(self):
        base = (ROOT / "python/djust/components/base.py").read_text()
        assert "Component" in taint.dynamic_class_names([base])

    def test_a_subclass_of_it_is_not_trusted_for_its_class_attributes(self):
        base = (ROOT / "python/djust/components/base.py").read_text()
        dynamic = taint.dynamic_class_names([base])
        source = self._dedent(self.BADGE)
        assert flagged(source, dynamic_bases=dynamic) == ["Badge._render_custom"]
        # ... and the same class without that base is a constant, as before
        assert flagged(source.replace("(Component)", "")) == []

    def test_an_indirect_subclass_is_not_trusted_either(self):
        source = self._dedent(
            """
            class Mid(Component):
                pass
            class Badge(Mid):
                color = "blue"
                def r(self):
                    return mark_safe(f'<span class="{self.color}">x</span>')
            """
        )
        assert flagged(source, dynamic_bases=frozenset({"Component"})) == ["Badge.r"]

    def test_the_classes_found_by_name_across_files(self):
        found = taint.dynamic_class_names(
            [
                "class A:\n    def __init__(self, **kw):\n        for k, v in kw.items():\n            setattr(self, k, v)\n",
                "class B(A):\n    pass\n",
                "class C(B):\n    X = 1\n",
                "class D:\n    X = 1\n",
            ]
        )
        assert found - taint.EXTERNAL_DYNAMIC_BASES == {"A", "B", "C"}

    def test_djangos_class_based_views_and_forms_are_dynamic_bases(self):
        """``View.__init__`` ``setattr``s every ``as_view()`` keyword (#3864)."""
        found = taint.dynamic_class_names(["class Page(TemplateView):\n    X = 1\n"])
        assert {"View", "TemplateView", "Form", "Page"} <= found

    def test_the_command_line_passes_the_dynamic_classes_it_finds(self, tmp_path, capsys):
        """#3864: the CLI did not pass ``dynamic_class_names``, so a class attribute of a subclass of
        a keyword-writing base in another file was trusted as a constant on the command line."""
        (tmp_path / "base.py").write_text(
            "class Base:\n    def __init__(self, **kw):\n        for k, v in kw.items():\n"
            "            setattr(self, k, v)\n"
        )
        (tmp_path / "badge.py").write_text(
            "from django.utils.safestring import mark_safe\nfrom base import Base\n"
            "class Badge(Base):\n    color = 'blue'\n    def r(self):\n"
            "        return mark_safe(f'<b class={self.color}>x</b>')\n"
        )
        assert taint._main([str(tmp_path)]) == 1
        assert "Badge.r" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "hazard",
        [
            "setattr(o, k, v)",
            "object.__setattr__(o, k, v)",
            "o.__dict__.update(kw)",
            "vars(o)[k] = v",
            "delattr(o, k)",
        ],
    )
    def test_any_of_these_anywhere_in_the_file_turns_class_trust_off(self, hazard):
        source = (
            'class C:\n    LABEL = "ok"\n    def r(self):\n'
            '        return mark_safe(f"<b>{self.LABEL}</b>")\n'
            f"def tweak(o, k, v, kw):\n    {hazard}\n"
        )
        assert flagged(source) == ["C.r"]

    def test_the_real_package_flags_nothing_new_for_a_hazard_free_file(self):
        assert (
            flagged(
                'class C:\n    LABEL = "ok"\n    def r(self):\n        return mark_safe(f"<b>{self.LABEL}</b>")\n'
            )
            == []
        )


class TestWrappers:
    def test_the_known_wrappers_are_found_by_data_flow(self):
        for path in ("python/djust/_html.py", "python/djust/components/rust_handlers.py"):
            tree = ast.parse((ROOT / path).read_text())
            assert (
                taint.wrapper_names(tree, taint.SINKS - taint.WRAPPER_SINKS) & taint.WRAPPER_SINKS
            )

    def test_every_wrapper_in_the_package_is_a_named_sink(self):
        """Any top-level function, lambda or ``partial`` alias in ``python/djust`` whose every returned
        value is a sink applied to (a text-preserving transform of) one of its parameters must be in
        ``SINKS``, or the next one is invisible exactly as ``_safe`` and ``safe_html`` were."""
        unnamed = []
        for path in audit.package_files():
            tree = ast.parse(path.read_text())
            for name in taint.wrapper_names(tree, taint.SINKS, top_level_only=False):
                if name not in taint.SINKS:
                    unnamed.append(f"{path.relative_to(ROOT)}:{name}")
        assert unnamed == [], (
            "a function that marks its argument safe is not a sink of the guard; add its name to "
            f"SINKS / WRAPPER_SINKS in scripts/lib/safe_markup_taint.py: {unnamed}"
        )

    def test_a_wrapper_defined_in_the_file_is_a_sink_without_being_named(self):
        source = "def _ms(v):\n    x = v.strip()\n    return mark_safe(x)\ndef f(r):\n    return _ms(f'<b>{r}</b>')\n"
        assert flagged(source) == ["f"]
        assert taint.wrapper_names(ast.parse(source), {"mark_safe"}) == {"_ms"}

    def test_a_builder_is_not_a_wrapper(self):
        source = "def bold(v):\n    return mark_safe(f'<b>{escape(v)}</b>')\n"
        assert taint.wrapper_names(ast.parse(source), {"mark_safe"}) == set()


class TestTheGuardsEdges:
    def test_a_trusted_origin_is_not_a_rebound_name(self):
        """An import the file rebinds is not the thing it was imported as."""
        for label in (
            "an escaper rebound at module level",
            "an escaper rebound from another function through global",
        ):
            assert flagged(rows.ROWS[label][0]) != [], label

    def test_depth_fails_closed(self):
        source = rows.ROWS["a 60-hop alias chain"][0]
        assert flagged(source) == ["f"]
        # the chain is a bare passthrough of a built string: only the depth bound can flag the sink
        shallow = "\n".join(
            ["def f(raw):", "    a0 = f'<b>{raw}</b>'"]
            + [f"    a{i + 1} = a{i}" for i in range(5)]
            + ["    return mark_safe(a5)"]
        )
        assert flagged(shallow) == ["f"]

    def test_the_limits_the_review_found_are_documented(self):
        doc = " ".join((taint.__doc__ or "").split()).lower()
        for needle in ("dict key", "default argument", "t-strings", "html_safe"):
            assert needle in doc, needle


class TestKnownMisses:
    """False negatives the review found and the guard does not (yet) catch: pinned so the day it learns
    one this fails and the case moves to ``safe_markup_corpus_3848``."""

    @pytest.mark.parametrize("case", sorted(misses.CASES))
    def test_the_case_really_emits_the_payload_raw(self, case):
        source, args = misses.CASES[case]
        assert "<x>" in run(source, args), "not unsafe: fix or drop the case"

    @pytest.mark.parametrize("case", sorted(misses.CASES))
    def test_the_guard_still_misses_it(self, case):
        source, _ = misses.CASES[case]
        assert flagged(source) == [], f"the guard learned {case}: move it to the corpus"

    def test_the_two_lists_do_not_overlap(self):
        assert not set(misses.CASES) & set(corpus.CASES)

    def test_every_known_miss_says_why(self):
        assert set(misses.REASONS) == set(misses.CASES)
        assert all(len(reason) > 40 for reason in misses.REASONS.values())

    def test_a_literal_payload_miss_has_a_data_twin_the_guard_flags(self):
        """A miss kept because its payload is a literal names a twin with data from elsewhere; the twin
        is in the corpus (so it is RUN and must be flagged)."""
        import re

        for case, reason in misses.REASONS.items():
            for twin in re.findall(r"see (\w+) in safe_markup_corpus_3848", reason):
                assert twin in corpus.CASES, (case, twin)
