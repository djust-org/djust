"""
Rendered-page accessibility pass for ``djust_audit --a11y`` (#3700).

The ``Y`` rules (``djust.checks.accessibility``) scan template *text*, so
they cannot see text and non-text contrast (WCAG 1.4.3, 1.4.11), landmarks,
heading order, or an accessible name that only exists after render. This
module loads each page in headless Chromium, injects `axe-core
<https://github.com/dequelabs/axe-core>`_ and reports its violations.

Optional extra
==============

The browser tooling is **not** a core dependency. ``pip install
'djust[a11y]'`` brings Playwright (the browser driver djust's own browser
tests use) and ``axe-playwright-python``, which ships ``axe.min.js``. djust
reads that file from the installed package and injects it into each page
itself: nothing is fetched at audit time and no axe-core copy is vendored
into djust. Importing djust, or running any other audit, never imports
Playwright; :func:`load_tooling` raises :class:`A11yExtraMissing` naming the
extra when it is not installed.

Exit contract
=============

axe-core reports an ``impact`` per violation: ``minor``, ``moderate``,
``serious`` or ``critical``. The rendered pass keeps djust_audit's
warning-only contract for accessibility:

* normal mode exits 0 whatever the violations;
* ``--strict`` exits 1 when any violation is ``serious`` or ``critical``
  (:data:`FAILING_IMPACTS`); ``minor`` / ``moderate`` stay warnings;
* a page that could not be audited (unreachable, HTTP 4xx/5xx, navigation
  timeout) exits 1 in both modes, as ``--live`` does for an unreachable
  target: a CI gate must not pass on a page it never saw.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

#: The pip extra that brings the browser tooling.
A11Y_EXTRA = "djust[a11y]"
INSTALL_HINT = (
    "The rendered-page accessibility pass needs the optional browser tooling: "
    "pip install '%s' (Playwright and axe-core), then once: playwright install chromium"
    % A11Y_EXTRA
)

#: axe-core impact levels, least to most severe.
IMPACT_ORDER: Tuple[str, ...] = ("minor", "moderate", "serious", "critical")
#: Impacts that fail ``--strict``.
FAILING_IMPACTS = frozenset({"serious", "critical"})

#: The package in the ``a11y`` extra that ships axe-core's ``axe.min.js``.
AXE_PACKAGE = "axe_playwright_python"
AXE_FILE = "axe.min.js"

#: Nodes listed per violation in the terminal report (JSON lists them all).
MAX_NODES_SHOWN = 5


class A11yExtraMissing(RuntimeError):
    """The ``djust[a11y]`` extra (Playwright / axe-core) is not installed."""


class A11yRouteError(ValueError):
    """The routes to audit could not be determined (bad input)."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def impact_rank(impact: Optional[str]) -> int:
    """Position of ``impact`` in :data:`IMPACT_ORDER`; -1 when unknown/None."""
    try:
        return IMPACT_ORDER.index(impact or "")
    except ValueError:
        return -1


def is_failing_impact(impact: Optional[str]) -> bool:
    """True when a violation of this impact fails ``--strict``."""
    return impact in FAILING_IMPACTS


@dataclass
class RenderedViolation:
    """One axe-core rule violated on one page."""

    url: str
    rule_id: str
    impact: Optional[str]
    help: str
    description: str = ""
    help_url: str = ""
    tags: List[str] = field(default_factory=list)
    nodes: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_axe(cls, url: str, raw: Mapping[str, Any]) -> "RenderedViolation":
        nodes = []
        for node in raw.get("nodes") or ():
            nodes.append(
                {
                    "target": [str(t) for t in node.get("target") or ()],
                    "html": str(node.get("html") or ""),
                    "failure_summary": str(node.get("failureSummary") or ""),
                }
            )
        return cls(
            url=url,
            rule_id=str(raw.get("id") or ""),
            impact=raw.get("impact"),
            help=str(raw.get("help") or ""),
            description=str(raw.get("description") or ""),
            help_url=str(raw.get("helpUrl") or ""),
            tags=[str(t) for t in raw.get("tags") or ()],
            nodes=nodes,
        )

    @property
    def failing(self) -> bool:
        return is_failing_impact(self.impact)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.rule_id,
            "impact": self.impact,
            "help": self.help,
            "description": self.description,
            "help_url": self.help_url,
            "tags": list(self.tags),
            "url": self.url,
            "nodes": [dict(n) for n in self.nodes],
        }


@dataclass
class PageResult:
    """The axe run (or the failure to run it) for one URL."""

    url: str
    final_url: str = ""
    status: Optional[int] = None
    error: str = ""
    violations: List[RenderedViolation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "error": self.error,
            "violations": len(self.violations),
        }


@dataclass
class RenderedA11yReport:
    """Aggregated result of the rendered pass over every URL."""

    pages: List[PageResult] = field(default_factory=list)
    axe_version: str = ""

    @property
    def violations(self) -> List[RenderedViolation]:
        return [v for page in self.pages for v in page.violations]

    @property
    def failing(self) -> List[RenderedViolation]:
        return [v for v in self.violations if v.failing]

    @property
    def page_errors(self) -> List[PageResult]:
        return [p for p in self.pages if p.error]

    def impact_counts(self) -> Dict[str, int]:
        counts = {impact: 0 for impact in IMPACT_ORDER}
        for v in self.violations:
            key = v.impact if v.impact in counts else "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "pages": len(self.pages),
            "page_errors": len(self.page_errors),
            "violations": len(self.violations),
            "failing": len(self.failing),
        }
        summary.update(self.impact_counts())
        return {
            "axe_version": self.axe_version,
            "pages": [p.to_dict() for p in self.pages],
            "a11y_rendered_findings": [v.to_dict() for v in self.violations],
            "summary": summary,
        }


def exit_code(report: RenderedA11yReport, strict: bool) -> int:
    """Map a report onto djust_audit's exit contract (see the module doc)."""
    if report.page_errors:
        return 1
    if strict and report.failing:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def resolve_urls(urls: Iterable[str], base_url: Optional[str]) -> List[str]:
    """Absolute URLs for ``--url`` values; a relative one joins ``base_url``."""
    out: List[str] = []
    for raw in urls:
        url = raw.strip()
        if urlparse(url).scheme in ("http", "https"):
            out.append(url)
        elif base_url:
            out.append(urljoin(base_url, url))
        else:
            raise A11yRouteError(
                "--url %r is not an absolute http(s) URL; pass a full URL or --base-url" % raw
            )
    return list(dict.fromkeys(out))


def routes_from_permissions(
    view_paths: Iterable[str],
    route_map: Mapping[str, str],
) -> Tuple[List[str], List[str]]:
    """Paths to audit for the views a permissions document declares.

    ``route_map`` is ``{path: "module.QualName"}`` as
    :func:`djust.routing.build_route_map_from_urlconf` returns it (URL
    parameters already written ``:name``). Each declared view contributes
    the first route that needs no URL argument. Returns ``(paths, notes)``:
    ``notes`` names each declared view that has no such route, so the
    caller can say what was not audited.
    """
    declared = list(dict.fromkeys(view_paths))
    by_view: Dict[str, List[str]] = {}
    for path, view in route_map.items():
        by_view.setdefault(view, []).append(path)
    paths: List[str] = []
    notes: List[str] = []
    for view in declared:
        candidates = by_view.get(view, [])
        plain = [p for p in candidates if ":" not in p]
        if plain:
            paths.append(plain[0])
        elif candidates:
            notes.append("%s: every route needs URL arguments (%s)" % (view, candidates[0]))
        else:
            notes.append("%s: no route in the URLconf" % view)
    return list(dict.fromkeys(paths)), notes


def urls_from_permissions_file(
    permissions_path: str, base_url: str, urlconf: Any = None
) -> Tuple[List[str], List[str]]:
    """Absolute URLs for the routes of the views ``permissions_path`` declares."""
    from djust.permissions import PermissionsDocument, PermissionsDocumentError
    from djust.routing import build_route_map_from_urlconf

    if not os.path.exists(permissions_path):
        raise A11yRouteError(
            "no --url given and permissions document %r not found; pass --url "
            "or --permissions <path>" % permissions_path
        )
    try:
        doc = PermissionsDocument.load(permissions_path)
    except PermissionsDocumentError as exc:
        raise A11yRouteError("permissions document error: %s" % exc) from exc
    paths, notes = routes_from_permissions(doc.views, build_route_map_from_urlconf(urlconf))
    return [urljoin(base_url, p) for p in paths], notes


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


def load_tooling() -> Tuple[Any, str]:
    """``(sync_playwright, axe_source)``, or :class:`A11yExtraMissing`.

    Imported lazily so nothing outside the rendered pass needs the extra.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise A11yExtraMissing("Playwright is not installed. " + INSTALL_HINT) from exc
    try:
        from importlib.resources import files

        axe_source = files(AXE_PACKAGE).joinpath(AXE_FILE).read_text(encoding="utf-8")
    except (ImportError, FileNotFoundError, OSError) as exc:
        raise A11yExtraMissing("axe-core is not installed. " + INSTALL_HINT) from exc
    return sync_playwright, axe_source


_RUN_AXE = """async (options) => {
  const results = await window.axe.run(document, options);
  return {version: results.testEngine && results.testEngine.version,
          violations: results.violations};
}"""


def run_rendered_audit(
    urls: List[str],
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> RenderedA11yReport:
    """Load each URL in headless Chromium and run axe-core over it.

    Raises :class:`A11yExtraMissing` when the ``a11y`` extra is not installed,
    or when Chromium cannot be launched (no browser installed).
    """
    sync_playwright, axe_source = load_tooling()
    from playwright.sync_api import Error as PlaywrightError

    report = RenderedA11yReport()
    timeout_ms = timeout * 1000
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except PlaywrightError as exc:
            raise A11yExtraMissing(
                "Chromium could not be launched (%s). %s" % (str(exc).splitlines()[0], INSTALL_HINT)
            ) from exc
        try:
            # bypass_csp: a strict Content-Security-Policy must not stop the
            # injected axe script; the page itself is unchanged.
            context = browser.new_context(extra_http_headers=extra_headers or None, bypass_csp=True)
            for url in urls:
                report.pages.append(_audit_page(context, url, axe_source, timeout_ms, report))
            context.close()
        finally:
            browser.close()
    return report


def _audit_page(
    context: Any, url: str, axe_source: str, timeout_ms: float, report: RenderedA11yReport
) -> PageResult:
    from playwright.sync_api import Error as PlaywrightError

    result = PageResult(url=url)
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="load", timeout=timeout_ms)
        result.final_url = page.url
        result.status = response.status if response is not None else None
        if result.status is not None and result.status >= 400:
            result.error = "HTTP %s" % result.status
            return result
        try:
            # Let a LiveView finish mounting; a page that never goes quiet
            # (polling, long-lived requests) is audited as it stands.
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
        except PlaywrightError:
            logger.debug("%s did not reach networkidle; auditing as loaded", url)
        page.evaluate(axe_source)
        raw = page.evaluate(_RUN_AXE, {"resultTypes": ["violations"]})
        report.axe_version = report.axe_version or str(raw.get("version") or "")
        result.violations = [RenderedViolation.from_axe(url, v) for v in raw["violations"]]
        result.violations.sort(key=lambda v: (-impact_rank(v.impact), v.rule_id))
    except PlaywrightError as exc:
        result.error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    finally:
        page.close()
    return result
