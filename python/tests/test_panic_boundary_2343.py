"""A Rust panic must reach Python as an ``Exception``, and ``stringformat:""``
must not panic in the first place (#2343).

The bug
-------
``{{ p|stringformat:"" }}`` did not render wrongly — it took the WebSocket
session down. ``apply_stringformat`` read the conversion character as
``spec.chars().last().unwrap_or('s')``, so an empty spec entered the ``'s'``
arm and reached ``&spec[..spec.len() - 1]``, where ``0usize - 1`` underflows.
Debug builds trap it as ``attempt to subtract with overflow``; release wraps to
``usize::MAX`` and the slice panics one line later. Same blast radius, different
message.

Why the blast radius is the SESSION and not the render
------------------------------------------------------
PyO3 converts an unwind into ``pyo3_runtime.PanicException``, whose MRO is
``['PanicException', 'BaseException', 'object']`` — deliberately NOT an
``Exception``. ``LiveViewConsumer.receive`` wraps its dispatch in
``except Exception`` → ``handle_exception`` → ``send_json``, which is what
normally turns a bad render into an error frame while the socket stays open. A
panic walks straight past that handler.

That premise is falsification-tested rather than asserted from memory (#1867),
but in RUST — ``crates/djust_live/src/lib.rs::panic_boundary_tests::
pyo3s_panic_exception_is_not_an_exception``. It cannot live here: PyO3 creates
the ``pyo3_runtime`` module lazily on the first panic, and after this fix there
is no reachable panic left to create it, so a Python-side check could only ever
skip. If a future PyO3 reparents ``PanicException`` under ``Exception``, that
Rust test goes red and the boundary guard's rationale needs re-reading.

The two halves of the fix
-------------------------
1. ``apply_stringformat`` guards the empty spec and answers ``""`` — which is
   what Django answers, because its body is ``("%" + arg) % value`` and
   ``"%" % value`` raises ``ValueError: incomplete format``, one of the two
   exceptions its ``except (ValueError, TypeError)`` catches.
2. ``guard_panic`` in ``crates/djust_live/src/lib.rs`` wraps the ``_rust``
   entry points that run the engine, so ANY future panic degrades to a
   ``RuntimeError`` — an ``Exception``, therefore containable — instead of
   killing the transport. The guard is a backstop, not a licence: a caught
   panic names an internal file and line rather than the template construct at
   fault, so #2343's own underflow was fixed at its source as well.

Two mechanisms, and each is separately reachable (#2129/#2135): reverting only
half 1 leaves :meth:`TestEmptySpecMatchesDjango.test_empty_spec_renders_djangos_answer`
red while the panic sweep reports the cell as ``GUARDED`` rather than
``PANIC``; reverting only half 2 leaves the sweep reporting ``PANIC`` again for
whatever future defect the guard was netting. The structural pin
(:class:`TestGuardCoversTheRenderSurface`) is what keeps half 2 honest, because
after half 1 there is no reachable panic left for a behavioural test to fire.

What ``catch_unwind`` cannot do
-------------------------------
An allocator ABORT is not an unwind. That is why the padding filters cap their
width (#2348) rather than relying on this, and why
:meth:`TestGuardCoversTheRenderSurface.test_release_profile_does_not_abort_on_panic`
pins the absence of ``panic = "abort"`` — a profile setting would disable the
whole mechanism silently.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_LIB_RS = _REPO / "crates" / "djust_live" / "src" / "lib.rs"
_WORKSPACE_TOML = _REPO / "Cargo.toml"


def _render_both(source: str, value: Any) -> tuple[str, str]:
    return (
        DjangoTemplate(source).render(DjangoContext({"p": value})),
        _rust.render_template(source, {"p": value}),
    )


# The values are chosen to span the arms `apply_stringformat` dispatches on:
# an int and a bool reach the `d`/`i` arm's `int_digits_of`, a float the
# `f`/`e` arms, a str/None/list the `s` arm's `to_string`. Django answers ""
# for every one of them, because the format string never gets as far as the
# value.
_VALUES: list[Any] = [42, -1, 0, "abc", "", None, True, False, 1.5, [1, 2], {"a": 1}]


# The premise itself -- "PanicException is a BaseException, not an Exception" --
# is falsification-tested in Rust, at
# `crates/djust_live/src/lib.rs::panic_boundary_tests`, not here. PyO3 creates
# the `pyo3_runtime` module lazily on the FIRST panic, and after this fix there
# is no reachable panic left to create it, so `import pyo3_runtime` raises
# `ModuleNotFoundError` in a healthy process. An `importorskip` here would be a
# test that can only ever skip. The Rust side can name the type without needing
# a panic to have happened, and also proves `guard_panic`'s own conversion
# lands on an `Exception` subclass.


class TestEmptySpecMatchesDjango:
    """Half 1: the underflow itself."""

    @pytest.mark.parametrize("value", _VALUES)
    def test_empty_spec_renders_djangos_answer(self, value: Any) -> None:
        django_out, djust_out = _render_both('{{ p|stringformat:"" }}', value)
        assert django_out == "", "Django's own answer changed; re-derive this test"
        assert djust_out == django_out

    @pytest.mark.parametrize("value", _VALUES)
    def test_empty_spec_does_not_panic(self, value: Any) -> None:
        """Even a wrong answer would be containable; a panic is not.

        Written as ``except BaseException`` deliberately: a bare
        ``pytest.raises``/no-raise assertion cannot tell the two apart, and the
        distinction IS the bug.
        """
        try:
            _rust.render_template('{{ p|stringformat:"" }}', {"p": value})
        except Exception:  # noqa: BLE001 - an Exception is containable, so it passes
            pass
        except BaseException as exc:  # noqa: BLE001
            pytest.fail(f"{type(exc).__name__} escaped `except Exception` for {value!r}: {exc}")

    def test_a_quoted_and_an_unquoted_empty_spec_agree(self) -> None:
        """Both spellings reach the same filter argument."""
        assert _rust.render_template("{{ p|stringformat:'' }}", {"p": 42}) == ""
        assert _rust.render_template('{{ p|stringformat:"" }}', {"p": 42}) == ""

    def test_the_neighbouring_specs_still_work(self) -> None:
        """The guard must not swallow a spec that HAS a conversion.

        Without these, ``return String::new()`` for every spec would pass the
        cells above.
        """
        for spec, expected in (("s", "42"), ("d", "42"), ("05d", "00042")):
            got = _rust.render_template('{{ p|stringformat:"%s" }}' % spec, {"p": 42})
            assert got == expected, f"stringformat:{spec!r} -> {got!r}"


class TestGuardCoversTheRenderSurface:
    """Half 2: the boundary, pinned structurally.

    After half 1 there is no reachable panic left to fire behaviourally, so
    coverage is pinned by reading the source. The pin is load-bearing rather
    than decorative (#1859): a new engine-running entry point that forgets the
    guard is not in ``_GUARDED`` and fails
    :meth:`test_every_engine_entry_point_is_guarded`, and one that IS in
    ``_GUARDED`` but drops the call fails it too.
    """

    # Every `_rust` entry point that executes template source, walks HTML, or
    # converts a user value -- i.e. everything that can reach the 25k lines of
    # the template engine and the differ. The config setters/getters
    # (`set_active_timezone`, `virtual_keyed_ops_enabled`, ...) and the actor
    # handles are deliberately absent: they store or read a scalar and never
    # enter the engine.
    _GUARDED = frozenset(
        {
            "render_markdown_py",
            "render_template",
            "render_template_with_dirs",
            "diff_html",
            "fast_json_dumps",
            "resolve_template_inheritance",
            "serialize_queryset_py",
            "serialize_context_py",
            "extract_template_variables_py",
            "dj_model_fields_from_template",
            "compute_template_hash",
            "render",
            "render_with_diff",
            "render_binary_diff",
            "template_hash",
            "dj_model_fields",
        }
    )

    @staticmethod
    def _production_source() -> str:
        """``lib.rs`` up to its ``#[cfg(test)]`` module.

        The in-crate test module calls ``guard_panic("probe_entry", ...)`` to
        prove the mechanism converts an unwind; that call is not an entry
        point and must not be read as one.
        """
        source = _LIB_RS.read_text()
        cut = source.index("#[cfg(test)]")
        return source[:cut]

    @classmethod
    def _guarded_in_source(cls) -> set[str]:
        """Every name passed to ``guard_panic(``, read off the Rust source."""
        return set(re.findall(r'guard_panic\(\s*"([a-z_0-9]+)"', cls._production_source()))

    def test_every_engine_entry_point_is_guarded(self) -> None:
        assert self._guarded_in_source() == self._GUARDED

    def test_each_guard_names_its_own_function(self) -> None:
        """The label is the diagnostic, so a copy-pasted one is a real defect."""
        source = self._production_source()
        for name in sorted(self._GUARDED):
            fn = re.search(
                r"^\s*(?:pub )?fn %s\s*\(.*?^(\s*)guard_panic\(\s*\"([a-z_0-9]+)\""
                % re.escape(name),
                source,
                re.S | re.M,
            )
            assert fn is not None, f"no guard_panic call found in fn {name}"
            assert fn.group(2) == name, f"fn {name} passes the label {fn.group(2)!r} to guard_panic"

    def test_guard_converts_to_a_python_exception_subclass(self) -> None:
        """``PyRuntimeError`` is the conversion target, and it IS containable."""
        source = self._production_source()
        assert "PyRuntimeError::new_err" in source
        assert "catch_unwind" in source
        assert issubclass(RuntimeError, Exception)

    def test_release_profile_does_not_abort_on_panic(self) -> None:
        """``panic = "abort"`` would disable ``catch_unwind`` entirely."""
        toml = _WORKSPACE_TOML.read_text()
        assert not re.search(r"^\s*panic\s*=\s*\"abort\"", toml, re.M), (
            "a profile sets panic=abort; catch_unwind cannot run and the "
            "guard_panic boundary is silently dead"
        )


class TestNoReachablePanicAcrossTheFilterSurface:
    """The corpus gap the empty spec fell through, closed.

    The two-build filter differential compares OUTPUTS, so a cell that panics
    is not a divergence it can report — the render never returns a string. This
    sweep asks the other question: does any filter x argument x value reach an
    unwind at all? Every argument spelling that has ever produced one lives in
    ``_ARGS``, the empty string first.
    """

    _ARGS = [
        None,
        '""',
        "''",
        '"0"',
        '"-"',
        '"."',
        '"%"',
        '"s"',
        '"d"',
        '"e"',
        '"f"',
        '"x"',
        '"05d"',
        '".2f"',
        '"-1"',
        '"1,2"',
        '":"',
        '","',
        '"9999999999999999999999"',
        "0",
        "-1",
        "1",
        "1.5",
        "-1.5",
        "True",
        "False",
        "None",
    ]
    _VALUES = [
        5,
        -7,
        0,
        10**30,
        "ab",
        "",
        "中文",
        "<a href='x'>&",
        [1, 2],
        [],
        (1, 2),
        {"a": 1},
        None,
        True,
        False,
        1.5,
        float("inf"),
        float("nan"),
        "  a  b  ",
        "a\nb\r\nc",
    ]

    def test_no_filter_argument_value_cell_unwinds(self) -> None:
        from django.template.defaultfilters import register

        panicked: list[str] = []
        for name in sorted(register.filters):
            for arg in self._ARGS:
                source = "{{ p|%s }}" % name if arg is None else "{{ p|%s:%s }}" % (name, arg)
                for value in self._VALUES:
                    try:
                        _rust.render_template(source, {"p": value})
                    except Exception as exc:  # noqa: BLE001
                        # A raise is a legitimate answer for many of these
                        # cells (#2328 made the numeric arguments raise). A
                        # CAUGHT panic is not -- it means the engine still
                        # unwinds somewhere and only the net is saving us.
                        if "the Rust engine panicked" in str(exc):
                            panicked.append(f"{source} on {value!r}: {exc}")
                    except BaseException as exc:  # noqa: BLE001
                        panicked.append(
                            f"{source} on {value!r}: UNGUARDED {type(exc).__name__}: {exc}"
                        )
        assert not panicked, "\n".join(panicked[:20])
