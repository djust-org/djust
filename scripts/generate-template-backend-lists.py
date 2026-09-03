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
* Bridged library tags and filters — detected, not assumed: the generator runs
  the real ``{% load %}`` hook (``template_libraries.load_libraries``, #2547 /
  #2558, the same call the parser makes) for each of Django's libraries on a
  djust-only ``TEMPLATES`` and reads back what landed in ``djust._rust``'s
  registries. A library tag or filter that lands there is *bridged on
  ``{% load``*: it resolves on any ``TEMPLATES`` shape once the template loads
  its library. The ``tz`` filters the bridge refuses by name (#2216: they need a
  datetime object the Rust ``Value`` cannot carry) are listed unsupported.

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
extension, a registry name that is not a plain identifier, an unwritable doc).
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import os
import re
import sys
import tempfile
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
        "endfilter",
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
_ARITY_ROW_RE = re.compile(r'^[ \t]*\("([a-z_0-9]+)",', re.M)
_SCOREBOARD_RE = re.compile(r"Unsupported template tag '\{% ([a-z_0-9]+)")
# Every name the doc block emits comes from a registry the generator reads at
# runtime (Django's, djust's tag handlers, the Rust filter registry). A name
# that is not a plain template identifier cannot be a real tag or filter and
# would land verbatim in the doc, so it is refused rather than rendered.
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


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
        # Both engines, the doc's recommended shape. The library buckets are
        # detected on the djust backend alone (``load_bridged_library_entries``
        # runs the ``{% load %}`` hook, which needs no other engine); the
        # ``DjangoTemplates`` fallback is here so the registries match a
        # project's, exactly as the Quick Start configures them.
        settings.configure(
            INSTALLED_APPS=["django.contrib.staticfiles"],
            STATIC_URL="/static/",
            TEMPLATES=[
                {
                    "BACKEND": "djust.template_backend.DjustTemplateBackend",
                    "NAME": "djust",
                    "DIRS": [],
                    "APP_DIRS": False,
                    "OPTIONS": {},
                },
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "NAME": "django",
                    "DIRS": [],
                    "APP_DIRS": False,
                    "OPTIONS": {},
                },
            ],
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


def scope_tags(parser_rs: Path = DEFAULT_PARSER_RS) -> set[str]:
    """Tag names with a native scope node the parser arms on ``{% load %}`` (#2558).

    ``{% language %}``, ``{% localize %}``, ``{% localtime %}`` and
    ``{% timezone %}`` are not match arms (``djust_native_tags`` cannot see
    them): each is a guard ``tag_name == "<name>" && scope_tag_armed("<name>")``
    that the library loader arms. Read off the ``scope_tag_armed("…")`` sites.
    """
    src = parser_rs.read_text(encoding="utf-8")
    names = set(re.findall(r'scope_tag_armed\("([a-z_0-9]+)"\)', src))
    if not names:
        raise ExtractionError(f'{parser_rs}: no `scope_tag_armed("…")` site — the regex is stale')
    return names


def load_bridged_library_entries(
    libraries: dict[str, tuple[set[str], set[str]]],
    parser_rs: Path = DEFAULT_PARSER_RS,
) -> tuple[set[str], set[str], set[str]]:
    """``(tags, filters, refused_filters)`` that ``{% load <lib> %}`` bridges (#2558).

    Runs the real ``{% load %}`` hook (``template_libraries.load_libraries``,
    the call the parser makes) for each library on the configured djust
    backend and reads back what landed in ``djust._rust``'s registries: the
    inline / block / raw-block tag registries, the scope nodes the loader
    arms (declared in ``template_libraries._NATIVE_SCOPE_TAGS`` and
    cross-checked against ``parser.rs``'s ``scope_tag_armed`` sites — a
    declared name with no parser arm, or an arm no library declares, is a
    structural error), and the custom-filter registry. ``refused_filters`` are
    the names the bridge registers as LOUD refusals rather than callables
    (``_TZ_FILTER_REFUSALS``, #2216): they parse, then raise. Detected rather
    than asserted so the buckets follow the loader if it changes what it
    bridges; a ``{% load %}`` that fails is a structural error, never an
    empty bucket.
    """
    _setup_django()
    try:
        from djust import _rust, template_libraries
    except ImportError as exc:  # pragma: no cover — environment, not logic
        raise ExtractionError(
            "djust._rust is not importable — build the extension (`make dev-build`) "
            f"before running this check ({exc})"
        ) from exc
    armed_sites = scope_tags(parser_rs)
    declared = {n for names in template_libraries._NATIVE_SCOPE_TAGS.values() for n in names}
    if declared != armed_sites:
        raise ExtractionError(
            "scope-node drift: template_libraries._NATIVE_SCOPE_TAGS declares "
            f"{sorted(declared)} but parser.rs arms {sorted(armed_sites)}"
        )
    tags: set[str] = set()
    filters: set[str] = set()
    refused: set[str] = set()
    for lib, (lib_tags, lib_filters) in libraries.items():
        try:
            template_libraries.load_libraries([lib])
        except Exception as exc:  # noqa: BLE001 — surfaced as a structural error
            raise ExtractionError(f"`{{% load {lib} %}}` failed: {exc}") from exc
        registered = set(_rust.get_registered_tags())
        tags |= {
            name
            for name in lib_tags
            if name in registered
            or _rust.has_block_tag_handler(name)
            or _rust.has_raw_block_tag_handler(name)
        }
        module = f"django.templatetags.{lib}"
        if module in template_libraries._DJANGO_LIBRARIES_BRIDGED:
            tags |= lib_tags & set(template_libraries._NATIVE_SCOPE_TAGS.get(module, ()))
            refused |= lib_filters & template_libraries.refused_filters(module)
        filters |= (lib_filters & set(_rust.get_registered_custom_filters())) - refused
    return tags, filters, refused


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
    #: What ``{% load <lib> %}`` bridges into the Rust registries (#2558):
    #: tags, filters, and the filters it registers as loud refusals.
    load_bridged_tags: set[str] = field(default_factory=set)
    load_bridged_filters: set[str] = field(default_factory=set)
    refused_filters: set[str] = field(default_factory=set)
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
        """Library tags the engine handles natively OR bridges on ``{% load %}``."""
        return self.library_tags & (self.djust_tags | self.load_bridged_tags)

    @property
    def unsupported_library_tags(self) -> set[str]:
        return self.library_tags - self.supported_library_tags

    @property
    def supported_library_filters(self) -> set[str]:
        """Library filters the Rust engine implements itself (no bridge needed)."""
        return self.library_filters & self.djust_filters

    @property
    def bridged_library_filters(self) -> set[str]:
        """Library filters that resolve once the template ``{% load %}``s
        their library (#2558) — on any ``TEMPLATES`` shape."""
        return (self.library_filters & self.load_bridged_filters) - self.djust_filters

    @property
    def unsupported_library_filters(self) -> set[str]:
        """Never resolve to a value: unknown to both, or bridged as a loud
        refusal (the ``tz`` three, #2216)."""
        return self.library_filters - self.djust_filters - self.load_bridged_filters

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
    # Read the handler registries BEFORE the `{% load %}` pass below, so a
    # bridged library tag is bucketed as bridged and not as a Python handler.
    inline, assign = djust_python_handlers()
    load_tags, load_filters, refused = load_bridged_library_entries(libraries, parser_rs)
    return SupportReport(
        django_version=version,
        django_filters=dj_filters,
        django_tags=dj_tags,
        libraries=libraries,
        djust_filters=djust_arity_filters(arity_rs),
        native_tags=djust_native_tags(parser_rs),
        handler_tags=inline | assign,
        load_bridged_tags=load_tags,
        load_bridged_filters=load_filters,
        refused_filters=refused,
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _codes(names: set[str]) -> str:
    bad = sorted(n for n in names if not _NAME_RE.match(n))
    if bad:
        raise ExtractionError(
            f"refusing to emit registry names that are not plain identifiers: {bad}"
        )
    return ", ".join(f"`{n}`" for n in sorted(names)) if names else "none"


def _by_library(report: SupportReport, pick: str, chosen_pool: set[str]) -> str:
    """``chosen_pool`` grouped by the library that registers each name."""
    parts = []
    for lib in DJANGO_LIBRARIES:
        tags, filters = report.libraries[lib]
        pool = tags if pick == "tags" else filters
        chosen = pool & chosen_pool
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
        f"{_by_library(report, 'tags', report.supported_library_tags)}",
        "",
        f"**Library tags — unsupported ({len(report.unsupported_library_tags)}):** "
        f"{_by_library(report, 'tags', report.unsupported_library_tags)}",
        "",
        "A supported library tag is either a native Rust node or bridged on `{% load %}` "
        "(#2547 / #2558): the load imports Django's library and registers its tags with the "
        "Rust engine, so the tag is rendered by Django's own compile function and node — "
        "`{% blocktranslate %}` crosses its body as raw source; `{% language %}`, "
        "`{% localize %}`, `{% localtime %}` and `{% timezone %}` are native scope nodes "
        "the load arms.",
        "",
        f"**Library filters — native ({len(report.supported_library_filters)}):** "
        f"{_codes(report.supported_library_filters)}",
        "",
        f"**Library filters — bridged on `{{% load %}}` "
        f"({len(report.bridged_library_filters)}):** "
        f"{_by_library(report, 'filters', report.bridged_library_filters)}",
        "",
        "Bridged filters are Django's own callables, forwarded to the Rust engine by the "
        "filter bridge when the template loads their library (#2558) — on any `TEMPLATES` "
        "shape, a `DjangoTemplates` engine beside djust or not.",
        "",
        f"**Library filters — unsupported ({len(report.unsupported_library_filters)}):** "
        f"{_by_library(report, 'filters', report.unsupported_library_filters)}",
        "",
        f"Refused loudly on `{{% load %}}` ({len(report.refused_filters)}): "
        f"{_by_library(report, 'filters', report.refused_filters)} — each needs a datetime "
        "object on the wire, which the Rust `Value` cannot carry (#2216), so the load "
        "registers it as a filter that raises `TemplateSyntaxError` naming the filter and "
        "pointing at `date` with the active zone (#2209) — never a silent blank (#2541).",
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


def _write_atomically(doc: Path, text: str) -> None:
    """Replace ``doc`` via a same-directory temp file + ``os.replace``.

    A read-only doc is refused up front (``os.replace`` would otherwise
    swap it out regardless of its mode); a failure mid-write leaves the
    original untouched and removes the temp file.
    """
    if doc.exists() and not os.access(doc, os.W_OK):
        raise PermissionError(f"{doc} is not writable")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{doc.name}.", suffix=".tmp", dir=doc.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if doc.exists():
            os.chmod(tmp, doc.stat().st_mode & 0o7777)
        os.replace(tmp, doc)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        report = build_report(arity_rs=args.arity_rs, parser_rs=args.parser_rs)
        block = render_block(report)
    except ExtractionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.print:
        sys.stdout.write(block)
        return 0

    if args.cross_check is not None:
        if not args.cross_check.exists():
            print(
                f"ERROR: {_rel(args.cross_check)} not found (run `make django-template-suite`)",
                file=sys.stderr,
            )
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
        try:
            _write_atomically(args.doc, splice_block(doc_text, block))
        except OSError as exc:
            print(f"ERROR: cannot write {_rel(args.doc)}: {exc}", file=sys.stderr)
            return 2
        print(f"{_rel(args.doc)}: generated block rewritten")
        return 0

    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        block.splitlines(keepends=True),
        fromfile=f"{_rel(args.doc)} (committed)",
        tofile=f"{_rel(args.doc)} (generated)",
    )
    sys.stdout.writelines(diff)
    print(
        f"\nERROR: the generated block in {_rel(args.doc)} is stale — "
        "run: make template-backend-lists"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
