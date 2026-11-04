"""Which tags give a different answer for a value's TYPE, found by rendering (#3756).

``objects.py`` asks "does the tag READ AN ATTRIBUTE of this argument", with a recorder
that raises on attribute access. That cannot see a tag that dispatches on TYPE
(``isinstance(value, dict)``, ``isinstance(value, (int, float))``) because dispatching
reads no attribute. A recorder that is "a recognisable instance of a type" is a
different question, and answering it by construction needs a recorder per type. This
module answers it by measurement.

**The definition.** The Rust engine hands a native handler a JSON-shaped *stand-in* for a
value it cannot carry: ``str(v)`` for a ``datetime`` / ``date`` / ``UUID`` / ``Path`` /
``Enum`` / ``bytes`` / ``set``, a ``float`` for a ``Decimal``, a ``list`` for a ``tuple``.
A tag is *type-sensitive* in a parameter when the PYTHON tag renders the value differently
from its stand-in: Django ran the tag on the real object, so a native handler that only
sees the stand-in cannot be right unless it can recover the type. That definition is
relative, so it is immune to the tag's other, already-measured differences (a tag that
differs from Django for every value, a plain ``int`` included, is the parity ratchet's
business, not a type finding), which is what a "do the engines agree on a control value"
guard got wrong in a first version.

For every cell the report carries whether the Rust engine (with its native handler)
nevertheless agrees with Django (``rust_agrees``), and whether the two engines agree on the
STAND-IN (``isolated``). A type-sensitive cell that is ``isolated`` and not ``rust_agrees``
is a parity defect caused by the lost type and nothing else; one that is not ``isolated``
differs for a reason of its own (the parity ratchet's) and is not counted as type loss. A cell where Django REFUSES the real value but
renders the stand-in (``refused``) is invalid input, #3667's territory, and only counted.

Pure measurement, nothing here changes a handler.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import pathlib
import re
import uuid
from typing import Any, Callable

from . import variants


class Colour(enum.Enum):
    RED = "red"


class Level(enum.IntEnum):
    HIGH = 3


class _Text(str):
    """A ``str`` subclass: ``type(v) is str`` fails, ``isinstance(v, str)`` holds."""


def _text(v: Any) -> str:
    return str(v)


#: ``{label: (factory, stand-in)}``. Factories, not instances: ``datetime`` and ``date``
#: are replaced by the harness (``install_determinism``), and a value built at import time
#: would be an instance of the real class.
TYPED_VALUES: dict[str, tuple[Callable[[], Any], Callable[[Any], Any]]] = {
    "datetime": (
        lambda: datetime.datetime(2024, 3, 15, 11, 0, 0, tzinfo=datetime.timezone.utc),
        _text,
    ),
    "naive datetime": (lambda: datetime.datetime(2024, 3, 15, 11, 0, 0), _text),
    "date": (lambda: datetime.date(2024, 3, 15), _text),
    "time": (lambda: datetime.time(11, 30), _text),
    "timedelta": (lambda: datetime.timedelta(minutes=90), _text),
    "Decimal": (lambda: decimal.Decimal("1.5"), float),
    "UUID": (lambda: uuid.UUID(int=7), _text),
    "bytes": (lambda: b"ab", _text),
    "Enum": (lambda: Colour.RED, _text),
    "IntEnum": (lambda: Level.HIGH, int),
    "Path": (lambda: pathlib.PurePosixPath("/a/b"), _text),
    "tuple": (lambda: (1, 2), list),
    "set": (lambda: {1}, _text),
    "frozenset": (lambda: frozenset({1}), _text),
    "str subclass": (lambda: _Text("txt"), str),
}


def _normalise(text: str) -> str:
    text = re.sub(r"0x[0-9a-f]{6,}", "0xADDR", text)
    return re.sub(r"\s+", " ", text).strip()


def _render(
    engine: Any, src: str, ctx: dict[str, Any], reset: Callable[[], None]
) -> tuple[str, str]:
    reset()
    try:
        return "ok", _normalise(str(engine.from_string(src).render(ctx)))
    except Exception as exc:  # noqa: BLE001 - the exception IS the observation
        return "err", "%s: %s" % (type(exc).__name__, str(exc)[:200])


def differences(
    infos: dict[str, variants.TagInfo],
    python_engine: Any,
    rust_engine: Any,
    make_context: Callable[[], dict[str, Any]],
    reset: Callable[[], None],
) -> dict[str, Any]:
    """``{"cells", "refused", "dispatches": [...]}`` over every native tag's parameters.

    Each dispatch is ``{"tag", "param", "type", "rust_agrees", "isolated", "python",
    "standin", "rust"}`` with a short excerpt of each output. A tag is rendered with the parameters
    the generated calls always pass (switches and required ones) fixed and ``<param>=probe``
    varied, the way ``objects.object_parameters`` does.
    """
    found: list[dict[str, Any]] = []
    cells = 0
    refused = 0
    for key, info in sorted(infos.items()):
        if not info.native:
            continue
        names = variants.passed_names(info)
        required = [s[0] for s in info.sig if s[1] == "req" and s[0] != "context"]
        switches = [n for n in names if variants.kind_of(n) == "bool"]
        fixed = list(dict.fromkeys(switches + [r for r in required if r in names]))
        parent = variants.PARENT.get(info.name)
        parent_info = infos.get("%s:%s" % (info.lib, parent)) if parent else None
        parent_args = "open=True" if parent_info and "open" in parent_info.readable() else ""
        for name in names:
            call = ["%s=%s" % (m, variants.token(m, "typical")) for m in fixed if m != name]
            call.append("%s=probe" % name)
            src = variants.wrap(
                info.lib, info.name, " ".join(call), info.is_block, parent, parent_args
            )
            for label, (factory, standin) in TYPED_VALUES.items():
                cells += 1
                ctx = make_context()
                ctx["probe"] = factory()
                python = _render(python_engine, src, ctx, reset)
                ctx = make_context()
                ctx["probe"] = standin(factory())
                stand = _render(python_engine, src, ctx, reset)
                if python[0] == "err" and stand[0] == "ok":
                    refused += 1
                    continue
                if python[0] != "ok" or stand[0] != "ok" or python[1] == stand[1]:
                    continue
                ctx = make_context()
                ctx["probe"] = factory()
                rust = _render(rust_engine, src, ctx, reset)
                ctx = make_context()
                ctx["probe"] = standin(factory())
                rust_stand = _render(rust_engine, src, ctx, reset)
                found.append(
                    {
                        "tag": key,
                        "param": name,
                        "type": label,
                        "rust_agrees": rust == python,
                        # The engines agree on the stand-in, so the stand-in is the ONLY
                        # difference between this cell's two renders: a disagreement
                        # here is the lost type and nothing else.
                        "isolated": rust_stand == stand,
                        "python": python[1][-160:],
                        "standin": stand[1][-160:],
                        "rust": rust[1][-160:],
                    }
                )
    return {"cells": cells, "refused": refused, "dispatches": found}
