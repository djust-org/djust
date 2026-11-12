"""Fail a test that defines a LiveView without stating its ``exposure_policy``.

``LiveView.exposure_policy`` defaults to ``"explicit"`` since 2.0
(``docs/plans/2.0-exposure-flip.md``, PR 1). ``"legacy"`` is still accepted when
a class states it, and about 1,850 test and example views do (PR 0d), so that
the flip changed no test outcome. Until the ``"legacy"`` arm is deleted (PR 11)
both policies run in this suite, and which one a test view runs under is part
of what the test means: a test written against the attribute walk that states
nothing would silently run as explicit, and its assertions about context,
persistence or diagnostics would be about a different policy than its author
had in mind. So every test view states one, and this plugin makes a missing
statement visible at once.

It records every ``LiveView`` subclass as Python creates it (class statements,
``type(...)`` and source run with ``exec`` alike) and judges each one when the
test that created it ends, so a policy assigned after the class statement still
counts. A class relies on the default when no class before ``LiveView`` in its
MRO has ``exposure_policy`` in its own ``__dict__``. Classes shipped in ``djust``
are not the tests' to pin and are ignored.

It does not see what is not a ``LiveView`` subclass: a bare ``LiveView()``, or a
stub object standing in for a view.

Classes created while test modules are imported (module level) are reported by
one test, ``tests/unit/test_exposure_policy_pin_guard.py``.

The few test files that leave a policy unset on purpose are listed, with the
reason, in ``UNPINNED_ON_PURPOSE``.
"""

from __future__ import annotations

import weakref

import pytest

#: Test files (by file name) whose views are left without a policy on purpose,
#: each with its reason. Two kinds belong here: a test whose subject is the
#: default itself, and a test that executes views it does not own (the code
#: blocks in the docs, the README and the shipped component catalogue), which
#: must run as a reader would paste them.
UNPINNED_ON_PURPOSE: dict[str, str] = {
    "test_doc_snippets_execute.py": "executes the docs' code blocks as written",
    "test_readme_snippets_execute.py": "executes the README's code blocks as written",
    "test_doc_examples.py": "executes the documented examples as written",
    "test_catalogue_ux.py": "executes the shipped catalogue's usage snippets as written",
    "test_exposure_default_policy.py": "its subject is the default policy itself",
}

#: One record per LiveView subclass created in this process, in creation order:
#: ``(weak reference, dotted name, test file name, verdict at creation)``. Weak,
#: because tests assert that view classes can be collected.
_created: list[tuple[weakref.ref, str, str, bool]] = []
#: Classes already recorded (``compile_subscriptions`` is called again for them).
_seen: weakref.WeakSet = weakref.WeakSet()
#: ``len(_created)`` when collection ended: everything before it was created by
#: importing test modules, not by a test.
_import_time_count: list[int] = []


def _module_of(cls: type) -> str:
    """``cls.__module__``, or "" for a class whose metaclass refuses the lookup."""
    try:
        return cls.__module__ or ""
    except Exception:  # noqa: BLE001 — a test's metaclass may raise on any attribute
        return ""


def relies_on_default(cls: type) -> bool:
    """True for a test-defined LiveView that inherits ``LiveView``'s own policy."""
    from djust.live_view import LiveView

    module = _module_of(cls)
    if module == "djust" or (module.startswith("djust.") and ".tests." not in module):
        return False
    for base in cls.__mro__:
        if base is LiveView:
            return True
        if "exposure_policy" in base.__dict__:
            return False
    return False


def _offenders(records: list[tuple[weakref.ref, str, str, bool]]) -> list[str]:
    """Names of the recorded classes that rely on the default.

    A class still alive is judged now, so a policy assigned after the class
    statement counts. One already collected keeps its verdict from creation.
    """
    names = set()
    for ref, name, file_name, at_creation in records:
        cls = ref()
        verdict = at_creation if cls is None else relies_on_default(cls)
        if verdict and file_name not in UNPINNED_ON_PURPOSE:
            names.add(name)
    return sorted(names)


def import_time_offenders() -> list[str]:
    """Unpinned views created before the first test ran (module-level classes)."""
    end = _import_time_count[0] if _import_time_count else len(_created)
    return _offenders(_created[:end])


def install() -> bool:
    """Record every LiveView subclass as it is created. Idempotent.

    ``LiveView.__init_subclass__`` calls ``compile_subscriptions(cls)`` for each
    new subclass, looked up in its module at call time, so replacing that module
    attribute sees every class without adding a frame between the class statement
    and ``__init_subclass__`` (wrapping ``__init_subclass__`` itself moved the
    frame a ``stacklevel`` warning points at, which
    ``test_deprecations_1_3.py`` pins). The function has other callers, for
    classes that already exist; ``_seen`` keeps those out of the record.
    ``test_exposure_policy_pin_guard.py`` fails if this stops recording.
    """
    try:
        from djust import _component_subscriptions as module
        from djust.live_view import LiveView
    except Exception:  # noqa: BLE001 — djust not importable: nothing to guard
        return False
    original = module.compile_subscriptions
    if getattr(original, "_djust_pin_guard", False):
        return True

    def recording_compile_subscriptions(owner):
        result = original(owner)
        if isinstance(owner, type) and issubclass(owner, LiveView) and owner not in _seen:
            _seen.add(owner)
            name = _module_of(owner)
            _created.append(
                (
                    weakref.ref(owner),
                    f"{name}.{owner.__qualname__}",
                    name.rsplit(".", 1)[-1] + ".py",
                    relies_on_default(owner),
                )
            )
        return result

    recording_compile_subscriptions._djust_pin_guard = True  # type: ignore[attr-defined]
    recording_compile_subscriptions.__wrapped__ = original  # type: ignore[attr-defined]
    module.compile_subscriptions = recording_compile_subscriptions
    return True


def pytest_sessionstart(session):
    install()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session):
    # tryfirst: before the root conftest's gc.freeze(), though nothing here depends on it.
    if not _import_time_count:  # a nested pytester session must not move the mark
        _import_time_count.append(len(_created))


@pytest.fixture(autouse=True)
def _no_liveview_relies_on_the_default_policy(request):
    start = len(_created)
    yield
    if request.node.path.name in UNPINNED_ON_PURPOSE:
        return
    offenders = _offenders(_created[start:])
    if offenders:
        names = ", ".join(offenders)
        pytest.fail(
            "LiveView defined without an exposure_policy: "
            f'{names}. Set exposure_policy = "explicit" (or "legacy", for a test of the '
            "legacy policy) on the class, or on the dict passed to type(...). The default is "
            '"explicit"; a test view states its policy while both exist '
            "(docs/plans/2.0-exposure-flip.md). A file that must leave it unset is listed in "
            "UNPINNED_ON_PURPOSE in tests/exposure_policy_pin_guard.py.",
            pytrace=False,
        )
