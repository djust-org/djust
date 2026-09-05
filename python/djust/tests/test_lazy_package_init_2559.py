"""Regression tests for #2559 — lazy package init.

``import djust`` / ``import djust.template.backend`` must NOT import the
LiveView stack (``channels``, ``djust.live_view``, ``djust.presence``, ...).
The public surface (``from djust import LiveView`` etc.) is resolved on first
access via PEP 562 ``__getattr__`` in ``python/djust/__init__.py``.

Two layers:

* **Fresh-subprocess pins** (``_fresh_import``): a child interpreter with
  Django configured minimally runs one statement and reports the ``djust.*``
  entries in ``sys.modules`` plus a fixed probe list. The allowlists are EXACT
  sets, not floors (#1125) — growth is a review question, shrinkage a prompt
  to update the constant. A child that crashes is a test ERROR, never an empty
  list (#2135).
* **In-process agreement pins**: ``__all__`` == eager | ``_LAZY``; ``dir()``
  lists every public name before resolution; every lazy entry resolves to the
  source-module object (a typo'd module path is invisible to mypy because the
  package init is lenient under ADR-023).

Gate-off (#1468): restore ONE eager line in ``__init__.py`` —
``from .presence import PresenceMixin, ...`` — and
``test_backend_import_never_loads_the_liveview_stack`` fails naming
``channels`` + ``djust.presence`` and both allowlist tests fail. Remove the
``if TYPE_CHECKING:`` block and ``mypy python/djust`` reports 4 errors in
``components/gallery/live_views.py``.
"""

from __future__ import annotations

import importlib
import json
import os
import pickle
import subprocess
import sys

import pytest

import djust

# --------------------------------------------------------------------------- #
# Allowlists — the EXACT ``djust.*`` module set each import loads (#1125).
# --------------------------------------------------------------------------- #
ALLOWLIST_PACKAGE = frozenset(
    {
        "djust",
        "djust._html",
        "djust._rust",
        "djust.render_env",
        "djust.template_libraries",
        "djust.template_tags",
        "djust.template_tags._builtin",
        "djust.template_tags._django_expr",
        "djust.template_tags.client_config",
        "djust.template_tags.debug",
        "djust.template_tags.flash",
        "djust.template_tags.live_render",
        "djust.template_tags.lorem",
        "djust.template_tags.markdown",
        "djust.template_tags.now",
        "djust.template_tags.pwa",
        "djust.template_tags.querystring",
        "djust.template_tags.regroup",
        "djust.template_tags.static",
        "djust.template_tags.templatetag",
        "djust.template_tags.url",
        "djust.utils",
    }
)
assert len(ALLOWLIST_PACKAGE) == 22

ALLOWLIST_BACKEND = ALLOWLIST_PACKAGE | frozenset(
    {
        "djust.optimization",
        "djust.optimization.cache",
        "djust.optimization.codegen",
        "djust.optimization.fingerprint",
        "djust.optimization.query_optimizer",
        "djust.serialization",
        "djust.session_utils",
        "djust.template",
        "djust.template.backend",
        "djust.template.exceptions",
        "djust.template.rendering",
        "djust.template.serialization",
    }
)
assert len(ALLOWLIST_BACKEND) == 34


# The compat shim (``djust/template_backend.py``) is one extra module.
ALLOWLIST_BACKEND_SHIM = ALLOWLIST_BACKEND | {"djust.template_backend"}

# Each asserted absent by name so the failure names the leak (#1104).
FORBIDDEN = (
    "channels",
    "daphne",
    "msgpack",
    "djust.live_view",
    "djust.websocket",
    "djust.presence",
    "djust.push",
    "djust.mixins",
    "djust.components",
    "djust.forms",
    "djust.streaming",
)

# Exported names that shadow a submodule name (§2.3): whichever binds LAST wins,
# so in-process identity cannot be pinned; the subprocess tests pin the order.
_COLLIDING = frozenset({"live_view", "rate_limit"})

# Names bound eagerly by ``__init__.py`` (everything else in ``__all__`` is lazy).
_EAGER_PUBLIC = frozenset(
    {
        "get_template_dirs",
        "clear_template_dirs_cache",
        "render_template",
        "diff_html",
        "RustLiveView",
        "enable_hot_reload",
    }
)

_CHILD = """
import json, sys
import django
from django.conf import settings
settings.configure(
    DEBUG=False,
    SECRET_KEY="x",
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust"],
    TEMPLATES=[{
        "BACKEND": "djust.template.backend.DjustTemplateBackend",
        "DIRS": [], "APP_DIRS": True, "OPTIONS": {},
    }],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
exec(sys.argv[1])
probes = %s
print("@@RESULT@@" + json.dumps({
    "djust": sorted(m for m in sys.modules if m == "djust" or m.startswith("djust.")),
    "present": [p for p in probes if p in sys.modules],
    "extra": (globals().get("EXTRA") or {}),
}))
""" % json.dumps(list(FORBIDDEN))


def _fresh_import(stmt: str) -> dict:
    """Run ``stmt`` in a fresh interpreter; return the ``sys.modules`` report.

    Errors (never silently returns an empty list, #2135) when the child fails.
    The child runs the SAME package this test process imported (``djust`` is
    put on ``PYTHONPATH`` from its ``__file__``) and never sees
    ``PYTEST_CURRENT_TEST`` so ``DjustConfig.ready()`` takes its real path.
    """
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(djust.__file__)))
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, stmt],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"child interpreter failed (rc={proc.returncode}) for {stmt!r}:\n{proc.stderr}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    if len(lines) != 1:
        raise RuntimeError(f"no result line from child for {stmt!r}:\n{proc.stdout}\n{proc.stderr}")
    report = json.loads(lines[0][len("@@RESULT@@") :])
    assert report["djust"], "child reported no djust modules at all — harness broken"
    return report


def _assert_none_forbidden(report: dict, stmt: str) -> None:
    for name in FORBIDDEN:
        assert name not in report["present"], (
            f"{stmt!r} imported {name!r} — the LiveView stack leaked back into the "
            f"package init (#2559)"
        )


# --------------------------------------------------------------------------- #
# 5.1 — subprocess pins
# --------------------------------------------------------------------------- #
class TestImportFootprint:
    def test_package_import_loads_exactly_the_allowlist(self):
        report = _fresh_import("import djust")
        assert set(report["djust"]) == ALLOWLIST_PACKAGE, (
            f"extra={sorted(set(report['djust']) - ALLOWLIST_PACKAGE)} "
            f"missing={sorted(ALLOWLIST_PACKAGE - set(report['djust']))}"
        )

    def test_backend_import_loads_exactly_the_allowlist(self):
        report = _fresh_import("import djust.template.backend")
        assert set(report["djust"]) == ALLOWLIST_BACKEND, (
            f"extra={sorted(set(report['djust']) - ALLOWLIST_BACKEND)} "
            f"missing={sorted(ALLOWLIST_BACKEND - set(report['djust']))}"
        )

    def test_backend_shim_import_loads_exactly_the_allowlist(self):
        report = _fresh_import("import djust.template_backend")
        assert set(report["djust"]) == ALLOWLIST_BACKEND_SHIM, (
            f"extra={sorted(set(report['djust']) - ALLOWLIST_BACKEND_SHIM)} "
            f"missing={sorted(ALLOWLIST_BACKEND_SHIM - set(report['djust']))}"
        )

    @pytest.mark.parametrize(
        "stmt",
        ["import djust", "import djust.template.backend", "import djust.template_backend"],
    )
    def test_import_never_loads_the_liveview_stack(self, stmt):
        _assert_none_forbidden(_fresh_import(stmt), stmt)

    def test_django_setup_on_templates_only_project_never_loads_channels(self):
        """``DjustConfig.ready()`` (checks registration, filter bridge, hot-reload
        gate) runs without importing the LiveView stack — the "ready() needs no
        split" claim as a test."""
        stmt = "import djust.template.backend; django.setup()"
        report = _fresh_import(stmt)
        for name in ("channels", "djust.live_view", "djust.websocket", "djust.presence"):
            assert name not in report["present"], f"django.setup() imported {name!r}"
        assert "djust.apps" in report["djust"], "ready() did not run — harness not exercising it"

    def test_harness_detects_a_forbidden_module(self):
        """In-suite canary (#1459/#2135): the harness REPORTS a leak when there is one."""
        report = _fresh_import("import djust.presence, djust.template.backend")
        assert "djust.presence" in report["present"]
        assert "channels" in report["present"]
        with pytest.raises(AssertionError):
            _assert_none_forbidden(report, "canary")

    def test_hasattr_on_a_lazy_name_imports_its_module(self):
        """Documented side effect: ``hasattr(djust, "PresenceMixin")`` imports the module."""
        report = _fresh_import("import djust; EXTRA={'has': hasattr(djust, 'PresenceMixin')}")
        assert report["extra"]["has"] is True
        assert "djust.presence" in report["present"]
        assert "channels" in report["present"]

    def test_dir_lists_every_public_name_before_resolution(self):
        report = _fresh_import("import djust; EXTRA={'dir': dir(djust)}")
        missing = set(djust.__all__) - set(report["extra"]["dir"])
        assert not missing, f"dir(djust) misses unresolved public names: {sorted(missing)}"
        # And listing them did not resolve them.
        _assert_none_forbidden(report, "dir(djust)")

    def test_live_view_name_is_the_decorator_after_package_style_import(self):
        """Collision case A (#2559 §2.3): ``from djust import LiveView`` then
        ``from djust import live_view`` yields the DECORATOR, as before."""
        report = _fresh_import(
            "from djust import LiveView; from djust import live_view; "
            "m = sys.modules['djust.live_view']; "
            "EXTRA={'kind': type(live_view).__name__, 'same': live_view is m.live_view}"
        )
        assert report["extra"]["kind"] == "function"
        assert report["extra"]["same"] is True

    def test_live_view_is_the_module_after_submodule_first_import_with_no_lazy_resolution(self):
        """Collision case B: ``import djust.live_view`` BEFORE any lazy name
        has resolved leaves the attribute bound to the MODULE (the import
        system's own ``setattr`` on the parent, which cannot be intercepted)
        -- the same contract ``djust.rate_limit`` has always had. This is the
        one order that differs from the eager pre-#2559 init, where the
        rebind always won."""
        report = _fresh_import(
            "import djust.live_view; import djust; EXTRA={'kind': type(djust.live_view).__name__}"
        )
        assert report["extra"]["kind"] == "module"

    @pytest.mark.parametrize(
        "lazy_name",
        ["LiveView", "event_handler", "PresenceMixin"],
        ids=["from-live_view-module", "from-decorators", "from-presence"],
    )
    def test_live_view_is_the_decorator_once_any_lazy_name_resolves_after_submodule_first_import(
        self, lazy_name
    ):
        """Collision case C: submodule-first, then ANY lazy resolution -- of a
        name from ``djust.live_view`` OR from an unrelated module -- rebinds
        ``djust.live_view`` to the decorator, so the binding never depends on
        WHICH public name was touched first."""
        report = _fresh_import(
            f"import djust.live_view; import djust; djust.{lazy_name}; "
            "m = sys.modules['djust.live_view']; "
            "EXTRA={'kind': type(djust.live_view).__name__, "
            "'same': djust.live_view is m.live_view}"
        )
        assert report["extra"]["kind"] == "function"
        assert report["extra"]["same"] is True

    def test_live_view_stays_the_decorator_when_the_submodule_is_imported_afterwards(self):
        """Collision case D: lazy resolution first, then ``import
        djust.live_view`` -- the already-loaded submodule is NOT re-bound onto
        the package, so the decorator binding is stable."""
        report = _fresh_import(
            "import djust; djust.LiveView; import djust.live_view; "
            "EXTRA={'kind': type(djust.live_view).__name__}"
        )
        assert report["extra"]["kind"] == "function"

    def test_rate_limit_name_is_the_decorator_before_the_submodule_is_imported(self):
        report = _fresh_import(
            "from djust import rate_limit; import djust.decorators as d; "
            "EXTRA={'kind': type(rate_limit).__name__, 'same': rate_limit is d.rate_limit}"
        )
        assert report["extra"]["kind"] == "function"
        assert report["extra"]["same"] is True

    def test_rate_limit_name_is_the_module_once_dispatch_is_imported(self):
        """Pins today's order contract that ``test_api_dispatch.py`` relies on:
        after ``djust.api.dispatch`` imports the ``djust.rate_limit`` submodule,
        ``from djust import rate_limit`` is the MODULE."""
        report = _fresh_import(
            "import djust.api.dispatch; from djust import rate_limit; "
            "EXTRA={'kind': type(rate_limit).__name__, "
            "'cap': hasattr(rate_limit, '_HANDLER_BUCKET_CAP')}"
        )
        assert report["extra"]["kind"] == "module"
        assert report["extra"]["cap"] is True


# --------------------------------------------------------------------------- #
# 5.2 / 5.3 — in-process agreement pins
# --------------------------------------------------------------------------- #
class TestPublicSurfaceAgreement:
    def test_all_equals_eager_union_lazy(self):
        assert set(djust.__all__) == _EAGER_PUBLIC | set(djust._LAZY), (
            f"only_in_all={sorted(set(djust.__all__) - _EAGER_PUBLIC - set(djust._LAZY))} "
            f"only_in_impl={sorted((_EAGER_PUBLIC | set(djust._LAZY)) - set(djust.__all__))}"
        )
        assert not (_EAGER_PUBLIC & set(djust._LAZY))

    def test_every_lazy_entry_resolves_to_the_source_object(self):
        for name, (module_name, attr) in djust._LAZY.items():
            if name in _COLLIDING:
                continue  # order-dependent by design; pinned in TestImportFootprint
            resolved = getattr(djust, name)
            module = importlib.import_module(module_name)
            expected = module if not attr else getattr(module, attr)
            assert resolved is expected, f"djust.{name} is not {module_name}.{attr or '<module>'}"

    def test_every_public_name_is_reachable(self):
        for name in djust.__all__:
            getattr(djust, name)

    def test_unknown_name_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="module 'djust' has no attribute 'nope'"):
            djust.nope  # noqa: B018

    def test_star_import_exposes_every_public_name(self):
        ns: dict = {}
        exec("from djust import *", ns)
        missing = set(djust.__all__) - set(ns)
        assert not missing, sorted(missing)

    def test_from_djust_import_liveview_is_the_live_view_module_class(self):
        from djust import LiveView

        assert LiveView is importlib.import_module("djust.live_view").LiveView

    def test_pickle_roundtrip_of_lazy_class(self):
        from djust import LiveView, StreamingMixin

        assert pickle.loads(pickle.dumps(LiveView)) is LiveView
        assert pickle.loads(pickle.dumps(StreamingMixin)) is StreamingMixin

    def test_type_checking_block_mirrors_lazy_map(self):
        """The ``if TYPE_CHECKING:`` block must import every lazy name — it is
        what keeps the ADR-023 strict islands typed (#1960). Parsed from source
        so a name added to ``_LAZY`` without a static import trips here."""
        import ast

        with open(djust.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        static: set[str] = set()
        for node in tree.body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                for stmt in node.body:
                    if isinstance(stmt, ast.ImportFrom):
                        static.update(a.asname or a.name for a in stmt.names)
        assert static, "no `if TYPE_CHECKING:` import block in djust/__init__.py"
        assert set(djust._LAZY) == static, (
            f"lazy_not_static={sorted(set(djust._LAZY) - static)} "
            f"static_not_lazy={sorted(static - set(djust._LAZY))}"
        )
