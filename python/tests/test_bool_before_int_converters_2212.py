"""No Python->value converter may check `i64` before `bool` (#2212).

PyO3 0.29 extracts a Python ``True`` as ``i64`` ``1`` (its own ``test_i64_bool``
asserts this). So in any function that tries ``i64`` first, the ``bool`` arm is
**dead code** and a bool silently becomes an integer.

This is checked structurally rather than behaviourally because the failure is
invisible by construction: a dead ``if let`` arm is not a compile error, clippy
does not flag it (the arms have different types), and each converter would need
its own end-to-end test to catch it. Six functions extract both types; only two
had a behavioural test before this.

It is NOT a strict subset of a behavioural test — which is the #2167 objection
to source-grep pins — because four of the six converters have no behavioural
coverage at all. The behavioural test for the one this issue was filed about
lives in ``test_template_serialization.py::TestBoolRoundTrip2212``.

History: djust_core always checked bool first; ``python_to_value`` was fixed in
#2211; ``serialize_python_value`` in #2212. The issue claimed three converters
existed — a sweep found **six**. That undercount is exactly why this guard is
structural: enumerating by hand missed half the surface.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FN_START = re.compile(r"^\s*(?:pub )?(?:async )?fn (\w+)")


def _converters() -> list[tuple[str, str, int, bool]]:
    """Every Rust fn extracting BOTH i64 and bool, with whether bool comes first."""
    found = []
    for path in sorted(ROOT.glob("crates/**/*.rs")):
        lines = path.read_text().splitlines()
        starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := FN_START.match(line))]
        for idx, (start, name) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
            body = "\n".join(lines[start:end])
            if "extract::<i64>()" in body and "extract::<bool>()" in body:
                bool_first = body.index("extract::<bool>()") < body.index("extract::<i64>()")
                found.append((str(path.relative_to(ROOT)), name, start + 1, bool_first))
    return found


def test_the_sweep_actually_finds_converters() -> None:
    """Guard the guard: a parser that matches nothing can never fail.

    If the fn-detection regex stops matching — a formatting change, a macro —
    this test goes red instead of the suite silently losing its protection.
    """
    found = _converters()
    assert len(found) >= 5, (
        f"expected at least 5 converters extracting both i64 and bool, found "
        f"{len(found)}. The parser probably stopped matching; fix it rather "
        f"than lowering this bound.\n{found}"
    )


@pytest.mark.parametrize(
    "path,name,line",
    [(p, n, ln) for p, n, ln, _ in _converters()],
    ids=[f"{n}" for _, n, _, _ in _converters()],
)
def test_bool_is_checked_before_i64(path: str, name: str, line: int) -> None:
    matching = [c for c in _converters() if c[0] == path and c[1] == name and c[2] == line]
    assert matching, f"{name} vanished from the sweep"
    assert matching[0][3], (
        f"{path}:{line} `{name}` extracts i64 BEFORE bool.\n"
        f"PyO3 extracts a Python True as i64 1, so the bool arm below it is dead "
        f"code and a bool silently becomes an integer. Move the bool check above "
        f"the i64 check (#2212)."
    )
