"""The Rust ``Avatar`` reads a URL's scheme the way Python's ``urlsplit`` does (#3748).

``crates/djust_components/src/simple/avatar.rs`` mirrors ``djust.components.utils.url_attr``
(``image=True``). Its ``url_scheme`` removed only tab, CR and LF before reading the scheme, so a
leading C0 control character other than those (``\\x01blob:...``, ``\\x00file:...``) failed the
leading-letter test and the value was taken to have NO scheme: ``blob:`` / ``file:`` /
``intent:`` reached ``<img src>`` raw where ``url_attr`` returns ``#``. (``javascript:`` /
``vbscript:`` / ``data:`` were never affected: the Rust ``probe`` filter strips every control
character before it compares.) ``urlsplit`` (and a browser) strip every leading C0 control and
space first; ``url_scheme`` now does too.

This is the differential that proves it, not a restatement of the Rust unit tests
(``leading_c0_control_does_not_hide_a_non_navigation_scheme``): the reference is the Python
function itself, driven over

* every C0 control, space and DEL, alone and doubled, in front of every scheme the sweep knows;
* the same controls BETWEEN scheme letters (a browser removes tab / CR / LF only);
* a seeded random sweep over alphabet and length, so a case nobody listed is still compared.

``decision`` compares what each side does with the value (kept, or ``#``); where both keep it,
the printed attribute is compared as well.

Refs #3748, #3747, #3665, #1825, #1468.
"""

from __future__ import annotations

import random
import re

import pytest

from djust.components.utils import url_attr

try:  # the compiled extension: a worktree without it cannot run the differential
    from djust._rust import RustAvatar
except ImportError:  # pragma: no cover
    RustAvatar = None

pytestmark = [
    pytest.mark.skipif(RustAvatar is None, reason="the Rust extension is not built"),
]

C0_AND_SPACE = [chr(i) for i in range(0x21)]
SCHEMES = [
    "blob:https://e.com/x",
    "BLOB:https://e.com/x",
    "file:///etc/passwd",
    "intent://e.com/x#Intent;scheme=https;end",
    "javascript:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<b>x</b>",
    "data:image/png;base64,iVBORw0KGgo=",
    "livescript:alert(1)",
    "x-app://open/1",
    "https://e.com/a.png",
    "http://e.com/a.png",
    "mailto:a@e.com",
    "tel:+15551234567",
    "/rel/a.png",
    "a.png",
    "?q=1",
    "#frag",
    "",
]


def _rust_src(src: str) -> str | None:
    """The ``src`` the Rust avatar prints, or ``None`` when it prints no image."""
    html = RustAvatar(src, "", None, "md", "circle", None).render()
    match = re.search(r'<img src="([^"]*)"', html)
    return match.group(1) if match else None


def _decision(src: str) -> tuple[str | None, str]:
    """``(rust, python)`` printed ``src`` values for the same input."""
    return _rust_src(src), url_attr(src, image=True)


def _agree(src: str) -> None:
    rust, python = _decision(src)
    if rust is None:  # an empty src prints no <img>: Python prints ""
        assert python == "", repr(src)
        return
    kept_rust, kept_python = rust != "#", python != "#"
    assert kept_rust == kept_python, "decision differs for %r: rust=%r python=%r" % (
        src,
        rust,
        python,
    )
    if kept_rust:
        # both keep it: the same text (both escape once, both print the value as given)
        assert rust.strip() == python.strip(), (src, rust, python)


@pytest.mark.parametrize("scheme", SCHEMES)
class TestLeadingControlsAreStrippedLikeUrlsplit:
    @pytest.mark.parametrize("control", C0_AND_SPACE, ids=lambda c: "u%04x" % ord(c))
    def test_one_leading_control(self, scheme, control):
        _agree(control + scheme)

    @pytest.mark.parametrize("control", C0_AND_SPACE, ids=lambda c: "u%04x" % ord(c))
    def test_two_leading_controls(self, scheme, control):
        _agree(control + control + scheme)
        _agree(" " + control + scheme)

    def test_a_run_of_every_control(self, scheme):
        _agree("".join(C0_AND_SPACE) + scheme)

    def test_upper_case_after_controls(self, scheme):
        _agree("\x01" + scheme.upper())


@pytest.mark.parametrize("scheme", ["blob:x", "file:///x", "intent://x", "javascript:x"])
class TestControlsInsideTheScheme:
    """A browser removes tab, CR and LF anywhere; any other control inside breaks the scheme."""

    @pytest.mark.parametrize("control", C0_AND_SPACE, ids=lambda c: "u%04x" % ord(c))
    def test_a_control_between_two_letters(self, scheme, control):
        _agree(scheme[:2] + control + scheme[2:])

    @pytest.mark.parametrize("control", C0_AND_SPACE, ids=lambda c: "u%04x" % ord(c))
    def test_a_control_before_the_colon(self, scheme, control):
        head, rest = scheme.split(":", 1)
        _agree(head + control + ":" + rest)


class TestTheNamedPayloads:
    """The three the review measured, as the issue states them."""

    @pytest.mark.parametrize(
        "src",
        ["\x01blob:https://e.com/x", "\x01file:///etc/passwd", "\x01intent://e.com/x#Intent;end"],
    )
    def test_they_no_longer_reach_the_img_src(self, src):
        rust, python = _decision(src)
        assert rust == "#" and python == "#", (rust, python)

    def test_a_navigation_url_behind_a_control_is_still_kept(self):
        rust, python = _decision("\x01https://e.com/a.png")
        assert "https://e.com/a.png" in rust and "https://e.com/a.png" in python


def test_a_seeded_random_sweep_agrees():
    """Alphabet: controls, space, scheme-ish letters, ``:`` and ``/``; 4000 values."""
    rng = random.Random(3748)
    alphabet = C0_AND_SPACE + list("bBlLoOfFiIeEjJsScCrRpPtT:/.+-1x") * 3
    seeds = ["blob:", "file:", "intent:", "javascript:", "https:", "data:image/png;base64,", ""]
    for _ in range(4000):
        prefix = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 4)))
        middle = rng.choice(seeds)
        suffix = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 6)))
        _agree(prefix + middle + suffix)
