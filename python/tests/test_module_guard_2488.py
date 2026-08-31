"""A class with no ``__module__`` does not crash the branch that names it (#2488).

The crash
---------
``normalize_django_value``'s final fallback built its warning message with an
UNGUARDED ``type(value).__module__``::

    value_module = type(value).__module__     # AttributeError: __module__

``__module__`` is not guaranteed. ``type(name, bases, ns)`` fills it from the
**calling frame's** ``__name__``, so a class built in a namespace that has none
— which is exactly what ``eval(compile(...), {})`` gives you — has no
``__module__`` at all.

Why that is worse than it looks: the path is the LiveView render path (every
WebSocket event normalizes the context), and the value reaching that branch is
already the *"we don't know how to serialize this"* case. The guard that was
supposed to produce a helpful warning was the thing that raised — a crash
inside the error handling, which is the branch least likely to be exercised.

The sink, not the callers
-------------------------
Per the drain canon, the fix greps for the SINK — every ``type(...).__module__``
read on an ARBITRARY value — rather than for the one caller the report cited.
Three were unguarded and all three are message-building or diagnostic paths:

============================================ =====================================
``serialization.py``                          the cited one; the render path
``observability/tracebacks.py``               the exception RECORDER
``checks/configuration.py``                   the ASGI walk in ``manage.py check``
============================================ =====================================

``templatetags/live_tags.py`` reads the same pair inside its own
``except AttributeError``, so it was already safe and is the one exemption the
source pin below names.

How it was found
----------------
#2482 put a ``type()``-built class instance in the differential corpus. The
script builds it in a module that HAS ``__name__``, so the script-built instance
rendered fine; the corpus reader rebuilds the same expression from the AST in a
names-only namespace, so the reader-built instance crashed — the same corpus row
behaving differently depending on which reader constructed it.

Refs #2488, #2482.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import types

import pytest

pytest.importorskip("django")

from djust.serialization import normalize_django_value  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO / "python" / "djust"


def _module_less(body: str = 'type("C", (), {})') -> type:
    """A class with NO ``__module__``, built the way #2482's corpus reader does.

    ``eval`` with a bare globals dict: the frame has no ``__name__``, so
    ``type()`` has nothing to copy into ``__module__``.
    """
    node = ast.parse(body).body[0]
    return eval(  # noqa: S307 — the expression is a literal in this file
        compile(ast.Expression(node.value), "<x>", "eval"), {}
    )


class TestThePremiseIsMeasured:
    """The bug's premise, asked of live CPython rather than transcribed."""

    def test_a_type_built_class_really_has_no___module__(self) -> None:
        cls = _module_less()
        assert cls.__name__ == "C"
        with pytest.raises(AttributeError):
            cls.__module__

    def test_an_ordinary_class_does_have_one(self) -> None:
        """The other direction, so the fixture is not vacuously exotic."""
        assert type(self).__module__ == __name__


class TestTheWarningBranchNoLongerCrashes:
    def test_normalize_django_value_ANSWERS_instead_of_raising(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The issue's own reproducer. It raised ``AttributeError: __module__``
        before the guard; it answers now.

        The assertion was ``isinstance(out["p"], str)`` until #2477/#2489, and
        the RETURN is no longer this test's subject. That fix carries an object
        the conversion models — a falsy, attribute-less class is one — past the
        warning instead of stringifying it, so the branch under test still runs
        and still builds its message, and the value that comes back is the
        object.

        What #2488 is about is that the branch does not CRASH while naming a
        type with no ``__module__``, so that is what is asserted: it returns
        without raising, and it warns. The module placeholder has its own test
        one method down.
        """
        obj = _module_less('type("C", (), {"__bool__": lambda self: False})()')
        with caplog.at_level("WARNING", logger="djust.serialization"):
            out = normalize_django_value({"p": obj})
        assert isinstance(out, dict)
        assert out["p"] is obj
        assert any("non-serializable value" in r.getMessage() for r in caplog.records)

    def test_the_warning_still_names_the_type_and_marks_the_module(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The guard must not silence the warning it exists to build — the
        placeholder is what a reader sees where the module would have been."""
        obj = _module_less('type("Widget", (), {})()')
        with caplog.at_level("WARNING", logger="djust.serialization"):
            normalize_django_value({"p": obj})
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "Widget" in text
        assert "<unknown>" in text

    def test_a_class_WITH_a_module_still_names_it(self, caplog: pytest.LogCaptureFixture) -> None:
        """The placeholder is reached only by the absent case.

        Named for what it checks rather than for the return type, for the
        reason the reproducer above records: #2477/#2489 carries an object the
        conversion models past the warning, so the message — not the value —
        is where the placeholder is visible.
        """

        class Ordinary:
            pass

        with caplog.at_level("WARNING", logger="djust.serialization"):
            normalize_django_value({"p": Ordinary()})
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "Ordinary" in text
        assert __name__ in text
        assert "<unknown>" not in text

    def test_strict_serialization_still_raises_TypeError_not_AttributeError(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The strict-mode branch builds the SAME message. Before the guard it
        raised ``AttributeError`` from message construction rather than the
        ``TypeError`` the mode promises."""
        from djust import config as config_mod

        monkeypatch.setattr(
            config_mod.config, "get", lambda k, d=None: True if k == "strict_serialization" else d
        )
        with pytest.raises(TypeError, match="<unknown>"):
            normalize_django_value({"p": _module_less('type("C", (), {})()')})


class TestTheOtherTwoSinks:
    def test_the_exception_recorder_survives_a_module_less_exception(self) -> None:
        """A second exception raised INSIDE the recorder has nowhere to go."""
        from djust.observability import tracebacks

        exc_cls = _module_less('type("Boom", (Exception,), {})')
        caught: BaseException | None = None
        try:
            raise exc_cls("bang")
        except Exception as exc:  # noqa: BLE001
            caught = exc
        # Called OUTSIDE the `except` block on purpose: inside it, a raise from
        # the recorder chains onto the module-less exception and pytest's own
        # reporter dies formatting the chain (INTERNALERROR), which reports a
        # SHORT pass count rather than a failure. A clean assertion is better
        # evidence than an abort.
        assert caught is not None
        try:
            tracebacks.record_traceback(caught)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"the recorder raised {type(exc).__name__}: {exc}")
        recorded = tracebacks.get_recent_tracebacks(1)[0]
        assert recorded["exception_type"] == "Boom"
        assert recorded["exception_module"] == "<unknown>"
        # The traceback string is still recorded — through the fail-soft
        # spelling, because CPython's own formatter cannot format it.
        assert "Boom: bang" in recorded["traceback"]

    def test_CPython_itself_cannot_format_such_an_exception(self) -> None:
        """The measurement that made the first version of the fix insufficient.

        Guarding this module's own ``__module__`` read left the recorder
        crashing one line later, in ``traceback.format_exception`` —
        ``TracebackException.format_exception_only`` reads
        ``self.exc_type.__module__`` unguarded. So the fail-soft wrapper is
        load-bearing rather than belt-and-braces: without it the recorder still
        raises."""
        import traceback as tb_mod

        exc_cls = _module_less('type("Boom", (Exception,), {})')
        try:
            raise exc_cls("bang")
        except Exception as exc:  # noqa: BLE001
            with pytest.raises(AttributeError):
                tb_mod.format_exception(exc)

    def test_the_asgi_walk_survives_a_module_less_wrapper(self) -> None:
        """``manage.py check`` walks arbitrary ASGI wrapper objects. This drives
        the REAL check function through a real ``ASGI_APPLICATION`` setting
        rather than calling the expression directly."""
        from django.test import override_settings

        from djust.checks.configuration import check_configuration

        wrapper_cls = _module_less('type("Wrap", (), {})')
        wrapper = wrapper_cls()

        mod = types.ModuleType("djust_test_asgi_2488")
        app = types.SimpleNamespace(application_mapping={"websocket": wrapper})
        mod.application = app  # type: ignore[attr-defined]
        sys.modules["djust_test_asgi_2488"] = mod
        try:
            with override_settings(ASGI_APPLICATION="djust_test_asgi_2488.application"):
                # The assertion is that this RETURNS rather than raising
                # `AttributeError: __module__` out of the check framework.
                messages = check_configuration(None)
            assert isinstance(messages, list)
        finally:
            del sys.modules["djust_test_asgi_2488"]


class TestTheSinkIsPinnedAsASet:
    """Grep the SINK and pin the SET, not a floor (#1125).

    The rule: a ``type(...).__module__`` read is either wrapped in ``getattr``
    with a default, or it lives inside its own ``except AttributeError``. The
    second shape has exactly one inhabitant and it is named, so a FOURTH
    unguarded read reddens this in the same breath as a deleted guard.
    """

    #: ``type(<anything>).__module__`` not written as `getattr(type(x), "__module__"`.
    BARE = re.compile(r"(?<!getattr\()type\([^)]*\)\.__module__")

    @staticmethod
    def _sources() -> dict[pathlib.Path, str]:
        return {
            p: p.read_text(encoding="utf-8") for p in PKG.rglob("*.py") if "tests" not in p.parts
        }

    def test_the_only_bare_read_is_the_one_with_its_own_except(self) -> None:
        hits = {
            p.relative_to(REPO).as_posix(): self.BARE.findall(src)
            for p, src in self._sources().items()
            if self.BARE.search(src)
        }
        assert set(hits) == {"python/djust/templatetags/live_tags.py"}, (
            "an unguarded `type(...).__module__` appeared: "
            f"{sorted(hits)} — see #2488, the crash is in the branch that "
            "builds the message"
        )

    def test_the_exemption_really_has_its_own_except(self) -> None:
        src = (PKG / "templatetags" / "live_tags.py").read_text(encoding="utf-8")
        block = src.split("type(view).__module__", 1)[1]
        assert "except AttributeError" in block.split("\n\n", 1)[0]

    def test_the_three_fixed_sites_are_written_with_getattr(self) -> None:
        for rel in (
            "serialization.py",
            "observability/tracebacks.py",
            "checks/configuration.py",
        ):
            src = (PKG / rel).read_text(encoding="utf-8")
            assert "getattr(type(" in src and '"__module__"' in src, rel

    def test_the_pin_goes_red_in_BOTH_directions(self) -> None:
        """The canary. Each mutation asserts it APPLIED before its count is
        read, so a no-op edit cannot report a passing number (#2129/#2135)."""
        guarded = 'value_module = getattr(type(value), "__module__", "<unknown>")'
        assert not self.BARE.search(guarded), "the guarded form must not match"
        bare = "value_module = type(value).__module__"
        assert bare != guarded, "the mutation did not apply"
        assert self.BARE.search(bare), "the bare form must match"
