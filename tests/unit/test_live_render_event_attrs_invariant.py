"""Invariant: every ``_LIVE_RENDER_EVENT_ATTRS`` entry has a client-side binding.

``{% live_render %}`` stamps ``view_id`` on embedded elements carrying any
attribute in ``_LIVE_RENDER_EVENT_ATTRS``
(``python/djust/templatetags/live_tags.py``). Membership is the framework
asserting "this directive exists and fires". Issue #2869 found three entries
(``dj-keypress``, ``dj-mouseenter``, ``dj-mouseleave``) that the client never
bound: they were stamped like working directives but installed no listener,
so a developer writing ``dj-mouseenter="highlight"`` got silence — no error,
no warning, no event. This test converts that audit finding into a
maintenance invariant: an entry with no client-side binding fails the moment
it is added, not at the next audit.

Resolution (#2869, as decided by the maintainer): ``dj-keypress`` was pruned
(DOM-deprecated in favour of ``keydown``, which the framework already ships
with a full modifier system). ``dj-mouseenter`` / ``dj-mouseleave`` were
WIRED instead of pruned — direct per-element listeners in
``09-event-binding.js`` (they do not bubble, so the delegated shape cannot
serve them) — making the stamp-list promise true. On the invariant's first
run it also caught two further never-bound entries,
``dj-viewport-enter`` / ``dj-viewport-leave`` (same v0.6.0 commit as the
cited three, zero references anywhere); there is no ``viewportenter`` DOM
event to wire them to, so they were pruned as the ``dj-keypress`` class.

How detection works
-------------------

The client reads event directives via quoted attribute-name string literals —
``element.getAttribute('dj-click')``, ``_KEYBOARD_KEY_DIRECTIVES = {
'dj-keydown': true, ... }``, and so on — across the modules in
``python/djust/static/djust/src/``. For every entry in the stamp list this
test requires at least one **quoted string literal** match of the exact name
in some ``src/*.js`` module: the character immediately after the name must be
a quote, backtick, or ``.`` (a dotted-modifier reference such as
``'dj-keydown.escape'``). A bare prose mention ("the dj-click directive") is
NOT enough, and ``dj-click`` does not match inside ``dj-click-away``.

One binding path is dynamic and cannot be seen by literal matching: the
scoped window/document directives (``dj-window-click``, ``dj-document-scroll``,
...) are composed at run time from the ``scopedPrefixes`` ×
``scopedEventTypes`` arrays in ``09-event-binding.js`` (``_scanScopedElements``).
Rather than restating that cross product here — a copy that would silently
drift — the test PARSES the two arrays out of the client source and adds
their product to the bound set. If the client renames or removes the arrays,
the extraction assertion fails loudly rather than silently weakening.

Blind spots (stated, not hidden)
--------------------------------

A name-matching check cannot prove an event *fires*. Specifically it cannot
see:

1. **A quoted mention inside a comment.** ``// TODO: wire 'dj-mouseenter'``
   satisfies the matcher while nothing binds. Minification strips comments,
   but this test scans the readable ``src/`` modules (the maintained source
   of truth), where comments survive.
2. **A dead string reference.** A name left sitting in a lookup map or a
   warning message but never dispatched to a listener passes — the inert
   class #2842 de-silenced. Only behavioural JS tests can catch that.
3. **Dynamically-constructed handler names.** Any binding built at run time
   outside the ``scopedPrefixes`` × ``scopedEventTypes`` path — e.g.
   ``'dj-' + eventType`` — is invisible. As of #2869 the only such
   construction produces ``dj-keydown``/``dj-keyup``
   (``09-event-binding.js``, ``_handleDjKeyboard``), both of which ALSO
   appear as literals (``_KEYBOARD_KEY_DIRECTIVES``), so they pass without
   special handling. A new dynamically-named directive must also keep a
   literal occurrence in its wiring module, or extend the extraction above.

So the test proves "the client names this attribute in code-shaped text" —
a strong floor against re-adding a never-bound name, not a substitute for
the behavioural suites in ``test_live_render_tag.py`` and ``tests/js/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import djust
from djust.templatetags.live_tags import _LIVE_RENDER_EVENT_ATTRS

_SRC_DIR = Path(djust.__file__).resolve().parent / "static" / "djust" / "src"
_SCOPED_MODULE = "09-event-binding.js"

# The name must be introduced by a quote and followed by a quote, backtick,
# or a dotted-modifier separator — i.e. it must read like a string literal,
# not prose. The trailing character class deliberately excludes ``-`` so
# ``dj-click`` cannot be satisfied by ``dj-click-away``.
_QUOTED_NAME_RE = re.compile(r"""['"`](?P<name>[a-z-]+)(?:\.|['"`])""")

# Extraction of the client's dynamic scoped-binding arrays (09-event-binding.js,
# _scanScopedElements): 'dj-window-'/'dj-document-' prefix x event type.
_SCOPED_PREFIXES_RE = re.compile(r"const\s+scopedPrefixes\s*=\s*\[(?P<items>[^\]]*)\]")
_SCOPED_EVENT_TYPES_RE = re.compile(r"const\s+scopedEventTypes\s*=\s*\[(?P<items>[^\]]*)\]")
_QUOTED_ITEM_RE = re.compile(r"""['"`]([^'"`]+)['"`]""")


def _client_modules() -> dict[str, str]:
    if not _SRC_DIR.is_dir():
        raise AssertionError(
            "djust client source directory not found at %s — the invariant "
            "test cannot run against a package without static/djust/src/." % _SRC_DIR
        )
    modules = sorted(_SRC_DIR.glob("*.js"))
    assert modules, "no .js modules found under %s" % _SRC_DIR
    return {path.name: path.read_text(encoding="utf-8") for path in modules}


def _quoted_literals(text: str) -> set[str]:
    """Quoted string literals in *text* that read like directive names,
    including any dotted-modifier variants (``dj-keydown`` and
    ``dj-keydown.escape`` both count for ``dj-keydown``)."""
    names = set()
    for match in _QUOTED_NAME_RE.finditer(text):
        literal = match.group("name")
        names.add(literal)
        if "." in literal:
            names.add(literal.split(".", 1)[0])
    return names


def _dynamically_bound_directives(modules: dict[str, str]) -> set[str]:
    """Derive the scoped window/document directive names from the client's
    own ``scopedPrefixes`` x ``scopedEventTypes`` arrays (#2727: derive,
    never restate). Asserts the arrays were found — a silent miss would
    weaken the invariant instead of failing."""
    source = modules.get(_SCOPED_MODULE)
    assert source is not None, (
        "%s not found under %s — the client's dynamic scoped-binding module "
        "moved; update _SCOPED_MODULE." % (_SCOPED_MODULE, _SRC_DIR)
    )
    prefixes_m = _SCOPED_PREFIXES_RE.search(source)
    types_m = _SCOPED_EVENT_TYPES_RE.search(source)
    assert prefixes_m and types_m, (
        "Could not extract scopedPrefixes/scopedEventTypes from %s — "
        "_scanScopedElements was renamed or restructured. Update the "
        "extraction regexes in this test so the dynamic binding path stays "
        "derived from the client, not restated." % _SCOPED_MODULE
    )
    prefixes = _QUOTED_ITEM_RE.findall(prefixes_m.group("items"))
    event_types = _QUOTED_ITEM_RE.findall(types_m.group("items"))
    assert prefixes and event_types, (
        "scopedPrefixes=%r scopedEventTypes=%r extracted empty from %s"
        % (prefixes, event_types, _SCOPED_MODULE)
    )
    return {prefix + evt for prefix in prefixes for evt in event_types}


def test_every_stamp_list_entry_has_a_client_binding():
    """Each ``_LIVE_RENDER_EVENT_ATTRS`` entry must be bound by the client
    (#2869): either a quoted string literal in some src module, or a member
    of the derived scoped-prefix x event-type cross product."""
    modules = _client_modules()
    bound: set[str] = set()
    for text in modules.values():
        bound |= _quoted_literals(text)
    bound |= _dynamically_bound_directives(modules)

    dead = [attr for attr in _LIVE_RENDER_EVENT_ATTRS if attr not in bound]
    assert not dead, (
        "These _LIVE_RENDER_EVENT_ATTRS entries have no client-side binding: "
        "%s. The stamp list is the framework asserting a directive exists — "
        "a name the client never binds must be removed from the list (or the "
        "directive wired) before it is stamped on user elements. See #2869." % ", ".join(dead)
    )


def test_invariant_detector_recognises_a_known_bound_directive():
    """The detector must find directives the client demonstrably binds.

    Guards the invariant itself against silent decay: if a rename or a
    quoting-style change in the client made the matcher match nothing, the
    main test above would name every entry and this test names the mechanism.
    """
    assert "dj-click" in _quoted_literals("el.getAttribute('dj-click') || ''")
    assert "dj-keydown" in _quoted_literals(
        "const _KEYBOARD_KEY_DIRECTIVES = { 'dj-keydown': true };"
    )
    # A dotted-modifier reference satisfies the base name...
    assert "dj-keydown" in _quoted_literals("warn('dj-keydown.escape not supported');")
    # ...but bare prose and a longer sibling name do not.
    assert "dj-click" not in _quoted_literals("// the dj-click directive fires on click")
    assert "dj-click" not in _quoted_literals("el.getAttribute('dj-click-away')")


def test_scoped_dynamic_path_is_derived_not_rested():
    """The dynamic-path extraction must yield the scoped directives the
    client actually ships (dj-window-*/dj-document-* family) — proving the
    derivation runs against real client source, not a hardcoded fallback."""
    modules = _client_modules()
    dynamic = _dynamically_bound_directives(modules)
    assert "dj-window-click" in dynamic
    assert "dj-document-click" in dynamic
    assert "dj-window-keydown" in dynamic
    # The product must stay inside the stamp list's scoped family — an
    # extraction gone wrong (matching some other array) would drift out.
    assert dynamic, "dynamic set unexpectedly empty"
    assert all(name.startswith(("dj-window-", "dj-document-")) for name in dynamic)
    assert dynamic <= set(_LIVE_RENDER_EVENT_ATTRS)
