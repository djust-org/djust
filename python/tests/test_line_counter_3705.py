"""A scan that reports a line per match reads the template once, not once per match (#3705).

``content[: match.start()].count("\\n") + 1`` re-reads the text from its start for every
match, so a template with very many matches costs O(template x matches). T027 already
used an incremental counter (#3691); the same idiom sat in T001, S007, T004, T013, T010,
T014, T015, T017, T025, A070, A075, A090, T016, T024, Y001-Y006 and S011, and in
``djust_typecheck``. They now share one helper, ``djust.checks.utils.LineCounter``.

What each part proves, and what it cannot:

* ``TestLineCounterIsCorrect`` -- the helper answers ``text[:offset].count("\\n") + 1``
  for any offset in any order (the counter moves backward as well as forward).
* ``TestNoSiteRescansFromTheStart`` -- the structural pin. It finds every site by the
  SINK expression (a ``.count("\\n")`` whose receiver is a prefix slice, or that starts
  at offset 0), in every module under ``checks/`` plus ``djust_typecheck``, so a new
  site written the old way fails here whatever check it belongs to. It carries a
  positive control, so it cannot pass by reading nothing.
* ``TestScansReadTheTemplateOnce`` -- the character tally: through the real
  ``check_templates`` / ``check_accessibility`` / ``check_inline_script_csp``, every
  counter reads at most ``len(template)`` characters however many matches it answers,
  and the line each finding reports is still the line its token is on. No clock.

The tally runs the sites that can fire on one ``finditer`` template. It does not run
T005, T011 (``_UNSUPPORTED_TAGS`` is empty, so it cannot fire), T025, T024 or
``djust_typecheck``; the structural pin is what covers those.

Refs #3705, #3691, #1468 (a pin must go red when the thing it pins drifts).
"""

from __future__ import annotations

import ast
import collections
import pathlib

import pytest

from djust.checks import utils as checks_utils
from djust.checks.utils import LineCounter

pytestmark = pytest.mark.pr_tier

PACKAGE = pathlib.Path(checks_utils.__file__).resolve().parent.parent  # python/djust


class TestLineCounterIsCorrect:
    TEXTS = [
        "",
        "no newline at all",
        "\n",
        "a\nb\nc",
        "a\nb\nc\n",
        "\n\n\n",
        "line one\r\nline two\r\nline three",
        "x" * 50 + "\n" + "y" * 50 + "\n" + "z",
    ]

    @staticmethod
    def _naive(text: str, offset: int) -> int:
        return text[:offset].count("\n") + 1

    @pytest.mark.parametrize("text", TEXTS)
    def test_ascending_offsets_match_the_naive_count(self, text):
        counter = LineCounter(text)
        for offset in range(len(text) + 1):
            assert counter.line_at(offset) == self._naive(text, offset), (text, offset)

    @pytest.mark.parametrize("text", TEXTS)
    def test_descending_offsets_match_the_naive_count(self, text):
        counter = LineCounter(text)
        for offset in range(len(text), -1, -1):
            assert counter.line_at(offset) == self._naive(text, offset), (text, offset)

    @pytest.mark.parametrize("text", TEXTS)
    def test_a_fixed_out_of_order_sequence_and_repeats_and_past_the_end(self, text):
        n = len(text)
        counter = LineCounter(text)
        for offset in [n, 0, n // 2, n // 2, 1, n + 5, n + 5, 0, n, 2, n + 100, 3]:
            assert counter.line_at(offset) == self._naive(text, offset), (text, offset)

    def test_the_first_line_is_one(self):
        assert LineCounter("a\nb").line_at(0) == 1


def _offending_calls(source: str) -> list[int]:
    """Line numbers of ``X[:y].count("\\n")`` and ``X.count("\\n", 0, y)`` calls in ``source``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "count"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "\n"
        ):
            continue
        receiver = node.func.value
        is_prefix_slice = (
            isinstance(receiver, ast.Subscript)
            and isinstance(receiver.slice, ast.Slice)
            and receiver.slice.lower is None
            and receiver.slice.upper is not None
        )
        from_zero = (
            len(node.args) == 3
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == 0
        )
        if is_prefix_slice or from_zero:
            found.append(node.lineno)
    return found


def _scanned_files() -> list[pathlib.Path]:
    files = sorted((PACKAGE / "checks").glob("*.py"))
    files.append(PACKAGE / "management" / "commands" / "djust_typecheck.py")
    return [path for path in files if path.exists()]


class TestNoSiteRescansFromTheStart:
    def test_the_pin_sees_the_idiom_in_every_spelling_it_names(self):
        """Positive control: the pin is not blind."""
        assert _offending_calls('n = text[: match.start()].count("\\n") + 1\n') == [1]
        assert _offending_calls('n = text[:offset].count("\\n")\n') == [1]
        assert _offending_calls('n = src.count("\\n", 0, match.start()) + 1\n') == [1]

    def test_the_pin_leaves_the_incremental_and_the_unrelated_forms_alone(self):
        assert _offending_calls('n += scan.count("\\n", counted_to, match.start())\n') == []
        assert _offending_calls('n = body.count("\\n")\n') == []
        assert _offending_calls('n = text[start:].count("\\n")\n') == []
        assert _offending_calls("n = text[:offset].count(sep)\n") == []

    def test_the_files_scanned_are_the_checks_and_typecheck(self):
        names = {path.name for path in _scanned_files()}
        assert {"templates.py", "accessibility.py", "security.py", "bindings.py"} <= names
        assert "djust_typecheck.py" in names

    def test_no_check_counts_newlines_from_the_start_per_match(self):
        offenders = {
            "%s:%d" % (path.relative_to(PACKAGE), lineno): None
            for path in _scanned_files()
            for lineno in _offending_calls(path.read_text(encoding="utf-8"))
        }
        assert not offenders, (
            "a per-match line number must come from djust.checks.utils.LineCounter, "
            "not a count from the start of the text (#3705): %s" % sorted(offenders)
        )


# One repeat of each construct, with the token the line a finding reports must contain.
MAIN_TEMPLATE = """<div @click="a">x</div>
{{ f.client_name|safe }}
<script>document.addEventListener('djust:foo', f)</script>
<div dj-view="{{ view_name }}"></div>
<div dj-view="nodot"></div>
{% live_render "a.B" sticky=True lazy=True %}
{% dj_activity %}x{% enddj_activity %}
{% djust_markdown x %}
<a dj-navigate="/x/">go</a>
<span data-dj-id="1"></span>
<div data-djust-root></div>
<table><tbody dj-root><tr><td>c</td></tr></tbody></table>
<button dj-click="go" data-view="x">Go</button>
<RustButton id="b" />
"""
A11Y_TEMPLATE = """<button><i class="icon"></i></button>
<img src="a.png">
<input type="text" name="q">
<div tabindex="3">x</div>
<div role="img"></div>
<svg><path d="M0 0"/></svg>
<ul class="legend"><li><span class="swatch" style="background:red"></span></li></ul>
<script>var x = 1;</script>
"""
TOKENS = {
    "djust.T001": ("@click",),
    "djust.S007": ("client_name",),
    "djust.T004": ("addEventListener",),
    "djust.T013": ("dj-view",),
    "djust.A075": ("live_render",),
    "djust.A070": ("dj_activity",),
    "djust.T014": ("data-dj-id",),
    "djust.T015": ("data-djust-root",),
    "djust.T017": ("<tbody",),
    "djust.T010": ("dj-click",),
    "djust.T027": ("RustButton",),
    "djust.Y001": ("<button",),
    "djust.Y002": ("<img",),
    "djust.Y003": ("<input",),
    "djust.Y004": ("tabindex",),
    "djust.Y005": ('role="img"', "<svg"),
    "djust.Y006": ("<li",),
    "djust.S011": ("<script",),
}


@pytest.fixture
def tally(monkeypatch):
    """Record every ``LineCounter`` a check creates and the characters it reads.

    ``_count_newlines`` is the only place a counter reads its text, so ``stop - start``
    summed over its calls is the characters read. Each counter in ``checks.*`` is
    replaced by a subclass that books its own reads and queries.
    """
    reads = [0]
    real = checks_utils._count_newlines

    def counting(text, start, end):
        reads[0] += max(0, min(end, len(text)) - min(start, len(text)))
        return real(text, start, end)

    monkeypatch.setattr(checks_utils, "_count_newlines", counting)

    counters: list = []

    class Recording(LineCounter):
        def __init__(self, text):
            super().__init__(text)
            self.length = len(text)
            self.queries = 0
            self.read = 0
            counters.append(self)

        def line_at(self, offset):
            before = reads[0]
            line = super().line_at(offset)
            self.queries += 1
            self.read += reads[0] - before
            return line

    from djust.checks import accessibility, bindings, security, templates

    for module in (templates, accessibility, security, bindings):
        monkeypatch.setattr(module, "LineCounter", Recording)
    return counters


def _write_project(tmp_path, repeats: int):
    main_dir = tmp_path / "main"
    a11y_dir = tmp_path / "a11y"
    main_dir.mkdir()
    a11y_dir.mkdir()
    wrap = '<div dj-root dj-view="a.b.C">\n%s</div>\n'
    (main_dir / "page.html").write_text(wrap % (MAIN_TEMPLATE * repeats))
    (a11y_dir / "page.html").write_text(wrap % (A11Y_TEMPLATE * repeats))
    return main_dir, a11y_dir


def _run(tmp_path, settings, repeats: int):
    from djust.checks import check_accessibility, check_templates
    from djust.checks.security import check_inline_script_csp

    main_dir, a11y_dir = _write_project(tmp_path, repeats)
    settings.TEMPLATES = [
        {
            "DIRS": [str(main_dir), str(a11y_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplates",
        }
    ]
    findings = []
    for check in (check_templates, check_accessibility, check_inline_script_csp):
        findings.extend(
            e for e in check(None) if str(getattr(e, "file_path", "")).startswith(str(tmp_path))
        )
    return findings


class TestScansReadTheTemplateOnce:
    @pytest.mark.parametrize("repeats", [20, 80])
    def test_every_counter_reads_at_most_the_template_however_many_matches(
        self, tmp_path, settings, tally, repeats
    ):
        findings = _run(tmp_path, settings, repeats)
        by_id = collections.Counter(e.id for e in findings)
        # The sites were reached: each construct fired once per repeat (the instrument
        # is not blind), so a counter that answered nothing would be a missing site.
        for check_id in TOKENS:
            assert by_id[check_id] >= repeats, (check_id, dict(by_id))
        assert tally, "no LineCounter was created: the checks no longer use the helper"
        busiest = max(counter.queries for counter in tally)
        assert busiest >= repeats, busiest
        for counter in tally:
            assert counter.read <= counter.length, (counter.read, counter.length, counter.queries)
        # Every reported finding line came through a counter, plus the two scans that tally
        # a hit per match without reporting it (A090 ``djust_markdown``, T016 ``dj-navigate``).
        # A site reverted to the old idiom answers fewer queries than that, by ``repeats``.
        reported = sum(1 for e in findings if e.id in TOKENS)
        assert sum(counter.queries for counter in tally) == reported + 2 * repeats

    def test_the_characters_read_grow_linearly_not_quadratically(self, tmp_path, settings, tally):
        small_dir, large_dir = tmp_path / "small", tmp_path / "large"
        small_dir.mkdir()
        large_dir.mkdir()

        def total(directory, repeats):
            del tally[:]
            _run(directory, settings, repeats)
            return sum(counter.read for counter in tally)

        small, large = total(small_dir, 25), total(large_dir, 100)
        assert small > 0, "the tally saw no reads"
        # Quadrupling the template and its matches: linear is ~4x, the old idiom ~16x.
        assert large < 6 * small, (small, large)

    def test_each_finding_reports_the_line_its_token_is_on(self, tmp_path, settings, tally):
        findings = _run(tmp_path, settings, 12)
        checked = collections.Counter()
        for finding in findings:
            tokens = TOKENS.get(finding.id)
            if tokens is None or not finding.line_number:
                continue
            lines = pathlib.Path(finding.file_path).read_text().split("\n")
            line = lines[finding.line_number - 1]
            assert any(token in line for token in tokens), (finding.id, finding.line_number, line)
            checked[finding.id] += 1
        assert set(checked) == set(TOKENS), sorted(set(TOKENS) - set(checked))
