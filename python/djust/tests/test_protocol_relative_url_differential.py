"""A URL slot that must stay on this site is judged on the URL as a browser reads it.

A browser normalises a URL before it reads it: it removes TAB / LF / CR anywhere, drops leading
and trailing C0 controls and spaces, and reads a backslash as a slash (for http and https). The
data table's row navigation and ``validate_mount_url`` apply the same
normalisation before their same-site test, through one definition (``djust.security.urls``).

The decision is checked against three oracles over a generated corpus:

* the browser's own URL parser (WHATWG ``new URL``, run by node): the authority;
* Django's ``url_has_allowed_host_and_scheme`` with no allowed hosts: the framework's own idea of
  "this site only", used by the auth redirects;
* the JavaScript guard, loaded UNCHANGED from the static file, so the Python helper and the
  script cannot drift apart.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from django.utils.http import url_has_allowed_host_and_scheme

import djust
from djust.security.urls import url_as_parsed, url_is_protocol_relative
from djust.security.mount import validate_mount_url

NODE = shutil.which("node")
ROW_CLICK_JS = (
    Path(djust.__file__).parent
    / "components"
    / "static"
    / "djust_components"
    / "data-table-row-click.js"
)
BASE = "https://site.example/app/page?q=1"

# --- the corpus -------------------------------------------------------------------------------

LEADS = ["", " ", "\t", "\x01", "\x0b", "\u00a0"]
HEADS = ["", ".", "http:", "ht\ttp:", "javascript:"]
SEPS = [
    "/", "\\", "\t", "\n", "\r", "\x00", " ", "\x0c",
    "%2f", "%5c", "%09", "&#47;", "\uff0f", "\u2215", "\u3002", ".",
]  # fmt: skip
TAILS = ["other.example/x", "u:p@other.example/", "x", "", "a:b"]

#: The generated part: lead x head x sep x sep x (sep | nothing) x tail.
GENERATED = [
    lead + head + a + b + c + tail
    for lead, head, a, b, c, tail in itertools.product(
        LEADS, HEADS, SEPS, SEPS, ["", "/", "\\"], TAILS
    )
]

#: Hand-written shapes.
NAMED = [
    "/\\other.example/x", "/\t/other.example", "/\n/other.example", "/\r/other.example",
    "//other.example", "///other.example", "\\\\other.example", "\\/other.example",
    "/\\/other.example", "/ /other.example", "http:other.example", "http:/other.example",
    "http://other.example", "http:\\\\other.example", "https:\\\\other.example",
    "https://other.example", "HTTPS://other.example", "ht\ttps://other.example",
    "/%09/other.example", "/%5cother.example", "/%2fother.example", "/ok", "/ok/%2e%2e/x",
    "./ok", "../ok", ".", "..", "?q=1", "#frag", "ok", "", " ", "javascript:alert(1)",
    "\x01javascript:alert(1)", "data:text/html,x", "mailto:a@b.c", "/\x00/other.example",
    "\x00//other.example", "//", "/", "\\", "/\\", "\\/", "\\\\",
]  # fmt: skip

_INPUTS = list(dict.fromkeys(NAMED + GENERATED))


#: What the Python decisions RETURN is judged too: an output the decision built (a normalised path)
#: must itself stay on this site, so the outputs join the corpus the browser is asked about.
def _decide(fn, value):
    """``fn(value)``, or ``""`` when it raises: the tests below assert it does not."""
    try:
        return fn(value)
    except ValueError:
        return ""


_OUTPUTS = {out for value in _INPUTS for out in (_decide(validate_mount_url, value),) if out}
CORPUS = list(dict.fromkeys(_INPUTS + sorted(_OUTPUTS)))


def test_the_corpus_is_large_and_covers_the_named_shapes():
    assert len(CORPUS) > 100_000
    for named in ("/\\other.example/x", "/\t/other.example"):
        assert named in CORPUS


# --- the oracles ------------------------------------------------------------------------------

_NODE_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const [corpusPath, jsPath, base] = process.argv.slice(2);
const values = JSON.parse(fs.readFileSync(corpusPath, "utf8"));
const window = {};
const document = {
  readyState: "complete",
  querySelectorAll: () => [],
  addEventListener: () => {},
  body: null,
};
vm.runInNewContext(fs.readFileSync(jsPath, "utf8"), { window, document });
const guard = window.djustDataTableRowClick.safeRowHref;
const baseOrigin = new URL(base).origin;
const out = values.map((v) => {
  let state = "error";
  let protocol = null;
  try {
    const u = new URL(v, base);
    protocol = u.protocol;
    state = u.origin === baseOrigin ? "same" : "other";
  } catch (_e) {}
  return [state, protocol, guard(v)];
});
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    """``(state, protocol, js_guard_target)`` per corpus value, from node. ``state`` is where the
    browser would go: ``same`` origin, an ``other`` origin, or ``error`` (the URL does not parse,
    so ``location.assign`` throws and nothing happens)."""
    if NODE is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("urlcorpus")
    corpus = tmp / "corpus.json"
    script = tmp / "oracle.js"
    corpus.write_text(json.dumps(CORPUS))
    script.write_text(textwrap.dedent(_NODE_SCRIPT))
    proc = subprocess.run(
        [NODE, str(script), str(corpus), str(ROW_CLICK_JS), BASE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    rows = json.loads(proc.stdout)
    assert len(rows) == len(CORPUS)
    return dict(zip(CORPUS, rows))


def _has_scheme(text: str) -> bool:
    """Whether the parsed text starts ``<scheme>:`` (so it is not a relative reference)."""
    parsed = url_as_parsed(text)
    head, colon, _ = parsed.partition(":")
    return (
        bool(colon)
        and bool(head)
        and head[0].isalpha()
        and all(ch.isalnum() or ch in "+-." for ch in head)
    )


def _django_site_only(url: str) -> bool:
    return url_has_allowed_host_and_scheme(url, allowed_hosts=set())


# --- the helper agrees with the browser -------------------------------------------------------


def test_protocol_relative_is_exactly_what_the_browser_sends_to_another_host(browser):
    """For a relative reference (no scheme), ``url_is_protocol_relative`` is true exactly when the
    browser resolves it OFF this origin. Both directions: a miss leaves the origin, a false
    alarm would refuse a working path."""
    checked = misses = false_alarms = 0
    examples: list[str] = []
    for value, (state, _protocol, _guard) in browser.items():
        if _has_scheme(value):
            continue
        checked += 1
        flagged = url_is_protocol_relative(value)
        if state == "other" and not flagged:
            misses += 1
            examples.append(f"MISS {value!r}")
        if state == "same" and flagged:
            false_alarms += 1
            examples.append(f"FALSE ALARM {value!r}")
    assert checked > 50_000
    assert not examples, (misses, false_alarms, examples[:8])


def test_a_flagged_value_is_off_origin_for_the_browser_whatever_its_shape(browser):
    for value, (state, _protocol, _guard) in browser.items():
        if url_is_protocol_relative(value):
            assert state != "same", repr(value)


def test_named_values_are_protocol_relative_and_the_plain_ones_are_not():
    for value in ("/\\other.example/x", "/\t/other.example", "//h", "\\\\h", "\\/h", " \x01//h"):
        assert url_is_protocol_relative(value), repr(value)
    for value in ("/h", "/%2Fh", "/%5Ch", "\\h", "h", "", "/", "./h", "?q", "#f", "/ /h"):
        assert not url_is_protocol_relative(value), repr(value)


def test_the_helper_drops_edge_junk_and_inner_tabs_and_newlines():
    assert url_as_parsed(" \t/x\x01 ") == "/x"
    assert url_as_parsed("/\t/\nx\r") == "//x"
    assert url_as_parsed("/a b") == "/a b"


def test_a_mount_url_with_leading_or_trailing_junk_is_never_kept_as_given():
    """``validate_mount_url`` requires a leading slash before it normalises, so a value that only
    becomes protocol-relative once its edge junk is dropped is refused by that first test; the
    outcome is the fallback either way."""
    for value in (" //h", "\t//h", "\x01//h", "//h ", "/\\h", "/\t/h"):
        assert validate_mount_url(value) == "/", repr(value)
    assert validate_mount_url("/ok/") == "/ok/"


# --- Django's own idea of "this site only" is not weaker than the helper ----------------------


def test_django_never_calls_a_value_site_only_that_the_browser_sends_elsewhere(browser):
    """The oracle itself: where Django says a value stays on this site, the browser agrees
    (Django 5.x normalises backslashes and control characters; an older Django did not)."""
    for value, (state, _protocol, _guard) in browser.items():
        if _django_site_only(value):
            assert state != "other", repr(value)


def test_the_helper_is_at_least_as_strict_as_django_for_a_leading_slash(browser):
    """``/``-led values the helper does not flag are ones Django also accepts, except where Django
    refuses a leading control character that a browser silently drops."""
    differing = []
    for value in browser:
        parsed = url_as_parsed(value)
        if not parsed.startswith("/") or url_is_protocol_relative(value):
            continue
        if not _django_site_only(value):
            differing.append(value)
    # Every difference is a value Django refuses for a reason the browser does not share
    # (a leading control/space it drops, or a control inside it); none is a value
    # the browser sends off this origin (the previous test), so Django is only the stricter
    # of the two here.
    for value in differing:
        assert value != url_as_parsed(value) or any(ord(ch) < 0x20 for ch in value), repr(value)


# --- the decisions built on it ----------------------------------------------------------------


def test_the_row_click_guard_agrees_with_the_helper_and_the_browser(browser):
    """The unchanged JavaScript guard: nothing it hands to ``navigate()`` is protocol-relative,
    everything it accepts that is not an absolute http(s) URL stays on this origin."""
    accepted = 0
    for value, (state, protocol, target) in browser.items():
        if not target:
            continue
        accepted += 1
        assert not url_is_protocol_relative(target), repr(value)
        if target.lower().startswith(("http://", "https://")):
            assert protocol in ("http:", "https:", None), repr(value)
            continue
        assert state == "same", f"the guard accepted {value!r} and the browser leaves this origin"
    assert accepted > 50


def test_the_row_click_guard_refuses_everything_the_helper_flags(browser):
    for value, (_same, _protocol, target) in browser.items():
        if url_is_protocol_relative(value) and not _has_scheme(value):
            assert not target, repr(value)


def test_validate_mount_url_never_names_another_origin(browser):
    for value in _INPUTS:
        out = validate_mount_url(value)
        assert out.startswith("/"), repr(value)
        assert not url_is_protocol_relative(out), (value, out)
        assert browser[out][0] != "other", f"{value!r} -> {out!r} leaves this origin"
