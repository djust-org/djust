"""One walk over a context value for the Rust state sync.

``_sync_state_to_rust`` used to walk every structured context value it sent
three times, one after another:

1. :func:`djust.change_detection.deep_fingerprint` — the structural fingerprint
   change detection compares against the next render (#2664);
2. ``rust_bridge._collect_safe_keys`` — the dotted paths of every
   ``SafeString``, so the renderer does not escape them;
3. :func:`djust.serialization.normalize_django_value` — the value the Rust
   renderer is handed.

On a data-heavy page (a few hundred plain rows) those walks were most of the
sync's Python time, and all of it holds the GIL. :func:`fingerprint_and_convert`
produces all three results in one traversal; :func:`convert_for_render`
produces the last two when the fingerprint was already needed earlier, to
decide whether the value changed at all.

**The outputs are the old functions' outputs, not approximations of them.**
The walk handles only shapes whose three answers are known without asking
anything outside it: exact ``dict`` / ``list`` / ``tuple`` nodes and a short
list of exact leaf types whose conversion cannot log, raise or depend on
configuration. Anything else — a model, a form, a component, a ``TypedState``,
any subclass, a value nested deeper than the fingerprint's depth cap, a cycle
— makes the conversion give up (:data:`NOT_CONVERTED`) and the caller runs the
original two functions for that key, at the point it always ran them. The
fingerprint is always exact: it is :func:`change_detection._walk`, node for
node and budget unit for budget unit.

``test_state_single_pass.py`` checks all three outputs against the three
original functions over a generated corpus.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Hashable, List, Optional, Tuple
from uuid import UUID

from django.utils.safestring import SafeString

from .change_detection import (
    _EXACT_SCALARS,
    _MAX_DEPTH,
    _TAG_DICT,
    _TAG_ID,
    _TAG_LIST,
    _TAG_TRUNCATED,
    _TAG_TUPLE,
    _TAG_VALUE,
    DEFAULT_BUDGET,
    _walk,
    _walk_general,
)

__all__ = ["NOT_CONVERTED", "fingerprint_and_convert", "convert_for_render"]


class _NotConverted:
    """Sentinel: this value needs the original safe-key and conversion passes."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NOT_CONVERTED"


NOT_CONVERTED: Any = _NotConverted()

# Exact scalar types ``normalize_django_value`` returns unchanged and that can
# never be a ``SafeString``. ``bytes`` is an exact fingerprint scalar but is NOT
# here: ``normalize_django_value`` does not pass it through.
_PLAIN_SCALARS = frozenset((str, int, float, bool, type(None)))

# Exact leaf types ``normalize_django_value`` returns unchanged (outside the
# session round trip, which this module never serves). Exact types only: a
# subclass may override anything the conversion consults.
_PASSTHROUGH_LEAVES = frozenset((Decimal, datetime, date, time, timedelta, set, frozenset))

# A path is a linked tuple ``(parent, is_dict, key)`` rooted at the top-level
# prefix string, spelled out only when a ``SafeString`` is found. Spelling it
# out replays ``_collect_safe_keys``'s own prefix rule, falsy prefixes included.
_Path = Any


def _spell(path: _Path) -> Any:
    parts = []
    while type(path) is tuple:
        path, is_dict, key = path
        parts.append((is_dict, key))
    prefix = path
    for is_dict, key in reversed(parts):
        if is_dict:
            prefix = f"{prefix}.{key}" if prefix else key
        else:
            prefix = f"{prefix}.{key}" if prefix else str(key)
    return prefix


def _leaf_out(value: Any, t: type, path: _Path, safe: List[Any]) -> Any:
    """Conversion of a non-container, non-plain-scalar leaf, or NOT_CONVERTED."""
    if t is SafeString:
        prefix = _spell(path)
        if prefix:
            safe.append(prefix)
        return value
    if t in _PASSTHROUGH_LEAVES:
        return value
    if t is UUID:
        return str(value)
    return NOT_CONVERTED


def _fused(
    value: Any,
    counter: List[int],
    depth: int,
    path_ids: Tuple[int, ...],
    path: _Path,
    safe: List[Any],
) -> Tuple[Hashable, Any]:
    """``(_walk(value), converted)``; *converted* may be NOT_CONVERTED.

    The fingerprint half is ``change_detection._walk`` verbatim; keep the two
    in lock-step.
    """
    t = type(value)
    if t in _EXACT_SCALARS:
        counter[0] -= 1
        fp: Hashable = (_TAG_TRUNCATED, id(value)) if counter[0] < 0 else (_TAG_VALUE, t, value)
        return fp, (value if t in _PLAIN_SCALARS else NOT_CONVERTED)
    if t is not dict and t is not list and t is not tuple:
        return _walk_general(value, counter, depth, path_ids), _leaf_out(value, t, path, safe)
    counter[0] -= 1
    if counter[0] < 0:
        # Budget spent: the fingerprint stops here, the conversion need not.
        return (_TAG_TRUNCATED, id(value)), _convert(value, depth, path, safe)
    vid = id(value)
    if depth >= _MAX_DEPTH or vid in path_ids:
        return (_TAG_ID, vid), NOT_CONVERTED
    inner = path_ids + (vid,)
    child_depth = depth + 1
    fps: List[Hashable] = []
    append = fps.append
    ok = True
    if t is dict:
        out_d: dict = {}
        for k, v in value.items():
            tk = type(k)
            if tk in _EXACT_SCALARS:
                counter[0] -= 1
                kf: Hashable = (_TAG_VALUE, tk, k) if counter[0] >= 0 else (_TAG_TRUNCATED, id(k))
            else:
                kf = _walk(k, counter, child_depth, inner)
            tv = type(v)
            if tv in _EXACT_SCALARS:
                counter[0] -= 1
                vf: Hashable = (_TAG_VALUE, tv, v) if counter[0] >= 0 else (_TAG_TRUNCATED, id(v))
                if ok:
                    if tv in _PLAIN_SCALARS:
                        out_d[k] = v
                    else:
                        ok = False
            elif ok:
                vf, o = _fused(v, counter, child_depth, inner, (path, True, k), safe)
                if o is NOT_CONVERTED:
                    ok = False
                else:
                    out_d[k] = o
            else:
                vf = _walk(v, counter, child_depth, inner)
            append((kf, vf))
        return (_TAG_DICT, tuple(fps)), (out_d if ok else NOT_CONVERTED)
    out_l: list = []
    out_append = out_l.append
    for i, v in enumerate(value):
        tv = type(v)
        if tv in _EXACT_SCALARS:
            counter[0] -= 1
            append((_TAG_VALUE, tv, v) if counter[0] >= 0 else (_TAG_TRUNCATED, id(v)))
            if ok:
                if tv in _PLAIN_SCALARS:
                    out_append(v)
                else:
                    ok = False
        elif ok:
            vf, o = _fused(v, counter, child_depth, inner, (path, False, i), safe)
            append(vf)
            if o is NOT_CONVERTED:
                ok = False
            else:
                out_append(o)
        else:
            append(_walk(v, counter, child_depth, inner))
    fp_tuple = (_TAG_LIST if t is list else _TAG_TUPLE, tuple(fps))
    return fp_tuple, (out_l if ok else NOT_CONVERTED)


def _convert(value: Any, depth: int, path: _Path, safe: List[Any]) -> Any:
    """The conversion half alone; NOT_CONVERTED when it must be left to the old pass.

    Gives up past the fingerprint's depth cap, which also ends a cycle (the
    original conversion has no cycle guard, so a cycle keeps its old outcome).
    """
    t = type(value)
    if t in _PLAIN_SCALARS:
        return value
    if t is dict:
        if depth >= _MAX_DEPTH:
            return NOT_CONVERTED
        out_d = {}
        for k, v in value.items():
            if type(v) in _PLAIN_SCALARS:
                out_d[k] = v
                continue
            o = _convert(v, depth + 1, (path, True, k), safe)
            if o is NOT_CONVERTED:
                return NOT_CONVERTED
            out_d[k] = o
        return out_d
    if t is list or t is tuple:
        if depth >= _MAX_DEPTH:
            return NOT_CONVERTED
        out_l = []
        for i, v in enumerate(value):
            if type(v) in _PLAIN_SCALARS:
                out_l.append(v)
                continue
            o = _convert(v, depth + 1, (path, False, i), safe)
            if o is NOT_CONVERTED:
                return NOT_CONVERTED
            out_l.append(o)
        return out_l
    return _leaf_out(value, t, path, safe)


def fingerprint_and_convert(
    value: Any, prefix: str, budget: int = DEFAULT_BUDGET
) -> Tuple[Hashable, bool, Optional[Tuple[List[Any], Any]]]:
    """One walk: ``(fingerprint, truncated, converted)``.

    ``fingerprint`` and ``truncated`` equal ``deep_fingerprint(value, budget)``.
    ``converted`` is ``(safe_paths, rendered)`` where ``safe_paths`` equals
    ``_collect_safe_keys(value, prefix)`` and ``rendered`` equals
    ``normalize_django_value(value)``; it is ``None`` when the value holds
    anything this walk leaves to those functions.
    """
    counter = [budget]
    safe: List[Any] = []
    fp, out = _fused(value, counter, 0, (), prefix, safe)
    return fp, counter[0] < 0, (None if out is NOT_CONVERTED else (safe, out))


def convert_for_render(value: Any, prefix: str) -> Optional[Tuple[List[Any], Any]]:
    """``(safe_paths, rendered)`` in one walk, or ``None`` (see above)."""
    safe: List[Any] = []
    out = _convert(value, 0, prefix, safe)
    return None if out is NOT_CONVERTED else (safe, out)
