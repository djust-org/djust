#!/usr/bin/env python3
"""Real-browser check that inserted nodes get the namespace of their parent.

An ``{% if %}`` / ``{% elif %}`` block inside ``<svg>`` or ``<math>`` is
inserted with an ``InsertSubtree`` patch (HTML the client parses in a
``<template>``) or an ``InsertChild`` patch (a node the client creates). Either
way the inserted elements must come out SVG / MathML, as the full render has
them, not HTML. For each case this renders the template with the
real Rust renderer (state 1, then state 2), loads the state-1 page in headless
Chromium, applies the real patches with the real ``client.js``, and compares
the result with a fresh load of the state-2 page: every element's namespace,
name, attributes (dj-id aside) and text, and every load / error event and
navigation that clicking links and submitting forms causes.

    pip install playwright && playwright install chromium
    DJUST_PYTHON=.venv/bin/python python tests/playwright/test_insert_subtree_namespace.py

``DJUST_PYTHON`` is an interpreter that can import ``djust._rust`` (default:
this one). ``CHROMIUM_EXECUTABLE`` optionally names a Chromium binary. Exits 0
on success, non-zero with the list of failures. Not part of the CI suite (see
README.md).
"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "python" / "djust" / "static" / "djust" / "client.js"

H = "__hit"
ROOTS = {
    "div>svg": ("<div dj-root><svg>", "</svg></div>", "", ""),
    "div>math": ("<div dj-root><math>", "</math></div>", "", ""),
    "svg>g root": ("<svg><g dj-root>", "</g></svg>", "<svg>", "</svg>"),
    "math>mrow root": ("<math><mrow dj-root>", "</mrow></math>", "<math>", "</math>"),
    "div>svg>foreignObject": (
        "<div dj-root><svg><foreignObject>",
        "</foreignObject></svg></div>",
        "",
        "",
    ),
    "div>math>annotation-xml html": (
        '<div dj-root><math><annotation-xml encoding="text/html">',
        "</annotation-xml></math></div>",
        "",
        "",
    ),
    "div": ("<div dj-root>", "</div>", "", ""),
}
# (name, branch markup, value of `v`)
BRANCHES = [
    ("iframe srcdoc", f"<iframe srcdoc=\"<script>parent.{H}('iframe')</script>\"></iframe>", ""),
    ("iframe src js", f"<iframe src=\"javascript:parent.{H}('iframe-js')\"></iframe>", ""),
    ("form action js", f"<form action=\"javascript:{H}('form')\"><button>go</button></form>", ""),
    ("details ontoggle", f"<details open ontoggle=\"{H}('details')\"></details>", ""),
    ("video source", f"<video><source src=x onerror=\"{H}('video')\"></video>", ""),
    ("a href js", f"<a href=\"javascript:{H}('href')\">x</a>", ""),
    ("img", f"<image href=x onerror=\"{H}('image')\"></image>", ""),
    ("user href", '<a href="{{ v }}">x</a>', f"javascript:{H}('u-href')"),
    ("user iframe src", '<iframe src="{{ v }}"></iframe>', f"javascript:parent.{H}('u-iframe')"),
    ("style text", "<style>{{ v }}</style>", f"</style><img src=x onerror={H}('style')>"),
    ("noscript text", "<noscript>{{ v }}</noscript>", f"<img src=x onerror={H}('noscript')>"),
    ("noscript markup", f"<noscript><img src=x onerror=\"{H}('ns-markup')\"></noscript>", ""),
    (
        "nested style",
        "<style><style>a</style>{{ v }}</style>",
        f"<img src=x onerror={H}('nested')>",
    ),
]
FORMS = {
    # A swap between two branches: the new branch is an InsertSubtree.
    "elif": "{pre}{{% if s == 1 %}}<g>one</g>{{% elif s == 2 %}}{branch}{{% endif %}}<g>tail</g>{post}",
    # A block that appears: its element is an InsertChild.
    "if": "{pre}<g>head</g>{{% if s == 2 %}}{branch}{{% endif %}}<g>tail</g>{post}",
}

GENERATE = textwrap.dedent(
    """
    import json, sys
    import msgpack
    from djust._rust import RustLiveView
    out = []
    for case in json.load(sys.stdin):
        for method in ("render_with_diff", "render_binary_diff"):
            view = RustLiveView(case["template"], [])
            view.update_state({"s": 1, "v": case["v"]})
            before = getattr(view, method)()[0]
            view.update_state({"s": 2, "v": case["v"]})
            patches = getattr(view, method)()[1]
            if isinstance(patches, (bytes, bytearray)):
                patches = msgpack.unpackb(patches, raw=False)
            else:
                patches = json.loads(patches) if patches else []
            fresh = RustLiveView(case["template"], [])
            fresh.update_state({"s": 2, "v": case["v"]})
            after = getattr(fresh, method)()[0]
            out.append(dict(case, method=method, before=before, after=after, patches=patches))
    json.dump(out, sys.stdout)
    """
)

INIT = """
window.__hits = [];
window.__hit = function (x) { (window.top.__hits || window.__hits).push(String(x)); };
window.addEventListener('error', function (e) {
  var t = e.target;
  if (t && t !== window && t.localName) {
    (window.top.__hits || window.__hits).push('error:' + (t.namespaceURI || '').split('/').pop() + ':' + t.localName);
  }
}, true);
"""
SHAPE = """() => {
  const prefix = (n) => ({'http://www.w3.org/1999/xhtml': '', 'http://www.w3.org/2000/svg': 'svg:',
                          'http://www.w3.org/1998/Math/MathML': 'math:'})[n.namespaceURI] ?? '?:';
  function shape(n) {
    if (n.nodeType === 3) { const t = n.data.trim(); return t ? JSON.stringify(t) : ''; }
    if (n.nodeType !== 1) return '';
    const attrs = n.localName === 'body' ? '' : [...n.attributes].filter((a) => a.name !== 'dj-id')
      .map((a) => a.name + '=' + JSON.stringify(a.value)).sort().join(' ');
    const kids = [...(n.localName === 'template' ? n.content.childNodes : n.childNodes)].map(shape).join('');
    return '<' + prefix(n) + n.localName + (attrs ? ' ' + attrs : '') + '>' + kids + '</>';
  }
  return shape(document.body);
}"""
ACT = """async () => {
  for (const a of document.querySelectorAll('a')) a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
  for (const f of document.querySelectorAll('form')) { try { f.requestSubmit(); } catch (e) {} }
  await new Promise((r) => setTimeout(r, 150));
  return window.__hits.slice().sort();
}"""


INSERTS = ("InsertSubtree", "InsertChild")
# `InsertChild` creates only allow-listed names, and `style` is not one (it
# becomes an inert `<span>` anywhere, HTML included), so these run as an
# InsertSubtree only.
ELIF_ONLY = {"style text", "nested style"}
# Authored `<img>` inside `<noscript>` is markup for the page's parser too
# when the `<noscript>` is foreign, and an `<img>` start tag ends `<svg>` /
# `<math>`: the full render then holds the branch's siblings outside the
# foreign element, a different tree shape that patches do not express.
HTML_ROOTS_ONLY = {"noscript markup"}
HTML_ROOTS = {"div", "div>svg>foreignObject", "div>math>annotation-xml html"}


def cases():
    todo = []
    for root, (pre, post, wrap_open, wrap_close) in ROOTS.items():
        for name, branch, value in BRANCHES:
            for form, shape in FORMS.items():
                if form == "if" and name in ELIF_ONLY:
                    continue
                if name in HTML_ROOTS_ONLY and root not in HTML_ROOTS:
                    continue
                template = shape.format(pre=pre, post=post, branch=branch)
                todo.append(
                    {
                        "root": root,
                        "branch": name,
                        "form": form,
                        "template": template,
                        "v": value,
                        "wrap": [wrap_open, wrap_close],
                    }
                )
    python = os.environ.get("DJUST_PYTHON", sys.executable)
    done = subprocess.run(
        [python, "-c", GENERATE],
        input=json.dumps(todo),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


async def run():
    failures = []
    runs = 0
    async with async_playwright() as p:
        launch = {}
        if os.environ.get("CHROMIUM_EXECUTABLE"):
            launch["executable_path"] = os.environ["CHROMIUM_EXECUTABLE"]
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context()
        await context.add_init_script(INIT)
        pages = {}

        async def serve(route):
            await route.fulfill(
                status=200, content_type="text/html", body=pages.pop(route.request.url, "")
            )

        await context.route("http://insert-subtree.test/**", serve)
        page = await context.new_page()
        count = [0]

        async def load(html, wrap):
            count[0] += 1
            url = f"http://insert-subtree.test/{count[0]}"
            pages[url] = (
                "<!DOCTYPE html><html><head></head><body>"
                + wrap[0]
                + html
                + wrap[1]
                + "</body></html>"
            )
            await page.goto(url, wait_until="load")

        for case in cases():
            if not any(p.get("type") in INSERTS for p in case["patches"]):
                continue  # this path sent the full HTML; nothing to apply
            runs += 1
            label = f"{case['root']} / {case['branch']} / {case['form']} / {case['method']}"
            await load(case["after"], case["wrap"])
            await page.wait_for_timeout(100)
            full_hits = await page.evaluate(ACT)
            full_shape = await page.evaluate(SHAPE)
            await load(case["before"], case["wrap"])
            await page.add_script_tag(path=str(CLIENT))
            await page.evaluate("() => { window.__hits.length = 0; }")
            ok = await page.evaluate(
                "async (p) => { try { return await window.djust.applyPatches(p); }"
                " catch (e) { return 'threw ' + e; } }",
                case["patches"],
            )
            await page.wait_for_timeout(100)
            patched_hits = await page.evaluate(ACT)
            patched_shape = await page.evaluate(SHAPE)
            if ok is not True:
                failures.append(f"{label}: applyPatches returned {ok!r}")
            elif patched_shape != full_shape:
                failures.append(f"{label}:\n  full    {full_shape}\n  patched {patched_shape}")
            elif patched_hits != full_hits:
                failures.append(
                    f"{label}: events {patched_hits} after the patch, {full_hits} in the full render"
                )
        await browser.close()
    return runs, failures


def main():
    runs, failures = asyncio.run(run())
    print(f"{runs} patched pages compared with a full render")
    if failures:
        print(f"{len(failures)} FAILED:")
        for failure in failures:
            print(" -", failure)
        return 1
    if runs == 0:
        print("no insert patch was produced")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
