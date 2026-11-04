#!/usr/bin/env python3
"""Revert each #3665 fix, and break the sweep; each must turn a test red.

A sweep that survives the reversion of the fix it exists to guard is a decoration.
Each mutation below undoes ONE fix (or one guarantee of the sweep); the test file
named beside it is run against the mutated tree and must FAIL. A mutation that
leaves it green is reported and fails this script.

Same machinery and rules as ``template-library-parity-gateoff.py`` (which it
imports): the mutations are applied to a THROWAWAY COPY of the tree, never to this
checkout; the mutation text must be found and must change the source; HUNG and
ERROR are reported as themselves and are not a pass; the copy starts without
``__pycache__`` and runs with ``PYTHONDONTWRITEBYTECODE`` so a stale ``.pyc`` cannot
certify a mutation that never ran; pytest's own summary decides (a collection error
is ``ERROR``, not ``RED``).

Run: ``python scripts/url-sweep-gateoff.py [--list] [--only TEXT] [--no-control]``.
The fast mutations take seconds; the ones aimed at the sweep (marked ``sweep``)
take about three minutes each, because the sweep is the thing under test.
``python/tests/test_url_scheme_sweep_3665.py`` pins that every mutation text below
still exists, so the list cannot rot.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "parity_gateoff", ROOT / "scripts" / "template-library-parity-gateoff.py"
)
base = importlib.util.module_from_spec(_spec)
sys.modules["parity_gateoff"] = base
_spec.loader.exec_module(base)

TAGS = ROOT / "python/djust/components/templatetags/djust_components.py"
HANDLERS = ROOT / "python/djust/components/rust_handlers.py"
UTILS = ROOT / "python/djust/components/utils.py"
COMP = ROOT / "python/djust/components/components"
SWEEP = ROOT / "scripts/lib/template_library_parity/url_sweep.py"

THEMING = ROOT / "python/djust/theming/templatetags/theme_components.py"
PAGES = ROOT / "python/djust/theming/templatetags/theme_pages.py"
THEMING_TEST = "python/tests/test_theming_url_allowlist_3677.py"
HARDENING_TEST = "python/tests/test_theming_url_hardening_3749.py"
REVIEW_TEST = "python/tests/test_theming_url_review_3765.py"
AVATAR_RS = ROOT / "crates/djust_components/src/simple/avatar.rs"

AVATAR = "python/tests/test_avatar_src_scheme_3665.py"
SINKS = "python/tests/test_image_url_sinks_3665.py"
SWEEPTEST = "python/tests/test_url_scheme_sweep_3665.py"
CAPACITY = "python/tests/test_url_scheme_sweep_capacity.py"
SWEEP_K = "no_tag_emits or no_class_component or planted_hole or Scanner"

#: (label, file, text to find, replacement, test file, -k expression or "")
MUTATIONS: list[tuple[str, pathlib.Path, str, str, str, str]] = [
    # --- the fixes: each reverted alone, caught by the fast tests
    (
        "avatar tag: src printed as given (the issue)",
        TAGS,
        '        "src": url_attr(src, image=True),',
        '        "src": src,',
        AVATAR,
        "",
    ),
    (
        "responsive_image tag: srcset only escaped",
        TAGS,
        "    e_srcset = srcset_attr(srcset)\n",
        "    e_srcset = str(conditional_escape(srcset))\n",
        SINKS,
        "",
    ),
    (
        "responsive_image handler: srcset only escaped",
        HANDLERS,
        '        srcset = srcset_attr(kw.get("srcset", ""))',
        '        srcset = conditional_escape(str(kw.get("srcset", "")))',
        SINKS,
        "",
    ),
    (
        "srcset_attr: candidates are not checked",
        UTILS,
        "        candidate = url_attr(url, image=True, honor_safe=False)\n",
        "        candidate = str(conditional_escape(url))\n",
        SINKS,
        "",
    ),
    (
        "srcset_attr: a marked-safe value is no longer returned unchanged",
        UTILS,
        "    if honor_safe and isinstance(value, SafeData):\n        return str(value)\n    parts = []",
        "    parts = []",
        SINKS,
        "",
    ),
    (
        "ImageCropper: src only escaped",
        COMP / "image_cropper.py",
        "e_src = url_attr(self.src, image=True, honor_safe=False)",
        "e_src = html.escape(self.src)",
        SINKS,
        "",
    ),
    (
        "ImageUploadPreview: preview url only escaped",
        COMP / "image_upload_preview.py",
        "e_url = url_attr(url, image=True, honor_safe=False)",
        "e_url = html.escape(str(url))",
        SINKS,
        "",
    ),
    (
        "LiveIndicator: avatar only escaped",
        COMP / "live_indicator.py",
        "e_avatar = url_attr(avatar, image=True, honor_safe=False)",
        "e_avatar = html.escape(str(avatar))",
        SINKS,
        "",
    ),
    (
        "MentionsInput: avatar only escaped",
        COMP / "mentions_input.py",
        'avatar_src = url_attr(user.get("avatar", ""), image=True, honor_safe=False)',
        'avatar_src = html.escape(str(user.get("avatar", "")))',
        SINKS,
        "",
    ),
    (
        "OrgChart: avatar only escaped",
        COMP / "org_chart.py",
        'src="{url_attr(avatar, image=True, honor_safe=False)}"',
        'src="{html.escape(str(avatar))}"',
        SINKS,
        "",
    ),
    (
        "ResponsiveImage: srcset only escaped",
        COMP / "responsive_image.py",
        "e_srcset = srcset_attr(self.srcset, honor_safe=False)",
        "e_srcset = html.escape(self.srcset)",
        SINKS,
        "",
    ),
    # --- the review's findings (#3675)
    (
        "srcset parser NESTS parentheses (the review's bypass): the independent oracle must catch it",
        UTILS,
        '                    current += c\n                    if c == ")":\n                        state = "descriptor"',
        '                    current += c\n                    if c == ")" and current.count("(") <= current.count(")"):\n                        state = "descriptor"',
        SINKS,
        "",
    ),
    (
        "srcset parser NESTS parentheses: the FUZZ against the independent port alone must catch it",
        UTILS,
        '                    current += c\n                    if c == ")":\n                        state = "descriptor"',
        '                    current += c\n                    if c == ")" and current.count("(") <= current.count(")"):\n                        state = "descriptor"',
        SINKS,
        "random_srcsets or token_fuzz or no_random_srcset",
    ),
    (
        "srcset_attr: an empty (stripped-to-nothing) candidate is not skipped",
        UTILS,
        "        if not candidate:\n            continue\n",
        "        if False:\n            continue\n",
        SINKS,
        "",
    ),
    (
        "srcset parser splits on Unicode whitespace and VT, not ASCII whitespace only",
        UTILS,
        r'_ASCII_WS = "\t\n\f\r "',
        r'_ASCII_WS = "\t\n\f\r \u00a0\x0b"',
        SINKS,
        "",
    ),
    (
        "the Python responsive_image tag prints an empty srcset attribute",
        TAGS,
        "    e_srcset = srcset_attr(srcset)\n    if e_srcset:",
        "    e_srcset = srcset_attr(srcset)\n    if srcset:",
        SINKS,
        "",
    ),
    (
        "ResponsiveImage class honours the safe marker for srcset",
        COMP / "responsive_image.py",
        "srcset_attr(self.srcset, honor_safe=False)",
        "srcset_attr(self.srcset)",
        SINKS,
        "",
    ),
    (
        "ImageCropper honours the safe marker (a widened opt-out)",
        COMP / "image_cropper.py",
        "url_attr(self.src, image=True, honor_safe=False)",
        "url_attr(self.src, image=True)",
        SINKS,
        "",
    ),
    (
        "ImageUploadPreview honours the safe marker",
        COMP / "image_upload_preview.py",
        "url_attr(url, image=True, honor_safe=False)",
        "url_attr(url, image=True)",
        SINKS,
        "",
    ),
    (
        "LiveIndicator honours the safe marker",
        COMP / "live_indicator.py",
        "url_attr(avatar, image=True, honor_safe=False)",
        "url_attr(avatar, image=True)",
        SINKS,
        "",
    ),
    (
        "MentionsInput honours the safe marker",
        COMP / "mentions_input.py",
        'url_attr(user.get("avatar", ""), image=True, honor_safe=False)',
        'url_attr(user.get("avatar", ""), image=True)',
        SINKS,
        "",
    ),
    (
        "OrgChart honours the safe marker",
        COMP / "org_chart.py",
        "url_attr(avatar, image=True, honor_safe=False)",
        "url_attr(avatar, image=True)",
        SINKS,
        "",
    ),
    (
        "avatar tag hands the template plain text (double escaping)",
        TAGS,
        '        "src": url_attr(src, image=True),',
        '        "src": "".join([url_attr(src, image=True)]),',
        AVATAR,
        "",
    ),
    # --- #3677: theming judges URLs with the components allowlist, not a list of its own
    (
        "theming _neutralise_url: back to a denylist (the pre-#3677 rule)",
        THEMING,
        "    if not url_allowed(value, image=image):\n        logger.debug(",
        '    if str(value).strip().lower().startswith(("javascript:", "vbscript:", "data:", "livescript:", "mhtml:")):\n        logger.debug(',
        THEMING_TEST,
        "TestEverySink",
    ),
    (
        "theming _check_url (developer literals): neutralises only the three script schemes",
        THEMING,
        "    if not url_allowed(value):\n        logger.warning(",
        '    if str(value).strip().lower().startswith(("javascript:", "vbscript:", "data:")):\n        logger.warning(',
        THEMING_TEST,
        "TestLiteralAndPassthroughSinks",
    ),
    (
        "theming avatar: src not neutralised",
        THEMING,
        '    src = _neutralise_url("src", src, image=True)\n',
        "    src = src\n",
        THEMING_TEST,
        "TestEverySink and theme_avatar",
    ),
    (
        "theming lists (nav, nav_group, breadcrumb, sidebar_nav): item urls not neutralised",
        THEMING,
        "    return _map_item_attribute(items, name, lambda value: _neutralise_url(name, value))",
        "    return items",
        THEMING_TEST,
        "TestEverySink and (nav or breadcrumb)",
    ),
    (
        "theming pagination: the formatted url is not neutralised",
        THEMING,
        '        return _neutralise_url("url_pattern", url_pattern.format(page))',
        "        return url_pattern.format(page)",
        THEMING_TEST,
        "TestEverySink and theme_pagination",
    ),
    (
        "theming login page: the form action is not neutralised",
        PAGES,
        '    action = _neutralise_url("action", action)\n    forgot_password_url = _neutralise_url(',
        "    forgot_password_url = _neutralise_url(",
        THEMING_TEST,
        "TestEverySink and theme_login_page",
    ),
    (
        "theming: a marked-safe URL is printed raw (no longer made plain text)",
        THEMING,
        "    return str.__str__(str(value))\n\n\ndef _raises_for_scheme",
        "    return value\n\n\ndef _raises_for_scheme",
        THEMING_TEST,
        "marked_safe or plain_text",
    ),
    (
        "theming literal sinks: every rejected scheme raises (the pre-fix branch behaviour)",
        THEMING,
        "    if not url_allowed(value):\n        logger.warning(",
        '    if not url_allowed(value):\n        raise ValueError("refuses")\n        logger.warning(',
        THEMING_TEST,
        "TestLiteralAndPassthroughSinks",
    ),
    (
        "theming literal sinks: a script scheme no longer raises",
        THEMING,
        "    if _raises_for_scheme(value):\n        raise ValueError(",
        "    if False:\n        raise ValueError(",
        THEMING_TEST,
        "TestLiteralAndPassthroughSinks",
    ),
    (
        "shared allowlist: a value urlsplit cannot parse is allowed",
        UTILS,
        "    except ValueError:\n        return False\n    if scheme and scheme not in _SAFE_URL_SCHEMES:",
        "    except ValueError:\n        return True\n    if scheme and scheme not in _SAFE_URL_SCHEMES:",
        THEMING_TEST,
        "parser_cannot_read",
    ),
    (
        "shared allowlist: an empty URL is rejected",
        UTILS,
        "    if not s:\n        return True\n    if image and _is_data_image(s):",
        "    if not s:\n        return False\n    if image and _is_data_image(s):",
        THEMING_TEST,
        "empty_url",
    ),
    (
        "template pin: a composite href is added to nav_item.html",
        ROOT / "python/djust/theming/templates/djust_theming/components/nav_item.html",
        '<a href="{{ url }}"\n',
        '<a href="{{ url }}{{ attrs.x }}"\n',
        THEMING_TEST,
        "TestTheSinkSetIsPinned",
    ),
    (
        "template pin: a ping attribute is added to nav_item.html",
        ROOT / "python/djust/theming/templates/djust_theming/components/nav_item.html",
        '<a href="{{ url }}"\n',
        '<a href="{{ url }}" ping="{{ url }}"\n',
        THEMING_TEST,
        "TestTheSinkSetIsPinned",
    ),
    (
        "template pin: an unexplained variable in the layout's URL attribute",
        ROOT / "python/djust/theming/templates/djust_theming/layouts/base.html",
        "href=\"{% static 'djust_theming/css/layouts.css' %}\"",
        "href=\"{{ user_url }}{% static 'djust_theming/css/layouts.css' %}\"",
        THEMING_TEST,
        "TestTheSinkSetIsPinned",
    ),
    (
        "shared allowlist: the scheme is no longer checked against it",
        UTILS,
        "    if scheme and scheme not in _SAFE_URL_SCHEMES:\n        return False",
        "    if scheme and scheme not in _SAFE_URL_SCHEMES:\n        pass",
        THEMING_TEST,
        "TestEverySink",
    ),
    (
        "shared allowlist widened (blob: accepted): the scanner's copy no longer agrees",
        UTILS,
        '_SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp", "ftps"})',
        '_SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp", "ftps", "blob"})',
        SWEEPTEST,
        "allowlist_is_the_production_one",
    ),
    (
        "scanner: only the three script schemes are read as dangerous (the pre-#3677 scanner)",
        SWEEP,
        "    if m and m.group(1) not in NAVIGATION_SCHEMES:\n        return m.group(1)",
        "    if False:\n        return m.group(1)",
        SWEEPTEST,
        "Scanner",
    ),
    # --- #3749: a callable item url, the lazy marked-safe value, the **attrs pass-through
    (
        "theming items: the callable url is judged as a callable, not as what it returns",
        THEMING,
        "        safe = transform(_resolve_callable(value))\n        if safe is value:",
        "        safe = transform(value)\n        if safe is value:",
        HARDENING_TEST,
        "TestCallableUrl",
    ),
    (
        "theming _resolve_callable: an alters_data callable is called (the engine never does)",
        THEMING,
        '    if getattr(value, "alters_data", False):\n        return value\n    try:',
        "    try:",
        HARDENING_TEST,
        "alters_data",
    ),
    (
        "theming _resolve_callable: a do_not_call_in_templates value is called",
        THEMING,
        '    if not callable(value) or getattr(value, "do_not_call_in_templates", False):',
        "    if not callable(value):",
        HARDENING_TEST,
        "do_not_call",
    ),
    (
        "theming pass-through: URL attributes other than href/src/action/formaction are not judged",
        THEMING,
        "        elif value is not True and _is_url_attribute(lowered):",
        "        elif False:",
        HARDENING_TEST,
        "TestEveryPassthroughTagAndAttribute or TestEveryPayloadThroughOneTag",
    ),
    (
        "theming pass-through: a srcset / ping list is judged as ONE url",
        THEMING,
        '            if lowered in ("srcset", "ping"):',
        "            if False:",
        HARDENING_TEST,
        "TestListValuedAttributes",
    ),
    (
        "theming pass-through: a data:image URI is kept where no image loads (ping)",
        THEMING,
        '        if not url_allowed(url, image=image):\n            logger.warning("theming: %s= URL with a disallowed scheme rendered as \'#\'", name)\n            url = "#"\n        kept.append(url)',
        '        if not url_allowed(url, image=True):\n            logger.warning("theming: %s= URL with a disallowed scheme rendered as \'#\'", name)\n            url = "#"\n        kept.append(url)',
        HARDENING_TEST,
        "data_image_is_not_kept",
    ),
    (
        "theming pin: the production set drops dj-patch (the decision table no longer agrees)",
        THEMING,
        '{"ping", "srcset", "poster", "data", "background", "dj-navigate", "dj-patch", "dj-patch-reload"}',
        '{"ping", "srcset", "poster", "data", "background", "dj-navigate", "dj-patch-reload"}',
        HARDENING_TEST,
        "ThePassthroughSetIsPinned",
    ),
    (
        "theming pin: another tag starts printing arbitrary **attrs (theme_badge)",
        THEMING,
        '        "variant": variant,\n        "attrs": remaining_attrs,\n        "css_prefix": _css_prefix(),\n        **slots,\n    }\n    return mark_safe(tmpl.render(ctx))\n\n\n_ASSERTIVE_ALERT_VARIANTS',
        '        "variant": variant,\n        "attrs": remaining_attrs,\n        "css_prefix": _css_prefix(),\n        "x": _passthrough_attrs(attrs, skip=()),\n        **slots,\n    }\n    return mark_safe(tmpl.render(ctx))\n\n\n_ASSERTIVE_ALERT_VARIANTS',
        HARDENING_TEST,
        "ThePassthroughSetIsPinned",
    ),
    # --- PR #3765 review findings
    (
        "review 1: a callable's callable result is written back still callable (the engine calls it again)",
        THEMING,
        "    return str(result) if callable(result) else result",
        "    return result",
        REVIEW_TEST,
        "TestACallableReturningACallable or callable_items",
    ),
    (
        "review 2: the attribute is read before the subscript (the engine reads the subscript first)",
        THEMING,
        "    try:\n        return item[name]\n    except (TypeError, AttributeError, KeyError, ValueError, IndexError):\n        pass\n    try:\n        return getattr(item, name)\n    except (TypeError, AttributeError):\n        return _MISSING",
        "    try:\n        return getattr(item, name)\n    except (TypeError, AttributeError):\n        pass\n    try:\n        return item[name]\n    except (TypeError, AttributeError, KeyError, ValueError, IndexError):\n        return _MISSING",
        REVIEW_TEST,
        "TestAnItemTheEngineReadsBySubscript",
    ),
    (
        "review 2: the proxy answers the attribute but not the subscript",
        THEMING,
        "        if key == self._name:\n            return self._value\n        return self._item[key]",
        "        return self._item[key]",
        REVIEW_TEST,
        "TestAnItemTheEngineReadsBySubscript",
    ),
    (
        "review 3: a value is judged as text but handed to the template as the object (stringified twice)",
        THEMING,
        "    return str.__str__(str(value))\n\n\ndef _raises_for_scheme",
        "    str(value)\n    return value\n\n\ndef _raises_for_scheme",
        REVIEW_TEST,
        "converted_to_text_once or judged_is_the_text",
    ),
    (
        "review 3: the pass-through URL is judged as text but printed as the object",
        THEMING,
        '    value = _plain_url(value)\n    if not url_allowed(value, image=image):\n        logger.warning("theming: %s= URL with a disallowed scheme rendered as \'#\'", name)\n        return "#"\n    return value\n\n\nclass _ItemAttributeProxy',
        '    if not url_allowed(_plain_url(value), image=image):\n        logger.warning("theming: %s= URL with a disallowed scheme rendered as \'#\'", name)\n        return "#"\n    return value\n\n\nclass _ItemAttributeProxy',
        REVIEW_TEST,
        "converted_to_text_once",
    ),
    (
        "review 4: every data-*/dj-* name is judged again (the name heuristic)",
        THEMING,
        "    return name in _URL_ATTRS or name in _QUIET_URL_ATTRS",
        '    return name in _URL_ATTRS or name in _QUIET_URL_ATTRS or name.startswith(("data-", "dj-"))',
        REVIEW_TEST,
        "not_a_url_is_printed_as_written",
    ),
    (
        "review 4: an HTML URL-named attribute decided not to be judged (icon) is judged again",
        THEMING,
        "    return name in _URL_ATTRS or name in _QUIET_URL_ATTRS",
        '    return name in _URL_ATTRS or name in _QUIET_URL_ATTRS or name in ("cite", "icon", "longdesc", "archive")',
        HARDENING_TEST,
        "TestAnUnjudgedNameIsPrintedAsWritten or every_other_name",
    ),
    (
        "review 5: a template prints an attrs.<name> into a URL position (nav_item.html)",
        ROOT / "python/djust/theming/templates/djust_theming/components/nav_item.html",
        '<a href="{{ url }}"\n',
        '<a href="{{ url }}" ping="{{ attrs.link }}"\n',
        REVIEW_TEST,
        "TestAttrsReadsInUrlPositionsArePinned",
    ),
    (
        "sweep (tags): the pass-through attribute names are no longer driven, the SWEEP must go red",
        SWEEP,
        '    return any(s[2] == "VAR_KEYWORD" for s in info.sig)',
        "    return False",
        SWEEPTEST,
        "theming_sink",
    ),
    # --- the sweep must itself notice a reverted fix, through its own path
    (
        "sweep (tags): the avatar fix reverted, the SWEEP must go red",
        TAGS,
        '        "src": url_attr(src, image=True),',
        '        "src": src,',
        SWEEPTEST,
        SWEEP_K,
    ),
    (
        "sweep (tags): the srcset fix reverted in the Python tag, the SWEEP must go red",
        TAGS,
        "    e_srcset = srcset_attr(srcset)\n",
        "    e_srcset = str(conditional_escape(srcset))\n",
        SWEEPTEST,
        SWEEP_K,
    ),
    (
        "sweep (components): OrgChart's fix reverted (a LIST-of-dicts parameter), the SWEEP must go red",
        COMP / "org_chart.py",
        'src="{url_attr(avatar, image=True, honor_safe=False)}"',
        'src="{html.escape(str(avatar))}"',
        SWEEPTEST,
        SWEEP_K,
    ),
    (
        "sweep (tags): the srcset parser nests parentheses, the SWEEP's independent oracle must go red",
        UTILS,
        '                    current += c\n                    if c == ")":\n                        state = "descriptor"',
        '                    current += c\n                    if c == ")" and current.count("(") <= current.count(")"):\n                        state = "descriptor"',
        SWEEPTEST,
        SWEEP_K,
    ),
    (
        "sweep (classes): LiveComponent and LiveView classes are no longer discovered",
        SWEEP,
        "                    and issubclass(obj, (Component, LiveComponent, LiveView))",
        "                    and issubclass(obj, (Component,))",
        SWEEPTEST,
        "driven_or_pinned",
    ),
    # --- the sweep's affordability: its shortcuts change nothing, and its size is budgeted
    (
        "capacity: no context name is passed to a call (referenced_names returns nothing)",
        SWEEP,
        '    return tuple(n for n in names if re.search(r"\\b%s\\b" % re.escape(n), src))',
        "    return ()",
        CAPACITY,
        "TheShortcutsChangeNothing",
    ),
    (
        "capacity: a context name matches as a substring, not a word",
        SWEEP,
        '    return tuple(n for n in names if re.search(r"\\b%s\\b" % re.escape(n), src))',
        "    return tuple(n for n in names if re.search(re.escape(n), src))",
        CAPACITY,
        "",
    ),
    (
        "capacity: carries is not measured for a tag with a native handler",
        SWEEP,
        '    return native or label == "https"',
        '    return label == "https"',
        CAPACITY,
        "TheShortcutsChangeNothing",
    ),
    (
        "capacity: the read an output is shared with a cell loses the attributes carries reads",
        SWEEP,
        "                read[out] = (findings_in(attrs), marked_attrs(attrs))",
        "                read[out] = (findings_in(attrs), [])",
        CAPACITY,
        "TheShortcutsChangeNothing",
    ),
    (
        "capacity: --exhaustive still gives each call only the names it uses",
        SWEEP,
        "                call_ctx = ctx if exhaustive else {",
        "                call_ctx = ctx if False else {",
        CAPACITY,
        "",
    ),
    (
        "capacity: --exhaustive still skips carries where nothing reads it",
        SWEEP,
        "                carried = exhaustive or needs_carries(",
        "                carried = False or needs_carries(",
        CAPACITY,
        "",
    ),
    (
        "budget: the calibration size is lowered, so the sweep is over its ceiling",
        ROOT / SWEEPTEST,
        "CALIBRATION_CELLS = 228_522",
        "CALIBRATION_CELLS = 100_000",
        SWEEPTEST,
        "stays_inside_its_cell_budget",
    ),
    (
        "budget: the CI slowdown is lowered, so the ceiling is no longer bounded",
        ROOT / SWEEPTEST,
        "CI_SLOWDOWN = 3.0",
        "CI_SLOWDOWN = 0.3",
        SWEEPTEST,
        "cannot_be_raised",
    ),
    (
        "budget: every parameter is driven three times over, so the sweep outgrows its ceiling",
        SWEEP,
        "    for name in names:\n        for shape in (*shapes, SAFE_SHAPE):",
        "    for name in names:\n        for shape in (*shapes, *shapes, *shapes, SAFE_SHAPE):",
        SWEEPTEST,
        "stays_inside_its_cell_budget",
    ),
    # --- the sweep's own guarantees
    (
        "scanner: nothing is ever dangerous",
        SWEEP,
        "    scheme = scheme_of(attr.value)\n    if not scheme or not is_url_attr(attr.name):\n        return False",
        "    scheme = scheme_of(attr.value)\n    if True:\n        return False",
        SWEEPTEST,
        "Scanner or planted_hole",
    ),
    (
        "scanner: control characters and whitespace are not stripped before the scheme probe",
        SWEEP,
        '_STRIP = re.compile(r"[\\x00-\\x20]+")',
        '_STRIP = re.compile(r"^$")',
        SWEEPTEST,
        "Scanner",
    ),
    (
        "scanner: attribute values are not entity-decoded",
        SWEEP,
        "            yield Attr(element, m.group(1).lower(), html.unescape(raw))",
        "            yield Attr(element, m.group(1).lower(), raw)",
        SWEEPTEST,
        "Scanner",
    ),
]


def run(work: pathlib.Path, target: str, expr: str) -> tuple[str, str]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(work / "python"), str(work)]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("DJANGO_SETTINGS_MODULE", None)
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", target]
    if expr:
        cmd += ["-k", expr]
    try:
        done = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return "HUNG", ""
    out = done.stdout + done.stderr
    first = next((ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR "))), "")
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)), ""
    )
    if done.returncode == 0 and "passed" in summary and "failed" not in summary:
        return "GREEN", ""
    if "failed" in summary and not re.search(r"\d+ error", summary):
        return "RED", first
    return "ERROR", first


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for label, path, *_ in MUTATIONS:
            print("%-100s %s" % (label, path.relative_to(ROOT)))
        return 0
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    chosen = [m for m in MUTATIONS if only is None or only in m[0]]
    if not chosen:
        print("no mutation matches %r" % only)
        return 2
    base._COPY_FILES = base._COPY_FILES + (
        AVATAR,
        SINKS,
        SWEEPTEST,
        CAPACITY,
        "scripts/template-library-url-sweep.py",
        "scripts/url-sweep-gateoff.py",
        THEMING_TEST,
        HARDENING_TEST,
        REVIEW_TEST,
    )
    real = [m[1] for m in MUTATIONS]
    before = base.tree_digest(real)
    survivors = 0
    with base.workdir() as work:
        print("workdir: %s" % work, flush=True)
        if "--no-control" not in argv:
            for target in (AVATAR, SINKS):
                control, _ = run(work, target, "")
                print("control (unmutated) %s: %s" % (target, control), flush=True)
                if control != "GREEN":
                    return 2
        for label, path, old, new, target, expr in chosen:
            file = work / path.relative_to(ROOT)
            original = file.read_text(encoding="utf-8")
            assert old in original, "MUTATION TEXT NOT FOUND for %s" % label
            mutated = original.replace(old, new, 1)
            assert mutated != original, "NO-OP MUTATION for %s" % label
            file.write_text(mutated, encoding="utf-8")
            for cache in work.rglob("__pycache__"):
                base.shutil.rmtree(cache, ignore_errors=True)
            print("mutating: %s" % label, flush=True)
            outcome, first = run(work, target, expr)
            file.write_text(original, encoding="utf-8")
            survivors += outcome != "RED"
            print("%-6s %s\n         %s" % (outcome, label, first[:170]), flush=True)
    assert base.tree_digest(real) == before, "the real tree changed during the run"
    print("%d of %d mutations turned the test red" % (len(chosen) - survivors, len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
