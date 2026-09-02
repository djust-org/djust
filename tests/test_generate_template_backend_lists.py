"""Tests for scripts/generate-template-backend-lists.py — #2533.

The doc's supported/unsupported tag and filter lists are generated from the
engine's registries; these tests pin (1) that the extraction matches what the
registries expose, (2) that the check goes red on a hand-edited block, (3)
that regeneration is idempotent, (4) that the generated unsupported-tag set
agrees with the #2517 scoreboard, and (5) the generator→engine direction:
every name the block calls unsupported is refused by the backend and every
name it calls supported is not, and the library filters it calls *bridged*
resolve with a ``DjangoTemplates`` engine configured and raise ``Unknown
filter`` without one. Every ``*_fails`` case asserts BOTH the exit code AND a
specific name in the output (#1200 / #254): the check cannot pass by exiting 1
for an unrelated reason.

Subprocess runs mirror tests/test_check_doc_snippets.py (path-override flags
against temp copies); the extraction tests import the script in-process. The
direction tests render through ``DjustTemplateBackend`` in a subprocess so
each ``TEMPLATES`` shape (djust-only, djust + ``DjangoTemplates``) gets a
fresh Django configuration.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import pathlib
import re
import stat
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
# Subprocesses (the script, the render probes) must import THIS tree's djust —
# the package under test — not whichever checkout the venv's editable install
# points at (identical in CI; different in a worktree). ``python/`` first, the
# repo root second for the script's own imports.
_PYTHONPATH = os.pathsep.join([str(_REPO / "python"), str(_REPO)])


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


def _run_split(*args: str) -> tuple[int, str, str]:
    """Run the script via subprocess. Returns ``(exit_code, stdout, stderr)``."""
    env = {**os.environ, "PYTHONPATH": _PYTHONPATH}
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run(*args: str) -> tuple[int, str]:
    """Run the script via subprocess. Returns ``(exit_code, stdout+stderr)``."""
    code, out, err = _run_split(*args)
    return code, out + err


# Renders each source through ``DjustTemplateBackend`` under a fresh Django
# configuration: djust-only, or djust next to the ``DjangoTemplates`` fallback
# engine the doc's Quick Start recommends. ``djust`` is deliberately NOT in
# INSTALLED_APPS, so the only thing that can bridge a library filter is the
# backend's own render path (``template/rendering.py`` →
# ``_ensure_custom_filters_bridged``), which is the path a project's
# ``render()`` takes. Reads ``{"with_django_engine", "context", "sources"}``
# as JSON on stdin; prints ``{name: "OK:<html>" | "ERR:<message>"}``.
_RENDER_SCRIPT = """
import json, sys
import django
from django.conf import settings
job = json.load(sys.stdin)
templates = [{"BACKEND": "djust.template_backend.DjustTemplateBackend", "NAME": "djust",
              "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}]
if job["with_django_engine"]:
    templates.append({"BACKEND": "django.template.backends.django.DjangoTemplates",
                      "NAME": "django", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
settings.configure(INSTALLED_APPS=["django.contrib.staticfiles"], STATIC_URL="/static/",
                   TEMPLATES=templates, USE_TZ=True, TIME_ZONE="UTC")
django.setup()
from django.template import engines
engine = engines["djust"]
out = {}
for name, src in job["sources"].items():
    try:
        out[name] = "OK:" + engine.from_string(src).render(dict(job["context"]))
    except Exception as exc:  # noqa: BLE001 — the message is the datum
        out[name] = "ERR:" + f"{type(exc).__name__}: {exc}"
json.dump(out, sys.stdout)
"""

_RENDER_CONTEXT = {"x": 1234, "xs": [{"k": "a"}, {"k": "b"}], "a": 1, "b": 2, "c": "de"}


def _render_via_backend(sources: dict[str, str], *, with_django_engine: bool) -> dict[str, str]:
    env = {**os.environ, "PYTHONPATH": _PYTHONPATH}
    proc = subprocess.run(
        [sys.executable, "-c", _RENDER_SCRIPT],
        input=json.dumps(
            {
                "with_django_engine": with_django_engine,
                "context": _RENDER_CONTEXT,
                "sources": sources,
            }
        ),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# One plausible source per tag, so a tag that needs arguments or a closer is
# exercised in the form Django documents. Anything not listed renders as a
# bare ``{% name %}`` (with the owning library loaded) — enough to tell
# "Unsupported template tag" from any other outcome, which is all the
# direction check asserts on.
_TAG_SOURCES = {
    "block": "{% block a %}x{% endblock %}",
    "comment": "{% comment %}x{% endcomment %}",
    "cycle": '{% for i in xs %}{% cycle "a" "b" %}{% endfor %}',
    "extends": '{% extends "zzz_missing.html" %}',
    "firstof": "{% firstof a b %}",
    "for": "{% for i in xs %}{{ i.k }}{% endfor %}",
    "if": "{% if a %}y{% endif %}",
    "include": '{% include "zzz_missing.html" %}',
    "load": "{% load static %}",
    "now": '{% now "Y" %}',
    "spaceless": "{% spaceless %} <b> x </b> {% endspaceless %}",
    "templatetag": "{% templatetag openblock %}",
    "verbatim": "{% verbatim %}{{ x }}{% endverbatim %}",
    "widthratio": "{% widthratio a b 100 %}",
    "with": "{% with y=a %}{{ y }}{% endwith %}",
    "regroup": "{% regroup xs by k as g %}{{ g|length }}",
    "url": '{% url "zzz_missing" %}',
    "static": '{% load static %}{% static "a.css" %}',
    "autoescape": "{% autoescape on %}{{ x }}{% endautoescape %}",
    "filter": "{% filter upper %}x{% endfilter %}",
    "ifchanged": "{% for i in xs %}{% ifchanged %}{{ i.k }}{% endifchanged %}{% endfor %}",
    "querystring": "{% querystring a=1 %}",
    "resetcycle": '{% for i in xs %}{% cycle "a" "b" %}{% resetcycle %}{% endfor %}',
    "blocktrans": "{% load i18n %}{% blocktrans %}x{% endblocktrans %}",
    "blocktranslate": "{% load i18n %}{% blocktranslate %}x{% endblocktranslate %}",
    "get_available_languages": "{% load i18n %}{% get_available_languages as langs %}",
    "get_current_language": "{% load i18n %}{% get_current_language as lang %}",
    "get_current_language_bidi": "{% load i18n %}{% get_current_language_bidi as bidi %}",
    "get_language_info": '{% load i18n %}{% get_language_info for "de" as li %}',
    "get_language_info_list": "{% load i18n %}{% get_language_info_list for xs as lis %}",
    "language": '{% load i18n %}{% language "de" %}x{% endlanguage %}',
    "trans": '{% load i18n %}{% trans "x" %}',
    "translate": '{% load i18n %}{% translate "x" %}',
    "localize": "{% load l10n %}{% localize on %}{{ x }}{% endlocalize %}",
    "get_current_timezone": "{% load tz %}{% get_current_timezone as tz %}",
    "localtime": "{% load tz %}{% localtime on %}x{% endlocaltime %}",
    "timezone": '{% load tz %}{% timezone "UTC" %}x{% endtimezone %}',
    "cache": "{% load cache %}{% cache 500 k %}x{% endcache %}",
}

_UNSUPPORTED_TAG_TEXT = "Unsupported template tag"


def _tag_sources(report) -> dict[str, str]:
    owner = {t: lib for lib, (tags, _) in report.libraries.items() for t in tags}
    names = (
        report.all_unsupported_tags
        | report.supported_native_tags
        | report.supported_handler_tags
        | report.supported_library_tags
    )
    sources = {}
    for name in sorted(names):
        if name in _TAG_SOURCES:
            sources[name] = _TAG_SOURCES[name]
        elif name in owner:
            sources[name] = f"{{% load {owner[name]} %}}{{% {name} %}}"
        else:
            sources[name] = f"{{% {name} %}}"
    return sources


def _direction_findings(report, rendered: dict[str, str]) -> list[str]:
    """Names whose engine outcome contradicts the bucket the block puts them in."""
    findings = []
    for name in sorted(report.all_unsupported_tags):
        if _UNSUPPORTED_TAG_TEXT not in rendered[name]:
            findings.append(f"listed unsupported, but the engine did not refuse it: {name}")
    supported = (
        report.supported_native_tags | report.supported_handler_tags | report.supported_library_tags
    )
    for name in sorted(supported):
        if _UNSUPPORTED_TAG_TEXT in rendered[name]:
            findings.append(f"listed supported, but the engine refused it: {name}")
    return findings


def _filter_sources(names: set[str]) -> dict[str, str]:
    owner = {}
    for lib in ("i18n", "l10n", "tz"):
        mod = importlib.import_module(f"django.templatetags.{lib}")
        for name in mod.register.filters:
            owner[name] = lib
    arg = {"timezone": ':"Asia/Tokyo"'}
    value = {
        "language_bidi": "c",
        "language_name": "c",
        "language_name_local": "c",
        "language_name_translated": "c",
    }
    return {
        name: f"{{% load {owner[name]} %}}{{{{ {value.get(name, 'x')}|{name}{arg.get(name, '')} }}}}"
        for name in sorted(names)
    }


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
        # The five Python-handler tags (#2556 added three) and the native collision rule
        # (`templatetag` has both an arm and a handler; the arm wins).
        assert handler == {"debug", "lorem", "querystring", "regroup", "url"}
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

    def test_missing_cross_check_file_is_a_structural_error_on_stderr(self, tmp_path):
        missing = tmp_path / "last-run.txt"
        code, out, err = _run_split("--cross-check", str(missing))
        assert code == 2, err
        assert "ERROR:" in err and "not found" in err and "last-run.txt" in err
        assert out == "", "the error must go to stderr like every other ERROR"


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

    def test_write_refuses_a_read_only_doc_cleanly(self, tmp_path):
        """No traceback, no rewrite: a clean ``ERROR:`` on stderr and exit 2."""
        text = _DOC.read_text(encoding="utf-8")
        needle = "`querystring`, "
        doc = tmp_path / "TEMPLATE_BACKEND.md"
        doc.write_text(text.replace(needle, "`zzz_bogus`, ", 1), encoding="utf-8")
        before = doc.read_bytes()
        doc.chmod(0o444)
        try:
            code, out, err = _run_split("--doc", str(doc), "--write")
        finally:
            doc.chmod(0o644)
        assert code == 2, out + err
        assert "ERROR: cannot write" in err and "TEMPLATE_BACKEND.md" in err
        assert "Traceback" not in err
        assert doc.read_bytes() == before, "a read-only doc must not be rewritten"
        assert [p.name for p in tmp_path.iterdir()] == ["TEMPLATE_BACKEND.md"], "no temp file"

    def test_write_is_atomic_and_keeps_the_docs_mode(self, tmp_path):
        text = _DOC.read_text(encoding="utf-8")
        needle = "`querystring`, "
        doc = tmp_path / "TEMPLATE_BACKEND.md"
        doc.write_text(text.replace(needle, "`zzz_bogus`, ", 1), encoding="utf-8")
        doc.chmod(0o640)
        code, out = _run("--doc", str(doc), "--write")
        assert code == 0, out
        assert doc.read_text(encoding="utf-8") == text
        assert stat.S_IMODE(doc.stat().st_mode) == 0o640
        assert [p.name for p in tmp_path.iterdir()] == ["TEMPLATE_BACKEND.md"], "no temp file"


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

    def test_never_exercised_set_is_exactly_the_four_the_doc_names(self, gen, report):
        """The generator is the authority for names the suite never reaches as
        a tag error; the doc's Conformance sentence names them, and this pins
        that the sentence names the WHOLE set — a fifth never-exercised name
        (or one the suite starts reaching) must move the sentence."""
        board = gen.scoreboard_unsupported_tags(_SCOREBOARD)
        never_exercised = report.all_unsupported_tags - board
        assert never_exercised == {"get_current_timezone", "localize", "localtime", "timezone"}
        text = _DOC.read_text(encoding="utf-8")
        # The names sit between the two em-dashes of the sentence; the prose
        # around them cites `template_tests`, which is not a tag.
        sentence = re.search(
            r"never appear on the scoreboard[^\n]*?as a tag error — ([^—\n]+) —", text
        )
        assert sentence is not None
        assert set(re.findall(r"`([a-z_]+)`", sentence.group(1))) == never_exercised
        assert sentence.group(0).startswith(
            "never appear on the scoreboard because no `template_tests`"
        )


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

    def test_render_block_refuses_a_registry_name_that_is_not_an_identifier(self, gen, report):
        """Every emitted name is validated; a registry entry carrying Markdown
        or HTML (a backtick, a tag) is a structural error, never doc content."""
        for bad in ("bad`<script>alert(1)</script>", "x|y", "Upper", "1st"):
            poisoned = dataclasses.replace(report, handler_tags=report.handler_tags | {bad})
            with pytest.raises(gen.ExtractionError, match="not plain identifiers") as info:
                gen.render_block(poisoned)
            assert bad in str(info.value)
        assert gen.render_block(report), "the real registries pass the validator"

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
            "python/djust/template_filters.py",  # the bridge the bridged line is detected from
            "scripts/generate-template-backend-lists.py",
            "uv.lock",
        ):
            assert files_re.search(path), path
        assert not files_re.search("crates/djust_templates/src/renderer.rs")

        workflow = (_REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        assert "scripts/generate-template-backend-lists.py" in workflow


# --------------------------------------------------------------------------- #
# (5) generator → engine direction: the block's buckets match the backend
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def rendered_tags(report):
    """Every listed tag rendered through the backend under BOTH ``TEMPLATES``
    shapes. Tags are never bridged, so the two must agree — and they are the
    same subprocesses the filter tests below reuse."""
    sources = _tag_sources(report)
    return {
        "djust_only": _render_via_backend(sources, with_django_engine=False),
        "with_django": _render_via_backend(sources, with_django_engine=True),
    }


class TestGeneratedTagBucketsMatchTheEngine:
    def test_every_listed_unsupported_tag_is_refused_and_every_supported_one_is_not(
        self, report, rendered_tags
    ):
        """Closes the regex-misses-an-arm direction: an arm the extractor
        overlooks would list a working tag as unsupported (or a removed arm
        would keep a refused tag listed as supported); either is a finding
        here, by name. The message asserted is the engine's, not a render
        success — ``extends``/``include``/``url`` fail for other reasons."""
        for shape, rendered in rendered_tags.items():
            assert _direction_findings(report, rendered) == [], shape
        # The buckets are not vacuous.
        assert _UNSUPPORTED_TAG_TEXT in rendered_tags["djust_only"]["autoescape"]
        assert rendered_tags["djust_only"]["with"] == "OK:1"
        assert rendered_tags["djust_only"]["regroup"] == "OK:2"

    def test_tag_outcomes_do_not_depend_on_a_django_engine(self, rendered_tags):
        """The bridge forwards filters only; a configured ``DjangoTemplates``
        engine must not change which tags the backend accepts."""
        only, with_dj = rendered_tags["djust_only"], rendered_tags["with_django"]
        assert {n for n, r in only.items() if _UNSUPPORTED_TAG_TEXT in r} == {
            n for n, r in with_dj.items() if _UNSUPPORTED_TAG_TEXT in r
        }


class TestBridgedLibraryFilters:
    """The block's library-filter lines are true for BOTH ``TEMPLATES`` shapes,
    and say which one each name needs (#2533 review 🔴: the previous block
    called all nine unsupported, false in the doc's own recommended config)."""

    @pytest.fixture(scope="class")
    def rendered_filters(self, report):
        sources = _filter_sources(report.library_filters)
        return {
            "djust_only": _render_via_backend(sources, with_django_engine=False),
            "with_django": _render_via_backend(sources, with_django_engine=True),
        }

    def test_unlocalize_resolves_only_next_to_a_django_engine(self, rendered_filters):
        assert rendered_filters["with_django"]["unlocalize"] == "OK:1234"
        assert "Unknown filter: unlocalize" in rendered_filters["djust_only"]["unlocalize"]
        assert rendered_filters["with_django"]["language_name"] == "OK:German"

    def test_bridged_line_names_exactly_the_filters_that_behave_that_way(
        self, gen, report, rendered_filters
    ):
        """Wording pinned to behaviour: a name is on the *bridged* line iff it
        raises ``Unknown filter`` djust-only AND resolves with a
        ``DjangoTemplates`` engine; *native* iff it resolves both ways;
        *unsupported* iff it raises both ways."""
        only, with_dj = rendered_filters["djust_only"], rendered_filters["with_django"]

        def unknown(result: str) -> bool:
            return "Unknown filter" in result

        behaves_bridged = {n for n in only if unknown(only[n]) and not unknown(with_dj[n])}
        behaves_native = {n for n in only if not unknown(only[n]) and not unknown(with_dj[n])}
        behaves_unsupported = {n for n in only if unknown(only[n]) and unknown(with_dj[n])}
        assert behaves_bridged | behaves_native | behaves_unsupported == report.library_filters

        assert report.bridged_library_filters == behaves_bridged
        assert report.supported_library_filters == behaves_native
        assert report.unsupported_library_filters == behaves_unsupported
        assert "unlocalize" in behaves_bridged

        block = gen.render_block(report)
        line = next(ln for ln in block.splitlines() if ln.startswith("**Library filters — bridged"))
        assert set(re.findall(r"`([a-z_]+)`", line)) - {"DjangoTemplates"} == behaves_bridged
        assert f"({len(behaves_bridged)})" in line
        assert "raises `Unknown filter`" in block
        assert "**Library filters — unsupported (0):** none" in block
        # The committed doc carries the same three filter lines. Compared via
        # the script's own process: the in-process registry also holds the
        # test settings' extension tags (theming), which the doc does not list.
        code, printed, err = _run_split("--print")
        assert code == 0, err
        doc_block = gen.current_block(_DOC.read_text(encoding="utf-8"))
        assert printed == doc_block
        filter_lines = [ln for ln in block.splitlines() if ln.startswith("**Library filters")]
        assert filter_lines == [
            ln for ln in doc_block.splitlines() if ln.startswith("**Library filters")
        ]
        assert len(filter_lines) == 3

    def test_bridging_detection_refuses_to_run_without_a_django_engine(self, gen, monkeypatch):
        """An ambient ``TEMPLATES`` with no ``DjangoTemplates`` engine cannot
        silently produce an empty bridged set (which would read as
        "unsupported (9)" again); it is a structural error."""
        from django.template import engines

        monkeypatch.setattr(engines, "all", lambda: [])
        with pytest.raises(gen.ExtractionError, match="DjangoTemplates"):
            gen.bridged_library_filters({"unlocalize"})
