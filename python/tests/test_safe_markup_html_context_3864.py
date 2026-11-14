"""An escaper that only HTML-escapes, written where that is not enough (#3864).

``escape`` / ``conditional_escape`` / ``html.escape`` / ``attr_text`` make a value safe as element
text and inside an ordinary quoted attribute. They do not make it safe

* at the start of a URL attribute (``href="{escape(x)}"``: ``javascript:alert(1)`` has nothing to
  escape),
* inside an event handler (the HTML parser turns ``&#x27;`` back into a quote before the script
  runs), a ``<script>`` or ``<style>`` body (entities are not decoded there: the text is code) or
  ``srcdoc`` (decoded once, then parsed as a document),
* as an unquoted attribute value (a space starts a new attribute).

The guard of ``main`` trusted them in every position (``TestKnownGaps`` of
``test_safe_markup_guard_3864.py`` pinned it). Pass B of the guard now reads the literal text in
front of such an output (``_Module._html_context``) in the four builders that give a part a position
(an f-string, a ``+`` chain, ``%`` and ``str.format``). It only adds findings: pass A is untouched.

Every row here is RUN against the hostile payloads of the call fuzz and its output parsed, so the
verdict the test expects is the one a browser would reach, not one read off the source.

Refs #3864.
"""

from __future__ import annotations

import pytest
import safe_markup_call_fuzz_3864 as fuzz

from scripts.lib import safe_markup_taint as taint
from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.components

context = taint._Module._html_context


def flagged(module: str) -> bool:
    return bool(find_unsafe_markup(module))


def unsafe(module: str) -> bool:
    return fuzz.run(module)[0]


def module(expr: str) -> str:
    """``f(x)`` returning ``mark_safe(<expr>)``, under the call fuzz's imports."""
    return fuzz.HEADER + f"def f(x):\n    return mark_safe({expr})\n"


# ---------------------------------------------------------------------------
# The position reader
# ---------------------------------------------------------------------------

#: literal text in front of the field -> where HTML escaping is not enough (``None``: it is enough)
CONTEXTS: dict[str, str | None] = {
    # a URL attribute, at the start of its value
    '<a href="': "url",
    "<a href='": "url",
    '<A HREF = "': "url",
    '<a\nhref\n=\n"  ': "url",
    '<a\thref\t=\t"': "url",
    '<a/href="': "url",
    '<a class="c" href="': "url",
    '<a x=y href="': "url",
    '<a title="a>b" href="': "url",
    '<a title="href=" href="': "url",
    '<a href="x">t</a><a href="': "url",
    '<!-- c --><a href="': "url",
    '<script>x</script><a href="': "url",
    '<script type="application/json">x</script><a href="': "url",
    '<img src="': "url",
    '<img srcset="': "url",
    '<form action="': "url",
    '<button formaction="': "url",
    '<svg><a xlink:href="': "url",
    '<script src="': "url",
    # a literal ``javascript:`` in front makes the rest of the value code
    '<a href="javascript:go(': "url",
    '<a href=" JavaScript:': "url",
    # ... and not past the start, or behind a part the guard cannot read
    '<a href="/u/': None,
    '<a href="\x00': None,
    '<a href="\x00javascript:': None,
    '<a href="&#32;': None,
    # not a URL attribute
    '<a title="': None,
    '<a data-href="': "url",  # the client follows it (a table row's link)
    '<a data-hrefs="': None,
    '<a href="\x01\t ': "url",  # a browser drops leading control characters and spaces
    '<a hrefx="': None,
    '<a xhref="': None,
    '<a href="x" title="': None,
    "<a title='href=\"' x=\"": None,
    '<p>a href="': None,
    # an event handler
    "<b onclick=\"go('": "handler",
    '<b ONCLICK="': "handler",
    '<b title="x" onmouseover=\'a': "handler",
    '<b on="': None,
    # a script or style body
    "<script>var a=": "script",
    "<SCRIPT>": "script",
    '<script type="x">': "script",
    '<script type="module">': "script",
    '<script type="text/javascript">': "script",
    "<script>if(a<b){": "script",
    '<a href="x"><script>': "script",
    "<style>.c{color:": "style",
    "<script>a</script><b>": None,
    '<script>"</script>': None,
    "<style></style>": None,
    # a JSON data block is never run, and an HTML escaper leaves no ``<`` to close it with
    '<script type="application/json">': None,
    '<script type="application/json" class="c">a': None,
    "<script type='application/ld+json'>": None,
    "<script type=application/json>": None,
    '<script type=" Application/JSON ">': None,
    # ... only when the literal text says so, by the FIRST ``type`` (the one a browser reads)
    '<script type="\x00">': "script",
    '<script data-type="application/json">': "script",
    '<script type="module" type="application/json">': "script",
    '<script type="application/json"></script><script>': "script",
    '<style type="application/json">': "style",
    # srcdoc
    '<iframe srcdoc="': "srcdoc",
    # an unquoted value
    "<a title=": "unquoted",
    "<a title=ab": "unquoted",
    "<a title = ": "unquoted",
    "<a href=": "unquoted",
    '<a x=y/href="': "unquoted",  # ``/`` does not end an unquoted value
    "<a title=ab ": None,
    # element text and the rest of a tag
    "": None,
    "<p>": None,
    "<textarea>": None,
    "<a ": None,
    "<a title": None,
}


class TestThePositionReader:
    @pytest.mark.parametrize("prefix", sorted(CONTEXTS), ids=repr)
    def test_the_context_after_a_literal_prefix(self, prefix):
        assert context(prefix) == CONTEXTS[prefix]

    def test_every_url_attribute_the_fuzz_runs_is_one_the_guard_knows(self):
        assert set(fuzz.URL_ATTRIBUTES) <= taint.URL_ATTRIBUTES
        for name in sorted(taint.URL_ATTRIBUTES):
            assert context(f'<a {name}="') == "url", name
            assert context(f'<a {name.upper()}="') == "url", name


# ---------------------------------------------------------------------------
# Every escaper in every placement of the call fuzz, written in place
# ---------------------------------------------------------------------------

HTML_ONLY = ("escape", "cond_escape", "attr_text")
#: Unsafe when run and still cleared (the same on ``main``): where the guard does not read. An HTML
#: escaper between attributes, as an attribute or tag name (flagging the position flagged correct
#: sites of the tree: a comment, a slot sentinel, the nav handlers), and anything in a ``style``
#: attribute (CSS: eleven sites of the tree put an escaped value there; ``safe_css_*`` is the rule).
REMAINING = {
    *((s, p) for s in ("escape", "cond_escape") for p in ("between", "attr_name", "tag_name")),
    *((s, "style_attr") for s in (*HTML_ONLY, "url")),
}
#: Flagged although no payload of the fuzz gets through: an escaped value inside a quoted JS string
#: of a script body (a trailing backslash still eats the closing quote), and a context-sensitive
#: escaper in a comment (flagged by ``main`` too: a comment is no position it knows).
CONSERVATIVE = {
    *((s, "script") for s in (*HTML_ONLY, "url")),
    ("attr_text", "comment"),
    ("url", "comment"),
}
MATRIX = [(s, p) for s in (*HTML_ONLY, "url", "digits", "literal") for p in fuzz.PLACEMENTS]


class TestEveryPlacement:
    @pytest.mark.parametrize("source,placement", MATRIX)
    def test_the_verdict_is_the_one_a_run_reaches(self, source, placement):
        mod = fuzz.build(source, [], placement)
        hole, found = unsafe(mod), flagged(mod)
        if (source, placement) in REMAINING:
            assert hole and not found, "the guard learned it: take it out of REMAINING"
        elif (source, placement) in CONSERVATIVE:
            assert found and not hole, "no longer conservative: take it out of CONSERVATIVE"
        else:
            assert found is hole

    def test_the_new_findings_are_the_positions_this_adds(self):
        """What pass B finds and the guard of ``main`` (pass A) does not, over the matrix."""
        new = {
            (s, p)
            for s, p in MATRIX
            if flagged(fuzz.build(s, [], p)) and not taint._Module(fuzz.build(s, [], p)).findings()
        }
        urls = {"url"} | {"url_" + n.replace(":", "_") for n in fuzz.URL_ATTRIBUTES[1:]}
        code = {"event", "script", "script_unquoted", "style", "srcdoc"}
        expected = {(s, p) for s in HTML_ONLY for p in urls | code}
        expected |= {(s, "unquoted") for s in ("escape", "cond_escape")}
        expected |= {("url", p) for p in code}
        assert new == expected


# ---------------------------------------------------------------------------
# Other spellings of the same hole, and the four builders
# ---------------------------------------------------------------------------

E = "escape(x)"
#: label -> the expression handed to ``mark_safe``. Each is unsafe when run.
SPELLINGS = {
    "upper-case names": f"f'<A HREF=\"{{{E}}}\">l</A>'",
    "single quotes": f"f\"<a href='{{{E}}}'>l</a>\"",
    "spaces around =": f"f'<a href = \"{{{E}}}\">l</a>'",
    "a newline and a tab in the tag": f"f'<a\\n\\thref=\"{{{E}}}\">l</a>'",
    "a slash between attributes": f"f'<a/href=\"{{{E}}}\">l</a>'",
    "leading white space in the value": f"f'<a href=\"  {{{E}}}\">l</a>'",
    "a data- attribute the client follows": f"f'<a data-href=\"{{{E}}}\">l</a>'",
    "after another attribute": f'f\'<a class="c" title="t" href="{{{E}}}">l</a>\'',
    "after a > inside a quoted value": f'f\'<a title="a>b" href="{{{E}}}">l</a>\'',
    "after a closed script": f"f'<script>1</script><a href=\"{{{E}}}\">l</a>'",
    "after a JSON data block": f'f\'<script type="application/json">1</script><a href="{{{E}}}">l</a>\'',
    "after a comment": f"f'<!-- c --><a href=\"{{{E}}}\">l</a>'",
    "a mixed-case handler": f"f'<b OnClick=\"go(\\'{{{E}}}\\')\">b</b>'",
    "a handler in single quotes": f"f\"<b onclick='go(&quot;{{{E}}}&quot;)'>b</b>\"",
    "an upper-case script": f"f'<SCRIPT>var a={{{E}}};</SCRIPT>'",
    "a script with a type that runs": f"f'<script type=\"module\">var a={{{E}}};</script>'",
    "a data-block type on the second type attribute": (
        f'f\'<script type="module" type="application/json">var a={{{E}}};</script>\''
    ),
    "a + chain": f"'<a href=\"' + {E} + '\">l</a>'",
    "a + chain with the tag split": f"'<a ' + 'href=' + '\"' + {E} + '\">l</a>'",
    "a % format": f"'<a href=\"%s\">l</a>' % {E}",
    "a % format with two arguments": f"'<a class=\"%s\" href=\"%s\">l</a>' % ('c', {E})",
    "str.format": f"'<a href=\"{{}}\">l</a>'.format({E})",
    "str.format by keyword": f"'<a href=\"{{u}}\">l</a>'.format(u={E})",
    "str.format by index": f"'<a class=\"{{0}}\" href=\"{{1}}\">l</a>'.format('c', {E})",
    "a handler in a + chain": f"'<b onclick=\"go(\\'' + {E} + '\\')\">b</b>'",
    "a script body in a % format": f"'<script>var a=%s;</script>' % {E}",
    "an unquoted value in str.format": f"'<b title={{}}>b</b>'.format({E})",
    "conditional_escape": "f'<a href=\"{conditional_escape(x)}\">l</a>'",
    "attr_text": "f'<a href=\"{attr_text(x)}\">l</a>'",
    "an escaper around an escaper": f"f'<a href=\"{{conditional_escape({E})}}\">l</a>'",
    "a checked URL in a handler": "f'<b onclick=\"go(\\'{safe_url(x)}\\')\">b</b>'",
    "a checked URL in a script body": "f'<script>var a={safe_url(x)};</script>'",
}
#: label -> a whole ``f``: the output reaches the position through a name or a parameter
THROUGH = {
    "a name": "def f(x):\n    u = escape(x)\n    return mark_safe(f'<a href=\"{u}\">l</a>')\n",
    "a name of a name": (
        "def f(x):\n    u = escape(x)\n    v = u\n    return mark_safe(f'<a href=\"{v}\">l</a>')\n"
    ),
    "one arm of a conditional": (
        "def f(x):\n    u = safe_url(x) if len(x) > 99 else escape(x)\n"
        "    return mark_safe(f'<a href=\"{u}\">l</a>')\n"
    ),
    "a branch that rebinds the name": (
        "def f(x):\n    u = safe_url(x)\n    if len(x) < 99:\n        u = escape(x)\n"
        "    return mark_safe(f'<a href=\"{u}\">l</a>')\n"
    ),
    "a walrus": "def f(x):\n    return mark_safe(f'<a href=\"{(u := escape(x))}\">l</a>')\n",
    "a helper's parameter": (
        "def tag(u):\n    return f'<a href=\"{u}\">l</a>'\n"
        "def f(x):\n    return mark_safe(tag(escape(x)))\n"
    ),
    "a lambda's parameter": (
        "tag = lambda u: f'<a href=\"{u}\">l</a>'\ndef f(x):\n    return mark_safe(tag(escape(x)))\n"
    ),
    "an f-string leaf of a + chain": (
        "def f(x):\n    return mark_safe('<a href=\"' + f'{escape(x)}' + '\">l</a>')\n"
    ),
    "an f-string leaf with text of its own": (
        "def f(x):\n    return mark_safe('<a ' + f'href=\"{escape(x)}\">l</a>')\n"
    ),
    "an f-string nested in an f-string": (
        'def f(x):\n    return mark_safe(f\'<a href="{f"{escape(x)}"}">l</a>\')\n'
    ),
    "a + chain nested in an f-string": (
        'def f(x):\n    return mark_safe(f\'<b {("onclick=" + chr(34) + escape(x))}">b</b>\')\n'
    ),
    "a name that holds an f-string of it": (
        "def f(x):\n    u = f'{escape(x)}'\n    return mark_safe(f'<a href=\"{u}\">l</a>')\n"
    ),
    "a name that holds a + chain of it": (
        "def f(x):\n    u = '' + escape(x)\n    return mark_safe(f'<a href=\"{u}\">l</a>')\n"
    ),
    "str() of it": ("def f(x):\n    return mark_safe(f'<a href=\"{str(escape(x))}\">l</a>')\n"),
    "an f-string as a % argument": (
        "def f(x):\n    return mark_safe('<a href=\"%s\">l</a>' % f'{escape(x)}')\n"
    ),
    "html.escape": (
        "import html\ndef f(x):\n    return mark_safe(f'<a href=\"{html.escape(x)}\">l</a>')\n"
    ),
    "an alias of the escaper": (
        "from django.utils.html import escape as e\n"
        "def f(x):\n    return mark_safe(f'<a href=\"{e(x)}\">l</a>')\n"
    ),
}

#: label -> the expression handed to ``mark_safe``. Each is safe when run, and not flagged.
CLEAN = {
    "an ordinary quoted attribute": f"f'<a title=\"{{{E}}}\">l</a>'",
    "element text": f"f'<p>{{{E}}}</p>'",
    "a data- attribute": f"f'<a data-id=\"{{{E}}}\">l</a>'",
    "past the start of a URL (a path segment)": f"f'<a href=\"/u/{{{E}}}\">l</a>'",
    "a query value": f"f'<a href=\"?q={{{E}}}\">l</a>'",
    "the checked URL in the href": "f'<a href=\"{safe_url(x)}\">l</a>'",
    "an escaper around the checked URL": "f'<a href=\"{conditional_escape(safe_url(x))}\">l</a>'",
    "a title after an href": f'f\'<a href="/x" title="{{{E}}}">l</a>\'',
    "text after a script closed": f"f'<script>1</script><p>{{{E}}}</p>'",
    "text that looks like an attribute": f"f'<p>href=\"{{{E}}}\"</p>'",
    "digits in an href": "f'<a href=\"{len(x)}\">l</a>'",
    "a % format, ordinary attribute": f"'<a title=\"%s\">l</a>' % {E}",
    "str.format, ordinary attribute": f"'<a title=\"{{}}\">l</a>'.format({E})",
    "a + chain, ordinary attribute": f"'<a title=\"' + {E} + '\">l</a>'",
    "an f-string leaf past the start of a URL": f"'<a href=\"/u/' + f'{{{E}}}' + '\">l</a>'",
    "an inner f-string that starts with a path": f'f\'<a href="{{f"/u/{{{E}}}"}}">l</a>\'',
    "an inner + whose left side is a path": f'f\'<a href="{{("/u/" + {E})}}">l</a>\'',
    "str() of it in an ordinary attribute": f"f'<a title=\"{{str({E})}}\">l</a>'",
}
#: label -> a whole ``f`` that is safe when run, and not flagged
CLEAN_MODULES = {
    "a local function named str is not the builtin": (
        "def str(v):\n    return 'k'\n"
        "def f(x):\n    return mark_safe(f'<a href=\"{str(escape(x))}\">l</a>')\n"
    ),
    "a name rebound to the checked URL before it is written": (
        "def f(x):\n    u = escape(x)\n    u = safe_url(x)\n"
        "    return mark_safe(f'<a href=\"{u}\">l</a>')\n"
    ),
    "a helper that puts its parameter in text": (
        "def bold(u):\n    return f'<b>{u}</b>'\ndef f(x):\n    return mark_safe(bold(escape(x)))\n"
    ),
}


class TestOtherSpellings:
    @pytest.mark.parametrize("label", sorted(SPELLINGS))
    def test_a_spelling_of_the_hole_is_unsafe_and_flagged(self, label):
        mod = module(SPELLINGS[label])
        assert unsafe(mod), label
        assert flagged(mod), label

    @pytest.mark.parametrize("label", sorted(THROUGH))
    def test_the_output_reaches_the_position_another_way(self, label):
        mod = fuzz.HEADER + THROUGH[label]
        assert unsafe(mod), label
        assert flagged(mod), label

    @pytest.mark.parametrize("label", sorted(CLEAN))
    def test_a_clean_twin_is_safe_and_not_flagged(self, label):
        mod = module(CLEAN[label])
        assert not unsafe(mod), label
        assert not flagged(mod), label

    @pytest.mark.parametrize("label", sorted(CLEAN_MODULES))
    def test_a_clean_module_is_safe_and_not_flagged(self, label):
        mod = fuzz.HEADER + CLEAN_MODULES[label]
        assert not unsafe(mod), label
        assert not flagged(mod), label

    @pytest.mark.parametrize("kind", ["application/json", "application/ld+json"])
    def test_a_json_data_block_is_not_a_script_body(self, kind):
        """The oracle of the fuzz reads every script body as code, so it is not asked here: a data
        block is never run. What an HTML escaper owes it is that nothing closes the element."""
        mod = module(f"f'<script type=\"{kind}\">{{{E}}}</script><p>after</p>'")
        assert not flagged(mod)
        namespace: dict = {}
        exec(compile(mod, "<row>", "exec"), namespace)  # noqa: S102 - the row above
        for payload in (*fuzz.PAYLOADS, "</script><script>INJ()</script>", "</SCRIPT ><x>"):
            out = str(namespace["f"](payload))
            head, sep, tail = out.partition("</script>")
            assert sep and tail == "<p>after</p>" and "<" not in head[1:], payload
        # the same text in a script that runs is flagged
        assert flagged(module(f"f'<script>{{{E}}}</script>'"))

    @pytest.mark.parametrize("placement", ["url", "event", "script_unquoted", "style", "srcdoc"])
    def test_the_finding_is_pass_b_s_alone(self, placement):
        """Pass A is the guard as it was (it cleared these, as ``main`` did): only pass B asks where
        an HTML escaper's output sits, so the union can only gain findings."""
        mod = fuzz.build("escape", [], placement)
        assert taint._Module(mod).findings() == []
        assert taint._Module(mod, call_binding=True).findings() != []


class TestTheGateOffListApplies:
    def test_every_mutation_text_still_applies(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        spec = json.loads((root / "scripts/safe-markup-html-context-gateoff.json").read_text())
        assert len(spec["mutations"]) >= 30
        for mutation in spec["mutations"]:
            text = (root / mutation["file"]).read_text()
            assert text.count(mutation["old"]) == 1, mutation["label"]
