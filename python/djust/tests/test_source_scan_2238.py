"""Tests for the shared prose-stripping primitive (#2238).

The primitive exists because structural pins that grep RAW source are wrong in
both directions — prose naming a pattern counts as a call site, and a call site
inside a commented-out block still counts. Both directions are exercised here
against the real ``websocket.py`` in
``test_ws_send_version_1788.py``; this file covers the primitive itself.
"""

from __future__ import annotations

import re

import pytest

from djust.tests._source_scan import code_only, without_prose

CALL = re.compile(r"\bdanger\s*\(")


# --------------------------------------------------------------------------- #
# What must be blanked (the false-positive half).
# --------------------------------------------------------------------------- #

PROSE_SHAPES = [
    ("hash comment", "# danger()\n"),
    ("trailing comment", "x = 1  # danger()\n"),
    ("module docstring", '"""Never call danger() here."""\nx = 1\n'),
    (
        "function docstring",
        'def f():\n    """Do not use danger() in f."""\n    return 1\n',
    ),
    (
        "class docstring",
        'class C:\n    """danger() is banned."""\n\n    x = 1\n',
    ),
    (
        "multi-line docstring with code after",
        '"""doc\nmentions danger()\n"""\nx = 1\n',
    ),
    (
        "implicitly concatenated docstring",
        'def f():\n    "part one says danger()" " and part two continues"\n    return 1\n',
    ),
    ("bare string statement", 'x = 1\n"danger() in a bare string statement"\nx = 2\n'),
]


@pytest.mark.parametrize("label,src", PROSE_SHAPES, ids=[s[0] for s in PROSE_SHAPES])
def test_without_prose_blanks_every_prose_shape(label, src):
    assert CALL.search(src), f"the fixture itself must contain the pattern: {label}"
    assert not CALL.search(without_prose(src)), f"prose leaked through: {label}"
    assert not CALL.search(code_only(src)), f"prose leaked through code_only: {label}"


# --------------------------------------------------------------------------- #
# What must survive (the false-negative half — blanking real code is the #2213
# failure, which blinded 1318 live lines).
# --------------------------------------------------------------------------- #

CODE_SHAPES = [
    ("bare call", "danger()\n"),
    ("attribute call", "mod.danger()\n"),
    ("indented call", "def f():\n    danger()\n"),
    ("after a comment line", "# a comment\ndanger()\n"),
    ("with a trailing comment", "danger()  # explained here\n"),
    ("code after a module docstring", '"""doc"""\ndanger()\n'),
    ("code after a multi-line docstring", '"""doc\nmore\n"""\ndanger()\n'),
    ("code after a function docstring", 'def f():\n    """doc"""\n    danger()\n'),
    ("second statement on a line", "x = 1; danger()\n"),
    ("CRLF line endings", "# c\r\ndanger()\r\n"),
]


@pytest.mark.parametrize("label,src", CODE_SHAPES, ids=[s[0] for s in CODE_SHAPES])
def test_without_prose_keeps_real_code(label, src):
    assert CALL.search(without_prose(src)), f"real code was blinded: {label}"
    assert CALL.search(code_only(src)), f"real code was blinded by code_only: {label}"


# --------------------------------------------------------------------------- #
# Where the two functions deliberately differ.
# --------------------------------------------------------------------------- #

STRING_VALUE_SHAPES = [
    ("getattr name", 'v = getattr(self, "danger()", 0)\n'),
    ("assigned message", 'MSG = "danger() is wrong"\n'),
    ("f-string", 'MSG = f"do not use danger() here"\n'),
    ("argument", 'log("danger() happened")\n'),
]


@pytest.mark.parametrize("label,src", STRING_VALUE_SHAPES, ids=[s[0] for s in STRING_VALUE_SHAPES])
def test_a_string_used_as_a_value_is_code_to_without_prose_and_prose_to_code_only(label, src):
    """A string literal is data, and data is sometimes the thing being pinned.

    ``_arm_recovery`` reaches its attribute through
    ``getattr(self, "_last_sent_version", 0)`` — the pin that greps for that
    name asserts nothing if string literals are blanked. So ``without_prose``
    keeps them and ``code_only`` (whose caller wants a MENTION not to count)
    does not. The f-string row is the PEP 701 case: on 3.12 the literal text is
    FSTRING_MIDDLE, not STRING, and leaks straight through a naive filter.
    """
    assert CALL.search(without_prose(src)), f"without_prose must keep string values: {label}"
    assert not CALL.search(code_only(src)), f"code_only must blank string values: {label}"


# --------------------------------------------------------------------------- #
# Layout, which the pins' regexes depend on.
# --------------------------------------------------------------------------- #


def test_layout_is_preserved_exactly():
    """Line count and column offsets must survive, in both functions.

    The #1817 pin tells an inline keyword argument (``version=self.f(``) from an
    assignment (``x = self.f(``) by the spacing around ``=``. A strip that
    re-joined tokens with spaces would collapse the two and silently count one
    as the other.
    """
    src = 'def f():\n    """doc\n    spanning lines\n    """\n    kw(version=g())  # note\n'
    for fn in (without_prose, code_only):
        out = fn(src)
        assert len(out.splitlines()) == len(src.splitlines()), fn.__name__
        for original, stripped in zip(src.splitlines(), out.splitlines()):
            assert len(stripped) == len(original), fn.__name__
        assert "version=g()" in out, "the keyword-argument spacing must survive"


def test_a_method_source_scans_like_a_module_source():
    """``inspect.getsource`` of a method is indented, which tokenize rejects.

    Several pins grep a single method. Without the dedent they would silently
    take the unparseable-source fallback and go back to matching prose.
    """
    import inspect

    class C:
        def m(self):
            """This docstring says danger() and the body does not."""
            return 1

    src = inspect.getsource(C.m)
    assert CALL.search(src), "fixture must contain the pattern in its docstring"
    assert not CALL.search(without_prose(src)), (
        "an indented method source fell back to unparsed — the dedent is missing"
    )


def test_unparseable_source_is_returned_unchanged():
    """A syntax error is somebody else's failure; exempting the file silently
    would be the same blindness in a new costume, so the raw text is returned
    and the caller keeps matching against it."""
    broken = "def f(:\n    danger()\n"
    assert without_prose(broken) == broken
    assert code_only(broken) == broken
