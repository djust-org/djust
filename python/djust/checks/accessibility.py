"""djust system checks — accessibility checks (Y0xx) — ARIA/WCAG template scanning.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import logging
import os
import re
from bisect import bisect_right
from typing import Any, Iterator, NamedTuple

from django.core.checks import CheckMessage, register

from djust.checks.utils import (
    DjustWarning,
    _is_check_suppressed,
    _iter_template_files,
    _get_template_dirs,
    _blank_template_comments,
    _strip_verbatim_blocks,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Accessibility (Y0xx) scanners
# ---------------------------------------------------------------------------
#
# Y001 — interactive element (<button>/<a>) with no accessible name.
# Y002 — <img> tag missing an `alt` attribute (WCAG 1.1.1, A-level).
# Y003 — form control (<input>/<select>/<textarea>) with no associated
#        label (WCAG 1.3.1 / 3.3.2, A-level).
# Y004 — positive tabindex (WCAG 2.4.3, Focus Order anti-pattern).
# Y005 — `role="img"` element or inline <svg> with no accessible name
#        (WCAG 1.1.1, A-level; #3685).
# Y006 — legend item that is only a colour swatch, no text (WCAG 1.4.1,
#        A-level; #3685).
#
# All are deliberately low-ambiguity a11y defects so the regex heuristics
# carry near-zero false positives (see the #1060 dogfood discipline note
# in the Stage-4 plan). The category is extensible — Y007+ are
# single-function-body additions in a follow-up.

# Y001 — captures the OPENING tag (group "open"), the tag name (group
# "tag"), and the inner content up to the matching close tag (group
# "inner"). Restricted to <button> and <a>; <a> only counts as
# interactive when it carries an href (a bare <a> is an anchor target,
# not a control). The follow-up heuristic in _content_is_icon_only()
# decides whether the inner content is icon-only.
_INTERACTIVE_EL_RE = re.compile(
    r"<(?P<tag>button|a)\b(?P<open>[^>]*)>(?P<inner>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
# An accessible-name attribute present on the opening tag silences Y001.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-aria-label='x'` is NOT mistaken for the element's real
# `aria-label` and used to wrongly silence a genuine Y001.
_ACCESSIBLE_NAME_ATTR_RE = re.compile(
    r"""(?<![\w-])(aria-label|aria-labelledby|title)\s*=\s*["'][^"']*["']""",
    re.IGNORECASE,
)
# href presence on an <a> opening tag (interactive only when linked).
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-href='/x'` is NOT mistaken for the anchor's real `href`.
_HREF_ATTR_RE = re.compile(r"""(?<![\w-])href\s*=\s*["'][^"']*["']""", re.IGNORECASE)
# "Icon-only" = the inner content is composed exclusively of HTML
# entities, <svg>...</svg>, self-closing tags (<i .../>, <img .../>),
# <i>...</i> / <span>...</span> wrappers whose own content is
# icon-only, Django template comments, and whitespace — i.e. no
# human-readable text and no {{ variable }} interpolation that could
# resolve to a label at render time.
_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);")
_SVG_BLOCK_RE = re.compile(r"<svg\b.*?</svg>", re.IGNORECASE | re.DOTALL)
_SELF_CLOSING_TAG_RE = re.compile(r"<[a-zA-Z][^>]*/\s*>")
_ICON_WRAPPER_RE = re.compile(
    r"<(?P<w>i|span|em)\b[^>]*>(?P<wi>.*?)</(?P=w)>", re.IGNORECASE | re.DOTALL
)

# Y002 — <img> tag missing an `alt` attribute. `alt=""` is the WCAG-
# correct way to mark a decorative image, so the regex only flags an
# <img> with NO `alt` token at all. {% ... %} / {{ ... }} dynamic
# attribute injection is treated as "alt may be present" (no flag).
_IMG_TAG_RE = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE | re.DOTALL)
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-alt='x'` is NOT mistaken for the image's real `alt` attribute.
_IMG_HAS_ALT_RE = re.compile(r"""(?<![\w-])alt\s*=""", re.IGNORECASE)
_IMG_DYNAMIC_ATTRS_RE = re.compile(r"\{[%{].*?[%}]\}", re.DOTALL)

# Y003 — form control (<input>/<select>/<textarea>) with no associated
# accessible name (WCAG 1.3.1 / 3.3.2, Level A). Matches the OPENING tag
# only (group "tag" = element name, group "open" = attribute text). For
# <input>, `type` values in {hidden, submit, button, reset, image} are
# skipped — those are not user-named text controls (submit/button/reset
# get their name from `value`, image inputs from `alt`).
_FORM_CONTROL_RE = re.compile(
    r"<(?P<tag>input|select|textarea)\b(?P<open>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
# <input type="..."> extraction — used to skip non-text-control types.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so `data-type='hidden'` is
# NOT mistaken for the control's real `type` attribute.
_INPUT_TYPE_RE = re.compile(r"""(?<![\w-])type\s*=\s*["']?\s*([a-zA-Z]+)""", re.IGNORECASE)
# <input> types that are NOT user-named text controls (no Y003 flag).
_Y003_SKIPPED_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})
# An `id="X"` attribute on a form control — pairs with a <label for="X">.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-id='X'` is NOT mistaken for the control's real `id` and wrongly
# paired with an unrelated <label for='X'>.
_CONTROL_ID_RE = re.compile(r"""(?<![\w-])id\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# A <label for="X"> attribute (the `for` value, file-scoped). The set of
# all `for` values silences any control whose `id` is in the set.
_LABEL_FOR_RE = re.compile(
    r"""<label\b[^>]*?\bfor\s*=\s*["']([^"']+)["']""", re.IGNORECASE | re.DOTALL
)
# A <label>...</label> span — a control appearing inside one is wrapped
# (its accessible name comes from the label text).
_LABEL_BLOCK_RE = re.compile(r"<label\b[^>]*>.*?</label>", re.IGNORECASE | re.DOTALL)
# {% ... %} / {{ ... }} dynamic attribute injection on a control's
# opening tag — the id / aria-* may be injected at render time (no flag).
_CONTROL_DYNAMIC_ATTRS_RE = re.compile(r"\{[%{].*?[%}]\}", re.DOTALL)

# Y004 — positive tabindex (WCAG 2.4.3, Focus Order anti-pattern). Only
# a value matching `[1-9]\d*` (a positive integer) is flagged; `0`, `-1`,
# and `{{ }}`-interpolated values do not match the body and are silent.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a JS-driven custom
# attribute like `data-tabindex='5'` is NOT flagged as a Y004 defect.
_POSITIVE_TABINDEX_RE = re.compile(
    r"""(?<![\w-])tabindex\s*=\s*["']\s*([1-9]\d*)\s*["']""", re.IGNORECASE
)

# Y005 — a `role="img"` element or an inline <svg> with no accessible name
# (WCAG 1.1.1, Level A; #3685). `<img>` is skipped — Y002 owns it (its
# `alt` is the name).
_ROLE_IMG_RE = re.compile(r"""(?<![\w-])role\s*=\s*["']img["']""", re.IGNORECASE)
_DECORATIVE_ROLE_RE = re.compile(
    r"""(?<![\w-])role\s*=\s*["'](?:presentation|none)["']""", re.IGNORECASE
)
_ARIA_HIDDEN_TRUE_RE = re.compile(r"""(?<![\w-])aria-hidden\s*=\s*["']true["']""", re.IGNORECASE)
# Unlike Y001's ``_ACCESSIBLE_NAME_ATTR_RE`` the value must contain a
# non-blank character: an empty ``aria-label=""`` is the lazy fix this rule
# exists to catch, not a name.
_NONBLANK_NAME_ATTR_RE = re.compile(
    r"""(?<![\w-])(?:aria-label|aria-labelledby|title)\s*=\s*(?:"[^"]*\S[^"]*"|'[^']*\S[^']*')""",
    re.IGNORECASE,
)
# A non-empty <title> child names an <svg>.
_SVG_TITLE_CHILD_RE = re.compile(r"<title\b[^>]*>[^<]*\S[^<]*</title\s*>", re.IGNORECASE)
# Sprite sheets / defs-only svgs are not rendered, so they need no name.
_HIDDEN_STYLE_RE = re.compile(
    r"""(?<![\w-])style\s*=\s*["'][^"']*display\s*:\s*none""", re.IGNORECASE
)
_HIDDEN_ATTR_RE = re.compile(r"""(?<![\w-])hidden(?![\w-])""", re.IGNORECASE)
_ZERO_SIZE_RE = re.compile(r"""(?<![\w-])(?:width|height)\s*=\s*["']0(?:px)?["']""", re.IGNORECASE)
_SYMBOL_RE = re.compile(r"<symbol\b", re.IGNORECASE)

# Y006 — a legend item that is nothing but a colour swatch (WCAG 1.4.1;
# #3685). A legend is an element whose class has a token containing
# "legend" (``legend``, ``chart-legend``, ``legend__list``).
_CLASS_ATTR_RE = re.compile(r"""(?<![\w-])class\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^<>]*>")

# Tag tokenizing for Y005/Y006. A regex such as ``<svg[^>]*>`` rescans to the
# end of the file from every unterminated ``<svg``, which is quadratic on
# malformed markup; this is one forward pass that stops a tag at the next ``<``.
_TAG_START_RE = re.compile(r"<([a-zA-Z][\w-]*+)(?=[\s/>])")
_CLOSE_TAG_RE = re.compile(r"</([a-zA-Z][\w-]*+)\s*>")


class _Tag(NamedTuple):
    start: int  # offset of "<"
    name: str  # lower-cased
    attrs: str  # text between the name and ">"
    end: int  # offset just past ">"

    @property
    def self_closing(self) -> bool:
        return self.attrs.rstrip().endswith("/")


def _iter_open_tags(source: str) -> Iterator[_Tag]:
    """Tokenize opening tags in one forward pass; a quoted ``>`` does not end one."""
    cursor = 0
    while match := _TAG_START_RE.search(source, cursor):
        pos = match.end()
        quote = None
        while pos < len(source):
            char = source[pos]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "<":
                break
            elif char == ">":
                yield _Tag(
                    match.start(), match.group(1).lower(), source[match.end() : pos], pos + 1
                )
                pos += 1
                break
            pos += 1
        cursor = pos


class _TagIndex:
    """Opening tags of a source plus, per tag name, each tag's matching close."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.opens = list(_iter_open_tags(source))
        self._closes: dict[str, list[tuple[int, int]]] = {}
        for match in _CLOSE_TAG_RE.finditer(source):
            self._closes.setdefault(match.group(1).lower(), []).append(match.span())
        self._pairs: dict[str, dict[int, tuple[int, int]]] = {}

    def pairs(self, name: str) -> dict[int, tuple[int, int]]:
        """Map the start of each matched ``<name>`` to its close ``(start, end)``.

        Same-name nesting is balanced with a stack; an unclosed element has no
        entry. Linear in the tags of that name.
        """
        if name not in self._pairs:
            result: dict[int, tuple[int, int]] = {}
            stack: list[int] = []
            closes = self._closes.get(name, [])
            j = 0
            for tag in self.opens:
                if tag.name != name or tag.self_closing:
                    continue
                while j < len(closes) and closes[j][0] < tag.start:
                    if stack:
                        result[stack.pop()] = closes[j]
                    j += 1
                stack.append(tag.start)
            for close in closes[j:]:
                if stack:
                    result[stack.pop()] = close
            self._pairs[name] = result
        return self._pairs[name]


def _merge_spans(spans: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """Merge overlapping spans; return parallel sorted ``(starts, ends)`` lists."""
    starts: list[int] = []
    ends: list[int] = []
    for start, end in sorted(spans):
        if ends and start < ends[-1]:
            ends[-1] = max(ends[-1], end)
        else:
            starts.append(start)
            ends.append(end)
    return starts, ends


def _in_spans(starts: list[int], ends: list[int], offset: int) -> bool:
    i = bisect_right(starts, offset) - 1
    return i >= 0 and offset < ends[i]


def _blank_script_and_style_bodies(source: str) -> str:
    """Blank <script>/<style> bodies, keeping newlines so line numbers hold.

    JS template literals and CSS are not rendered markup: an <svg> inside a
    script string belongs to a control the script builds and names itself.
    Linear: every ``find`` resumes from where the last one stopped.
    """
    lower = source.lower()
    parts: list[str] = []
    pos = 0  # source up to here is already emitted
    cursor = 0  # where the next opener is searched for
    nxt = {"script": -2, "style": -2}
    while True:
        for name in nxt:
            if nxt[name] != -1 and nxt[name] < cursor:
                nxt[name] = lower.find("<" + name, cursor)
        candidates = [(at, name) for name, at in nxt.items() if at != -1]
        if not candidates:
            break
        at, name = min(candidates)
        after = at + 1 + len(name)
        boundary = lower[after : after + 1]
        if boundary and boundary not in " \t\r\n/>":  # <scripty>, not <script>
            cursor = after
            continue
        open_end = lower.find(">", after)
        if open_end == -1:
            break
        close = lower.find("</" + name, open_end)
        stop = len(source) if close == -1 else close
        parts.append(source[pos : open_end + 1])
        parts.append(re.sub(r"[^\n]", " ", source[open_end + 1 : stop]))
        pos = cursor = stop
        if close == -1:
            break
    parts.append(source[pos:])
    return "".join(parts)


def _find_unnamed_images(source: str) -> list[tuple[int, str]]:
    """Return ``(offset, label)`` for each Y005 candidate in *source*.

    *label* is ``'role="img"'`` or ``"<svg>"`` (an ``<svg role="img">`` is
    reported once, as an svg).
    """
    source = _blank_script_and_style_bodies(source)
    index = _TagIndex(source)
    # A control owns the name of an icon inside it (Y001 covers an unnamed
    # control), so an svg within a <button> / <a href> is not judged here.
    control_spans = [(start, close[1]) for start, close in index.pairs("button").items()]
    anchors = index.pairs("a")
    control_spans += [
        (tag.start, anchors[tag.start][1])
        for tag in index.opens
        if tag.name == "a" and tag.start in anchors and _HREF_ATTR_RE.search(tag.attrs)
    ]
    control_starts, control_ends = _merge_spans(control_spans)
    svg_pairs = index.pairs("svg")
    outer_end = 0  # end of the outermost svg being read; later svgs inside it are nested
    found: list[tuple[int, str]] = []
    for tag in index.opens:
        is_svg = tag.name == "svg"
        if tag.name == "img" or not (is_svg or _ROLE_IMG_RE.search(tag.attrs)):
            continue
        if is_svg:
            # A nested svg is part of its outer svg's graphic: judge only the outer.
            if tag.start < outer_end:
                continue
            pair = svg_pairs.get(tag.start)
            inner_end = outer_end = tag.end
            if not tag.self_closing:
                inner_end = pair[0] if pair else len(source)
                outer_end = pair[1] if pair else len(source)
            if _in_spans(control_starts, control_ends, tag.start):
                continue
            if _DECORATIVE_ROLE_RE.search(tag.attrs):
                continue
            inner = source[tag.end : inner_end]
            if _SYMBOL_RE.search(inner):
                continue
            if (
                _HIDDEN_STYLE_RE.search(tag.attrs)
                or _HIDDEN_ATTR_RE.search(tag.attrs)
                or _ZERO_SIZE_RE.search(tag.attrs)
            ):
                continue
            if _SVG_TITLE_CHILD_RE.search(inner):
                continue
        if _ARIA_HIDDEN_TRUE_RE.search(tag.attrs) or _NONBLANK_NAME_ATTR_RE.search(tag.attrs):
            continue
        # {% ... %} / {{ ... }} on the opening tag may inject the name.
        if _CONTROL_DYNAMIC_ATTRS_RE.search(tag.attrs):
            continue
        found.append((tag.start, "<svg>" if is_svg else 'role="img"'))
    return found


def _find_colour_only_legend_items(source: str) -> list[int]:
    """Return the offset of each Y006 candidate ``<li>`` in *source*."""
    source = _blank_script_and_style_bodies(source)
    index = _TagIndex(source)
    legends: list[tuple[int, int]] = []
    outer_end = 0
    for tag in index.opens:
        if tag.name == "legend" or tag.start < outer_end:  # form <legend>; nested legend
            continue
        classes = _CLASS_ATTR_RE.search(tag.attrs)
        if not classes or "legend" not in classes.group(1).lower():
            continue
        pair = index.pairs(tag.name).get(tag.start)
        end = tag.end if tag.self_closing else (pair[1] if pair else len(source))
        legends.append((tag.start, end))
        outer_end = end
    if not legends:
        return []
    legend_starts, legend_ends = _merge_spans(legends)
    li_pairs = index.pairs("li")
    found = []
    for tag in index.opens:
        close = li_pairs.get(tag.start) if tag.name == "li" else None
        if close is None or not _in_spans(legend_starts, legend_ends, tag.start):
            continue
        inner = source[tag.end : close[0]]
        # {{ }} / {% %} may render the label; a name attribute on the item or
        # a descendant names it.
        if "{{" in inner or "{%" in inner or _CONTROL_DYNAMIC_ATTRS_RE.search(tag.attrs):
            continue
        if _NONBLANK_NAME_ATTR_RE.search(tag.attrs + inner):
            continue
        text = _HTML_ENTITY_RE.sub(" ", _ANY_TAG_RE.sub(" ", inner))
        if text.strip():
            continue
        found.append(tag.start)
    return found


def _content_is_icon_only(inner: str) -> bool:
    """Return True if *inner* has no human-readable accessible text.

    Used by Y001 to decide whether a <button>/<a> needs an explicit
    accessible-name attribute. Returns True only when the inner content
    is exclusively HTML entities, <svg> blocks, self-closing tags,
    icon-wrapper elements (<i>/<span>/<em>) that are themselves
    icon-only, template comments, and whitespace.

    A {{ variable }} interpolation is conservatively treated as
    "may resolve to a label" → returns False (no flag), keeping the
    false-positive rate near zero per the #1060 dogfood discipline.
    """
    stripped = inner
    # Template comments carry no rendered content.
    stripped = _blank_template_comments(stripped)
    # A {{ ... }} or {% ... %} could render visible text — bail out
    # (treat as "has a name", no flag).
    if "{{" in stripped or "{%" in stripped:
        return False
    # Recursively unwrap icon-wrapper elements so <span><svg/></span>
    # is still recognised as icon-only.
    prev = None
    while prev != stripped:
        prev = stripped
        stripped = _ICON_WRAPPER_RE.sub(lambda m: " " + m.group("wi") + " ", stripped)
    stripped = _SVG_BLOCK_RE.sub(" ", stripped)
    stripped = _SELF_CLOSING_TAG_RE.sub(" ", stripped)
    stripped = _HTML_ENTITY_RE.sub(" ", stripped)
    # Whatever remains must be whitespace only for the element to be
    # "icon-only" (no accessible name).
    return stripped.strip() == ""


@register("djust")
def check_accessibility(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Regex-scan template files for ARIA/WCAG accessibility issues.

    Checks:

    - **Y001** — an interactive ``<button>`` / ``<a href>`` whose visible
      content is icon-only (HTML entity, ``<svg>``, ``<i>``/``<span>``
      icon wrapper) and which has no ``aria-label`` / ``aria-labelledby``
      / ``title``. Screen-reader users hear nothing for such a control.
    - **Y002** — an ``<img>`` tag with no ``alt`` attribute (WCAG 1.1.1,
      Level A). ``alt=""`` (decorative image) is correct and not flagged.
    - **Y003** — a form control (``<input>`` / ``<select>`` /
      ``<textarea>``) with no associated label (WCAG 1.3.1 / 3.3.2,
      Level A). Satisfied by a ``<label for>`` pairing the control's
      ``id``, a wrapping ``<label>`` element, ``aria-label``, or
      ``aria-labelledby``. ``<input>`` types ``hidden`` / ``submit`` /
      ``button`` / ``reset`` / ``image`` are not flagged.
    - **Y004** — an element with a positive ``tabindex`` (WCAG 2.4.3,
      Focus Order). ``tabindex="0"`` and ``tabindex="-1"`` are valid and
      not flagged.
    - **Y005** — a ``role="img"`` element or an inline ``<svg>`` with no
      accessible name (WCAG 1.1.1, Level A): no non-blank ``aria-label`` /
      ``aria-labelledby`` / ``title`` attribute and, for an svg, no
      ``<title>`` child. ``aria-hidden="true"``, ``role="presentation"``,
      an svg inside a ``<button>`` / ``<a href>``, a nested svg and a
      hidden sprite sheet are not flagged.
    - **Y006** — a ``<li>`` inside a legend (an element whose ``class``
      has a token containing ``legend``) with no text and no name
      attribute, so the series differ only by colour (WCAG 1.4.1).

    All emit :class:`DjustWarning` (not error) so a stray false positive
    never fails ``manage.py check``; all are suppressible via
    ``DJUST_CONFIG['suppress_checks']``.
    """
    errors: list[CheckMessage] = []

    y001_suppressed = _is_check_suppressed("djust.Y001")
    y002_suppressed = _is_check_suppressed("djust.Y002")
    y003_suppressed = _is_check_suppressed("djust.Y003")
    y004_suppressed = _is_check_suppressed("djust.Y004")
    y005_suppressed = _is_check_suppressed("djust.Y005")
    y006_suppressed = _is_check_suppressed("djust.Y006")
    if all(
        (
            y001_suppressed,
            y002_suppressed,
            y003_suppressed,
            y004_suppressed,
            y005_suppressed,
            y006_suppressed,
        )
    ):
        return errors

    tpl_dirs = _get_template_dirs()
    if not tpl_dirs:
        return errors

    for filepath in _iter_template_files(tpl_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)
        # Docs / marketing pages routinely show literal HTML examples
        # inside {% verbatim %} regions — blank those out so they don't
        # false-positive (mirrors the A070 / #1004 fix).
        scan_source = _blank_template_comments(_strip_verbatim_blocks(content))

        # Y001 — interactive element missing an accessible name.
        if not y001_suppressed:
            for match in _INTERACTIVE_EL_RE.finditer(scan_source):
                open_attrs = match.group("open")
                tag = match.group("tag").lower()
                # <a> is only an interactive control when it has an href.
                if tag == "a" and not _HREF_ATTR_RE.search(open_attrs):
                    continue
                # Explicit accessible-name attribute → fine.
                if _ACCESSIBLE_NAME_ATTR_RE.search(open_attrs):
                    continue
                if not _content_is_icon_only(match.group("inner")):
                    continue
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- <%s> has no accessible name (icon-only content "
                        "and no aria-label)." % (relpath, lineno, tag),
                        hint=(
                            "Screen-reader users hear nothing for an icon-only "
                            'control. Add aria-label="..." (or aria-labelledby / '
                            "title) to the <%s> element so its purpose is "
                            "announced." % tag
                        ),
                        id="djust.Y001",
                        fix_hint=(
                            'Add an aria-label="..." attribute to the <%s> '
                            "element at line %d in `%s`." % (tag, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y002 — <img> missing an alt attribute.
        if not y002_suppressed:
            for match in _IMG_TAG_RE.finditer(scan_source):
                tag_text = match.group(0)
                if _IMG_HAS_ALT_RE.search(tag_text):
                    continue
                # Dynamic attribute injection ({% ... %} / {{ ... }})
                # may carry the alt — don't flag.
                if _IMG_DYNAMIC_ATTRS_RE.search(tag_text):
                    continue
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- <img> tag is missing an 'alt' attribute "
                        "(WCAG 1.1.1)." % (relpath, lineno),
                        hint=(
                            "Every <img> needs an alt attribute. Use "
                            'alt="describe the image" for informative images, '
                            'or alt="" for purely decorative ones.'
                        ),
                        id="djust.Y002",
                        fix_hint=(
                            'Add an alt="..." attribute to the <img> tag at '
                            'line %d in `%s` (use alt="" if decorative).' % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y003 — form control with no associated label.
        if not y003_suppressed:
            # File-scoped set of every <label for="X"> value — an <input
            # id="X"> whose id is in this set is considered named.
            label_for_ids = set(_LABEL_FOR_RE.findall(scan_source))
            # Spans of every <label>...</label> block — a control whose
            # opening tag starts inside one is wrapped (named by it).
            label_spans = [(m.start(), m.end()) for m in _LABEL_BLOCK_RE.finditer(scan_source)]
            for match in _FORM_CONTROL_RE.finditer(scan_source):
                open_attrs = match.group("open")
                tag = match.group("tag").lower()
                # <input> types that aren't user-named text controls.
                if tag == "input":
                    type_match = _INPUT_TYPE_RE.search(open_attrs)
                    input_type = type_match.group(1).lower() if type_match else "text"
                    if input_type in _Y003_SKIPPED_INPUT_TYPES:
                        continue
                # Dynamic attribute injection ({% ... %} / {{ ... }})
                # may carry id / aria-* — conservatively don't flag.
                if _CONTROL_DYNAMIC_ATTRS_RE.search(open_attrs):
                    continue
                # Explicit accessible-name attribute → named.
                if _ACCESSIBLE_NAME_ATTR_RE.search(open_attrs):
                    continue
                # id paired with a same-file <label for="..."> → named.
                id_match = _CONTROL_ID_RE.search(open_attrs)
                if id_match and id_match.group(1) in label_for_ids:
                    continue
                # Wrapped by a <label>...</label> element → named.
                if any(start <= match.start() < end for start, end in label_spans):
                    continue
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- <%s> form control has no associated label "
                        "(WCAG 1.3.1)." % (relpath, lineno, tag),
                        hint=(
                            "Assistive tech announces nothing meaningful for a "
                            "form control with no accessible name. Associate a "
                            'label via <label for="...">, wrap the control in a '
                            "<label>, or add aria-label / aria-labelledby. "
                            "Note: <label for> matching is file-scoped — a "
                            "label in a different template won't be detected."
                        ),
                        id="djust.Y003",
                        fix_hint=(
                            'Add a <label for="..."> (or aria-label) for the '
                            "<%s> control at line %d in `%s`." % (tag, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y004 — positive tabindex (focus-order anti-pattern).
        if not y004_suppressed:
            for match in _POSITIVE_TABINDEX_RE.finditer(scan_source):
                value = match.group(1)
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        '%s:%d -- positive tabindex="%s" overrides natural '
                        "focus order (WCAG 2.4.3)." % (relpath, lineno, value),
                        hint=(
                            "A positive tabindex forces this element to the "
                            "front of the tab order, ahead of earlier DOM "
                            "elements — a confusing, hard-to-maintain focus "
                            'order. Use tabindex="0" to add an element to the '
                            'natural order, or tabindex="-1" to make it '
                            "focusable only programmatically."
                        ),
                        id="djust.Y004",
                        fix_hint=(
                            'Change tabindex="%s" to tabindex="0" (or remove '
                            "it) at line %d in `%s`." % (value, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y005 — role="img" element / inline <svg> with no accessible name.
        if not y005_suppressed:
            for offset, label in _find_unnamed_images(scan_source):
                lineno = scan_source[:offset].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- %s has no accessible name (WCAG 1.1.1)."
                        % (relpath, lineno, label),
                        hint=(
                            "A screen reader announces a nameless graphic as just "
                            '"image" (or skips an svg). Add aria-label="..." or '
                            "aria-labelledby (a <title> child also names an svg), "
                            'or mark a purely decorative graphic aria-hidden="true".'
                        ),
                        id="djust.Y005",
                        fix_hint=(
                            'Add an aria-label="..." (or aria-hidden="true" if '
                            "decorative) to the %s at line %d in `%s`." % (label, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y006 — legend item that is only a colour swatch.
        if not y006_suppressed:
            for offset in _find_colour_only_legend_items(scan_source):
                lineno = scan_source[:offset].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- legend item has no text, so its series is "
                        "told apart by colour alone (WCAG 1.4.1)." % (relpath, lineno),
                        hint=(
                            "Users who cannot tell the colours apart cannot read "
                            "this legend. Put the series name in the <li> as "
                            "text (it may be visually hidden), or add an "
                            "aria-label / title to the item."
                        ),
                        id="djust.Y006",
                        fix_hint=(
                            "Add the series name as text (or an aria-label) to "
                            "the <li> at line %d in `%s`." % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

    return errors
