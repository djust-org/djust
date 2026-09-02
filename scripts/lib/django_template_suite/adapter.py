"""The seam: Django's ``Engine`` configuration surface, djust's rendering.

Django's ``template_tests`` never touch the ``TEMPLATES`` setting — they run
under ``override_settings(TEMPLATES=None)`` and build ``Engine(...)``
instances directly (``tests/template_tests/utils.py``, plus 68 direct
constructions in 15 other files). So the seam is the ``Engine`` class
itself: ``DjustEngine`` subclasses the real one, keeps its constructor
(``libraries``, ``loaders``, ``string_if_invalid``, ``debug``, ``autoescape``,
``builtins`` — all accepted, so ``InvalidTemplateLibrary`` tests behave as on
Django) and overrides the two methods that produce a template object.
``select_template`` and ``render_to_string`` are inherited and route through
``get_template``.

What the adapter does NOT do, on purpose:

* re-classify exceptions — unittest's own rule is the classification
  (``AssertionError`` → FAIL, anything else → ERROR); a Rust parse error
  arrives as the ``Exception`` ``DjustTemplate`` wraps it in and is an ERROR;
* honour ``string_if_invalid`` / ``debug`` / ``autoescape=False`` /
  ``builtins`` — nothing in the Rust engine reads them. They are stored, not
  applied, and show up as FAIL, which is a real finding about the backend;
* translate djust's errors into Django's ``TemplateSyntaxError`` — the 111
  "``TemplateSyntaxError`` not raised" FAILs are the engine's to fix.

``TOUCH`` counts adapter calls; the recorder snapshots it around each test to
tell "this test exercised djust" from "this test measured Django against
itself". The untouched set must be 100 % OK by construction — anything else
is a harness bug and the summary prints it as a WARNING.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

# Bind the real Engine into the DjangoTemplates backend module FIRST, so the
# TEMPLATES-configured backend (Django's own runner, admin checks, the
# ``Template("...")`` default engine) keeps the real class after install().
import django.template.backends.django  # noqa: F401
import django.template as _template_pkg
import django.template.engine as _engine_mod
from django.template import TemplateDoesNotExist
from django.utils.functional import cached_property

RealEngine = _engine_mod.Engine

#: Adapter call counter. ``{"count": n}`` rather than a bare int so the
#: recorder can read it without an import cycle or a ``global``.
TOUCH: dict[str, int] = {"count": 0}

#: The methods the adapter relies on the real class having. If a future
#: Django tag renames one, ``install()`` refuses rather than silently
#: measuring nothing.
_REQUIRED_ENGINE_ATTRS = ("from_string", "get_template", "select_template", "render_to_string")


class DjustEngine(RealEngine):  # type: ignore[misc, valid-type]
    """The real ``Engine``'s configuration; ``DjustTemplate`` for rendering."""

    # ``Engine.__repr__`` prints ``self.__class__.__qualname__`` — keep the
    # two ``test_engine.test_repr*`` cases Django-vs-Django.
    __qualname__ = "Engine"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._djust_tmp: str | None = None
        from djust._rust import render_template_with_dirs

        # What ``DjustTemplate`` reads off its ``backend``: ``template_dirs``
        # (below, lazily), ``context_processors`` (already an Engine attr),
        # and ``_render_fn_with_dirs``.
        self._render_fn_with_dirs = render_template_with_dirs

    # -- loaders → directories -------------------------------------------
    @cached_property
    def template_dirs(self) -> list[Path]:
        """The engine's loaders, materialised into directories the Rust
        engine's own filesystem loader can search for ``{% extends %}`` and
        ``{% include %}``.

        Lazy — the real ``Engine`` only instantiates its loaders on first
        use, and a test asserting that a bad loader raises at
        ``get_template`` time must see it raised there, not at construction.
        """
        from django.template.loaders.app_directories import Loader as AppDirsLoader
        from django.template.loaders.cached import Loader as CachedLoader
        from django.template.loaders.filesystem import Loader as FilesystemLoader
        from django.template.loaders.locmem import Loader as LocmemLoader

        dirs: list[Path] = []

        def walk(loader: Any) -> None:
            if isinstance(loader, CachedLoader):
                for inner in loader.loaders:
                    walk(inner)
            elif isinstance(loader, LocmemLoader):
                # A per-engine temp dir under TMPDIR — the outer runner (or
                # Django's runtests.py, which sets its own TMPDIR) removes it.
                if self._djust_tmp is None:
                    self._djust_tmp = tempfile.mkdtemp(prefix="djust-suite-")
                for name, source in loader.templates_dict.items():
                    path = Path(self._djust_tmp) / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(str(source), encoding="utf-8")
                dirs.append(Path(self._djust_tmp))
            elif isinstance(loader, (FilesystemLoader, AppDirsLoader)) or hasattr(
                loader, "get_dirs"
            ):
                dirs.extend(Path(d) for d in loader.get_dirs())

        for loader in self.template_loaders:
            walk(loader)
        return dirs

    # -- the two template-producing entry points ---------------------------
    def from_string(self, template_code: str) -> Any:
        from djust.template.rendering import DjustTemplate

        TOUCH["count"] += 1
        return DjustTemplate(template_code, backend=self)

    def get_template(self, template_name: str) -> Any:
        """Walk the loaders for SOURCES, the way ``loaders/base.py`` does,
        minus the ``Template(...)`` construction — Django's parser never runs."""
        from djust.template.rendering import DjustTemplate

        TOUCH["count"] += 1
        tried = []
        for loader in self.template_loaders:
            for origin in loader.get_template_sources(template_name):
                try:
                    contents = origin.loader.get_contents(origin)
                except TemplateDoesNotExist:
                    tried.append((origin, "Source does not exist"))
                    continue
                return DjustTemplate(str(contents), backend=self, origin=origin)
        raise TemplateDoesNotExist(template_name, tried=tried)


def install() -> None:
    """Rebind ``Engine`` in ``django.template`` and ``django.template.engine``.

    ``django.template.backends.django`` was imported at the top of this
    module, so its ``Engine`` name is already the real class and stays so.
    ``from django.template import Engine`` anywhere in the suite — including
    ``tests/template_tests/utils.py`` — resolves at import time, after this.
    """
    missing = [name for name in _REQUIRED_ENGINE_ATTRS if not hasattr(RealEngine, name)]
    if missing:
        raise RuntimeError(
            "django.template.engine.Engine lacks %s; the adapter's seam does not hold "
            "on this Django" % ", ".join(missing)
        )
    _engine_mod.Engine = DjustEngine
    _template_pkg.Engine = DjustEngine


def touch_count() -> int:
    """The number of adapter calls so far (``from_string`` + ``get_template``)."""
    return TOUCH["count"]
