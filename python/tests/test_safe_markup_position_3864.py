"""Where the safe-markup guard places a part: read, or reported, never assumed (#3864).

The guard checks where an escaper's output lands by the text in front of it. Before #3864 an earlier
part whose text it could not spell out counted as an ordinary character, so the part after it was
positioned as if nothing could move the position:

* ``pre() + safe_url(x)`` was cleared where ``pre`` returns ``'<a href='`` from one of two branches
  (and from a constant table, ``dict.get``, a tuple, a local with two values, ``str()`` /
  ``.strip()`` / a slice of a literal, a class attribute, an open ``format_html``, a ``%`` or
  ``.format`` field);
* a fragment ``' title="' + safe_url(x) + '"'`` was placed unchecked, also inside a quoted value
  (where the URL lands between attributes);
* a helper's markup placed in a larger string was not looked into (``<a{w()}>`` of a helper that
  returns `` href="{escape(v)}"``), nor an HTML escaper between attributes or as a tag name;
* a function the file replaces (``sys.modules[__name__].pre = ...``, ``setattr`` on the module,
  ``pre.__code__ = ...``) was followed as if its ``def`` were what runs.

The invariant pass B now holds (``_Module._misplaces`` / ``_walk``): the position of every part is
established from text the guard read, or the part is reported. A part it cannot spell out is
replaced by the symbol of what it can do (``SYM_*``): escaped text keeps the position, markup that
starts and ends in element text fits element text only, whole attributes fit between attributes,
and a part it cannot class makes every positioned part after it unknown. A value with a few known
spellings is read once per spelling.

Every row is RUN with the call fuzz's hostile payloads and the output parsed: the verdict the test
expects is the one the browser reaches. The randomized differential
(``safe_markup_position_fuzz_3864``) checks the same over generated modules.

Refs #3864, #3850.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
import safe_markup_call_fuzz_3864 as fuzz
import safe_markup_position_fuzz_3864 as positions

from scripts.lib import safe_markup_taint as taint
from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]
HEADER = fuzz.HEADER + "from django.utils.html import format_html, format_html_join\n"

#: label -> (the module after ``HEADER``, which defines ``f(x)``; flagged?). A ``True`` row lets a
#: payload out when run, a ``False`` row does not; ``CONSERVATIVE`` rows are flagged and do not.
ROWS: dict[str, tuple[str, bool]] = {
    # -- the text in front, from a part the guard used to read as nothing
    "a helper that returns one of two prefixes": (
        "def pre(k):\n    return '<a href=' if k else '<b title='\n"
        "def f(x):\n    return mark_safe(pre(1) + safe_url(x) + '>l</a>')\n",
        True,
    ),
    # ``max`` of a comprehension is a number only when each element is, read with the loop
    # variable unknown: a name bound outside the loop is not the loop's variable
    "max of the lengths in a loop is digits": (
        "def f(rows):\n    n = max(len(r) for r in rows)\n"
        "    return mark_safe(f'<td colspan={n}>')\n",
        False,
    ),
    "a helper with two returns": (
        "def pre(k):\n    if k:\n        return '<a href='\n    return '<b>'\n"
        "def f(x):\n    return mark_safe(pre(1) + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a constant table": (
        "PRE = {'a': '<a href='}\ndef f(x):\n    return mark_safe(PRE['a'] + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "dict.get of a constant table": (
        "PRE = {'a': '<a href='}\n"
        "def f(x):\n    return mark_safe(PRE.get('a', '') + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a tuple element": (
        "PRE = ('<a href=', '<b>')\ndef f(x):\n    return mark_safe(PRE[0] + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a local with two values": (
        "def f(x):\n    p = '<a href=' if len(x) > 1 else '<b title='\n"
        "    return mark_safe(p + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a loop variable over literals": (
        "def f(x):\n    p = ''\n    for c in ('<a href=',):\n        p = c\n"
        "    return mark_safe(p + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "str() of a literal": (
        "def f(x):\n    return mark_safe(str('<a href=') + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "strip() of a literal": (
        "def f(x):\n    return mark_safe('<a href= '.strip() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "replace() of a literal": (
        "def f(x):\n    return mark_safe('<a hrefQ'.replace('Q', '=') + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a slice of a literal": (
        "def f(x):\n    return mark_safe('<a href=XX'[:8] + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a class attribute": (
        "class C:\n    P = '<a href='\n    def r(self, x):\n"
        "        return mark_safe(self.P + safe_url(x) + '>l</a>')\n"
        "def f(x):\n    return C().r(x)\n",
        True,
    ),
    "format_html that leaves a tag open": (
        "def f(x):\n    return mark_safe(format_html('<a href={}', '') + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a .format field": (
        "def pre(k):\n    return '<a href=' if k else '<b>'\n"
        "def f(x):\n    return mark_safe('{}{}>l</a>'.format(pre(1), safe_url(x)))\n",
        True,
    ),
    "a % argument": (
        "def pre(k):\n    return '<a href=' if k else '<b>'\n"
        "def f(x):\n    return mark_safe('%s%s>l</a>' % (pre(1), safe_url(x)))\n",
        True,
    ),
    "an f-string field": (
        "def pre(k):\n    return '<a href=' if k else '<b>'\n"
        "def f(x):\n    return mark_safe(f'{pre(1)}{safe_url(x)}>l</a>')\n",
        True,
    ),
    "an HTML escaper behind a helper's quote": (
        "def pre(k):\n    return '<a href=\"' if k else '<b>'\n"
        "def f(x):\n    return mark_safe(pre(1) + escape(x) + '\">l</a>')\n",
        True,
    ),
    "a narrowed name": (
        "def f(x):\n    p = '<a href=' if len(x) > 1 else 'z'\n    if p in ('<a href=', '<b>'):\n"
        "        return mark_safe(p + safe_url(x) + '>l</a>')\n    return ''\n",
        True,
    ),
    # -- a fragment, read where it lands
    "a balanced fragment inside a quoted value": (
        "def f(x):\n    frag = ' title=\"' + safe_url(x) + '\"'\n"
        "    return mark_safe('<a href=\"' + frag + '\">l</a>')\n",
        True,
    ),
    "a balanced fragment as an unquoted value": (
        "def f(x):\n    frag = ' title=\"' + safe_url(x) + '\"'\n"
        "    return mark_safe('<a href=' + frag + '>l</a>')\n",
        True,
    ),
    "an escaped value as a tag's name": (
        "def f(x):\n    return mark_safe('<' + escape(x) + '>d</div>')\n",
        True,
    ),
    "an escaped value between attributes": (
        "def f(x):\n    return mark_safe(f'<div {escape(x)}>d</div>')\n",
        True,
    ),
    "a helper's markup with a URL attribute it leaves to the caller": (
        "def w(u):\n    return f' href=\"{u}\"'\ndef f(x):\n    return mark_safe(f'<a{w(escape(x))}>l</a>')\n",
        True,
    ),
    "a recursive helper whose base case leaves a quote open": (
        "def pre(n):\n    return '<a title=\"' if n == 0 else pre(n - 1) + '\"'\n"
        "def f(x):\n    return mark_safe(pre(1) + escape(x) + '>l</a>')\n",
        True,
    ),
    "a recursive helper whose deeper level closes the quote its step opens": (
        # ``pre(*[2])`` is read with ``n`` unknown: the inner call is first read as markup in
        # element text, which the result breaks (level 0 leaves a quote open), so it is read again
        # with the inner call unknown; level 2 puts the value between attributes
        "def pre(*a):\n    n = a[0]\n"
        '    return "<a title=\'" if n == 0 else pre(n - 1) + "\'\\""\n'
        "def f(x):\n    return mark_safe(pre(*[2]) + escape(x) + '>l</a>')\n",
        True,
    ),
    "a checked URL that may be empty, then an escaped value": (
        "def f(x):\n    return mark_safe(f'<a href=\"{safe_url(x[:0])}{escape(x)}\">l</a>')\n",
        True,
    ),
    "an attribute name out of a long table": (
        "NAMES = ('href', 'data-a0', 'data-a1', 'data-a2', 'data-a3', 'data-a4', 'data-a5', 'data-a6', 'data-a7', 'data-a8', 'data-a9', 'data-a10', 'data-a11', 'data-a12', 'data-a13', 'data-a14', 'data-a15', 'data-a16', 'data-a17', 'data-a18', 'data-a19', 'data-a20', 'data-a21', 'data-a22', 'data-a23', 'data-a24', 'data-a25', 'data-a26', 'data-a27', 'data-a28', 'data-a29', 'data-a30', 'data-a31', 'data-a32', 'data-a33')\n"
        "def f(x):\n    return mark_safe(f'<a {NAMES[len(x) % 1]}=\"{escape(x)}\">l</a>')\n",
        True,
    ),
    "pieces in a loop: one opens an unquoted value, a later one fills it": (
        "def f(x):\n    s = '<a'\n    for p in (' title=',):\n        s += p\n"
        "    s += escape(x)\n    return mark_safe(s + '>l</a>')\n",
        True,
    ),
    # -- clean twins: read, and placed where they fit
    "straight-line pieces that open a quote and fill it (read in order)": (
        "def f(x):\n    s = '<a title=\"'\n    s += escape(x)\n    s += '\">l</a>'\n"
        "    return mark_safe(s)\n",
        False,
    ),
    "a conditional quoted attribute, then another attribute": (
        "def f(x):\n    html = '<a'\n    if x:\n        html += f' title=\"{escape(x)}\"'\n"
        "    html += f' href=\"{safe_url(x)}\">l</a>'\n    return mark_safe(html)\n",
        False,
    ),
    "a helper's escaper around a URL the caller checked": (
        "def w(u):\n    return conditional_escape(u)\n"
        "def f(x):\n    return mark_safe(f'<a href=\"{w(safe_url(x))}\">l</a>')\n",
        False,
    ),
    "two prefixes that both end in element text": (
        "def pre(k):\n    return '<p>' if k else '<div class=\"c\">'\n"
        "def f(x):\n    return mark_safe(pre(1) + safe_url(x) + '</p>')\n",
        False,
    ),
    "two prefixes, each a quoted value": (
        "def pre(k):\n    return '<a title=\"' if k else '<a class=\"c\" title=\"'\n"
        "def f(x):\n    return mark_safe(pre(1) + escape(x) + '\">l</a>')\n",
        False,
    ),
    "a class name from a table in a quoted value, then a URL": (
        "CLS = {'a': 'btn', 'b': 'btn btn-x'}\n"
        'def f(x):\n    return mark_safe(f\'<a class="{CLS.get(x, "btn")}" href="{safe_url(x)}">l</a>\')\n',
        False,
    ),
    "an optional attribute between attributes": (
        "def f(x):\n    hidden = ' hidden' if len(x) > 3 else ''\n"
        "    return mark_safe(f'<a{hidden} href=\"{safe_url(x)}\">l</a>')\n",
        False,
    ),
    "numbers": (
        "def f(x):\n    n = len(x) * 2 - 1\n"
        "    return mark_safe(f'<a data-n={n} href=\"{safe_url(x)}\">l</a>')\n",
        False,
    ),
    "a loop that unpacks constant rows": (
        "ROWS = (('a', 'A'), ('b', 'B'))\n"
        "def f(x):\n    parts = [f'<b data-k=\"{k}\">{v}</b>' for k, v in ROWS]\n"
        "    return mark_safe(''.join(parts) + f'<a href=\"{safe_url(x)}\">l</a>')\n",
        False,
    ),
    "a recursive helper that renders markup": (
        "def node(d, n):\n    if n == 0:\n        return f'<i>{escape(d)}</i>'\n"
        "    return f'<ul>{node(d, n - 1)}</ul>'\n"
        "def f(x):\n    return mark_safe(node(x, 2) + f'<a href=\"{safe_url(x)}\">l</a>')\n",
        False,
    ),
    "a method that renders a tree through itself": (
        "class Tree:\n"
        "    def node(self, d, kids, depth):\n"
        "        card = f'<span class=\"n\">{escape(d)}</span>'\n"
        "        if not kids:\n"
        "            return f'<li>{card}</li>'\n"
        "        items = ''.join(self.node(k, kids[1:], depth + 1) for k in kids)\n"
        "        return f'<li>{card}<ul>{items}</ul></li>'\n"
        "    def render(self, x):\n"
        "        tree = self.node(x, [x, x], 0)\n"
        '        return mark_safe(f\'<ul class="t" title="{escape(x)}">{tree}</ul>'
        '<a href="{safe_url(x)}">l</a>\')\n'
        "def f(x):\n    return Tree().render(x)\n",
        False,
    ),
    "format_html markup, then a URL": (
        "def f(x):\n    return mark_safe(format_html('<b>{}</b>', x) + f'<a href=\"{safe_url(x)}\">l</a>')\n",
        False,
    ),
    "a balanced fragment between attributes": (
        "def f(x):\n    frag = ' title=\"' + safe_url(x) + '\"'\n"
        "    return mark_safe('<a' + frag + '>l</a>')\n",
        False,
    ),
    "a balanced fragment in element text": (
        "def f(x):\n    frag = ' title=\"' + safe_url(x) + '\"'\n"
        "    return mark_safe('<p>' + frag + '</p>')\n",
        False,
    ),
    "an escaped value in a comment": (
        "def f(x):\n    return mark_safe(f'<!-- {escape(x)} --><a href=\"{safe_url(x)}\">l</a>')\n",
        False,
    ),
    # -- a function the file replaces is not followed (its result is an unknown call's)
    "a module attribute replaced through sys.modules": (
        "import sys\ndef pre():\n    return '<b>'\n"
        "sys.modules[__name__].pre = lambda: '<a href='\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "setattr on the module": (
        "import sys\ndef pre():\n    return '<b>'\n"
        "setattr(sys.modules[__name__], 'pre', lambda: '<a href=')\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "setattr on the module with a name the file computes": (
        "import importlib\ndef pre():\n    return '<b>'\nNAME = 'p' + 're'\n"
        "setattr(importlib.import_module(__name__), NAME, lambda: '<a href=')\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "the module's __dict__": (
        "import sys\ndef pre():\n    return '<b>'\n"
        "sys.modules[__name__].__dict__['pre'] = lambda: '<a href='\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "a module object held by a name": (
        "import sys\ndef pre():\n    return '<b>'\nME = sys.modules[__name__]\n"
        "ME.pre = lambda: '<a href='\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "the function's code replaced": (
        "def pre():\n    return '<b>'\ndef _o():\n    return '<a href='\npre.__code__ = _o.__code__\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '>l</a>')\n",
        True,
    ),
    "setattr of another name on another object leaves the helper trusted": (
        "class O:\n    pass\nOBJ = O()\nsetattr(OBJ, 'other', 1)\n"
        "def pre():\n    return '<p>'\n"
        "def f(x):\n    return mark_safe(pre() + safe_url(x) + '</p>')\n",
        False,
    ),
}

#: Flagged, and no payload of the fuzz gets out when run: what pass B cannot establish.
CONSERVATIVE = {
    # the loop variable is not the outer ``r = 3``: rows of strings put a string in an unquoted
    # value (the run passes one string, whose largest character is harmless)
    "max over a loop variable that shadows a number": (
        "def f(rows):\n    r = 3\n    n = max(r for r in rows)\n"
        "    return mark_safe(f'<td colspan={n}>')\n"
    ),
    # ``</scripts>`` does not end a script: the value is in the script body (no payload runs
    # there, the body is a syntax error)
    "a value after </scripts> is still in the script": (
        "def f(x):\n    return mark_safe('<script>1</scripts><a title=\"' + escape(x) + '\">l</a>')\n"
    ),
    # foreign content's CDATA is not read: nothing is placed after it
    "a value after a CDATA section": (
        "def f(x):\n    return mark_safe('<svg><![CDATA[x]]><a title=\"' + escape(x) + '\">l</a></svg>')\n"
    ),
    # a value that honours mark_safe in a comment: a marked-safe ``-->`` closes it
    # (``test_safe_markup_html_context_3864.TestMarkedSafeInAnAttribute``)
    "conditional_escape in a comment": (
        "def f(x):\n    return mark_safe(f'<!-- {conditional_escape(x)} -->')\n"
    ),
    # a nested spec: the part is not read, so it is reported even in a quoted title (#4212)
    "an escaped value with a nested spec in a quoted title": (
        "def f(x, w=1):\n    return mark_safe(f'<a title=\"{escape(x):>{w}}\">l</a>')\n"
    ),
}


def flagged(module: str) -> bool:
    return bool(find_unsafe_markup(HEADER + module))


def unsafe(module: str) -> bool:
    """Run ``f`` with every payload (registered as a module, for the rows that patch one)."""
    for payload in fuzz.PAYLOADS:
        mod = types.ModuleType("position_rows_3864")
        sys.modules[mod.__name__] = mod
        try:
            exec(compile(HEADER + module, "<row>", "exec"), mod.__dict__)  # noqa: S102 - written here
            out = str(mod.f(payload))
        finally:
            sys.modules.pop(mod.__name__, None)
        if fuzz.injected(out):
            return True
    return False


D = "def f(x):\n    return mark_safe("
#: The second review of #3864: each unsafe in a browser (jsdom) and, where Python's parser sees it,
#: when run here too. ``True``: reported. ``False``: a twin that is safe and cleared.
REVIEW_ROWS: dict[str, tuple[str, bool]] = {
    # a format spec on an escaper's output pads it, it does not move it (R1)
    "a padded escaped value in an href": (D + "f'<a href=\"{escape(x):>1}\">l</a>')\n", True),
    "a padded escaped value as an unquoted value": (
        D + "f'<a title={escape(x):>20}>l</a>')\n",
        True,
    ),
    "a padded checked URL in a handler": (
        D + "f'<b onclick=\"go(\\'{safe_url(x):>3}\\')\">b</b>')\n",
        True,
    ),
    "!s with a spec": (D + "f'<a href=\"{escape(x)!s:>2}\">l</a>')\n", True),
    "str.format with a spec": (D + "'<a href=\"{:>3}\">l</a>'.format(escape(x)))\n", True),
    "a padded escaped value in a quoted title": (
        D + "f'<a title=\"{escape(x):>20}\">l</a>')\n",
        False,
    ),
    # abrupt comments put the tokenizer back in element text (R2)
    "an abrupt comment <!-->": (D + "'<!--><a title=' + escape(x) + '>l</a>')\n", True),
    "an abrupt comment <!--->": (D + "'<!---><a title=' + escape(x) + '>l</a>')\n", True),
    "an escaped value in a real comment": (D + "'<!-- c ' + escape(x) + ' -->')\n", False),
    # %c / :c write any character
    "%c writes a quote": (D + "'<a title=\"a%c %s\">l</a>' % (34, escape(x)))\n", True),
    "{:c} writes a quote": (D + "'<a title=\"a{:c} '.format(34) + escape(x) + '\">l</a>')\n", True),
    # a quote inside an unquoted value is a character of the value
    "a quote inside an unquoted value": (D + '"<a title=a\'b " + escape(x) + ">l</a>")\n', True),
    # a value can complete a character reference (``&#10`` + ``6;``), or follow a decoded one
    "a value completing a character reference": (
        D + "'<a href=\"&#10' + escape(x) + '\">l</a>')\n",
        True,
    ),
    "a value after a decoded tab": (D + "'<a href=\"&#9;' + escape(x) + '\">l</a>')\n", True),
    "a value after a literal data: scheme": (
        D + "'<iframe src=\"data:text/html,' + safe_url(x) + '\"></iframe>')\n",
        True,
    ),
    # RCDATA / RAWTEXT / script data escape / foreign content
    "inside a textarea a tag is text": (
        D + "'<textarea><a title=\"' + escape(x) + '\"></textarea>')\n",
        False,
    ),
    "a textarea's text then a quote": (
        D + "'<textarea><a title=\"</textarea><a title=' + escape(x) + '>l</a>')\n",
        True,
    ),
    "xmp ends at its end tag": (
        D + "'<xmp><b title=\"</xmp><a title=' + escape(x) + '>l</a>')\n",
        True,
    ),
    "a script's double escape": (
        D + "'<script><!--<script></script>' + escape(x) + '</script>')\n",
        True,
    ),
    "</scripts> does not end a script": (
        D + "'<script>1</scripts><a title=' + escape(x) + '>l</a>')\n",
        True,
    ),
    "a CDATA section": (
        D + "'<svg><![CDATA[<a title=\"]]><a title=' + escape(x) + '>l</a></svg>')\n",
        True,
    ),
    "an svg title is markup, not RCDATA": (
        D + "f'<svg><title>{conditional_escape(x)}</title></svg><a title=\"{escape(x)}\">l</a>')\n",
        False,
    ),
    # builders whose positions are now read
    "format_map of a dict": (D + "'<a href=\"{u}\">l</a>'.format_map({'u': escape(x)}))\n", True),
    "a % of a dict held by a name": (
        "def f(x):\n    d = {'u': escape(x)}\n    return mark_safe('<a href=\"%(u)s\">l</a>' % d)\n",
        True,
    ),
    "string.Template": (
        "import string\ndef f(x):\n    return mark_safe(string.Template('<a href=\"$u\">l</a>').substitute(u=escape(x)))\n",
        True,
    ),
    "string.Template in a title": (
        "import string\ndef f(x):\n    return mark_safe(string.Template('<a title=\"$u\">l</a>').substitute(u=escape(x)))\n",
        False,
    ),
    # escaping undone, or a value that may be nothing
    "replace() undoes the escaping": (
        D + "'<a title=\"' + escape(x).replace('&quot;', '\"') + '\">l</a>')\n",
        True,
    ),
    "a cut of digits may be nothing at the start of a URL": (
        D + "'<a href=\"' + str(len(x))[5:] + escape(x) + '\">l</a>')\n",
        True,
    ),
    # format_html's arguments land in its format string
    "format_html with an argument in an href": (
        "def f(x):\n    return format_html('<a href=\"{}\">l</a>', x)\n",
        True,
    ),
    "format_html with an argument in a handler": (
        "def f(x):\n    return format_html('<b onclick=\"go(\\'{}\\')\">b</b>', x)\n",
        True,
    ),
    "format_html with an argument as an unquoted value": (
        "def f(x):\n    return format_html('<a title={}>l</a>', x)\n",
        True,
    ),
    "format_html with a checked URL in an href": (
        "def f(x):\n    return format_html('<a href=\"{}\">l</a>', safe_url(x))\n",
        False,
    ),
    "an svg title holds markup: an unquoted value there": (
        D + "'<svg><title><a title=' + escape(x) + '>l</a></title></svg>')\n",
        True,
    ),
    "a format string held by a name": (
        "FMT = '<a href={}>l</a>'\ndef f(x):\n    return mark_safe(FMT.format(escape(x)))\n",
        True,
    ),
    "format_html with marked-safe script JSON in a script": (
        "import json\nfrom djust.security.json_script import escape_json_for_script\n"
        "def f(x):\n    return format_html('<script>var a={};</script>', "
        "mark_safe(escape_json_for_script(json.dumps(x))))\n",
        False,
    ),
    "format_html_join with an argument in an href": (
        "def f(x):\n    return format_html_join('', '<a href=\"{}\">l</a>', ((v,) for v in [x]))\n",
        True,
    ),
    # a cut of a checked URL is escaped text, not a checked URL: ``safe_url('/javascript:x')`` is
    # a path, and ``[1:]`` uncovers the scheme (#4212)
    "a slice of a checked URL in an href": (D + "f'<a href=\"{safe_url(x)[1:]}\">l</a>')\n", True),
    "a sliced checked URL concatenated into an href": (
        D + "'<a href=\"' + safe_url(x)[1:] + '\">l</a>')\n",
        True,
    ),
    "a checked URL with its leading slash stripped": (
        D + "'<a href=\"' + safe_url(x).lstrip('/') + '\">l</a>')\n",
        True,
    ),
    # format_html: a format string held in a name, a default or a branch, a join template held
    # by a name, and star arguments are placed too (#4212)
    "format_html with a format string in a module constant": (
        "FMT = '<a href=\"{}\">l</a>'\ndef f(x):\n    return format_html(FMT, x)\n",
        True,
    ),
    "format_html with a format string in a parameter default": (
        "def f(x, fmt='<a href=\"{}\">l</a>'):\n    return format_html(fmt, x)\n",
        True,
    ),
    "format_html with a format string chosen by a branch": (
        "def f(x, c=True):\n"
        "    return format_html('<a href=\"{}\">l</a>' if c else '<b>{}</b>', x)\n",
        True,
    ),
    "format_html_join with a template held by a name": (
        "T = '<a href=\"{}\">l</a>'\n"
        "def f(x):\n    return format_html_join('', T, ((v,) for v in [x]))\n",
        True,
    ),
    "format_html with *args into an href": (
        "def f(x):\n    return format_html('<a href=\"{}\">l</a>', *[x])\n",
        True,
    ),
    "format_html with **kwargs into an href": (
        "def f(x):\n    return format_html('<a href=\"{u}\">l</a>', **{'u': x})\n",
        True,
    ),
    "format_html with a format string in a module constant and a checked URL": (
        "FMT = '<a href=\"{}\">l</a>'\ndef f(x):\n    return format_html(FMT, safe_url(x))\n",
        False,
    ),
    "format_html with a format string in a module constant, title": (
        "FMT = '<a title=\"{}\">l</a>'\ndef f(x):\n    return format_html(FMT, x)\n",
        False,
    ),
    # a style attribute's value is CSS: HTML escaping does not stop ``;`` or ``url(...)`` (#4212)
    "an escaped value in a style attribute": (
        D + "f'<b style=\"color: {escape(x)}\">b</b>')\n",
        True,
    ),
    "format_html with an argument in a style attribute": (
        "def f(x):\n    return format_html('<b style=\"color: {}\">b</b>', x)\n",
        True,
    ),
    "a safe_css_color value in a style attribute": (
        "from djust.components.utils import safe_css_color\n"
        "def f(x):\n    return mark_safe(f'<b style=\"color: {safe_css_color(x)}\">b</b>')\n",
        False,
    ),
    # a nested format spec makes the part one the guard cannot read: it may not sit anywhere
    # (#4212: the behavioural witness of that rule)
    "an escaped value with a nested spec in an href": (
        "def f(x, w=1):\n    return mark_safe(f'<a href=\"{escape(x):>{w}}\">l</a>')\n",
        True,
    ),
    "a slice of a checked URL in a quoted title": (
        D + "f'<a title=\"{safe_url(x)[1:]}\">l</a>')\n",
        False,
    ),
}


def _outputs(module: str) -> list[str]:
    out = []
    for payload in positions.PAYLOADS:
        mod = types.ModuleType("position_rows_3864")
        sys.modules[mod.__name__] = mod
        try:
            exec(compile(HEADER + module, "<row>", "exec"), mod.__dict__)  # noqa: S102 - written here
            out.append(str(mod.f(payload)))
        except (TypeError, ValueError):
            pass
        finally:
            sys.modules.pop(mod.__name__, None)
    return out


class TestTheSecondReview:
    @pytest.mark.parametrize("label", sorted(REVIEW_ROWS))
    def test_the_guard(self, label):
        module, expected = REVIEW_ROWS[label]
        assert flagged(module) is expected, label

    @pytest.mark.parametrize("label", sorted(REVIEW_ROWS))
    def test_the_row_is_true_in_a_browser(self, label):
        positions.require_browser()
        module, expected = REVIEW_ROWS[label]
        outs = _outputs(module)
        verdicts = positions.browser_verdicts(outs, deadline=60)
        assert any(verdicts) is expected, (label, [o for o, v in zip(outs, verdicts) if v][:2])


class TestTheRows:
    @pytest.mark.parametrize("label", sorted(ROWS))
    def test_the_verdict_is_the_one_a_run_reaches(self, label):
        module, expected = ROWS[label]
        assert unsafe(module) is expected, f"the row is wrong: {label}"
        assert flagged(module) is expected, label

    @pytest.mark.parametrize("label", sorted(CONSERVATIVE))
    def test_a_conservative_row_is_flagged_and_safe(self, label):
        assert not unsafe(CONSERVATIVE[label]), label
        assert flagged(CONSERVATIVE[label]), label

    @pytest.mark.parametrize("label", sorted(k for k, (_m, v) in ROWS.items() if v))
    def test_the_finding_is_pass_b_s(self, label):
        """Pass A (the guard as it was) is unchanged; the union only gains."""
        module = HEADER + ROWS[label][0]
        assert taint._Module(module, call_binding=True).findings(), label


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

S = taint
#: text (literals and symbols) -> (every part fits, the state it ends in)
WALKS = {
    "<p>" + S.SYM_ESCAPED + "</p>": (True, "text"),
    '<a title="' + S.SYM_ESCAPED: (True, "quoted"),
    '<a href="' + S.SYM_ESCAPED: (False, "unknown"),
    '<a href="' + S.SYM_VALUE: (True, "quoted"),
    "<a href=" + S.SYM_VALUE: (False, "unknown"),
    "<a " + S.SYM_ESCAPED: (False, "unknown"),
    "<" + S.SYM_ESCAPED: (False, "unknown"),
    "<" + S.SYM_DIGITS: (True, "tag"),
    "<a" + S.SYM_ATTRS + ">": (True, "text"),
    '<a title="' + S.SYM_ATTRS: (False, "unknown"),
    '<a title="' + S.SYM_MARKUP: (False, "unknown"),
    "<p>" + S.SYM_MARKUP + S.SYM_ESCAPED: (True, "text"),
    "<a " + S.SYM_WORDS + ' href="' + S.SYM_VALUE: (True, "quoted"),
    "<a href=" + S.SYM_WORDS: (False, "unknown"),
    # a part it cannot class: nothing positioned after it has a known position
    S.SYM_UNKNOWN + "<p>": (True, "unknown"),
    S.SYM_UNKNOWN + S.SYM_ESCAPED: (False, "unknown"),
    S.SYM_UNKNOWN + S.SYM_DIGITS: (True, "unknown"),
    S.SYM_REFUSED: (False, "unknown"),
    # comments, script and style bodies, JSON data blocks
    "<!-- " + S.SYM_ESCAPED + " -->": (True, "text"),
    "<!-- " + S.SYM_MARKUP: (False, "unknown"),
    "<script>var a=" + S.SYM_ESCAPED: (False, "unknown"),
    "<script>var a=" + S.SYM_JSON: (True, "raw"),
    '<script type="application/json">' + S.SYM_ESCAPED: (True, "raw"),
    "<style>" + S.SYM_VALUE: (False, "unknown"),
    "<script>a</script><p>" + S.SYM_ESCAPED: (True, "text"),
    # spellings: each is read
    S.CHOICE_OPEN + "<p>" + S.CHOICE_SEP + '<a title="' + S.CHOICE_CLOSE + S.SYM_ESCAPED: (
        True,
        "unknown",
    ),
    S.CHOICE_OPEN + "<p>" + S.CHOICE_SEP + "<a href=" + S.CHOICE_CLOSE + S.SYM_VALUE: (
        False,
        "unknown",
    ),
}


class TestTheWalk:
    @pytest.mark.parametrize("text", sorted(WALKS), ids=repr)
    def test_the_walk(self, text):
        assert taint._walk(text) == WALKS[text]

    def test_a_class_is_the_weakest_that_fits(self):
        assert taint._text_class("") == S.SYM_WORDS  # a word that may be empty
        assert taint._text_class("12") == S.SYM_DIGITS
        assert taint._text_class(" hidden") == S.SYM_WORDS
        assert taint._text_class(' title="' + S.SYM_ESCAPED + '"') == S.SYM_ATTRS
        assert taint._text_class(' href="' + S.SYM_ESCAPED + '"') == S.SYM_MARKUP
        assert taint._text_class("<b>" + S.SYM_ESCAPED + "</b>") == S.SYM_MARKUP
        assert taint._text_class('<a href="') == S.SYM_UNKNOWN

    def test_too_many_spellings_are_read_as_their_class(self):
        texts = [f"<i{n}>" for n in range(taint._SPELLINGS + 1)]
        assert taint._summarize(texts) == S.SYM_MARKUP
        assert taint._summarize(texts[:3]).startswith(S.CHOICE_OPEN)

    def test_a_number_from_a_math_function_is_digits(self):
        mod = taint._Module(
            "import math\ndef f(x):\n    return mark_safe(f'{math.cos(len(x)) + 1}')\n",
            call_binding=True,
        )
        scope = next(s for s in mod.scopes if s.name == "f")
        field = scope.sinks[0].args[0].values[0].value
        assert mod._text_b(field, scope) == S.SYM_DIGITS

    def test_a_part_it_cannot_read_sits_in_element_text_only(self):
        """Also where it is the last part: a slice of the rendered body (clean, unreadable) in a
        quoted value is reported, in element text it is not (#3864)."""
        head = "class H:\n    def render(self, args, content, context):\n        return _safe("
        in_value = head + "'<a title=\"' + content[:3] + '\">l</a>')\n"
        in_text = head + "'<p>' + content[:3] + '</p>')\n"
        module = "djust.components.rust_handlers"
        assert [f.function for f in find_unsafe_markup(in_value, module)] == ["H.render"]
        assert find_unsafe_markup(in_text, module) == []

    def test_the_rendered_body_cut_at_an_offset_is_unknown(self):
        """A block handler's rendered body (``ENGINE_RENDERED_PARAMETERS``) is markup that starts
        and ends in element text; a slice of it at an offset is not, so a value escaper after it is
        reported (the three handlers of ``rust_handlers.py`` that do this are audited)."""
        src = (
            "from djust.components.utils import safe_url\n"
            "class H:\n    def render(self, args, content, context):\n"
            "        return _safe(content + '<a href=\"' + safe_url(args[0]) + '\">')\n"
            "class G:\n    def render(self, args, content, context):\n"
            "        return _safe(content[:3] + '<a href=\"' + safe_url(args[0]) + '\">')\n"
        )
        found = find_unsafe_markup(src, "djust.components.rust_handlers")
        assert [f.function for f in found] == ["G.render"]


# ---------------------------------------------------------------------------
# The randomized differential
# ---------------------------------------------------------------------------


def _pass_a(case) -> bool:
    """The guard as it was (pass A) flags the case for some spelling."""
    return any(taint._Module(positions.build(*case, k)).findings() for k in range(len(case[1])))


def _in_place(case) -> bool:
    """The guard flags each spelling's text written in place (the rule the case applies)."""
    producer, spellings, value, suffix, _how = case
    return any(
        find_unsafe_markup(
            positions.HEADER
            + f"def f(x):\n    return mark_safe({s!r} + {positions.VALUES[value]} + {suffix!r})\n"
        )
        for s in set(spellings)
    )


class TestTheDifferential:
    """Generated modules, each RUN once per spelling of its prefix (about 2 s per 300)."""

    @pytest.mark.parametrize("seed", [3864, 2])
    def test_no_module_a_payload_gets_out_of_is_cleared(self, seed):
        result = positions.survey(300, seed)
        assert result["false_negatives"] == [], result["false_negatives"][:3]
        assert result["disagree"] == [], "the guard read K"
        assert result["unsafe"] >= 80, result["unsafe"]  # not vacuous

    @pytest.mark.parametrize("seed", [3864])
    def test_no_module_a_browser_lets_a_payload_out_of_is_cleared(self, seed):
        """The same, judged by a browser's parser (jsdom through node; its run is bounded by a
        deadline and a heap cap): the tokenizer states Python's parser does not follow."""
        positions.require_browser()
        result = positions.survey(300, seed, browser=True)
        assert result["false_negatives"] == [], result["false_negatives"][:3]
        assert result["unsafe"] >= 100, result["unsafe"]

    @pytest.mark.parametrize("seed", [3864])
    def test_what_it_flags_beyond_a_run_is_what_it_flags_written_in_place(self, seed):
        """Reading the text in front never flags more than writing that text in place does (the
        documented position rules), beyond what the guard as it was flags, except a function the
        file replaces, which is not followed."""
        result = positions.survey(300, seed)
        extra = [
            case
            for case in result["false_positives"]
            if case[0] != "monkeypatched" and not _in_place(case) and not _pass_a(case)
        ]
        assert extra == [], extra[:3]


class TestTheGateOffListApplies:
    def test_every_mutation_text_still_applies(self):
        spec = json.loads((ROOT / "scripts/safe-markup-position-gateoff.json").read_text())
        assert len(spec["mutations"]) >= 15
        for entry in spec["mutations"]:
            source = (ROOT / entry["file"]).read_text()
            found = source.count(entry["old"])
            assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
            assert entry["old"] != entry["new"], entry["label"]


class TestTheBrowserOracleRunsInCI:
    """The jsdom rows skip where node and jsdom are missing; one CI job installs them and turns
    that skip into a failure, so they run (#4212)."""

    def test_the_skip_fails_when_the_oracle_is_required(self, monkeypatch):
        monkeypatch.setattr(positions, "browser_available", lambda: False)
        monkeypatch.setenv(positions.REQUIRE_BROWSER_ENV, "1")
        with pytest.raises(pytest.fail.Exception):
            positions.require_browser()
        monkeypatch.delenv(positions.REQUIRE_BROWSER_ENV)
        with pytest.raises(pytest.skip.Exception):
            positions.require_browser()

    def test_a_ci_job_installs_jsdom_and_runs_the_browser_rows(self):
        import yaml

        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((root / ".github/workflows/test.yml").read_text())
        jobs = []
        for name, job in workflow["jobs"].items():
            runs = [step.get("run", "") for step in job.get("steps", [])]
            if any("npm ci" in r for r in runs) and any(
                "test_safe_markup_position_3864.py" in r
                and f"{positions.REQUIRE_BROWSER_ENV}=1" in r
                for r in runs
            ):
                jobs.append(name)
        assert jobs, "no job installs jsdom and runs the browser rows with the oracle required"
