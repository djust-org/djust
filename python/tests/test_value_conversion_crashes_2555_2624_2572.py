"""Three crashes in the Python-to-``Value`` conversion (#2555, #2624, #2572).

Each was a whole-process failure — SIGSEGV or a hang — rather than an
exception, so every case here renders the offending input in a SUBPROCESS
with a timeout and asserts on its exit code. An in-process test cannot assert
"does not segfault": the segfault takes the test runner with it.

Every case runs on BOTH settings of ``template_resolve_lazy``: the two walks
differ, but both end in the same conversion, and all three crashes lived
there.

Root causes, symptom-up (not the ones the issues cited):

* #2555 — a ``str`` holding a lone surrogate fails ``extract::<String>()``
  (that is an encode), fell through to the fallback block, and was iterated
  as a sized, re-iterable object whose items are one-character ``str``s
  that fail the same way: unbounded recursion, stack overflow.
* #2624 — ``{{ v.0 }}`` on a ``list``-subclass class is Django's own
  ``current[int(bit)]`` and legitimately yields a ``types.GenericAlias``;
  converting THAT recursed, because ``iter(alias)`` yields a fresh starred
  copy of itself each level. The walk was right; the conversion had no
  depth ceiling.
* #2572 — PyO3's ``Vec`` extraction drives any ``PySequence_Check`` object
  through ``iter()``, which for a ``__getitem__``-only class is the legacy
  sequence protocol: index 0, 1, 2, … until ``IndexError``. Never raising
  means never stopping.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading

import pytest

# The child configures Django itself; the flag is taken from argv so the REAL
# config path (`LIVEVIEW_CONFIG["template_resolve_lazy"]`) is what the render
# reads, exactly as the ADR-027 characterization net does it.
_CHILD = textwrap.dedent(
    """
    import sys, threading
    import django
    from django.conf import settings

    settings.configure(
        SECRET_KEY="x",
        DEBUG=False,
        TEMPLATES=[{
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {},
        }],
        LIVEVIEW_CONFIG={"template_resolve_lazy": sys.argv[1] == "lazy"},
    )
    django.setup()
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )

    class L(list):
        do_not_call_in_templates = True

    class NeverRaises:
        def __getitem__(self, k):
            return "x"
        def __str__(self):
            return "never-raises"

    class SurrogateStr:
        def __str__(self):
            return "s\\udcc0"

    CASES = {
        "2555": ("{{ h }}", {"h": "\\udcc0x"}),
        "2555-object-str": ("{{ o }}", {"o": SurrogateStr()}),
        "2555-pair": ("{{ h }}|{{ h|length }}", {"h": "\\ud83d\\ude00"}),
        "2624": ("{{ v.0 }}", {"v": L}),
        "2572": ("{{ v }}", {"v": NeverRaises()}),
        "2572-index": ("{{ v.0 }}", {"v": NeverRaises()}),
        "2624-8mib": ("{{ v.0 }}", {"v": L}),
    }
    src, ctx = CASES[sys.argv[2]]
    if sys.argv[2] == "2624-8mib":
        threading.stack_size(8 * 1024 * 1024)

    def render():
        return backend.from_string(src).render(context=ctx, request=None)

    # Rendered on a thread with the platform's REAL default stack (16 MiB on
    # macOS, 8 MiB under glibc — the same as a `sync_to_async` worker), which
    # is the stack the depth ceiling has to fit. A first draft shrank this to
    # 512 KiB on the false premise that workers were that small.
    out = []
    t = threading.Thread(target=lambda: out.append(render()))
    t.start()
    t.join()
    sys.stdout.write(repr(out[0]))
    """
)


def _render_in_child(case: str, lazy: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _CHILD, "lazy" if lazy else "eager", case],
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("lazy", [True, False], ids=["lazy", "eager"])
class TestValueConversionDoesNotTakeTheProcess:
    def test_2555_a_lone_surrogate_renders_instead_of_segfaulting(self, lazy: bool) -> None:
        """Django renders ``'\\udcc0x'`` and only its HTTP encoding raises
        (``UnicodeEncodeError: surrogates not allowed``). Rust cannot hold the
        code point, so it crosses as U+FFFD — ``errors="replace"`` — and the
        character next to it survives."""
        result = _render_in_child("2555", lazy)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        assert result.stdout == repr("�x")

    def test_2555_an_object_whose_str_holds_a_surrogate(self, lazy: bool) -> None:
        """The same crossing for a ``__str__`` that yields one: the object
        keeps its carrier and renders lossily rather than declining."""
        result = _render_in_child("2555-object-str", lazy)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        assert result.stdout == repr("s�")

    def test_2624_numeric_index_on_a_container_subclass_class(self, lazy: bool) -> None:
        """Django renders ``str(L[0])`` — its step-3 ``current[int(bit)]``
        honours ``__class_getitem__`` — so djust does too, instead of
        recursing into the alias until the stack overflows.

        The ADR-027 sink matches Django to the byte. The escape-hatch walk
        reaches the alias one step earlier — its step 1 is the unguarded
        string-key ``L["0"]`` (the documented pre-ADR deviation the flip
        exists to retire), so its alias spells ``L['0']`` and autoescapes.
        Both are ``str(alias)``; neither is a crash."""
        result = _render_in_child("2624", lazy)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        if lazy:
            assert result.stdout == repr("__main__.L[0]")
        else:
            assert result.stdout == repr("__main__.L[&#x27;0&#x27;]")

    def test_2572_a_getitem_that_never_raises_renders_as_str(self, lazy: bool) -> None:
        """Django never iterates such an object for ``{{ v }}``: it is
        ``str(v)``. The conversion used to walk the legacy sequence protocol
        forever."""
        result = _render_in_child("2572", lazy)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        assert result.stdout == repr("never-raises")

    def test_2572_index_lookup_terminates(self, lazy: bool) -> None:
        """``{{ v.0 }}`` used to hang exactly as ``{{ v }}`` did — the hang
        was in converting the root, before any segment was walked.

        Only termination is pinned here. Django answers ``'x'`` (one
        ``__getitem__`` call); djust answers ``'n'``, because an unsized
        iterable past ``OPAQUE_ITEM_CAP`` is DECLINED to the terminal
        ``str(v)`` and the segment then indexes that string — the
        pre-existing decline behaviour of the opaque carrier, tracked at
        #2670 rather than widened here (#1079)."""
        result = _render_in_child("2572-index", lazy)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        assert result.stdout, result.stderr[-2000:]


class TestTheStringArmClaimsASurrogate:
    """The #2555 fix is the ``str`` arm claiming the value BY TYPE — not the
    depth ceiling catching the recursion 128 levels down. With the arm gated
    off, the ceiling alone would still render ``'�x'`` (through a 128-deep
    ``Encoded`` chain), so the subprocess cells above cannot tell the two
    mechanisms apart; this pin can (#2233: name the test that goes red when
    only THIS mechanism is removed)."""

    def test_a_surrogate_str_crosses_as_a_string_on_both_gates(self) -> None:
        from djust import _rust

        assert _rust.crosses_as_encoded("\udcc0x") is False
        assert _rust.crosses_as_encoded_by_conversion("\udcc0x") is False


class TestSurrogateReplacementIsPerCodePoint:
    """One U+FFFD per lone surrogate — and two adjacent lone surrogates that
    happen to form a valid UTF-16 pair are TWO code points in Python and stay
    two. The first fix round-tripped through UTF-16, which joined them into
    one astral character (`'😀'`, `|length` 1); the #2673 review caught it."""

    @pytest.mark.parametrize("lazy", [True, False], ids=["lazy", "eager"])
    def test_a_high_low_pair_is_two_replacements_not_one_character(self, lazy: bool) -> None:
        result = _render_in_child("2555-pair", lazy)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        assert result.stdout == repr("��|2")


class TestTheBoundedSequenceGate:
    """The #2572 gate is a rule about the operation, not a list of shapes:
    a sequence crosses as a list only when it states a bound and honours it."""

    def test_every_builtin_sequence_still_crosses_as_a_list(self) -> None:
        from collections import deque

        from djust import _rust

        for shape in ([1, 2], (1, 2), range(3), b"ab", bytearray(b"ab"), deque([1, 2])):
            assert _rust.crosses_as_encoded(shape) is False, shape

    def test_a_legacy_sequence_with_a_len_crosses_as_a_list(self) -> None:
        from djust import _rust

        class Bounded:
            def __len__(self):
                return 2

            def __getitem__(self, i):
                if i >= 2:
                    raise IndexError(i)
                return i

        assert _rust.crosses_as_encoded(Bounded()) is False
        assert _rust.crosses_as_encoded_by_conversion(Bounded()) is False

    def test_a_legacy_sequence_without_a_len_is_declined_by_both_gates(self) -> None:
        """The cheap probe and the real conversion must agree on the new
        decline, or `crosses_as_encoded` drifts from the arm it mirrors."""
        from djust import _rust

        class Unbounded:
            def __getitem__(self, i):
                return "x"

        done = []

        def probe():
            done.append(
                (
                    _rust.crosses_as_encoded(Unbounded()),
                    _rust.crosses_as_encoded_by_conversion(Unbounded()),
                )
            )

        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(timeout=20)
        assert done, "the probe hung (the legacy-sequence walk did not terminate)"
        # Declined by the list arm; not claimed by the opaque carrier either
        # (an unsized iterable past OPAQUE_ITEM_CAP), so it is the terminal
        # `str(o)` path on both.
        assert done[0] == (False, False)

    def test_a_sequence_that_yields_past_its_stated_bound_is_declined_not_truncated(self) -> None:
        from djust import _rust

        class Liar:
            def __len__(self):
                return 1

            def __iter__(self):
                return iter([1, 2, 3])

            def __getitem__(self, i):
                return [1, 2, 3][i]

        # Not a list of one element — the opaque carrier, whose items are
        # what `iter()` yields and whose `len` is what `__len__` says.
        assert _rust.crosses_as_encoded(Liar()) is True
        assert _rust.crosses_as_encoded_by_conversion(Liar()) is True


def _nested(depth: int) -> list:
    v: object = "leaf"
    for _ in range(depth):
        v = [v]
    return v  # type: ignore[return-value]


def _render_leaf(depth: int) -> str:
    """``{{ v.0.0…0 }}`` with ``depth`` segments over a ``depth``-deep list —
    the LEAF, which is what the ceiling can actually truncate. The first
    version of this test rendered ``{{ v|length }}`` of the root, which is
    ``1`` at any ceiling ≥ 1 and stayed green with the ceiling gated off (the
    #2673 review)."""
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    src = "{{ v" + ".0" * depth + " }}"
    return backend.from_string(src).render(context={"v": _nested(depth)}, request=None)


class TestTheDepthCeiling:
    def test_the_ceiling_is_pythons_own_recursion_limit(self) -> None:
        """1000 — the depth at which CPython itself refuses to ``repr`` /
        ``json.dumps`` a structure, so no structure Python's own tooling
        accepts can observe the ceiling. A first draft said 128 "for a 512 KiB
        worker"; that stack does not exist (16 MiB macOS / 8 MiB glibc) and 128
        regressed a 150-deep list `main` and Django both render."""
        from djust import _rust

        assert _rust.MAX_CONVERSION_DEPTH == 1000 == sys.getrecursionlimit()

    def test_python_refuses_a_ceiling_deep_context_before_rust_can(self) -> None:
        """Under Python's DEFAULT recursion limit the ceiling is unobservable:
        the backend's own `serialize_value` (`template/serialization.py`) is
        a recursive Python walk that spends one frame per level, so a
        (ceiling - 1)-deep list raises `RecursionError` in Python before the
        Rust conversion ever sees it. That is the design goal of choosing the
        limit itself as the ceiling."""
        from djust import _rust

        with pytest.raises(RecursionError):
            _render_leaf(_rust.MAX_CONVERSION_DEPTH - 1)

    def test_the_leaf_below_the_ceiling_renders_and_past_it_does_not(self) -> None:
        """With Python's limit lifted, the ceiling becomes observable — and
        exact: the root is depth 0, so a list nested (ceiling - 1) deep has
        its leaf at level (ceiling - 1), converted and reachable by index; one
        level past it the element AT the ceiling crosses as `str(list)` and
        the remaining index segments read into that string instead."""
        from djust import _rust

        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(limit * 8)
        try:
            assert _render_leaf(_rust.MAX_CONVERSION_DEPTH - 1) == "leaf"
            assert _render_leaf(_rust.MAX_CONVERSION_DEPTH + 1) != "leaf"
        finally:
            sys.setrecursionlimit(limit)

    def test_a_150_deep_list_renders_its_leaf(self) -> None:
        """The regression the first draft shipped: 150 < 1000 but > 128."""
        assert _render_leaf(150) == "leaf"

    def test_the_heaviest_chain_at_the_ceiling_fits_an_8_mib_stack(self) -> None:
        """The `GenericAlias` case builds one `Encoded` per level — the
        heaviest per-level frame the conversion has — and must convert a
        full ceiling-deep chain on the SMALLEST real thread stack (glibc's
        8 MiB default; macOS gives 16 MiB). Rendered on such a thread in a
        child, because a stack overflow takes the process."""
        result = _render_in_child("2624-8mib", lazy=True)
        assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
        assert result.stdout == repr("__main__.L[0]")
