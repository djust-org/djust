"""djust system checks — accessibility checks (Y0xx) — ARIA/WCAG template scanning.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import logging
import os
import re
from typing import Any

from django.core.checks import CheckMessage, register

from djust.checks.utils import (
    DjustWarning,
    _is_check_suppressed,
    _iter_template_files,
    _get_template_dirs,
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
#
# All are deliberately low-ambiguity a11y defects so the regex heuristics
# carry near-zero false positives (see the #1060 dogfood discipline note
# in the Stage-4 plan). The category is extensible — Y005+ are
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
_TEMPLATE_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)

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
    stripped = _TEMPLATE_COMMENT_RE.sub(" ", stripped)
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

    All emit :class:`DjustWarning` (not error) so a stray false positive
    never fails ``manage.py check``; all are suppressible via
    ``DJUST_CONFIG['suppress_checks']``.
    """
    errors: list[CheckMessage] = []

    y001_suppressed = _is_check_suppressed("djust.Y001")
    y002_suppressed = _is_check_suppressed("djust.Y002")
    y003_suppressed = _is_check_suppressed("djust.Y003")
    y004_suppressed = _is_check_suppressed("djust.Y004")
    if y001_suppressed and y002_suppressed and y003_suppressed and y004_suppressed:
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
        scan_source = _strip_verbatim_blocks(content)

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

    return errors
