"""tests/short_param_ids.py — a parametrize id is never the size of its value (#3800).

``gh run view --log`` stops at the first log line over 64 KiB. Two CI shards of #3800
looked like silent hangs ~100 s in, because ``test_resource_limits["x" * 65537]`` printed
a 65,636-byte line and ``gh`` showed nothing after it: not the 11 minutes of passing tests
that followed, and not the one real, unrelated failure that turned the job red.

Gate-off, per mechanism (#2135: the test that goes red when ONLY that mechanism is removed):

- ``pytest_make_parametrize_id`` returns None (pytest spells the value out again) ->
  ``test_a_big_value_is_named_by_its_size_not_its_content``;
- a salted ``hash()`` for the digest instead of SHA-1 ->
  ``test_every_process_collects_the_same_ids`` (xdist workers collect independently);
- the length threshold removed so every string is rewritten ->
  ``test_a_value_that_reads_fine_keeps_its_id``;
- drop the ``pytest_collection_modifyitems`` refusal ->
  ``test_an_id_over_the_limit_that_the_hook_never_sees_is_refused``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.short_param_ids import MAX_ID_CHARS, MAX_INT_BITS, MAX_VALUE_CHARS, short_id

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "tests.short_param_ids"
ARGV = ("-p", PLUGIN, "-p", "no:cacheprovider", "-p", "no:django", "--collect-only", "-q")

BIG_STR = "x" * 65537
BIG_BYTES = b"\x1b[31m" * 20000

SUITE = f"""
import pytest

@pytest.mark.parametrize("value", [{BIG_STR!r}, {BIG_BYTES!r}, "short", b"short", 7, ["a"] * 500])
def test_value(value):
    pass
"""


@pytest.fixture
def inner(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.delenv("DJUST_COLLECTED_FLOOR", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))  # subprocess runs import the plugin by name
    pytester.syspathinsert(ROOT)
    return pytester


def _collect(pytester: pytest.Pytester) -> pytest.RunResult:
    return pytester.runpytest(*ARGV)


def _ids(result: pytest.RunResult) -> list:
    return [line for line in result.outlines if "::test_value[" in line]


def test_a_big_value_is_named_by_its_size_not_its_content(inner: pytest.Pytester):
    inner.makepyfile(test_big=SUITE)
    ids = _ids(_collect(inner))
    assert len(ids) == 6, ids
    assert max(len(line) for line in ids) < 200, "no collected id is the size of its value"
    text = "\n".join(ids)
    assert "value-str65537-" in text
    assert f"value-bytes{len(BIG_BYTES)}-" in text
    assert "xxxx" not in text


def test_a_value_that_reads_fine_keeps_its_id(inner: pytest.Pytester):
    inner.makepyfile(test_big=SUITE)
    text = "\n".join(_ids(_collect(inner)))
    # pytest's own spelling, untouched, for everything short (and for non-strings).
    assert "test_value[short0]" in text and "test_value[short1]" in text  # str, then bytes
    assert "test_value[7]" in text and "test_value[value5]" in text  # an int; the list
    assert short_id("v", "a" * MAX_VALUE_CHARS) is None
    assert short_id("v", b"a" * MAX_VALUE_CHARS) is None
    assert short_id("v", "a" * (MAX_VALUE_CHARS + 1)) is not None
    for not_text in (7, ["a"] * 500, {"k": "v" * 5000}, None):
        assert short_id("v", not_text) is None


def test_a_huge_int_is_named_by_its_size_and_a_small_one_keeps_its_id():
    huge = 1 << 100000  # ``str()`` of this raises: the id could not even be formatted
    assert short_id("value", huge).startswith("value-int100001bit-")
    assert short_id("value", -huge) != short_id("value", huge)
    assert short_id("value", 1 << MAX_INT_BITS - 1) is None
    assert short_id("value", True) is None


def test_two_different_big_values_get_different_ids():
    n = MAX_VALUE_CHARS + 100
    a = short_id("value", "a" * n)
    b = short_id("value", "a" * (n - 1) + "b")  # same length, one character apart
    assert a != b and a.startswith(f"value-str{n}-") and b.startswith(f"value-str{n}-")
    assert short_id("value", "a" * n) == a
    assert short_id("value", ("a" * n).encode()) != a, "str and bytes do not collide"


def test_every_process_collects_the_same_ids(inner: pytest.Pytester, monkeypatch):
    """xdist workers each collect the suite; a per-process id means a lost-items failure."""
    inner.makepyfile(test_big=SUITE)
    runs = []
    for seed in ("1", "2"):
        monkeypatch.setenv("PYTHONHASHSEED", seed)
        runs.append(_ids(inner.runpytest_subprocess(*ARGV)))
    assert runs[0] and runs[0] == runs[1]


def test_an_id_over_the_limit_that_the_hook_never_sees_is_refused(inner: pytest.Pytester):
    # ``pytest.param(id=...)`` and ``ids=`` never ask the hook, so the refusal is the net.
    inner.makepyfile(
        test_explicit=f"""
        import pytest

        @pytest.mark.parametrize("v", [pytest.param(1, id={"y" * (MAX_ID_CHARS + 1)!r})])
        def test_explicit(v):
            pass
        """
    )
    result = _collect(inner)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "#3800" in result.stderr.str() + result.stdout.str()
    assert "test_explicit" in result.stderr.str() + result.stdout.str()


def test_an_id_at_the_limit_is_accepted(inner: pytest.Pytester):
    inner.makepyfile(
        test_edge=f"""
        import pytest

        @pytest.mark.parametrize("v", [pytest.param(1, id={"y" * 4000!r})])
        def test_edge(v):
            pass
        """
    )
    assert _collect(inner).ret == pytest.ExitCode.OK
