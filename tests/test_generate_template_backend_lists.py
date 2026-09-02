"""Tests for scripts/generate-template-backend-lists.py — #2533.

The doc's supported/unsupported tag and filter lists are generated from the
engine's registries; these tests pin (1) that the extraction matches what the
registries expose, (2) that the check goes red on a hand-edited block, (3)
that regeneration is idempotent, and (4) that the generated unsupported-tag
set agrees with the #2517 scoreboard. Every ``*_fails`` case asserts BOTH the
exit code AND a specific name in the output (#1200 / #254): the check cannot
pass by exiting 1 for an unrelated reason.

Subprocess runs mirror tests/test_check_doc_snippets.py (path-override flags
against temp copies); the extraction tests import the script in-process.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

import pytest

_SELF = pathlib.Path(__file__).resolve()
_REPO = _SELF.parents[1]
_SCRIPT = _REPO / "scripts" / "generate-template-backend-lists.py"
_DOC = _REPO / "docs" / "TEMPLATE_BACKEND.md"
_ARITY_RS = _REPO / "crates" / "djust_templates" / "src" / "filter_arity.rs"
_PARSER_RS = _REPO / "crates" / "djust_templates" / "src" / "parser.rs"
_SCOREBOARD = pathlib.Path(
    os.environ.get("DJUST_TEMPLATE_SUITE_LAST_RUN", _REPO / ".django-src" / "last-run.txt")
)


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_template_backend_lists", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before exec so `@dataclass` under `from __future__ import
    # annotations` can resolve the module's namespace.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_module()


@pytest.fixture(scope="module")
def report(gen):
    return gen.build_report()


def _run(*args: str) -> tuple[int, str]:
    """Run the script via subprocess. Returns ``(exit_code, stdout+stderr)``."""
    env = {**os.environ, "PYTHONPATH": str(_REPO)}
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO),
    )
    return proc.returncode, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# (1) extraction honesty — the supported sets equal the registries
# --------------------------------------------------------------------------- #


class TestExtractionMatchesTheRegistries:
    def test_arity_filter_set_equals_djangos_builtin_filter_registry(self, gen, report):
        from django.template import defaultfilters

        django_filters = set(defaultfilters.register.filters)
        assert report.djust_filters == django_filters
        assert report.supported_filters == django_filters
        assert report.unsupported_filters == set()
        assert "escapejs" in report.supported_filters

    def test_tag_buckets_partition_djangos_builtin_tag_registry(self, report):
        from django.template import defaulttags, loader_tags

        django_tags = set(defaulttags.register.tags) | set(loader_tags.register.tags)
        native = report.supported_native_tags
        handler = report.supported_handler_tags
        unsupported = report.unsupported_tags
        assert native | handler | unsupported == django_tags
        assert not (native & handler)
        assert not (native & unsupported)
        assert not (handler & unsupported)
        # The two Python-handler tags and the native collision rule
        # (`templatetag` has both an arm and a handler; the arm wins).
        assert handler == {"regroup", "url"}
        assert "templatetag" in native

    def test_native_tag_extraction_sees_an_injected_arm(self, gen, tmp_path):
        """Gate-off for the arm regex: a new arm in a copy of parser.rs is extracted.

        `autoescape` is asserted absent because it has no arm today; when the
        v1.2.0-3 autoescape row lands, this assertion flips and the generated
        unsupported list shrinks with it.
        """
        src = _PARSER_RS.read_text(encoding="utf-8")
        anchor = '                "if" => {'
        assert src.count(anchor) == 1
        mutated = src.replace(anchor, '                "zzz_probe" => {\n' + anchor, 1)
        probe = tmp_path / "parser.rs"
        probe.write_text(mutated, encoding="utf-8")
        names = gen.djust_native_tags(probe)
        assert "zzz_probe" in names
        assert "if" in names
        assert "autoescape" not in names
        assert "endif" not in names, "closers must be dropped"

    def test_hidden_arity_row_turns_the_check_red(self, tmp_path):
        """Coverage (1) gate-off: remove one filter from the ARITY input -> red.

        The committed doc says 57 of 57; hiding `escapejs` from the table the
        generator reads makes the regenerated block disagree, and the check
        must name the filter that moved.
        """
        src = _ARITY_RS.read_text(encoding="utf-8")
        row = re.search(r'^    \("escapejs", \d+, \d+, \d+\),\n', src, re.M)
        assert row is not None, "the escapejs ARITY row is the mutation target"
        hidden = tmp_path / "filter_arity.rs"
        hidden.write_text(src.replace(row.group(0), "", 1), encoding="utf-8")
        code, out = _run("--arity-rs", str(hidden))
        assert code == 1, out
        assert "unsupported (1):** `escapejs`" in out
        assert "make template-backend-lists" in out

    def test_stale_extraction_regex_fails_loudly(self, tmp_path):
        """A source refactor that moves the table must exit 2, not emit empty lists."""
        src = _ARITY_RS.read_text(encoding="utf-8")
        moved = tmp_path / "filter_arity.rs"
        moved.write_text(src.replace("const ARITY:", "const ARITY_V2:", 1), encoding="utf-8")
        code, out = _run("--arity-rs", str(moved))
        assert code == 2, out
        assert "const ARITY" in out and "not found" in out

    def test_reformatted_rows_trip_the_sentinel_guard(self, tmp_path):
        """Rows the row regex no longer matches must exit 2 ("stale"), never
        silently produce an empty filter set (which would read as 0 of 57)."""
        src = _ARITY_RS.read_text(encoding="utf-8")
        reformatted = tmp_path / "filter_arity.rs"
        reformatted.write_text(src.replace('    ("', '    ( "'), encoding="utf-8")
        code, out = _run("--arity-rs", str(reformatted))
        assert code == 2, out
        assert "row regex is stale" in out


# --------------------------------------------------------------------------- #
# (2) the check goes red on a hand-edited block
# --------------------------------------------------------------------------- #


class TestCheckMode:
    def test_committed_doc_is_current(self):
        code, out = _run()
        assert code == 0, out
        assert "unchanged" in out

    def test_hand_edited_unsupported_list_fails_the_check(self, tmp_path):
        text = _DOC.read_text(encoding="utf-8")
        needle = "`querystring`, "
        assert text.count(needle) == 1, "the unsupported line is the mutation target"
        edited = tmp_path / "TEMPLATE_BACKEND.md"
        edited.write_text(text.replace(needle, "", 1), encoding="utf-8")
        code, out = _run("--doc", str(edited))
        assert code == 1, out
        assert "querystring" in out
        assert "run: make template-backend-lists" in out

    def test_hand_edited_count_fails_the_check(self, tmp_path):
        text = _DOC.read_text(encoding="utf-8")
        needle = "57 of 57 supported"
        assert text.count(needle) == 1
        edited = tmp_path / "TEMPLATE_BACKEND.md"
        edited.write_text(text.replace(needle, "56 of 57 supported", 1), encoding="utf-8")
        code, out = _run("--doc", str(edited))
        assert code == 1, out
        assert "-**Built-in filters — 56 of 57" in out

    def test_missing_closing_marker_is_a_structural_error(self, tmp_path, gen):
        text = _DOC.read_text(encoding="utf-8")
        assert text.count(gen.MARKER_CLOSE) == 1
        broken = tmp_path / "TEMPLATE_BACKEND.md"
        broken.write_text(text.replace(gen.MARKER_CLOSE, "", 1), encoding="utf-8")
        code, out = _run("--doc", str(broken))
        assert code == 2, out
        assert "unclosed" in out

    def test_missing_opening_marker_is_a_structural_error(self, tmp_path, gen):
        text = _DOC.read_text(encoding="utf-8")
        broken = tmp_path / "TEMPLATE_BACKEND.md"
        broken.write_text(text.replace(gen.MARKER_OPEN, "", 1), encoding="utf-8")
        code, out = _run("--doc", str(broken))
        assert code == 2, out
        assert "opening marker" in out


# --------------------------------------------------------------------------- #
# (3) regeneration is idempotent
# --------------------------------------------------------------------------- #


class TestWriteMode:
    def test_write_restores_a_corrupted_block_and_is_idempotent(self, tmp_path):
        original = _DOC.read_bytes()
        text = original.decode("utf-8")
        needle = "`querystring`, "
        assert text.count(needle) == 1
        doc = tmp_path / "TEMPLATE_BACKEND.md"
        doc.write_text(text.replace(needle, "`zzz_bogus`, ", 1), encoding="utf-8")
        assert doc.read_bytes() != original

        code, out = _run("--doc", str(doc), "--write")
        assert code == 0, out
        assert "rewritten" in out
        assert doc.read_bytes() == original, "one --write must restore the committed bytes"

        code, out = _run("--doc", str(doc), "--write")
        assert code == 0, out
        assert "unchanged" in out
        assert doc.read_bytes() == original

    def test_write_only_touches_the_block(self, tmp_path, gen):
        """Prose outside the markers survives --write byte for byte."""
        text = _DOC.read_text(encoding="utf-8")
        start, end = gen.find_block(text)
        doc = tmp_path / "TEMPLATE_BACKEND.md"
        doc.write_text(text[:start] + gen.MARKER_OPEN + "\n" + gen.MARKER_CLOSE + "\n" + text[end:])
        code, out = _run("--doc", str(doc), "--write")
        assert code == 0, out
        rewritten = doc.read_text(encoding="utf-8")
        assert rewritten[:start] == text[:start]
        assert rewritten[-len(text[end:]) :] == text[end:]
        assert rewritten == text


# --------------------------------------------------------------------------- #
# (4) the unsupported-tag list agrees with the #2517 scoreboard
# --------------------------------------------------------------------------- #


class TestScoreboardParity:
    """Cross-check against `.django-src/last-run.txt` (gitignored, written by
    `make django-template-suite`). Skips with a reason when the file is absent
    so the main CI job stays green; the scoreboard job can run it explicitly
    with `--cross-check`."""

    @pytest.fixture(autouse=True)
    def _need_scoreboard(self):
        if not _SCOREBOARD.exists():
            pytest.skip(f"{_SCOREBOARD} absent — run `make django-template-suite` first")

    def test_every_scoreboard_django_tag_is_in_the_generated_unsupported_set(self, gen, report):
        board = gen.scoreboard_unsupported_tags(_SCOREBOARD)
        assert board, "the scoreboard regex found no `Unsupported template tag` lines"
        assert "autoescape" in board
        known = report.django_tags | report.library_tags
        django_names_on_the_board = board & known
        assert django_names_on_the_board, "the scoreboard names no Django tag at all?"
        missing = django_names_on_the_board - report.all_unsupported_tags
        assert missing == set(), (
            "scoreboard says unsupported, generator says supported — a divergence is a finding"
        )
        # Bucketing rule: names that are neither Django built-in nor library
        # tags are Django's own test-suite custom libraries
        # (template_tests/templatetags/custom.py); they are not support-list
        # items and must not leak into any generated list.
        custom = board - known
        assert not (custom & (report.djust_tags | report.all_unsupported_tags))
        assert gen.cross_check(report, _SCOREBOARD) == []

    def test_cross_check_flag_agrees(self):
        code, out = _run("--cross-check", str(_SCOREBOARD))
        assert code == 0, out
        assert "NOT in generated set: []" in out


class TestCrossCheckDetectsDisagreement:
    """Does not need the real scoreboard: a synthetic one claiming a supported
    tag is unsupported must be reported, by name, and fail the flag."""

    def test_synthetic_scoreboard_naming_a_supported_tag_is_a_finding(self, gen, report, tmp_path):
        board = tmp_path / "last-run.txt"
        board.write_text(
            "ERROR template_tests.x.y | Unsupported template tag '{% for x in y %}'. Register\n"
            "ERROR template_tests.x.z | Unsupported template tag '{% autoescape on %}'. Register\n"
            "ERROR template_tests.x.w | Unsupported template tag '{% badtag %}'. Register\n",
            encoding="utf-8",
        )
        assert gen.scoreboard_unsupported_tags(board) == {"for", "autoescape", "badtag"}
        problems = gen.cross_check(report, board)
        assert len(problems) == 1
        assert "['for']" in problems[0]
        code, out = _run("--cross-check", str(board))
        assert code == 1, out
        assert "NOT in generated set: ['for']" in out


# --------------------------------------------------------------------------- #
# doc regressions + wiring pins
# --------------------------------------------------------------------------- #


class TestDocAndWiring:
    def test_escapejs_is_listed_supported_and_the_stale_workaround_is_gone(self):
        text = _DOC.read_text(encoding="utf-8")
        supported_line = next(
            line for line in text.splitlines() if line.startswith("**Built-in filters — ")
        )
        assert "`escapejs`" in supported_line
        assert "escapejs }}" not in text
        assert "json.dumps(data)[1:-1]" not in text
        assert "`escapejs` filter for JavaScript string escaping" not in text

    def test_troubleshooting_quotes_the_engines_error_text(self):
        text = _DOC.read_text(encoding="utf-8")
        renderer = (_REPO / "crates" / "djust_templates" / "src" / "renderer.rs").read_text(
            encoding="utf-8"
        )
        # The Rust literal is a `\`-continued format string; collapse it to
        # the bytes the engine emits before comparing.
        engine_text = " ".join(
            re.search(
                r'"(Unsupported template tag \'\{tag_sig\}\'\. \\\n.*?instead\.)"', renderer, re.S
            )
            .group(1)
            .replace("\\\n", "\n")
            .split()
        )
        assert engine_text.startswith(
            "Unsupported template tag '{tag_sig}'. Register a handler via"
        )
        assert engine_text.replace("{tag_sig}", "{% tag_name %}") in text
        assert "Unsupported tag '{% tag_name %}'" not in text

    def test_rendered_block_has_no_timestamp(self, gen, report):
        block = gen.render_block(report)
        assert not re.search(r"\b20\d\d-\d\d-\d\d\b", block)
        assert block == gen.render_block(report), "rendering must be deterministic"

    def test_make_precommit_and_ci_are_wired(self):
        makefile = (_REPO / "Makefile").read_text(encoding="utf-8")
        assert "\ntemplate-backend-lists:" in makefile
        assert "\ncheck-template-backend-lists:" in makefile
        assert makefile.count("scripts/generate-template-backend-lists.py") == 2

        precommit = (_REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        hook = re.search(
            r"- id: check-template-backend-lists\n(?:.*\n)*?\s+files: (.*)\n", precommit
        )
        assert hook is not None
        files_re = re.compile(hook.group(1))
        for path in (
            "docs/TEMPLATE_BACKEND.md",
            "crates/djust_templates/src/parser.rs",
            "crates/djust_templates/src/filter_arity.rs",
            "python/djust/template_tags/regroup.py",
            "scripts/generate-template-backend-lists.py",
            "uv.lock",
        ):
            assert files_re.search(path), path
        assert not files_re.search("crates/djust_templates/src/renderer.rs")

        workflow = (_REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        assert "scripts/generate-template-backend-lists.py" in workflow
