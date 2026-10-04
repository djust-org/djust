"""A lazily rendered, trusted HTML chunk for the ``theme_context`` processor (#3028).

``theme_context`` used to render four theme tag bodies (``theme_head``,
``theme_panel``, ``theme_mode_toggle``, ``theme_preset_selector``) plus the
switcher on EVERY request that builds a ``RequestContext``, including JSON
views, redirects and admin pages that never print them. :class:`LazyThemeHTML`
defers that work to the first time a template reads the value.

Why not :class:`django.utils.functional.SimpleLazyObject`: it proxies
``__class__``, so every ``isinstance(value, str)`` check on the way to a
template (djust's ``_is_json_serializable``, ``_collect_safe_keys``, the
sidecar split in ``_sync_state_to_rust``) would evaluate it. This class is a
plain object whose ``__class__`` is its own, so those checks see "not a str"
and leave it unrendered; Django's own readers (``conditional_escape``,
``str()``) and the Rust renderer's sidecar read it through ``__html__`` /
``__str__``, which render once and memoise.

It is a :class:`django.utils.functional.Promise` (as a ``gettext_lazy`` string
is) so the places that must turn it into JSON do so as a plain string:
``DjangoJSONEncoder`` (``{{ theme_head|json_script:"x" }}``, ``JsonResponse``)
and djust's ``normalize_django_value`` both answer ``str(value)`` for a
``Promise``. Those paths render the chunk because they need its text; nothing
that merely carries the object does.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, cast

from django.utils.functional import Promise
from django.utils.html import conditional_escape
from django.utils.safestring import SafeData, mark_safe


class LazyThemeHTML(Promise, SafeData):
    """Trusted HTML that renders on first read and is then fixed.

    It is a :class:`~django.utils.safestring.SafeData`, so Django's
    autoescape and its ``is_safe`` filters treat it exactly like the
    ``SafeString`` it stands for. The factory is called at most once per
    object; a factory that raises renders as ``""`` (the per-tag fail-soft the
    eager processor had: one broken tag must not blank its siblings), and that
    empty result is memoised too.

    ``trust_plain_str`` says what a plain ``str`` result means. ``True`` (the
    default) marks it safe, as the eager processor did for ``theme_head`` and
    ``theme_switcher``. ``False`` keeps the eager processor's behaviour for the
    other tag bodies, which passed the tag's return value through untouched: a
    ``SafeString`` stays safe and a plain ``str`` (a downstream-shadowed tag)
    is escaped, so laziness never promotes untrusted text to trusted HTML.

    Evaluation is not locked. Two threads reading the same object at the same
    instant each run the factory and store equal strings; the factories here
    are pure functions of the request, so the only cost is the duplicate work.
    """

    __slots__ = ("_factory", "_trust_plain_str", "_value")

    def __init__(self, factory: Callable[[], Any], *, trust_plain_str: bool = True) -> None:
        self._factory: Callable[[], Any] | None = factory
        self._trust_plain_str = trust_plain_str
        self._value: str | None = None

    @property
    def evaluated(self) -> bool:
        """Whether the chunk has been rendered yet. Reading it never renders."""
        return self._value is not None

    def _render(self) -> str:
        value = self._value
        if value is None:
            factory = self._factory
            try:
                rendered = factory() if factory is not None else ""
            except Exception:  # noqa: BLE001 - fail-soft per chunk, as the eager processor was
                rendered = ""
            rendered = rendered or ""
            if self._trust_plain_str:
                value = cast(str, mark_safe(rendered))
            else:
                # ``conditional_escape`` leaves a SafeString/``__html__`` value alone.
                value = cast(str, mark_safe(conditional_escape(rendered)))
            self._value = value
            self._factory = None
        return value

    # --- the string face every reader uses -------------------------------
    def __str__(self) -> str:
        return self._render()

    def __html__(self) -> str:
        return self._render()

    def __format__(self, spec: str) -> str:
        return format(self._render(), spec)

    def __repr__(self) -> str:
        # Never renders: a debugger or a log line must not pay for the chunk.
        return f"<LazyThemeHTML evaluated={self.evaluated}>"

    # --- the value protocol a template or filter might use ---------------
    def __bool__(self) -> bool:
        return bool(self._render())

    def __len__(self) -> int:
        return len(self._render())

    def __iter__(self) -> Iterator[str]:
        return iter(self._render())

    def __contains__(self, item: str) -> bool:
        return item in self._render()

    def __getitem__(self, key: Any) -> str:
        return self._render()[key]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LazyThemeHTML):
            return self._render() == other._render()
        if isinstance(other, str):
            return self._render() == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._render())

    def __add__(self, other: str) -> str:
        return self._render() + other

    def __radd__(self, other: str) -> str:
        return other + self._render()

    def __getattr__(self, name: str) -> Any:
        # ``str`` methods (``.strip()``, ``.splitlines()``...) on the rendered
        # text. Underscore names never render the chunk: a ``copy`` / ``pickle``
        # probe, or a half-initialised instance, must not pay for it (or
        # recurse through the unset ``_value`` slot).
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._render(), name)
