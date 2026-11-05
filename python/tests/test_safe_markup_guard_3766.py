"""The guard against markup built from raw values and then marked safe (#3766, review of #3775).

The first guard (a test in ``test_theme_icon_size_3766.py``) matched ``mark_safe(<f-string>)``
literally. Run on ``origin/main``'s ``theme_components.py`` it found nothing, although the file held
#3766: the f-strings sat in a dict and the call was ``mark_safe(icons.get(name, ""))``. This one is
``scripts/lib/safe_markup_taint.py``: it follows the value through local variables, containers,
``+=`` / ``append`` and ``join`` inside one function. What it reads and what it cannot see is stated
in that module's docstring; the allowlist below records every flagged site that was audited and
kept, with the reason.

What each class proves:

* ``TestEveryShapeIsCaught`` pairs each shape with its CLEAN twin (the same code with the value
  escaped, ``format_html``-ed or ``int``-coerced): the dirty one must be flagged and the clean one
  must not, so a detector that matches everything or nothing fails.
* ``TestTheOriginalCode`` runs the detector on the verbatim pre-fix ``theme_icon`` and the
  pre-fix ``theme_form`` / ``theme_form_errors`` / ``_passthrough_attrs`` shapes: the reported bug
  and the two that came out of the review.
* ``TestTheming`` runs it on every file under ``djust/theming``: nothing flagged but what the
  allowlist names, and no allowlist entry that is no longer flagged.

Refs #3766, #3775, #1859, #1468.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.lib.safe_markup_taint import find_unsafe_markup

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[2]
THEMING = ROOT / "python/djust/theming"

#: A flagged site that was read and kept: ``(path relative to the repo, function) -> reason``.
#: Empty: the sites the detector found under ``djust/theming`` were fixed instead.
AUDITED: dict[tuple[str, str], str] = {}

PRELUDE = (
    "from django.utils.html import conditional_escape, escape, format_html\n"
    "from django.utils.safestring import SafeString, SafeText, mark_safe\n"
)

#: label -> (dirty source, clean twin). The twin differs only in how the raw value reaches the string.
SHAPES = {
    "dict-get-the-3766-shape": (
        'def f(name, size):\n    icons = {"a": f\'<svg width="{size}">\'}\n'
        '    return mark_safe(icons.get(name, ""))\n',
        'def f(name, size):\n    icons = {"a": format_html(\'<svg width="{}">\', size)}\n'
        '    return mark_safe(icons.get(name, ""))\n',
    ),
    "dict-subscript": (
        'def f(k, x):\n    d = {}\n    d[k] = f"<i>{x}</i>"\n    return mark_safe(d[k])\n',
        'def f(k, x):\n    d = {}\n    d[k] = format_html("<i>{}</i>", x)\n    return mark_safe(d[k])\n',
    ),
    "local-variable": (
        'def f(x):\n    s = f"<a {x}>"\n    return mark_safe(s)\n',
        'def f(x):\n    s = format_html("<a {}>", x)\n    return mark_safe(s)\n',
    ),
    "augmented-assignment": (
        'def f(x):\n    s = "<a "\n    s += x\n    return mark_safe(s)\n',
        'def f(x):\n    s = "<a "\n    s += escape(x)\n    return mark_safe(s)\n',
    ),
    "join-of-a-list-literal": (
        'def f(x, y):\n    return mark_safe("".join([x, y]))\n',
        'def f(x, y):\n    return mark_safe("".join([escape(x), "<b>"]))\n',
    ),
    "join-of-a-generator": (
        'def f(items):\n    return mark_safe("".join(f"<li>{i}</li>" for i in items))\n',
        'def f(items):\n    return mark_safe("".join(format_html("<li>{}</li>", i) for i in items))\n',
    ),
    "format-bound-to-a-variable": (
        'def f(x):\n    t = "<a {}>".format(x)\n    return mark_safe(t)\n',
        'def f(x):\n    t = "<a {}>".format(escape(x))\n    return mark_safe(t)\n',
    ),
    "format-in-place": (
        'def f(x):\n    return mark_safe("<a {}>".format(x))\n',
        'def f(x):\n    return mark_safe("<a {}>".format(escape(x)))\n',
    ),
    "percent": (
        'def f(x):\n    return mark_safe("<a %s>" % x)\n',
        'def f(x):\n    return mark_safe("<a %s>" % escape(x))\n',
    ),
    "concatenation": (
        'def f(x):\n    return mark_safe("<b>" + x + "</b>")\n',
        'def f(x):\n    return mark_safe("<b>" + escape(x) + "</b>")\n',
    ),
    "keyword-argument": (
        'def f(x):\n    return mark_safe(s=f"<a {x}>")\n',
        'def f(x):\n    return mark_safe(s=f"<a {escape(x)}>")\n',
    ),
    "import-alias": (
        "from django.utils.safestring import mark_safe as ms\n"
        'def f(x):\n    return ms(f"<a {x}>")\n',
        "from django.utils.safestring import mark_safe as ms\n"
        'def f(x):\n    return ms(f"<a {escape(x)}>")\n',
    ),
    "assignment-alias": (
        'ms = mark_safe\ndef f(x):\n    return ms(f"<a {x}>")\n',
        'ms = mark_safe\ndef f(x):\n    return ms(f"<a {escape(x)}>")\n',
    ),
    "alias-of-an-alias": (
        'a = mark_safe\nb = a\ndef f(x):\n    return b(f"<a {x}>")\n',
        'a = mark_safe\nb = a\ndef f(x):\n    return b(f"<a {escape(x)}>")\n',
    ),
    "SafeString": (
        'def f(x):\n    return SafeString(f"<a {x}>")\n',
        'def f(x):\n    return SafeString(f"<a {escape(x)}>")\n',
    ),
    "SafeText": (
        'def f(x):\n    return SafeText(f"<a {x}>")\n',
        'def f(x):\n    return SafeText(f"<a {escape(x)}>")\n',
    ),
    "attribute-call": (
        'from django.utils import safestring\ndef f(x):\n    return safestring.mark_safe(f"<a {x}>")\n',
        'from django.utils import safestring\ndef f(x):\n    return safestring.mark_safe(f"<a {escape(x)}>")\n',
    ),
    "list-append-then-join-the-theme_form-shape": (
        "def f(field_id):\n    lines = []\n    lines.append(f'<label for=\"{field_id}\">')\n"
        '    return mark_safe("\\n".join(lines))\n',
        "def f(field_id):\n    lines = []\n    lines.append(format_html('<label for=\"{}\">', field_id))\n"
        '    return mark_safe("\\n".join(lines))\n',
    ),
    "list-extend": (
        'def f(x):\n    lines = []\n    lines.extend([f"<i>{x}</i>"])\n    return mark_safe("".join(lines))\n',
        'def f(x):\n    lines = []\n    lines.extend([format_html("<i>{}</i>", x)])\n'
        '    return mark_safe("".join(lines))\n',
    ),
    "list-augmented": (
        'def f(x):\n    lines = ["<ul>"]\n    lines += [f"<li>{x}</li>"]\n    return mark_safe("".join(lines))\n',
        'def f(x):\n    lines = ["<ul>"]\n    lines += [format_html("<li>{}</li>", x)]\n'
        '    return mark_safe("".join(lines))\n',
    ),
    "list-insert": (
        'def f(x):\n    lines = ["<ul>"]\n    lines.insert(0, f"<li>{x}</li>")\n    return mark_safe("".join(lines))\n',
        'def f(x):\n    lines = ["<ul>"]\n    lines.insert(0, format_html("<li>{}</li>", x))\n'
        '    return mark_safe("".join(lines))\n',
    ),
    "bare-value-in-a-list": (
        'def f(x):\n    lines = ["<ul>"]\n    lines.append(x)\n    return mark_safe("".join(lines))\n',
        'def f(x):\n    lines = ["<ul>"]\n    lines.append(escape(x))\n    return mark_safe("".join(lines))\n',
    ),
    "conditional-expression": (
        'def f(x, c):\n    return mark_safe("<br>" if c else f"<a {x}>")\n',
        'def f(x, c):\n    return mark_safe("<br>" if c else f"<a {escape(x)}>")\n',
    ),
    "through-str": (
        'def f(x):\n    return mark_safe(str(f"<a {x}>"))\n',
        'def f(x):\n    return mark_safe(str(f"<a {escape(x)}>"))\n',
    ),
    "through-an-unknown-call": (
        'def f(x):\n    return mark_safe(dedent(f"<a {x}>"))\n',
        'def f(x):\n    return mark_safe(dedent(f"<a {escape(x)}>"))\n',
    ),
    "closure": (
        'def f(x):\n    s = f"<a {x}>"\n    def g():\n        return mark_safe(s)\n    return g\n',
        'def f(x):\n    s = format_html("<a {}>", x)\n    def g():\n        return mark_safe(s)\n    return g\n',
    ),
    "module-level": (
        'HTML = f"<a {open_value()}>"\nOUT = mark_safe(HTML)\n',
        'HTML = format_html("<a {}>", open_value())\nOUT = mark_safe(HTML)\n',
    ),
    "interpolated-format-string": (
        'def f(x):\n    return format_html(f"<a {x}>")\n',
        'def f(x):\n    return format_html("<a {}>", x)\n',
    ),
    "attribute-value": (
        'def f(obj):\n    return mark_safe(f"<a id={obj.id}>")\n',
        'def f(obj):\n    return mark_safe(f"<a id={int(obj.id)}>")\n',
    ),
    "loop-variable": (
        'def f(rows):\n    out = []\n    for r in rows:\n        out.append(f"<td>{r}</td>")\n'
        '    return mark_safe("".join(out))\n',
        'def f(rows):\n    out = []\n    for r in rows:\n        out.append(format_html("<td>{}</td>", r))\n'
        '    return mark_safe("".join(out))\n',
    ),
}

#: Code that must NOT be flagged: nothing in it carries a raw value into the string.
CLEAN = {
    "over-flag-from-the-review": 'def f(x):\n    return mark_safe("<b>" + escape(x) + "</b>")\n',
    "int-coerced": 'def f(x):\n    return mark_safe(f"<td width={int(x)}>")\n',
    "float-coerced": 'def f(x):\n    return mark_safe(f"<td width={float(x)}>")\n',
    "conditional_escape": 'def f(x):\n    return mark_safe(f"<a>{conditional_escape(x)}</a>")\n',
    "escape-aliased-on-import": (
        "from django.utils.html import escape as esc\n"
        'def f(x):\n    return mark_safe("<b>" + esc(x))\n'
    ),
    "template-output": "def f(tmpl, ctx):\n    return mark_safe(tmpl.render(ctx))\n",
    "template-output-from-an-interpolated-context": (
        "from django.template.loader import render_to_string\n"
        'def f(x):\n    return mark_safe(render_to_string("t.html", {"a": f"{x}"}))\n'
    ),
    "rendered-then-marked": (
        "def f(tmpl, ctx):\n    html = tmpl.render(ctx)\n    return mark_safe(html)\n"
    ),
    "constant-dict-lookup": (
        'BODIES = {"a": "<i>", "b": "<b>"}\n'
        'def f(name):\n    return mark_safe(BODIES.get(name, ""))\n'
        "def g(name):\n    return mark_safe(BODIES[name])\n"
    ),
    "a-parameter-passed-through": "def f(markup):\n    return mark_safe(markup)\n",
    "an-unknown-call": "def f():\n    return mark_safe(build())\n",
    "loop-over-literals": (
        'def f():\n    return [mark_safe(f"<b>{p}</b>") for p in ("a", "b")]\n'
        'def g():\n    for p in ("a", "b"):\n        yield mark_safe(f"<b>{p}</b>")\n'
    ),
    "join-of-literals": 'def f():\n    return mark_safe(", ".join(["<a>", "<b>"]))\n',
    "join-of-escaped": 'def f(items):\n    return mark_safe("".join(escape(i) for i in items))\n',
    "module-constant": 'TAG = "b"\ndef f():\n    return mark_safe(f"<{TAG}>")\n',
    "f-string-without-values": 'def f():\n    return mark_safe(f"<br>")\n',
    "format_html-lines": (
        'def f(a, b):\n    lines = ["<ul>"]\n    lines.append(format_html("<li>{}</li>", a))\n'
        '    lines += [format_html("<li>{}</li>", b)]\n    return mark_safe("\\n".join(lines))\n'
    ),
    "clean-augmented-string": (
        'def f(x):\n    s = "<a>"\n    s += escape(x)\n    s += "</a>"\n    return mark_safe(s)\n'
    ),
    "format_html-template-constant": 'def f(x):\n    return format_html("<a>{}</a>", x)\n',
    "a-recursive-name": 'def f(x):\n    s = ""\n    s += s\n    return mark_safe(s)\n',
}

#: Rules added after the re-review of #3775: each is a dirty/clean pair, so a rule that is
#: removed (or one that matches everything) fails by name. The label says which rule it pins.
SHAPES.update(
    {
        # --- what counts as an escaper is decided by ORIGIN, not spelling ----------------
        "origin-re.escape-is-not-an-html-escaper": (
            "import re\ndef f(x):\n    return mark_safe(f'<b data-x=\"{re.escape(x)}\">')\n",
            "import re\ndef f(x):\n    return mark_safe(f'<b data-x=\"{escape(x)}\">')\n",
        ),
        "origin-shlex.quote-is-not-an-html-escaper": (
            "import shlex\ndef f(x):\n    return mark_safe(f'<b data-x=\"{shlex.quote(x)}\">')\n",
            "import shlex\ndef f(x):\n    return mark_safe(f'<b data-x=\"{escape(x)}\">')\n",
        ),
        "origin-from-another-module": (
            'from re import escape\ndef f(x):\n    return mark_safe(f"<b>{escape(x)}</b>")\n',
            'def f(x):\n    return mark_safe(f"<b>{escape(x)}</b>")\n',
        ),
        "origin-locally-defined-escape": (
            'def escape(v):\n    return v\ndef f(x):\n    return mark_safe(f"<b>{escape(x)}</b>")\n',
            'def f(x):\n    return mark_safe(f"<b>{escape(x)}</b>")\n',
        ),
        "origin-parameter-named-escape": (
            'def f(escape, x):\n    return mark_safe(f"<b>{escape(x)}</b>")\n',
            'def f(esc, x):\n    return mark_safe(f"<b>{escape(x)}</b>")\n',
        ),
        "origin-assignment-alias-of-something-else": (
            'def f(x):\n    e = str.title\n    return mark_safe(f"<b>{e(x)}</b>")\n',
            'def f(x):\n    e = escape\n    return mark_safe(f"<b>{e(x)}</b>")\n',
        ),
        "origin-locally-defined-int": (
            'def int(v):\n    return v\ndef f(x):\n    return mark_safe(f"<b>{int(x)}</b>")\n',
            'def f(x):\n    return mark_safe(f"<b>{int(x)}</b>")\n',
        ),
        "origin-locally-defined-render_to_string": (
            "def render_to_string(v):\n    return v\n"
            'def f(x):\n    return mark_safe(render_to_string(f"<a {x}>"))\n',
            'def f(x):\n    return mark_safe(render_to_string("t.html", {"x": x}))\n',
        ),
        # --- numbers out of numbers --------------------------------------------------------
        "rule-min": (
            'def f(x):\n    return mark_safe(f"<td>{min(x, 5)}</td>")\n',
            'def f(x):\n    return mark_safe(f"<td>{min(int(x), 5)}</td>")\n',
        ),
        "rule-max": (
            'def f(x):\n    return mark_safe(f"<td>{max(x, 0)}</td>")\n',
            'def f(x):\n    return mark_safe(f"<td>{max(int(x), 0)}</td>")\n',
        ),
        "rule-round": (
            'def f(x):\n    return mark_safe(f"<td>{round(x, 2)}</td>")\n',
            'def f(x):\n    return mark_safe(f"<td>{round(float(x), 2)}</td>")\n',
        ),
        "rule-abs": (
            'def f(x):\n    return mark_safe(f"<td>{abs(x)}</td>")\n',
            'def f(x):\n    return mark_safe(f"<td>{abs(int(x))}</td>")\n',
        ),
        "rule-nested-min-max": (
            'def f(v, t):\n    pct = min(100.0, max(0.0, v / t))\n    return mark_safe(f"<i>{pct}</i>")\n',
            'def f(v, t):\n    pct = min(100.0, max(0.0, float(v) / float(t)))\n    return mark_safe(f"<i>{pct}</i>")\n',
        ),
        "rule-enumerate-index": (
            "def f(rows):\n    out = []\n    for i, row in enumerate(rows):\n"
            '        out.append(f"<td>{row}</td>")\n    return mark_safe("".join(out))\n',
            "def f(rows):\n    out = []\n    for i, row in enumerate(rows):\n"
            '        out.append(f"<td>{i}</td>")\n    return mark_safe("".join(out))\n',
        ),
        "rule-enumerate-index-in-a-comprehension": (
            'def f(rows):\n    return mark_safe("".join(f"<td>{r}</td>" for i, r in enumerate(rows)))\n',
            'def f(rows):\n    return mark_safe("".join(f"<td>{i}</td>" for i, r in enumerate(rows)))\n',
        ),
        "rule-numeric-format-spec": (
            'def f(x):\n    return mark_safe(f"<b>{x:>10}</b>")\n',
            'def f(x):\n    return mark_safe(f"<b>{x:.2f}</b>")\n',
        ),
        "rule-numeric-format-spec-d": (
            'def f(x):\n    return mark_safe(f"<b>{x:s}</b>")\n',
            'def f(x):\n    return mark_safe(f"<b>{x:05d}</b>")\n',
        ),
        "rule-format-spec-c-prints-a-character": (
            'def f(x):\n    return mark_safe(f"<b>{x:c}</b>")\n',
            'def f(x):\n    return mark_safe(f"<b>{x:x}</b>")\n',
        ),
        "rule-uuid-hex": (
            "import uuid\ndef f(obj):\n    return mark_safe(f'<i id=\"{obj.hex}\">')\n",
            "import uuid\ndef f(obj):\n    return mark_safe(f'<i id=\"{uuid.uuid4().hex[:6]}\">')\n",
        ),
        "rule-secrets-token": (
            "import secrets\ndef f(t):\n    return mark_safe(f'<i id=\"{t}\">')\n",
            "import secrets\ndef f(t):\n    return mark_safe(f'<i id=\"{secrets.token_hex(4)}\">')\n",
        ),
        # --- structure the first version missed ---------------------------------------------
        "rule-tuple-unpack": (
            'def f(x):\n    a, b = f"<i>{x}</i>", 1\n    return mark_safe(a)\n',
            'def f(x):\n    a, b = f"<i>{escape(x)}</i>", 1\n    return mark_safe(a)\n',
        ),
        "rule-nested-tuple-unpack": (
            'def f(x):\n    (a, b), c = (f"<i>{x}</i>", 1), 2\n    return mark_safe(a)\n',
            'def f(x):\n    (a, b), c = (f"<i>{escape(x)}</i>", 1), 2\n    return mark_safe(a)\n',
        ),
        "rule-walrus": (
            'def f(x):\n    return mark_safe((s := f"<a {x}>"))\n',
            'def f(x):\n    return mark_safe((s := f"<a {escape(x)}>"))\n',
        ),
        "rule-walrus-then-name": (
            'def f(x):\n    if (s := f"<a {x}>"):\n        return mark_safe(s)\n',
            'def f(x):\n    if (s := f"<a {escape(x)}>"):\n        return mark_safe(s)\n',
        ),
        "rule-dict-comprehension": (
            'def f(xs):\n    d = {k: f"<i>{k}</i>" for k in xs}\n    return mark_safe(d["a"])\n',
            'def f(xs):\n    d = {k: format_html("<i>{}</i>", k) for k in xs}\n    return mark_safe(d["a"])\n',
        ),
        "rule-list-comprehension": (
            'def f(xs):\n    items = [f"<i>{k}</i>" for k in xs]\n    return mark_safe("".join(items))\n',
            'def f(xs):\n    items = [format_html("<i>{}</i>", k) for k in xs]\n    return mark_safe("".join(items))\n',
        ),
        "rule-comprehension-in-place": (
            'def f(xs):\n    return mark_safe([f"<i>{k}</i>" for k in xs][0])\n',
            'def f(xs):\n    return mark_safe([format_html("<i>{}</i>", k) for k in xs][0])\n',
        ),
        "rule-format_map": (
            'def f(x):\n    return mark_safe("<a {x}>".format_map({"x": x}))\n',
            'def f(x):\n    return mark_safe("<a {x}>".format_map({"x": escape(x)}))\n',
        ),
        "rule-Template.substitute": (
            'from string import Template\ndef f(x):\n    return mark_safe(Template("<a $x>").substitute(x=x))\n',
            'from string import Template\ndef f(x):\n    return mark_safe(Template("<a $x>").substitute(x=escape(x)))\n',
        ),
        "rule-Template.safe_substitute": (
            'from string import Template\ndef f(x):\n    return mark_safe(Template("<a $x>").safe_substitute(x=x))\n',
            'from string import Template\ndef f(x):\n    return mark_safe(Template("<a $x>").safe_substitute(x=escape(x)))\n',
        ),
    }
)

#: Clean on purpose: each pins a precision rule (a false positive the review listed, or an
#: escaper spelled another way that IS an escaper).
CLEAN.update(
    {
        "origin-stdlib-html-escape": 'import html\ndef f(x):\n    return mark_safe(f"<b>{html.escape(x)}</b>")\n',
        "origin-from-stdlib-html-import": (
            "from html import escape as esc2\n"
            'def f(x):\n    return mark_safe(f"<b>{esc2(x)}</b>")\n'
        ),
        "origin-module-alias": (
            "from django.utils import html as h\n"
            'def f(x):\n    return mark_safe(f"<b>{h.escape(x)}</b>")\n'
        ),
        "origin-dotted-module-path": (
            "import django.utils.html\n"
            'def f(x):\n    return mark_safe(f"<b>{django.utils.html.escape(x)}</b>")\n'
        ),
        "origin-assignment-alias-of-the-escaper": (
            'def f(x):\n    e = escape\n    return mark_safe(f"<b>{e(x)}</b>")\n'
        ),
        "origin-escaper-used-inside-a-nested-function": (
            'def f(x):\n    def g():\n        return mark_safe(f"<b>{escape(x)}</b>")\n    return g\n'
        ),
        "rule-tuple-unpack-the-other-element": (
            'def f(x):\n    a, b = f"<i>{x}</i>", "<br>"\n    return mark_safe(b)\n'
        ),
        "rule-enumerate-index-and-an-escaped-item": (
            "def f(rows):\n    out = []\n    for i, row in enumerate(rows):\n"
            '        out.append(format_html("<td>{}:{}</td>", i, row))\n    return mark_safe("".join(out))\n'
        ),
        "rule-range-variable": (
            'def f(n):\n    return mark_safe("".join(f"<td>{i}</td>" for i in range(n)))\n'
        ),
        "rule-uuid-in-an-attribute": (
            "import uuid\n"
            "def f():\n    return mark_safe(f'<i id=\"dj-{uuid.uuid4().hex[:8]}\"></i>')\n"
        ),
        "rule-str-of-a-uuid": (
            "import uuid\ndef f():\n    return mark_safe(f'<i id=\"{str(uuid.uuid4())}\"></i>')\n"
        ),
        "rule-percent-format-spec": (
            'def f(n):\n    return mark_safe(f"<i>{n:.1%}</i> <b>{n:,d}</b> <u>{n:+08.3f}</u>")\n'
        ),
        "rule-slice-of-clean-text": (
            'def f():\n    s = "<i>hello</i>"\n    return mark_safe(s[:3])\n'
        ),
        "rule-walrus-of-clean": 'def f(x):\n    return mark_safe((s := f"<a {escape(x)}>"))\n',
        "rule-comprehension-of-literals": (
            'def f():\n    return mark_safe("".join([f"<i>{k}</i>" for k in ("a", "b")]))\n'
        ),
        "rule-format_map-of-clean": (
            'def f():\n    return mark_safe("<a {x}>".format_map({"x": "b"}))\n'
        ),
        "rule-Template-of-clean": (
            "from string import Template\n"
            'def f():\n    return mark_safe(Template("<a $x>").substitute(x="b"))\n'
        ),
        "rule-the-token-counter-shape": (
            "def f(used, limit):\n"
            "    pct = min(100.0, max(0.0, int(used) / int(limit) * 100))\n"
            "    return mark_safe(f'<div style=\"width: {pct:.0f}%\">{round(pct, 1)}</div>')\n"
        ),
    }
)

#: Dirty code the guard does NOT flag today, probed and pinned. The module docstring lists these
#: as limits; when the guard learns one, ``test_a_known_miss_is_still_a_miss`` fails and the
#: entry (and the sentence in the docstring, the changelog and the PR body) moves.
KNOWN_MISSES = {
    "reduce-over-raw-items": (
        "from functools import reduce\n"
        "def f(a, b):\n    return mark_safe(reduce(lambda p, q: p + q, [a, b]))\n"
    ),
    "partial-of-the-sink": (
        'from functools import partial\ndef f(x):\n    return partial(mark_safe, f"<a {x}>")()\n'
    ),
    "map-of-the-sink": 'def f(xs):\n    return list(map(mark_safe, [f"<a {x}>" for x in xs]))\n',
    "getattr-of-the-sink": (
        "from django.utils import safestring\n"
        'def f(x):\n    return getattr(safestring, "mark_safe")(f"<a {x}>")\n'
    ),
    "built-in-one-function-marked-safe-in-another (the switcher)": (
        "def options(presets):\n    out = ''\n    for p in presets:\n"
        "        out += f'<option value=\"{p}\">{p}</option>'\n    return f'<select>{out}</select>'\n"
        "def chunk(presets):\n    return mark_safe(options(presets))\n"
    ),
    "a-helper-returning-an-interpolated-string": (
        'def helper(x):\n    return f"<a {x}>"\ndef f(x):\n    return mark_safe(helper(x))\n'
    ),
    "a-value-stored-on-self": (
        'class V:\n    def build(self, x):\n        self.html = f"<a {x}>"\n'
        "    def out(self):\n        return mark_safe(self.html)\n"
    ),
    "a-bare-parameter": "def f(markup):\n    return mark_safe(markup)\n",
    "render-trusted-by-method-name": (
        'def f(thing, x):\n    return mark_safe(thing.render(f"<a {x}>"))\n'
    ),
}

#: The pre-fix code, verbatim from ``main`` before #3775.
ORIGINAL_THEME_ICON = '''
@register.simple_tag
def theme_icon(name: str, size: int = 20) -> SafeString:
    """Render an SVG icon."""
    # Placeholder SVG icons
    icons = {
        "check": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>',
        "x": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
        "alert": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>',
        "info": f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>',
    }
    return mark_safe(icons.get(name, ""))
'''

ORIGINAL_THEME_FORM = """
def theme_form(context, form, layout="stacked", **kwargs):
    prefix = _css_prefix()
    lines = []
    lines.append(f'<div class="{prefix}theme-form-{layout}">')
    for bound_field in form.visible_fields():
        lines.append(f'  <div class="{prefix}theme-form-field">')
        field_id = bound_field.id_for_label
        escaped_label = conditional_escape(bound_field.label)
        lines.append(
            f'    <label for="{field_id}" class="{prefix}theme-label">{escaped_label}</label>'
        )
        lines.append(f"    {bound_field}")
        lines.append("  </div>")
    lines.append("</div>")
    return mark_safe("\\n".join(lines))


def theme_form_errors(context, form):
    prefix = _css_prefix()
    lines = [
        f'<div class="{prefix}theme-form-errors" role="alert">',
        f'  <ul class="{prefix}theme-error-list">',
    ]
    for error in form.non_field_errors():
        lines.append(f"    <li>{conditional_escape(error)}</li>")
    return mark_safe("\\n".join(lines))
"""

ORIGINAL_PASSTHROUGH = """
def _passthrough_attrs(attrs, skip):
    parts = []
    for key, value in attrs.items():
        name = key.replace("_", "-")
        parts.append(name if value is True else f'{name}="{escape(value)}"')
    return mark_safe(" ".join(parts))
"""


def _flagged(source: str) -> list[str]:
    return [f.function for f in find_unsafe_markup(PRELUDE + source)]


class TestEveryShapeIsCaught:
    @pytest.mark.parametrize("label", sorted(SHAPES))
    def test_the_dirty_shape_is_flagged(self, label):
        assert _flagged(SHAPES[label][0]), label

    @pytest.mark.parametrize("label", sorted(SHAPES))
    def test_its_clean_twin_is_not(self, label):
        assert _flagged(SHAPES[label][1]) == [], label

    @pytest.mark.parametrize("label", sorted(CLEAN))
    def test_clean_code_is_not_flagged(self, label):
        assert _flagged(CLEAN[label]) == [], label

    def test_one_finding_per_sink_with_the_function_and_line(self):
        (finding,) = find_unsafe_markup(PRELUDE + SHAPES["local-variable"][0])
        assert finding.function == "f"
        assert finding.sink == "mark_safe"
        assert PRELUDE.count("\n") < finding.lineno
        assert "s" in str(finding)

    def test_every_sink_in_a_file_is_judged_on_its_own(self):
        source = SHAPES["local-variable"][0] + SHAPES["local-variable"][1].replace("def f", "def g")
        assert _flagged(source) == ["f"]

    def test_a_syntax_error_is_not_swallowed(self):
        with pytest.raises(SyntaxError):
            find_unsafe_markup("def f(:\n")


class TestKnownMisses:
    @pytest.mark.parametrize("label", sorted(KNOWN_MISSES))
    def test_a_known_miss_is_still_a_miss(self, label):
        """If this fails the guard learned something: move the entry and the docstring sentence."""
        assert _flagged(KNOWN_MISSES[label]) == [], label

    def test_the_misses_are_the_ones_the_docstring_names(self):
        from scripts.lib import safe_markup_taint

        doc = safe_markup_taint.__doc__ or ""
        for needle in (
            "partial(mark_safe",
            "map(mark_safe",
            "getattr(safestring",
            "reduce",
            "self",
        ):
            assert needle in doc, needle
        assert "switcher" in doc and "NAME-based" in doc and "SINGLE-FUNCTION" in doc


class TestTheOriginalCode:
    def test_the_pre_fix_theme_icon_is_flagged(self):
        """#3766 itself: the first guard returned ``[]`` for this."""
        assert _flagged(ORIGINAL_THEME_ICON) == ["theme_icon"]

    def test_the_pre_fix_theme_form_and_theme_form_errors_are_flagged(self):
        assert _flagged(ORIGINAL_THEME_FORM) == ["theme_form", "theme_form_errors"]

    def test_the_pre_fix_passthrough_attrs_is_flagged(self):
        assert _flagged(ORIGINAL_PASSTHROUGH) == ["_passthrough_attrs"]

    def test_the_first_guard_missed_all_of_them(self):
        """The literal ``mark_safe(<interpolated string>)`` shape, kept here to show the gap."""
        import ast

        def literal(source: str) -> list[int]:
            return [
                n.lineno
                for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "mark_safe"
                and any(
                    isinstance(c, ast.JoinedStr)
                    and any(isinstance(v, ast.FormattedValue) for v in c.values)
                    for a in n.args
                    for c in ast.walk(a)
                )
            ]

        for source in (ORIGINAL_THEME_ICON, ORIGINAL_THEME_FORM, ORIGINAL_PASSTHROUGH):
            assert literal(source) == []


Key = tuple[str, str]


def reconcile(
    found: dict[Key, list[str]], audited: dict[Key, str]
) -> tuple[dict[Key, list[str]], list[Key], list[Key]]:
    """``(flagged and not audited, audited but not flagged, audited without a reason)``."""
    unaudited = {k: v for k, v in found.items() if k not in audited}
    stale = sorted(k for k in audited if k not in found)
    blank = sorted(k for k, reason in audited.items() if not reason.strip())
    return unaudited, stale, blank


def scan(root: Path, base: Path = ROOT) -> dict[Key, list[str]]:
    found: dict[Key, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(base))
        for finding in find_unsafe_markup(path.read_text()):
            found.setdefault((rel, finding.function), []).append(str(finding))
    return found


class TestTheAllowlistIsEnforced:
    """``reconcile`` on a synthetic allowlist: an empty real one cannot exercise its three rules."""

    FOUND = {("a.py", "f"): ["f:3 mark_safe() <- x"], ("b.py", "g"): ["g:9 mark_safe() <- y"]}

    def test_a_flagged_site_that_is_not_audited_is_reported(self):
        unaudited, stale, blank = reconcile(self.FOUND, {("a.py", "f"): "validated by the regex"})
        assert unaudited == {("b.py", "g"): self.FOUND[("b.py", "g")]}
        assert stale == [] and blank == []

    def test_an_audited_entry_that_no_longer_flags_is_stale(self):
        audited = {**dict.fromkeys(self.FOUND, "why"), ("c.py", "h"): "was audited"}
        unaudited, stale, blank = reconcile(self.FOUND, audited)
        assert unaudited == {} and blank == []
        assert stale == [("c.py", "h")]

    def test_an_audited_entry_needs_a_reason(self):
        unaudited, stale, blank = reconcile(self.FOUND, {("a.py", "f"): "  ", ("b.py", "g"): "ok"})
        assert unaudited == {} and stale == []
        assert blank == [("a.py", "f")]

    def test_everything_audited_is_clean(self):
        assert reconcile(self.FOUND, dict.fromkeys(self.FOUND, "reason")) == ({}, [], [])


class TestTheScanReadsEveryFile:
    """An empty real result proves nothing unless the scan is shown to read a dirty file."""

    def test_a_dirty_file_anywhere_in_the_tree_is_found(self, tmp_path):
        dirty = PRELUDE + SHAPES["local-variable"][0]
        clean = PRELUDE + SHAPES["local-variable"][1]
        (tmp_path / "pkg" / "deep").mkdir(parents=True)
        (tmp_path / "a_first.py").write_text(dirty)
        (tmp_path / "pkg" / "deep" / "z_last.py").write_text(dirty)
        (tmp_path / "pkg" / "middle.py").write_text(clean)
        found = scan(tmp_path, base=tmp_path)
        assert sorted(found) == [("a_first.py", "f"), ("pkg/deep/z_last.py", "f")]


class TestTheming:
    def test_there_are_files_to_scan(self):
        names = {p.name for p in THEMING.rglob("*.py")}
        assert {"theme_components.py", "theme_form_tags.py", "context_processors.py"} <= names

    def test_the_theming_package_is_clean_but_for_the_audited_sites(self):
        unaudited, stale, blank = reconcile(scan(THEMING), AUDITED)
        assert unaudited == {}, (
            "markup built from a raw value and marked safe (escape it, or build it with "
            f"format_html; CLAUDE.md Security Rules 1): {unaudited}"
        )
        assert stale == [], f"audited but no longer flagged: delete the entry: {stale}"
        assert blank == [], f"an audited entry needs a reason: {blank}"
