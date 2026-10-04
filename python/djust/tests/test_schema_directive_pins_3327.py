"""Pins for the directive descriptions corrected in #3291 (follow-ups: #3327).

`schema.DIRECTIVES` is what `manage.py djust_ai_context` copies into CLAUDE.md,
`.cursorrules` and the Copilot instructions, so a wrong sentence there is
taught to every AI assistant that reads a project. #3291 corrected nine entries
against the shipped client; only `dj-submit` and `dj-target` were pinned
(`test_inert_api_claims.py`). Each test below pins one of the other seven, in
two halves:

* the key phrases of the corrected description (so a revert to the old wording
  fails), and
* the client (or parser) behaviour the description cites (so the client
  changing under the sentence fails, instead of the two drifting apart again).

The client halves match code structure with whitespace-tolerant regexes over
comment-stripped source, not literal strings, so a harmless reformat does not
fail them with a misleading message.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.schema import DIRECTIVES

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "python" / "djust" / "static" / "djust" / "src"
PARSER = ROOT / "crates" / "djust_vdom" / "src" / "parser.rs"
GUIDES = ROOT / "docs" / "website" / "guides"


def _entry(name: str) -> dict:
    matches = [d for d in DIRECTIVES if d.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name} entry, got {len(matches)}"
    return matches[0]


def _text(name: str) -> str:
    """The description, lower-cased and whitespace-collapsed."""
    return " ".join(str(_entry(name)["description"]).split()).lower()


def _js(filename: str) -> str:
    """A client source file with whole-line comments dropped."""
    lines = (SRC / filename).read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith(("//", "*", "/*")))


def _between(text: str, start: str, end: str) -> str:
    """The slice from `start` up to the next `end`, so a pin reads one function."""
    i = text.index(start)
    return text[i : text.index(end, i + len(start))]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.S) is not None


def _assert_phrases(name: str, phrases: list[str]) -> None:
    text = _text(name)
    missing = [p for p in phrases if p.lower() not in text]
    assert not missing, (
        f"{name}'s schema.py description lost corrected wording {missing} (#3291, #3327). "
        f"Description now: {text!r}"
    )


def test_dj_loading_is_documented_as_show_only_shorthand() -> None:
    _assert_phrases(
        "dj-loading",
        ["dj-loading.for", "dj-loading.show", "hidden", "does not disable anything"],
    )

    loading = _js("10-loading-states.js")
    register = _between(loading, "register(element, eventName) {", "\n    },")
    shorthand = _between(register, "hasAttribute('dj-loading')", "Array.from(element.attributes)")
    assert _has(r"modifiers\.push\(\{\s*type:\s*'show'\s*\}\)", shorthand), (
        "the dj-loading shorthand no longer registers a .show modifier; re-check its description"
    )
    assert "'disable'" not in shorthand, (
        "the dj-loading shorthand now touches `disable`; its description says it does not disable"
    )
    assert _has(
        r"getAttribute\('dj-loading'\)\s*\|\|\s*\w+\.getAttribute\('dj-loading\.for'\)", loading
    ), "dj-loading / dj-loading.for no longer share the event-name read; re-check both entries"


def test_dj_loading_for_is_documented_as_scoped_and_selector_only() -> None:
    _assert_phrases(
        "dj-loading.for",
        ["same liveview or component", "only picks the event", "on its own it does nothing"],
    )
    assert "anywhere on the page" not in _text("dj-loading.for")

    loading = _js("10-loading-states.js")
    scope = _between(loading, "scopeFor(element) {", "\n    },")
    assert _has(
        r"closest\(\s*'\[dj-view\]\[data-djust-embedded\],\s*\[data-component-id\]'", scope
    ), (
        "scopeFor no longer scopes to the owning view/component; the same-LiveView-or-component "
        "claim in dj-loading.for (and guides/loading-states.md) needs re-checking"
    )
    # The scope is enforced where loading starts and stops, not just defined.
    enforced = re.findall(r"this\.scopeFor\(\w+\)\s*===\s*owner", loading)
    assert len(enforced) >= 2, (
        "dj-loading.for elements are no longer matched by owner scope when an event starts/stops"
    )
    assert "[dj-loading\\\\.for]" in loading, "dj-loading.for is no longer scanned for"


def test_loading_states_guide_does_not_claim_unscoped_dj_loading_for() -> None:
    """The guide said 'regardless of where that element is in the DOM' (#3327 item 4)."""
    guide = " ".join((GUIDES / "loading-states.md").read_text(encoding="utf-8").split())
    assert "regardless of where that element is in the DOM" not in guide
    assert "same LiveView or component" in guide, (
        "guides/loading-states.md must state the scope of dj-loading.for, which scopeFor enforces"
    )


def test_dj_document_scroll_and_resize_are_documented_as_never_firing() -> None:
    _assert_phrases(
        "dj-document-*",
        ["never fire", "dj-window-scroll", "dj-window-resize", "keydown/keyup/click"],
    )
    assert "scroll/resize" not in _text("dj-document-*").replace("dj-window-scroll", "")

    binding = _js("09-event-binding.js")
    assert _has(
        r"target\s*===\s*document\s*&&\s*\(\s*evtType\s*===\s*'scroll'\s*\|\|\s*"
        r"evtType\s*===\s*'resize'\s*\)\s*\)\s*continue",
        binding,
    ), (
        "document-scoped scroll/resize listeners are now installed; "
        "dj-document-*'s description says they never fire"
    )
    assert _has(r"prefix:\s*'dj-window-',\s*target:\s*window", binding), (
        "dj-window-* is no longer window-scoped; the description sends agents there"
    )


def test_dj_prefetch_names_both_layers_and_the_client_honours_false_in_both() -> None:
    assert "via the service worker when the user hovers" not in _text("dj-prefetch")
    _assert_phrases(
        "dj-prefetch",
        [
            "65 ms",
            "touchstart",
            "save-data",
            "'false' opts a link out of both prefetch layers",
            "service-worker hover prefetch",
            "data-no-prefetch",
        ],
    )

    prefetch = _js("22-prefetch.js")
    sw = _between(prefetch, "function _shouldPrefetch(link) {", "\n    }\n")
    intent = _between(prefetch, "function _shouldIntentPrefetch(link) {", "\n    }\n")
    # Either `getAttribute('dj-prefetch') === 'false'` inline, or the value read
    # into a local first (the intent layer's shape); both compare to 'false'.
    reads_attr = r"getAttribute\('dj-prefetch'\)"
    compares_false = r"(===|!==)\s*'false'"
    assert _has(reads_attr, sw) and _has(compares_false, sw), (
        'the service-worker hover prefetch no longer honours dj-prefetch="false" (#3327 item 6); '
        "dj-prefetch's description says false opts out of both layers"
    )
    assert _has(reads_attr, intent) and _has(compares_false, intent), (
        'the intent prefetch no longer honours dj-prefetch="false"'
    )
    assert "data-no-prefetch" in sw, "the data-no-prefetch opt-out left the service-worker path"
    assert "saveData" in sw and "saveData" in intent
    assert "HOVER_DEBOUNCE_MS = 65" in prefetch and "touchstart" in prefetch


def test_dj_trigger_action_example_carries_the_id_the_text_names() -> None:
    _assert_phrases(
        "dj-trigger-action",
        ["self.trigger_submit('#form-id')", "a form without this attribute is refused"],
    )
    example = str(_entry("dj-trigger-action")["example"])
    assert _has(r"<form\b[^>]*\bid=\"[^\"]+\"", example), (
        "dj-trigger-action's example is the paste an agent copies; its <form> needs the id "
        "that trigger_submit('#...') selects, or the example cannot work as written"
    )

    polish = _js("34-form-polish.js")
    handler = _between(polish, "const handleTriggerAction = function", "form.submit();")
    # The refusal must be the last thing before the native submit: the guard's
    # block ends in `return;` with nothing between it and `form.submit()`.
    assert _has(
        r"!\s*form\.hasAttribute\('dj-trigger-action'\)\s*\)\s*\{.*?\breturn;\s*\}\s*$", handler
    ), (
        "the client no longer refuses a form without dj-trigger-action before submitting it; "
        "re-check the description (and the SECURITY note in 34-form-polish.js)"
    )
    assert (ROOT / "python" / "djust" / "mixins" / "push_events.py").read_text(
        encoding="utf-8"
    ).count("def trigger_submit(") == 1


def test_dj_patch_reload_is_read_only_by_dj_patch() -> None:
    _assert_phrases(
        "dj-patch-reload",
        ["full page navigation", "dj-patch", "dj-navigate ignores it"],
    )

    readers = {
        path.name: re.findall(r"dj-patch-reload", _js(path.name))
        for path in sorted(SRC.glob("*.js"))
        if "dj-patch-reload" in _js(path.name)
    }
    assert set(readers) == {"18-navigation.js"}, (
        f"dj-patch-reload is read in more files than the dj-patch path: {sorted(readers)}"
    )
    navigation = _js("18-navigation.js")
    assert len(re.findall(r"hasAttribute\('dj-patch-reload'\)", navigation)) == 1
    execute_patch = _between(navigation, "function _executePatch(", "\n    }\n")
    assert "hasAttribute('dj-patch-reload')" in execute_patch, (
        "dj-patch-reload moved out of the dj-patch handler; dj-navigate may now read it, "
        "and the description says it does not"
    )


def test_dj_key_description_matches_the_parser_and_the_differ() -> None:
    _assert_phrases(
        "dj-key",
        [
            "positional diffing",
            "rows are paired by position",
            "rewritten in place",
            "stay with the position instead of following the item",
            "data-key",
            "unique among siblings",
            "dje-051",
            "crates/djust_vdom/src/parser.rs",
        ],
    )
    assert "destroying and rebuilding" not in _text("dj-key")

    parser = PARSER.read_text(encoding="utf-8")
    assert _has(
        r'attr_name_lower\s*==\s*"dj-key"\s*\|\|\s*attr_name_lower\s*==\s*"data-key"', parser
    ), "the parser no longer reads dj-key / data-key into VNode.key; re-check dj-key's description"
    assert _has(r"key\s*=\s*Some\(attr_value", parser)

    differ = (ROOT / "crates" / "djust_vdom" / "src" / "diff.rs").read_text(encoding="utf-8")
    assert "DJE-051" in differ, "the duplicate-key warning code left the differ"
    assert "DJE-051" in (GUIDES / "error-codes.md").read_text(encoding="utf-8")


#: `djust_ai_context._section_directives` emits every category in `DIRECTIVES`
#: (#3353; it used to skip performance, animation and recovery, so the corrected
#: dj-prefetch / dj-key text never reached the generated files). Phrases from
#: those categories are pinned here beside the others, in the three generated
#: formats.
EMITTED_PHRASES = (
    "It does not disable anything.",
    "within the same LiveView or component",
    "parsed but never fire",
    "a form without this attribute is refused",
    "dj-navigate ignores it",
    '<form id="checkout-form"',
    "INERT: dj-target is dropped before the event is sent",
    # performance (omitted from the generated files before #3353)
    "65 ms",
    "'false' opts a link out of both prefetch layers",
    "positional diffing",
    "stay with the position instead of following the item",
)

#: The pre-#3291 / pre-#3327 sentences, each of which taught something false.
STALE_PHRASES = (
    "via the service worker when the user hovers",
    "destroying and rebuilding",
    "e.target.reset()",
    "Scope the server re-render to a specific element",
    "in flight anywhere on the page",
    "equivalent to dj-loading.disable",
    "dj-patch/dj-navigate link",
    "when the server pushes a trigger-action",
)


@pytest.mark.parametrize("fmt", ["claude", "cursor", "copilot"])
def test_generated_ai_context_carries_the_corrected_wording_and_none_of_the_old(fmt: str) -> None:
    """What reaches CLAUDE.md / .cursorrules / copilot, not just the schema dict."""
    from djust.management.commands.djust_ai_context import _generate_content
    from djust.schema import get_framework_schema

    content = " ".join(_generate_content(get_framework_schema(), {}, fmt).split())

    for phrase in EMITTED_PHRASES:
        assert phrase in content, f"{fmt} context lost corrected wording: {phrase!r}"
    for stale in STALE_PHRASES:
        assert stale not in content, f"{fmt} context still teaches: {stale!r}"
