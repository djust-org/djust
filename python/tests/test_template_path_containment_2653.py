"""A template name may not escape its search directory.

`FilesystemTemplateLoader::find_template` did a bare `dir.join(name)` with no
containment check. Django's loaders get that check from `safe_join`, which
raises `SuspiciousFileOperation` and makes the loader skip the directory.

On this branch the operand is a LITERAL — `{% include %}` and `{% extends %}`
use the token as written — so the sink is reached by a template AUTHOR, not by
request data. That still matters wherever template authorship is not fully
trusted (a CMS with user-editable templates, multi-tenant hosting): the engine
is the boundary, and it was not enforcing one. Measured before the fix,
`{% include "../../SECRET.txt" %}` rendered the file's contents where Django
answers `TemplateDoesNotExist`.

It also becomes reachable from DATA the moment the operand resolves against the
render context, which is a change already in flight — so this guard is a
prerequisite for that work, not a reaction to it.

The check is LEXICAL and runs before any filesystem call, so a symlink cannot
race it and the path need not exist. Note the interior case: `a/../../x` is
refused exactly like `../x`. A prefix-only reading — which is all
`construct_relative_path` does, and only for `{% extends %}` — would let it
through, which is why the guard lives in the loader rather than beside that
helper.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("django")

from djust import _rust  # noqa: E402


@pytest.fixture
def tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """A template dir with a secret one level above it."""
    (tmp_path / "secret.txt").write_text("TOP-SECRET-CONTENTS", encoding="utf-8")
    tpl = tmp_path / "templates"
    (tpl / "sub").mkdir(parents=True)
    (tpl / "plain.html").write_text("PLAIN-OK", encoding="utf-8")
    (tpl / "sub" / "ok.html").write_text("SUB-OK", encoding="utf-8")
    return tpl


def _render(tree: pathlib.Path, name: str) -> str:
    # A LITERAL operand — that is what this branch's `{% include %}` accepts.
    source = "{% include '" + name.replace("'", "\\'") + "' %}"
    return _rust.render_template_with_dirs(source, {}, [str(tree)])


#: Every shape that must NOT resolve. The interior-`..` and backslash rows are
#: the ones a prefix-only or POSIX-only check lets through.
_ESCAPES = [
    "../secret.txt",
    "./../secret.txt",
    "sub/../../secret.txt",
    "sub/../../../secret.txt",
    "a/b/../../../secret.txt",
    "..\\secret.txt",
    "sub\\..\\..\\secret.txt",
    "/etc/hosts",
    "//etc/hosts",
]


@pytest.mark.parametrize("name", _ESCAPES)
def test_escaping_names_do_not_read_the_file(tree: pathlib.Path, name: str) -> None:
    """The file's CONTENT is what must never appear — asserting "an exception
    was raised" would also pass if the read happened and then something else
    failed."""
    try:
        out = _render(tree, name)
    except Exception as exc:  # noqa: BLE001 — any refusal is acceptable
        assert "TOP-SECRET" not in str(exc), f"{name} leaked through the error"
        return
    assert "TOP-SECRET" not in out, f"{name} leaked the file contents"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("plain.html", "PLAIN-OK"),
        ("sub/ok.html", "SUB-OK"),
        # A `..` that stays INSIDE names a real template and must still work —
        # the guard rejects escaping, not the segment.
        ("sub/../plain.html", "PLAIN-OK"),
        ("./plain.html", "PLAIN-OK"),
    ],
)
def test_contained_names_still_resolve(tree: pathlib.Path, name: str, expected: str) -> None:
    assert _render(tree, name).strip() == expected


def test_the_guard_is_what_blocks_it(tree: pathlib.Path) -> None:
    """Non-vacuity: the secret is readable, and the template dir is its parent.

    If this setup could not read the file at all, every assertion above would
    pass for the wrong reason.
    """
    secret = tree.parent / "secret.txt"
    assert secret.read_text(encoding="utf-8") == "TOP-SECRET-CONTENTS"
    assert secret.parent == tree.parent
    # And the same content IS reachable when it sits inside the search dir,
    # proving the loader can read a plain text file when containment allows.
    (tree / "inside.txt").write_text("TOP-SECRET-CONTENTS", encoding="utf-8")
    assert "TOP-SECRET" in _render(tree, "inside.txt")


def test_a_directory_is_not_a_template(tree: pathlib.Path) -> None:
    """`is_file`, not `exists` (#1805 is_dir-parity): a DIRECTORY named like a
    template satisfied `exists()` and then failed on read."""
    (tree / "adir.html").mkdir()
    with pytest.raises(Exception, match="not found|Template"):
        _render(tree, "adir.html")
