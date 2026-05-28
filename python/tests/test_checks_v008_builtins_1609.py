"""Tests for #1609 — V008 false-positive on stdlib primitive-returning builtins.

V008 ("Non-primitive type assigned to self.X in mount()") previously
fired on `self.x = max(...)` because `max` wasn't in the SAFE_TYPES set —
the check author included type-constructor names but missed stdlib
builtins that always return primitives.
"""

import textwrap
from unittest.mock import patch


def _run_v008(tmp_path, source):
    """Helper: write source as `views.py` in tmp_path and run V008."""
    py_file = tmp_path / "views.py"
    py_file.write_text(textwrap.dedent(source))

    from djust.checks import _check_non_primitive_assignments_in_mount

    errors = []
    with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
        _check_non_primitive_assignments_in_mount(errors)
    return [e for e in errors if e.id == "djust.V008"]


class TestV008BuiltinsFalsePositive1609:
    """#1609: V008 must NOT fire on stdlib builtins returning primitives."""

    def test_max_does_not_fire_v008_1609(self, tmp_path):
        """Reporter's exact shape: self.x = max(1, len(...))."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.online_count = max(1, 2)
            """,
        )
        assert v008 == [], (
            "V008 should NOT fire on max() — always returns a primitive. Got: %r" % v008
        )

    def test_numeric_builtins_do_not_fire(self, tmp_path):
        """min, sum, abs, round, pow, divmod, len, ord, hash, id all return primitives."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.a = min(1, 2)
                    self.b = sum([1, 2, 3])
                    self.c = abs(-5)
                    self.d = round(3.7)
                    self.e = pow(2, 8)
                    self.f = divmod(10, 3)
                    self.g = len("hello")
                    self.h = ord("a")
                    self.i = hash("x")
                    self.j = id(self)
            """,
        )
        assert v008 == [], "Numeric builtins should not fire V008. Got: %r" % v008

    def test_string_conversion_builtins_do_not_fire(self, tmp_path):
        """bin, oct, hex, repr, chr, ascii, format all return strings."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.a = bin(255)
                    self.b = oct(8)
                    self.c = hex(255)
                    self.d = repr({"a": 1})
                    self.e = chr(65)
                    self.f = ascii("hello")
                    self.g = format(3.14, ".2f")
            """,
        )
        assert v008 == [], "String-conversion builtins should not fire V008. Got: %r" % v008

    def test_sorted_does_not_fire(self, tmp_path):
        """sorted() returns a list — element serializability is the user's responsibility,
        same trust contract as list() which is already in SAFE_TYPES."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.items = sorted([3, 1, 2])
            """,
        )
        assert v008 == [], "sorted() should not fire V008. Got: %r" % v008

    def test_frozenset_does_not_fire(self, tmp_path):
        """frozenset() returns an immutable set (JSON-serializable as a list)."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.tags = frozenset(["a", "b", "c"])
            """,
        )
        assert v008 == [], "frozenset() should not fire V008. Got: %r" % v008

    def test_bytes_does_not_fire(self, tmp_path):
        """bytes() returns bytes (base64-encodable; conventionally JSON-serializable
        via the framework's serializer)."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.data = bytes(b"hello")
            """,
        )
        assert v008 == [], "bytes() should not fire V008. Got: %r" % v008

    def test_custom_class_still_fires_v008(self, tmp_path):
        """Regression backstop: non-primitive custom classes STILL fire V008."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.report = ReportBuilder()
            """,
        )
        assert len(v008) == 1, "V008 must still fire on custom class. Got: %r" % v008
        assert "ReportBuilder" in v008[0].msg

    def test_complex_still_fires(self, tmp_path):
        """complex() returns complex numbers which are NOT JSON-serializable."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.z = complex(1, 2)
            """,
        )
        assert len(v008) == 1, "complex() must still fire V008. Got: %r" % v008

    def test_reversed_still_fires(self, tmp_path):
        """reversed() returns an iterator — user must materialize via list()."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.r = reversed([1, 2, 3])
            """,
        )
        assert len(v008) == 1, "reversed() must still fire V008. Got: %r" % v008
