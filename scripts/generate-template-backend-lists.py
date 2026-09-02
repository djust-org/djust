#!/usr/bin/env python3
"""Generate docs/TEMPLATE_BACKEND.md's supported/unsupported tag and filter lists (#2533).

The lists are derived from the engine's own registries, never written by hand:

* djust built-in filters — the ``ARITY`` table in
  ``crates/djust_templates/src/filter_arity.rs`` (read from source; it is the
  table ``filters::is_known_filter`` consults, and its equality with the
  dispatch arms is pinned by ``python/tests/test_unknown_filter_parse_time_2419.py``).
* djust native tags — the ``match tag_name.as_str()`` arms in
  ``crates/djust_templates/src/parser.rs`` (read from source, so the check sees
  the working tree rather than a possibly stale compiled extension).
* djust Python tag handlers — ``python/djust/template_tags/`` registrations,
  read at runtime out of ``djust._rust``'s tag registry (``url``, ``regroup`` ...).
* Django's sets — ``django.template.defaultfilters`` / ``defaulttags`` /
  ``loader_tags`` (the engine's ``default_builtins``) and the ``i18n``, ``l10n``,
  ``tz``, ``static`` and ``cache`` libraries, at the installed Django version.

Modes::

    python scripts/generate-template-backend-lists.py            # check: exit 1 + diff when stale
    python scripts/generate-template-backend-lists.py --write    # regenerate the block in place
    python scripts/generate-template-backend-lists.py --print    # print the block
    python scripts/generate-template-backend-lists.py --cross-check .django-src/last-run.txt

The generated region sits between ``<!-- generated:template-backend-lists -->``
and ``<!-- /generated:template-backend-lists -->`` in the doc. Output is
deterministic: sorted names, counts and the Django version only, no timestamps.

Exit codes: 0 clean, 1 the doc's block is stale (or the cross-check disagrees),
2 a structural error (missing markers, stale extraction regex, no compiled
extension).
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DOC = REPO / "docs" / "TEMPLATE_BACKEND.md"
DEFAULT_ARITY_RS = REPO / "crates" / "djust_templates" / "src" / "filter_arity.rs"
DEFAULT_PARSER_RS = REPO / "crates" / "djust_templates" / "src" / "parser.rs"

MARKER_OPEN = "<!-- generated:template-backend-lists -->"
MARKER_CLOSE = "<!-- /generated:template-backend-lists -->"

# Django's own ``{% load %}`` libraries, in the order they are reported.
DJANGO_LIBRARIES = ("i18n", "l10n", "tz", "static", "cache")

# Closing/branch keywords the parser matches on that Django does not register
# as tags either (``{% endif %}`` is consumed by ``{% if %}``'s parser).
CLOSERS = frozenset(
    {
        "endif",
        "endfor",
        "endblock",
        "else",
        "elif",
        "endcomment",
        "endverbatim",
        "endwith",
        "endspaceless",
    }
)

# Names that MUST come out of each source extraction. A refactor of the Rust
# source that moves the table/match out from under the regex fails here,
# loudly, instead of emitting an empty (and therefore all-unsupported) list.
ARITY_SENTINELS = frozenset({"escape", "date", "yesno"})
PARSER_SENTINELS = frozenset({"if", "for", "block"})

ARITY_HEADER = "const ARITY: &[(&str, u8, u8, u8)] = &["
PARSER_MATCH = "match tag_name.as_str() {"
_ARM_RE = re.compile(r'^ {16}("[a-z_0-9]+"(?:\s*\|\s*"[a-z_0-9]+")*)\s*=>')
_ARITY_ROW_RE = re.compile(r'^\s*\("([a-z_0-9]+)",', re.M)
_SCOREBOARD_RE = re.compile(r"Unsupported template tag '\{% ([a-z_0-9]+)")


class ExtractionError(RuntimeError):
    """A source extraction found nothing usable — the regex or the source moved."""


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


def djust_arity_filters(arity_rs: Path = DEFAULT_ARITY_RS) -> set[str]:
    """Filter names in the ``ARITY`` table of ``filter_arity.rs``."""
    src = arity_rs.read_text(encoding="utf-8")
    if ARITY_HEADER not in src:
        raise ExtractionError(f"{arity_rs}: `{ARITY_HEADER}` not found")
    body = src.split(ARITY_HEADER, 1)[1].split("];", 1)[0]
    names = set(_ARITY_ROW_RE.findall(body))
    missing = ARITY_SENTINELS - names
    if missing:
        raise ExtractionError(
            f"{arity_rs}: ARITY extraction is missing {sorted(missing)} — the row regex is stale"
        )
    return names


def djust_native_tags(parser_rs: Path = DEFAULT_PARSER_RS) -> set[str]:
    """Tag names with a native arm in ``parser.rs``'s ``match tag_name.as_str()``.

    Reads the arms at 16-space indent up to the ``_ =>`` fallthrough and
    drops the closers (``endif`` ...), which Django does not register as tags.
    """
    src = parser_rs.read_text(encoding="utf-8")
    if PARSER_MATCH not in src:
        raise ExtractionError(f"{parser_rs}: `{PARSER_MATCH}` not found")
    body = src.split(PARSER_MATCH, 1)[1]
    names: set[str] = set()
    for line in body.splitlines():
        if line.startswith("                _ =>"):
            break
        m = _ARM_RE.match(line)
        if m:
            names.update(re.findall(r'"([a-z_0-9]+)"', m.group(1)))
    missing = PARSER_SENTINELS - names
    if missing:
        raise ExtractionError(
            f"{parser_rs}: match-arm extraction is missing {sorted(missing)} — the arm regex is stale"
        )
    return names - CLOSERS


def _setup_django() -> None:
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=["django.contrib.staticfiles"],
            STATIC_URL="/static/",
            TEMPLATES=[],
        )
    from django.apps import apps

    if not apps.ready:
        django.setup()


def django_sets() -> tuple[str, set[str], set[str], dict[str, tuple[set[str], set[str]]]]:
    """``(version, builtin_filters, builtin_tags, {library: (tags, filters)})``."""
    _setup_django()
    import django
    from django.template import defaultfilters, defaulttags, loader_tags

    filters = set(defaultfilters.register.filters)
    tags = set(defaulttags.register.tags) | set(loader_tags.register.tags)
    libraries: dict[str, tuple[set[str], set[str]]] = {}
    for name in DJANGO_LIBRARIES:
        mod = importlib.import_module(f"django.templatetags.{name}")
        libraries[name] = (set(mod.register.tags), set(mod.register.filters))
    return django.__version__, filters, tags, libraries


def djust_python_handlers() -> tuple[set[str], set[str]]:
    """``(inline, assign)`` tag names registered from ``python/djust/template_tags/``."""
    _setup_django()
    try:
        from djust import _rust
    except ImportError as exc:  # pragma: no cover — environment, not logic
        raise ExtractionError(
            "djust._rust is not importable — build the extension (`make dev-build`) "
            f"before running this check ({exc})"
        ) from exc
    import djust.template_tags as template_tags

    inline = set(_rust.get_registered_tags())
    assign = {
        name
        for name in template_tags.get_registered_handlers()
        if _rust.has_assign_tag_handler(name)
    }
    return inline, assign


# --------------------------------------------------------------------------- #
# Bucketing
# --------------------------------------------------------------------------- #


@dataclass
class SupportReport:
    django_version: str
    django_filters: set[str]
    django_tags: set[str]
    libraries: dict[str, tuple[set[str], set[str]]]
    djust_filters: set[str]
    native_tags: set[str]
    handler_tags: set[str]
    scoreboard: set[str] = field(default_factory=set)

    # -- filters ---------------------------------------------------------- #
    @property
    def supported_filters(self) -> set[str]:
        return self.django_filters & self.djust_filters

    @property
    def unsupported_filters(self) -> set[str]:
        return self.django_filters - self.djust_filters

    # -- tags ------------------------------------------------------------- #
    @property
    def djust_tags(self) -> set[str]:
        return self.native_tags | self.handler_tags

    @property
    def supported_native_tags(self) -> set[str]:
        return self.django_tags & self.native_tags

    @property
    def supported_handler_tags(self) -> set[str]:
        # Native beats handler on a collision: the parser tries the match arms
        # before the registry fallthrough.
        return (self.django_tags & self.handler_tags) - self.native_tags

    @property
    def unsupported_tags(self) -> set[str]:
        return self.django_tags - self.djust_tags

    @property
    def djust_only_tags(self) -> set[str]:
        return self.djust_tags - self.django_tags - self.library_tags

    # -- libraries -------------------------------------------------------- #
    @property
    def library_tags(self) -> set[str]:
        return {t for tags, _ in self.libraries.values() for t in tags}

    @property
    def library_filters(self) -> set[str]:
        return {f for _, filters in self.libraries.values() for f in filters}

    @property
    def supported_library_tags(self) -> set[str]:
        return self.library_tags & self.djust_tags

    @property
    def unsupported_library_tags(self) -> set[str]:
        return self.library_tags - self.djust_tags

    @property
    def supported_library_filters(self) -> set[str]:
        return self.library_filters & self.djust_filters

    @property
    def unsupported_library_filters(self) -> set[str]:
        return self.library_filters - self.djust_filters

    @property
    def all_unsupported_tags(self) -> set[str]:
        """Every Django built-in or library tag the backend rejects."""
        return self.unsupported_tags | self.unsupported_library_tags


def build_report(
    *,
    arity_rs: Path = DEFAULT_ARITY_RS,
    parser_rs: Path = DEFAULT_PARSER_RS,
) -> SupportReport:
    version, dj_filters, dj_tags, libraries = django_sets()
    inline, assign = djust_python_handlers()
    return SupportReport(
        django_version=version,
        django_filters=dj_filters,
        django_tags=dj_tags,
        libraries=libraries,
        djust_filters=djust_arity_filters(arity_rs),
        native_tags=djust_native_tags(parser_rs),
        handler_tags=inline | assign,
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _codes(names: set[str]) -> str:
    return ", ".join(f"`{n}`" for n in sorted(names)) if names else "none"


def _by_library(report: SupportReport, pick: str) -> str:
    parts = []
    for lib in DJANGO_LIBRARIES:
        tags, filters = report.libraries[lib]
        pool = tags if pick == "tags" else filters
        chosen = pool & (
            report.unsupported_library_tags
            if pick == "tags"
            else report.unsupported_library_filters
        )
        if chosen:
            parts.append(f"{lib} {_codes(chosen)}")
    return "; ".join(parts) if parts else "none"


def render_block(report: SupportReport) -> str:
    """The generated region, markers included, ending with a newline."""
    n_f = len(report.django_filters)
    n_t = len(report.django_tags)
    supported_t = len(report.supported_native_tags) + len(report.supported_handler_tags)
    lines = [
        MARKER_OPEN,
        "_This block is generated by `scripts/generate-template-backend-lists.py` from the "
        "engine's own registries. Do not edit it by hand; run `make template-backend-lists`._",
        "",
        f"Reference: Django {report.django_version} — `django.template.defaultfilters`, "
        "`defaulttags` and `loader_tags` (the engine's `default_builtins`), plus the "
        + ", ".join(f"`{lib}`" for lib in DJANGO_LIBRARIES)
        + " libraries.",
        "",
        f"**Built-in filters — {len(report.supported_filters)} of {n_f} supported (native):** "
        f"{_codes(report.supported_filters)}",
        "",
        f"**Built-in filters — unsupported ({len(report.unsupported_filters)}):** "
        f"{_codes(report.unsupported_filters)}",
        "",
        f"**Built-in tags — {supported_t} of {n_t} supported:**",
        f"- native Rust ({len(report.supported_native_tags)}): "
        f"{_codes(report.supported_native_tags)}",
        f"- via Python handler ({len(report.supported_handler_tags)}): "
        f"{_codes(report.supported_handler_tags)}",
        "",
        f"**Built-in tags — unsupported ({len(report.unsupported_tags)}):** "
        f"{_codes(report.unsupported_tags)}",
        "",
        f"**Library tags (`{{% load … %}}`) — supported ({len(report.supported_library_tags)}):** "
        f"{_codes(report.supported_library_tags)}",
        "",
        f"**Library tags — unsupported ({len(report.unsupported_library_tags)}):** "
        f"{_by_library(report, 'tags')}",
        "",
        f"**Library filters — supported ({len(report.supported_library_filters)}):** "
        f"{_codes(report.supported_library_filters)}",
        "",
        f"**Library filters — unsupported ({len(report.unsupported_library_filters)}):** "
        f"{_by_library(report, 'filters')}",
        "",
        f"**djust extensions (not Django tags, not scored):** {_codes(report.djust_only_tags)}",
        MARKER_CLOSE,
    ]
    return "\n".join(lines) + "\n"


def find_block(doc_text: str) -> tuple[int, int]:
    """``(start, end)`` character offsets of the block, markers included."""
    start = doc_text.find(MARKER_OPEN)
    if start < 0:
        raise ExtractionError(f"opening marker `{MARKER_OPEN}` not found in the doc")
    end = doc_text.find(MARKER_CLOSE, start)
    if end < 0:
        raise ExtractionError(f"unclosed generated block: `{MARKER_CLOSE}` not found in the doc")
    end += len(MARKER_CLOSE)
    if doc_text.find(MARKER_OPEN, end) >= 0:
        raise ExtractionError(f"more than one `{MARKER_OPEN}` block in the doc")
    # Swallow the newline that terminates the closing marker line, if any.
    if doc_text[end : end + 1] == "\n":
        end += 1
    return start, end


def current_block(doc_text: str) -> str:
    start, end = find_block(doc_text)
    return doc_text[start:end]


def splice_block(doc_text: str, block: str) -> str:
    start, end = find_block(doc_text)
    return doc_text[:start] + block + doc_text[end:]


# --------------------------------------------------------------------------- #
# Scoreboard cross-check
# --------------------------------------------------------------------------- #


def scoreboard_unsupported_tags(path: Path) -> set[str]:
    """Distinct tag names in ``Unsupported template tag '{% X`` ERROR lines."""
    return set(_SCOREBOARD_RE.findall(path.read_text(encoding="utf-8")))


def cross_check(report: SupportReport, scoreboard_path: Path) -> list[str]:
    """Reconcile the generated unsupported set with the #2517 scoreboard.

    Rule: every scoreboard tag that is a Django built-in or library tag must be
    in the generated unsupported set. Names that are neither (Django's own
    test-suite custom libraries, ``template_tests/templatetags/custom.py``) are
    reported but not counted — they are not support-list items. Generated
    names the suite never exercises are reported; the generator is the
    authority there.
    """
    board = scoreboard_unsupported_tags(scoreboard_path)
    known = report.django_tags | report.library_tags
    generated = report.all_unsupported_tags
    problems = []
    disagree = (board & known) - generated
    if disagree:
        problems.append(
            "scoreboard reports these Django tags as unsupported but the generator "
            f"lists them as supported: {sorted(disagree)}"
        )
    return problems


def cross_check_summary(report: SupportReport, scoreboard_path: Path) -> str:
    board = scoreboard_unsupported_tags(scoreboard_path)
    known = report.django_tags | report.library_tags
    generated = report.all_unsupported_tags
    return "\n".join(
        [
            f"scoreboard: {len(board)} distinct unsupported tag names in {scoreboard_path}",
            f"  Django built-in/library, in generated set: {sorted(board & known & generated)}",
            f"  Django built-in/library, NOT in generated set: {sorted((board & known) - generated)}",
            f"  test-suite custom tags (not support-list items): {sorted(board - known)}",
            f"  generated-unsupported never exercised by the suite: {sorted(generated - board)}",
        ]
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="doc to check/rewrite")
    p.add_argument("--arity-rs", type=Path, default=DEFAULT_ARITY_RS)
    p.add_argument("--parser-rs", type=Path, default=DEFAULT_PARSER_RS)
    p.add_argument("--write", action="store_true", help="rewrite the generated block in place")
    p.add_argument("--print", action="store_true", help="print the generated block and exit")
    p.add_argument(
        "--cross-check",
        type=Path,
        metavar="LAST_RUN_TXT",
        help="reconcile against a `run-django-template-suite.py --parsed-output` file",
    )
    return p


def _rel(path: Path) -> Path:
    return path.relative_to(REPO) if path.is_relative_to(REPO) else path


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        report = build_report(arity_rs=args.arity_rs, parser_rs=args.parser_rs)
    except ExtractionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    block = render_block(report)

    if args.print:
        sys.stdout.write(block)
        return 0

    if args.cross_check is not None:
        if not args.cross_check.exists():
            print(f"ERROR: {args.cross_check} not found (run `make django-template-suite`)")
            return 2
        print(cross_check_summary(report, args.cross_check))
        problems = cross_check(report, args.cross_check)
        for problem in problems:
            print(f"ERROR: {problem}")
        if problems:
            return 1

    doc_text = args.doc.read_text(encoding="utf-8")
    try:
        old = current_block(doc_text)
    except ExtractionError as exc:
        print(f"ERROR: {args.doc}: {exc}", file=sys.stderr)
        return 2

    if old == block:
        print(f"{_rel(args.doc)}: unchanged")
        return 0

    if args.write:
        args.doc.write_text(splice_block(doc_text, block), encoding="utf-8")
        print(f"{_rel(args.doc)}: generated block rewritten")
        return 0

    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        block.splitlines(keepends=True),
        fromfile=f"{args.doc} (committed)",
        tofile=f"{args.doc} (generated)",
    )
    sys.stdout.writelines(diff)
    print(f"\nERROR: the generated block in {args.doc} is stale — run: make template-backend-lists")
    return 1


if __name__ == "__main__":
    sys.exit(main())
