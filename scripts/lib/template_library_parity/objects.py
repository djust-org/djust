"""Which arguments of a tag are OBJECTS, found by asking the Python tag (#3666).

The Rust engine hands a native handler its operands as strings (scalars) and JSON
(lists, dicts). A ``Form``, a ``BoundField``, a ``datetime``, a model instance in a
list: none of those survive the trip, so a handler cannot read ``field.errors`` from
what it is given. Which tags take such an argument is a fact about the PYTHON tags,
not a list anyone should maintain by hand, so this module measures it.

Every parameter of every tag is rendered, through Django's engine, with a recorder in
its place; a tag that uses the recorder as anything but text (reads an attribute that
is not a member of a ``str`` / ``int`` / ``list`` / ``dict``) takes an object. A
parameter that iterates its value is then rendered a second time with a LIST of
recorders, to see whether the ELEMENTS are objects (``avatar_group users=...`` calls
``user.get_full_name()``).

Pure measurement: nothing here changes a handler. Since 2.0 every tag without a kept native twin
renders its own Python node under the Rust engine (``djust.components.rust_handlers``), so objects
reach it as themselves; this measurement is what ``scripts/template-library-parity.py --fidelity``
reports.
"""

from __future__ import annotations

from typing import Any, Callable

from . import variants

#: Attributes of the builtin value types. A tag calling ``label.title()`` is treating
#: a ``str`` as a ``str``; the recorder is not a ``str``, so it sees the call as an
#: attribute read, and this is how that is told apart from ``field.errors``.
_BUILTIN_MEMBERS = frozenset(
    name
    for typ in (str, int, float, list, dict, tuple, bytes)
    for name in dir(typ)
    if not name.startswith("__")
)

#: What the recorder was asked for that says "I am a list / mapping", not "I am an object".
_CONTAINER_USES = frozenset({"iter", "len", "getitem", "contains"})


class Recorder:
    """A value that renders as text and remembers how else it was used."""

    def __init__(self, log: list[str]) -> None:
        object.__setattr__(self, "_log", log)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            # ``__html__`` (``conditional_escape``), ``__wrapped__``, ``__len__`` lookups by
            # introspection: protocols, not a read of a field.
            raise AttributeError(name)
        self._log.append("attr:" + name)
        raise AttributeError(name)

    def __iter__(self) -> Any:
        self._log.append("iter")
        return iter(())

    def __len__(self) -> int:
        self._log.append("len")
        return 0

    def __getitem__(self, key: Any) -> Any:
        self._log.append("getitem")
        raise KeyError(key)

    def __contains__(self, key: Any) -> bool:
        self._log.append("contains")
        return False

    def __bool__(self) -> bool:
        return True

    def __str__(self) -> str:
        return "RECORDER"

    __hash__ = object.__hash__


def _attrs(log: list[str]) -> list[str]:
    return sorted(
        {entry[5:] for entry in log if entry.startswith("attr:")} - _BUILTIN_MEMBERS,
    )


def object_parameters(
    infos: dict[str, variants.TagInfo],
    engine: Any,
    make_context: Callable[[], dict[str, Any]],
    reset: Callable[[], None],
) -> dict[str, dict[str, list[str]]]:
    """``{"<tag>|<param>": {"value": [attrs], "element": [attrs]}}`` for tags that take objects.

    ``value`` lists the attributes the tag reads of the argument itself; ``element``
    those it reads of the items of a list given for it. A parameter that uses neither
    is not reported. ``infos`` is the harness inventory; only tags with a native
    handler are looked at (the question is about those: a tag with none is rendered by
    the Python node already, whatever it takes).
    """
    found: dict[str, dict[str, list[str]]] = {}
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
            report: dict[str, list[str]] = {}
            call = ["%s=%s" % (m, variants.token(m, "typical")) for m in fixed if m != name]
            call.append("%s=probe" % name)
            src = variants.wrap(
                info.lib, info.name, " ".join(call), info.is_block, parent, parent_args
            )
            for shape in ("value", "element"):
                log: list[str] = []
                recorder = Recorder(log)
                ctx = make_context()
                ctx["probe"] = recorder if shape == "value" else [recorder]
                reset()
                try:
                    engine.from_string(src).render(ctx)
                except Exception:  # noqa: BLE001 - a tag that rejects the recorder still told us how it read it
                    pass
                attrs = _attrs(log)
                if attrs:
                    report[shape] = attrs
                if shape == "value" and not (_CONTAINER_USES & {e for e in log if ":" not in e}):
                    # Not a container, so its elements are not a question.
                    break
            if report:
                found["%s|%s" % (key, name)] = report
    return found
