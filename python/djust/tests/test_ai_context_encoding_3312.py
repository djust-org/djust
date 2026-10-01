"""``djust_ai_context`` writes UTF-8 and never leaves a partial file (#3312, item 1).

The generated text carries an em dash. The command opened its target with no
``encoding=``, so under an ASCII locale (``LC_ALL=C PYTHONUTF8=0``) the first
run hit ``UnicodeEncodeError`` AFTER ``open(..., "x")`` had created the file:
a 0-byte ``CLAUDE.md`` and, on the rerun, "already exists". The locale is
simulated by giving the module's ``open`` an ASCII default encoding, which is
what the builtin falls back to there.
"""

import builtins
import errno
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def ascii_locale(monkeypatch):
    def ascii_open(path, mode="r", *args, **kwargs):
        if len(args) < 2:  # buffering, encoding positional
            kwargs.setdefault("encoding", "ascii")
        return builtins.open(path, mode, *args, **kwargs)

    monkeypatch.setattr(
        "djust.management.commands.djust_ai_context.open", ascii_open, raising=False
    )


def _run(*args):
    call_command("djust_ai_context", *args, stdout=StringIO())


def test_generated_text_is_not_ascii():
    out = StringIO()
    call_command("djust_ai_context", "--print", stdout=out)
    assert not out.getvalue().isascii(), "the em dash this test relies on is gone"


def test_first_run_succeeds_under_an_ascii_locale(in_tmp, ascii_locale):
    _run()
    text = (in_tmp / "CLAUDE.md").read_bytes().decode("utf-8")
    assert text.startswith("# CLAUDE.md")


def test_force_succeeds_under_an_ascii_locale(in_tmp, ascii_locale):
    (in_tmp / "CLAUDE.md").write_text("old\n")
    _run("--force")
    assert (in_tmp / "CLAUDE.md").read_bytes().decode("utf-8").startswith("# CLAUDE.md")


class _FailingWrite:
    """A file whose write dies part-way, as a full disk does."""

    def __init__(self, real):
        self._real = real

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._real.close()
        return False

    def write(self, text):
        self._real.write(text[:10])
        self._real.flush()
        raise OSError(errno.ENOSPC, "No space left on device")


def test_a_failed_write_leaves_no_partial_file(in_tmp, monkeypatch):
    failing = [True]

    def failing_open(path, mode="r", *args, **kwargs):
        real = builtins.open(path, mode, *args, **kwargs)
        return _FailingWrite(real) if failing[0] else real

    monkeypatch.setattr(
        "djust.management.commands.djust_ai_context.open", failing_open, raising=False
    )
    with pytest.raises(OSError):
        _run()
    assert not (in_tmp / "CLAUDE.md").exists()
    # ...so the rerun is not refused as "already exists".
    failing[0] = False
    _run()
    assert (in_tmp / "CLAUDE.md").stat().st_size > 0


def test_a_failed_force_write_keeps_the_original(in_tmp, monkeypatch):
    (in_tmp / "CLAUDE.md").write_text("hand written\n")

    def failing_open(path, mode="r", *args, **kwargs):
        return _FailingWrite(builtins.open(path, mode, *args, **kwargs))

    monkeypatch.setattr(
        "djust.management.commands.djust_ai_context.open", failing_open, raising=False
    )
    with pytest.raises(OSError):
        _run("--force")
    assert (in_tmp / "CLAUDE.md").read_text() == "hand written\n"


def test_refusal_still_works(in_tmp):
    (in_tmp / "CLAUDE.md").write_text("mine\n")
    with pytest.raises(CommandError, match="--force"):
        _run()
    assert (in_tmp / "CLAUDE.md").read_text() == "mine\n"
