"""Run Django's own ``template_tests`` against djust's Rust engine (#2517).

The package behind ``scripts/run-django-template-suite.py``. It has five
parts, each importable on its own:

``adapter``
    ``DjustEngine`` — a subclass of the REAL ``django.template.engine.Engine``
    that keeps every constructor kwarg and overrides only the two
    template-producing entry points (``from_string``, ``get_template``) to
    return djust's ``DjustTemplate``. ``install()`` rebinds the ``Engine``
    name in ``django.template`` and ``django.template.engine`` — AFTER the
    ``TEMPLATES``-configured ``DjangoTemplates`` backend has bound the real
    class — so Django's own runner, admin checks and ``Template("...")``
    default engine stay on Django.

``recorder``
    ``RecordingResult`` — one flushed JSON line per test (id, status, first
    line of the failure, whether the test reached the adapter at all) — and
    ``DjustSuiteRunner``, a ``DiscoverRunner`` with that result class and an
    id-skip filter (the per-test crash-resume hook).

``settings``
    Django's ``test_sqlite`` settings plus ``TEST_RUNNER`` pointing at the
    recorder. Only importable with the checkout's ``tests/`` on ``sys.path``.

``child``
    The subprocess entry: ``runtests`` mode hands off to the checkout's own
    ``tests/runtests.py``; ``discover`` mode runs a synthetic test package
    for the tool's own tests, no checkout needed.

``report``
    Pure functions: load the JSON lines, summarise into the engine-exercising
    and whole-label buckets, format the summary, compare against a baseline.

The Rust engine is reached through the Django-backend path only —
``DjustTemplate.render`` → ``_rust.render_template_with_dirs`` — not the
LiveView path. A divergence the suite reports may or may not also exist on
the LiveView / component paths; that is not this tool's question.
"""

from __future__ import annotations

__all__ = ["adapter", "child", "recorder", "report", "settings"]
