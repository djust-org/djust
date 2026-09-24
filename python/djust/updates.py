"""One-line notice of newer djust releases and matching security advisories.

Design: docs/superpowers/specs/2026-09-16-update-check-design.md.

The notice never prompts, never upgrades and never blocks. Two GETs (PyPI for
the latest version, GitHub for published advisories) run at most once a day,
their result is cached in the user cache directory, and every failure is
silent. ``should_check`` is the single gate every entry point consults.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

logger = logging.getLogger("djust.updates")

PYPI_URL = "https://pypi.org/pypi/djust/json"
ADVISORIES_URL = (
    "https://api.github.com/repos/djust-org/djust/security-advisories?state=published&per_page=100"
)
ADVISORIES_PAGE = "https://github.com/djust-org/djust/security/advisories"
TIMEOUT_SECONDS = 2
CACHE_TTL = 24 * 60 * 60
# A cached advisory that MATCHES the installed version is re-checked after an
# hour, not a day (#3006): that is the case where a stale cache misleads, e.g.
# an advisory whose range was corrected to exclude the installed release kept
# telling developers to upgrade a patched version for up to 24 h.
ADVISORY_MATCH_TTL = 60 * 60
FAILURE_BACKOFF = 60 * 60
MAX_LISTED_ADVISORIES = 3


@dataclass(frozen=True)
class Advisory:
    ghsa_id: str
    severity: str
    range: str  # PEP 440 specifier, e.g. "<= 1.1.1"
    patched: str  # first fixed version, e.g. "1.1.2"

    def to_dict(self) -> dict:
        return {
            "ghsa_id": self.ghsa_id,
            "severity": self.severity,
            "range": self.range,
            "patched": self.patched,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Advisory":
        return cls(data["ghsa_id"], data["severity"], data["range"], data["patched"])


def parse_advisories(payload: Any) -> List[Advisory]:
    """Advisories from GitHub's ``security-advisories`` API; bad entries skipped."""
    found: List[Advisory] = []
    if not isinstance(payload, list):
        return found
    for entry in payload:
        try:
            ghsa_id = entry["ghsa_id"]
            severity = entry.get("severity") or "unknown"
            for vuln in entry["vulnerabilities"]:
                package = vuln.get("package") or {}
                if package.get("name", "djust") != "djust":
                    continue
                spec = vuln["vulnerable_version_range"]
                patched = vuln.get("patched_versions") or ""
                SpecifierSet(spec)  # validates
                found.append(Advisory(ghsa_id, severity, spec, patched))
        except (KeyError, TypeError, InvalidSpecifier):
            continue
    return found


def advisories_for(version: str, advisories: Iterable[Advisory]) -> List[Advisory]:
    """The advisories whose vulnerable range covers ``version``."""
    try:
        installed = Version(version)
    except InvalidVersion:
        return []
    matched = []
    for a in advisories:
        try:
            if SpecifierSet(a.range).contains(installed, prereleases=True):
                matched.append(a)
        except (InvalidSpecifier, TypeError):
            continue
    return matched


def is_newer(installed: str, latest: str) -> bool:
    """Whether ``latest`` is worth mentioning to someone on ``installed``.

    A final release is never told about a pre-release; a pre-release is told
    about anything higher, final or not.
    """
    try:
        have, new = Version(installed), Version(latest)
    except InvalidVersion:
        return False
    if new.is_prerelease and not have.is_prerelease:
        return False
    return new > have


@dataclass
class UpdateStatus:
    installed: str
    latest: Optional[str]
    advisories: List[Advisory]

    @property
    def has_update(self) -> bool:
        return bool(self.latest) and is_newer(self.installed, self.latest or "")

    def upgrade_target(self) -> Optional[str]:
        """The version to name: the highest patched version, or the latest
        release when that is higher still."""
        candidates = [a.patched for a in self.advisories if a.patched]
        if self.has_update and self.latest:
            candidates.append(self.latest)
        parsed = []
        for candidate in candidates:
            try:
                parsed.append(Version(candidate))
            except InvalidVersion:
                continue
        return str(max(parsed)) if parsed else None

    def message(self, install_hint: str) -> Optional[str]:
        if self.advisories:
            shown = [a.ghsa_id for a in self.advisories[:MAX_LISTED_ADVISORIES]]
            more = len(self.advisories) - len(shown)
            ids = ", ".join(shown) + (" and %d more" % more if more else "")
            noun = "advisory" if len(self.advisories) == 1 else "advisories"
            target = self.upgrade_target()
            upgrade = "upgrade to %s or later" % target if target else "upgrade"
            return "SECURITY: djust %s has %d published %s (%s): %s: %s. %s" % (
                self.installed,
                len(self.advisories),
                noun,
                ids,
                upgrade,
                install_hint,
                ADVISORIES_PAGE,
            )
        if self.has_update:
            return "djust %s is available (you have %s): %s" % (
                self.latest,
                self.installed,
                install_hint,
            )
        return None


# --- cache -------------------------------------------------------------------


def cache_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("DJUST_CACHE_DIR"):
        base = Path(env["DJUST_CACHE_DIR"])
    elif env.get("XDG_CACHE_HOME"):
        base = Path(env["XDG_CACHE_HOME"]) / "djust"
    elif os.name == "nt" and env.get("LOCALAPPDATA"):
        base = Path(env["LOCALAPPDATA"]) / "djust"
    else:
        base = Path.home() / ".cache" / "djust"
    return base / "updates.json"


def load_cache(environ: Optional[Mapping[str, str]] = None) -> dict:
    try:
        data = json.loads(cache_path(environ).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data: dict, environ: Optional[Mapping[str, str]] = None) -> None:
    try:
        path = cache_path(environ)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a concurrent reader never sees a partial file.
        tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        logger.debug("update-check cache is not writable", exc_info=True)


# --- gate --------------------------------------------------------------------


def should_check(
    environ: Optional[Mapping[str, str]] = None,
    isatty: Optional[bool] = None,
    debug: Optional[bool] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> bool:
    """The single decision every entry point uses.

    ``isatty`` and ``debug`` are only consulted when given: the CLI passes
    ``isatty``, Django entry points pass ``debug``, the system check passes
    neither.
    """
    env = os.environ if environ is None else environ
    if env.get("DJUST_NO_UPDATE_CHECK") or env.get("CI") or env.get("PYTEST_CURRENT_TEST"):
        return False
    if config is not None and config.get("update_check", True) is False:
        return False
    if isatty is False or debug is False:
        return False
    return True


# --- fetch -------------------------------------------------------------------


def _user_agent() -> dict:
    from djust import __version__

    return {"User-Agent": "djust/%s" % __version__, "Accept": "application/json"}


def fetch_latest() -> Optional[str]:
    import requests

    response = requests.get(PYPI_URL, headers=_user_agent(), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    version = response.json()["info"]["version"]
    Version(version)  # validates
    return version


def fetch_advisories() -> List[Advisory]:
    import requests

    response = requests.get(ADVISORIES_URL, headers=_user_agent(), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return parse_advisories(response.json())


def _recent(stamp: Any, now: float, ttl: float) -> bool:
    return isinstance(stamp, (int, float)) and now - stamp < ttl


def _cached_advisories_match(cached: Mapping[str, Any], installed: str) -> bool:
    entries = cached.get("advisories")
    advisories = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            advisories.append(Advisory.from_dict(entry))
        except (KeyError, TypeError):
            continue
    return bool(advisories_for(installed, advisories))


def refresh(
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
    installed: Optional[str] = None,
) -> dict:
    """Return cached data, fetching when stale; a failure backs off for an hour.

    Stale means older than ``CACHE_TTL``, or older than ``ADVISORY_MATCH_TTL``
    when a cached advisory covers ``installed`` (#3006).
    """
    now = time.time() if now is None else now
    cached = load_cache(environ)
    ttl = CACHE_TTL
    if installed and _cached_advisories_match(cached, installed):
        ttl = ADVISORY_MATCH_TTL
    if _recent(cached.get("checked_at"), now, ttl):
        return cached
    if _recent(cached.get("failed_at"), now, FAILURE_BACKOFF):
        return cached
    try:
        data = {
            "checked_at": now,
            "latest": fetch_latest(),
            "advisories": [a.to_dict() for a in fetch_advisories()],
        }
    except Exception:  # noqa: BLE001 — any network or payload problem is silent
        logger.debug("update check failed", exc_info=True)
        data = dict(cached, failed_at=now)
    save_cache(data, environ)
    return data


def check(
    now: Optional[float] = None,
    fetch: bool = True,
    environ: Optional[Mapping[str, str]] = None,
    installed: Optional[str] = None,
) -> Optional[UpdateStatus]:
    """The status for the installed version, or None when nothing is known."""
    try:
        return _check(now, fetch, environ, installed)
    except Exception:  # noqa: BLE001 — a hostile cache file must never break a caller
        logger.debug("update check failed", exc_info=True)
        return None


def _check(
    now: Optional[float],
    fetch: bool,
    environ: Optional[Mapping[str, str]],
    installed: Optional[str],
) -> Optional[UpdateStatus]:
    from djust import __version__

    installed = installed or __version__
    data = refresh(now, environ, installed) if fetch else load_cache(environ)
    if not isinstance(data.get("checked_at"), (int, float)):
        return None
    advisories = []
    entries = data.get("advisories")
    for entry in entries if isinstance(entries, list) else []:
        try:
            advisory = Advisory.from_dict(entry)
        except (KeyError, TypeError):
            continue
        if all(isinstance(v, str) for v in (advisory.ghsa_id, advisory.range, advisory.patched)):
            advisories.append(advisory)
    latest = data.get("latest")
    if not isinstance(latest, str):
        latest = None
    return UpdateStatus(installed, latest, advisories_for(installed, advisories))


def check_in_background(callback: Callable[[UpdateStatus], None]) -> threading.Thread:
    """Run ``check()`` on a daemon thread and hand a non-None status to ``callback``."""

    def run() -> None:
        try:
            status = check()
            if status is not None:
                callback(status)
        except Exception:  # noqa: BLE001 — never let the notice break startup
            logger.debug("background update check failed", exc_info=True)

    thread = threading.Thread(target=run, name="djust-update-check", daemon=True)
    thread.start()
    return thread


def cli_install_hint(argv0: Optional[str] = None, executable: Optional[str] = None) -> str:
    """How to upgrade the CLI the user is running.

    A ``uv tool install`` puts a shim on PATH whose interpreter lives under
    ``.../uv/tools/djust/``, so the interpreter path is the reliable signal.
    """
    candidates = [
        sys.argv[0] if argv0 is None else argv0,
        sys.executable if executable is None else executable,
        sys.prefix if executable is None else "",
    ]
    for candidate in candidates:
        normalized = (candidate or "").replace("\\", "/")
        if "/tools/djust/" in normalized or "/tool/djust/" in normalized:
            return "uv tool upgrade djust"
    return "uvx djust@latest"
