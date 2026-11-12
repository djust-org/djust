"""The passthrough attribute policy (#3818).

``{% live_input %}`` forwards every keyword it does not know to the tag as an attribute;
``FormMixin.as_live`` forwards a Django widget's ``attrs``; ``render_icon`` forwards its extras.
``build_tag`` escapes a VALUE and (since #3774) checks a NAME, but neither says what the attribute
DOES: ``formaction="javascript:..."`` has nothing to escape and ``onfocus`` is a legal name. The
policy is ``djust._attr_policy.passthrough_attrs``, one function every passthrough writer calls.

What these tests pin, in the order of the classes:

* the policy itself, one payload at a time (``on*`` in every case, script-bearing URLs read the
  way a browser reads them, ``type`` overrides, hostile names), and that an ordinary attribute
  comes out as the SAME OBJECT (so ordinary output is byte-identical);
* ``{% live_input %}`` on BOTH engines (the Django engine and the native Rust engine, which
  bridges to the same tag), and that the two agree on every payload;
* the other passthrough writers (``as_live`` widget attrs, ``render_icon`` and the native
  ``{% icon %}`` handler);
* parity with ``djust.theming``'s own implementation of the same policy (the sets, the
  severities, the verdict for every payload), so the two cannot drift apart silently;
* the set of passthrough writers: a tripwire that fails when a new function spreads ``**attrs``
  into a tag without being decided here.

Gate-off for each mechanism is recorded in the PR; the cells below are the ones that go red.

Refs #3818, #3774, #3749.
"""

from __future__ import annotations

import ast
import html
import re
from pathlib import Path

import pytest
from django import forms
from django.template import engines
from django.utils.safestring import mark_safe

from djust import _attr_policy as policy
from djust._attr_policy import passthrough_attrs

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "python" / "djust"

SCRIPT_URLS = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "JAVASCRIPT:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "java\rscript:alert(1)",
    "java\x00script:alert(1)",
    " javascript:alert(1)",
    "\x01javascript:alert(1)",
    "  \t\n javascript:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "livescript:alert(1)",
    "mhtml:x",
]
#: Other schemes: not script, but not on the allowlist either; rendered as ``#``.
OTHER_URLS = ["blob:https://x/y", "file:///etc/passwd", "intent://x#Intent;end", "myapp://open"]
GOOD_URLS = [
    "https://ok.example/p?q=1&r=2",
    "http://ok.example/",
    "mailto:a@b.example",
    "tel:+15551234",
    "/relative/path",
    "relative/path",
    "?q=1",
    "#frag",
    "//cdn.example/x.js",
    "",
]
RAISING = sorted(policy.URL_ATTRS_NAVIGATION)
QUIET = sorted(policy.URL_ATTRS_OTHER)
ON_NAMES = [
    "onfocus",
    "onFocus",
    "ONFOCUS",
    "onclick",
    "onmouseover",
    "onerror",
    "onload",
    "onanimationend",
    "on-focus",  # what ``on_focus`` becomes
    "@click",
    "@click.prevent",
    "x-on:click",
    "X-ON:CLICK",
    "v-on:click",
    "hx-on:click",
    "hx-on-click",
]
REFUSED_TYPES = [
    "image",
    "IMAGE",
    "Image",
    " image ",
    "submit",
    "Submit",
    "reset",
    "button",
    "BUTTON",
]
OK_TYPES = [
    "text",
    "date",
    "datetime-local",
    "color",
    "range",
    "file",
    "number",
    "hidden",
    "checkbox",
]
HOSTILE_NAMES = [
    "x onfocus=alert(1) y",
    'a"b',
    "a'b",
    "a<b",
    "a>b",
    "a/b",
    "a=b",
    "a\tb",
    "a\nb",
    "",
    "\x00a",
]
#: Ordinary attributes (name, value): every one must come back as the SAME object.
ORDINARY = [
    ("placeholder", 'Search "q" <b>&'),
    ("aria-label", "Search"),
    ("aria-describedby", "help"),
    ("maxlength", 20),
    ("rows", 5),
    ("pattern", "[a-z]+"),
    ("autofocus", True),
    ("data-x", "1"),
    ("data-url", "javascript:not-judged-it-is-the-authors-own-attribute"),
    ("dj-click", "handler"),
    ("x-model", "foo"),
    ("hx-get", "/x"),
    ("id", "i1"),
    ("class", "a b"),
    ("style", "color:red"),
    ("title", "t"),
    ("x-data", "{open: false}"),
    ("value", "v"),
    ("type", "text"),
    ("type", "date"),
]


def _refused(attrs):
    with pytest.raises(ValueError):
        passthrough_attrs(attrs)


class TestThePolicy:
    @pytest.mark.parametrize("name", ON_NAMES)
    def test_an_event_handler_attribute_is_refused(self, name):
        _refused({name: "alert(1)"})

    @pytest.mark.parametrize("name", ON_NAMES)
    def test_a_boolean_event_handler_attribute_is_refused_too(self, name):
        _refused({name: True})

    @pytest.mark.parametrize("name", ["onfocus", "@click"])
    def test_an_absent_attribute_is_not_judged(self, name):
        # ``None`` / ``False`` mean "not present" to every writer; nothing is printed.
        assert passthrough_attrs({name: None}) == {name: None}
        assert passthrough_attrs({name: False}) == {name: False}

    @pytest.mark.parametrize("name", RAISING)
    @pytest.mark.parametrize("url", SCRIPT_URLS)
    def test_a_script_bearing_url_in_a_navigation_attribute_renders_a_hash(self, name, url):
        # A VALUE can come from data (``formaction=profile.website``): it never raises, because
        # an error page an attacker's stored value triggers for every viewer is its own hole.
        assert passthrough_attrs({name: url}) == {name: "#"}

    @pytest.mark.parametrize("name", RAISING)
    @pytest.mark.parametrize("url", SCRIPT_URLS)
    def test_a_marked_safe_script_url_renders_a_hash_too(self, name, url):
        assert passthrough_attrs({name: mark_safe(url)}) == {name: "#"}

    @pytest.mark.parametrize("name", RAISING + QUIET)
    @pytest.mark.parametrize("url", OTHER_URLS)
    def test_another_rejected_scheme_renders_a_hash(self, name, url):
        if name == "srcset":
            url = url + " 2x"
        assert passthrough_attrs({name: url})[name] == ("#" + (" 2x" if name == "srcset" else ""))

    @pytest.mark.parametrize("name", QUIET)
    @pytest.mark.parametrize("url", SCRIPT_URLS)
    def test_a_quiet_url_attribute_never_raises_and_never_keeps_a_script_url(self, name, url):
        out = passthrough_attrs({name: url})[name]
        # ``srcset`` and ``ping`` are lists: a browser splits them on ASCII whitespace, so
        # ``java<TAB>script:x`` is the URL ``java`` and a junk token, never ``javascript:x``;
        # every other attribute is one URL read with the whitespace stripped.
        units = [out] if name not in ("srcset", "ping") else re.split(r"[\t\n\f\r ]+", out)
        for unit in units:
            compact = re.sub(r"[\s\x00-\x20]+", "", unit).lower()
            assert not compact.startswith(("javascript:", "vbscript:", "livescript:", "mhtml:"))
            if not (name in policy.IMAGE_URL_ATTRS and compact.startswith("data:image")):
                assert not compact.startswith("data:")

    @pytest.mark.parametrize("name", RAISING + QUIET)
    @pytest.mark.parametrize("url", GOOD_URLS)
    def test_an_allowed_url_is_printed_as_written(self, name, url):
        assert passthrough_attrs({name: url}) == {name: url}

    def test_srcset_and_ping_lists_are_judged_entry_by_entry(self):
        assert passthrough_attrs({"srcset": "/a.png 1x, javascript:x 2x, /b.png 3x"})["srcset"] == (
            "/a.png 1x, # 2x, /b.png 3x"
        )
        assert passthrough_attrs({"ping": "/a javascript:x /b"})["ping"] == "/a # /b"
        # An all-allowed list is printed as the caller wrote it, spacing included.
        assert passthrough_attrs({"srcset": "/a.png 1x,/b.png 2x"})["srcset"] == (
            "/a.png 1x,/b.png 2x"
        )

    def test_an_image_attribute_may_hold_an_inline_image(self):
        uri = "data:image/png;base64,AAAA"
        assert passthrough_attrs({"poster": uri})["poster"] == uri
        assert passthrough_attrs({"srcset": uri + " 1x"})["srcset"] == uri + " 1x"
        # ... and nothing else may: src does not get the image exemption.
        assert passthrough_attrs({"src": uri}) == {"src": "#"}
        assert passthrough_attrs({"data": uri})["data"] == "#"

    def test_a_url_is_converted_to_text_once(self):
        class Flaky:
            calls = 0

            def __str__(self):
                Flaky.calls += 1
                return "/ok" if Flaky.calls == 1 else "javascript:alert(1)"

        out = passthrough_attrs({"formaction": Flaky()})["formaction"]
        assert out == "/ok" and str(out) == "/ok"

    @pytest.mark.parametrize("value", REFUSED_TYPES)
    def test_a_submitting_type_is_left_out_not_raised(self, value):
        # ``type=field.kind`` can be data too: the attribute is dropped (the tag keeps the type
        # it fixed itself) and the call does not fail.
        assert passthrough_attrs({"type": value}) == {}
        assert passthrough_attrs({"TYPE": value, "placeholder": "p"}) == {"placeholder": "p"}

    def test_a_type_is_judged_and_returned_as_the_same_text(self):
        class Flaky:
            calls = 0

            def __str__(self):
                Flaky.calls += 1
                return "date" if Flaky.calls == 1 else "image"

        out = passthrough_attrs({"type": Flaky()})
        assert out == {"type": "date"} and type(out["type"]) is str

    def test_a_type_that_is_not_text_is_returned_as_text(self):
        class Kind:
            def __str__(self):
                return "color"

        assert passthrough_attrs({"type": Kind()}) == {"type": "color"}

    def test_a_dropped_type_is_logged_by_name_only(self, caplog):
        with caplog.at_level("WARNING", logger="djust._attr_policy"):
            passthrough_attrs({"type": "image"})
            passthrough_attrs({"type": mark_safe("submit")})
        assert len(caplog.records) == 2 and "image" not in caplog.text

    @pytest.mark.parametrize("value", OK_TYPES)
    def test_other_types_still_override(self, value):
        assert passthrough_attrs({"type": value}) == {"type": value}

    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_a_hostile_name_is_refused(self, name):
        _refused({name: "v"})

    def test_a_non_string_name_is_refused(self):
        _refused({1: "v"})

    @pytest.mark.parametrize(("name", "value"), ORDINARY)
    def test_an_ordinary_attribute_comes_back_untouched(self, name, value):
        out = passthrough_attrs({name: value})
        assert out == {name: value}
        assert out[name] is value, "an ordinary value is the caller's own object (byte-identical)"

    def test_it_returns_a_new_dict_and_does_not_mutate_the_input(self):
        attrs = {"formaction": "javascript:x"}
        try:
            passthrough_attrs(attrs)
        except ValueError:
            pass
        assert attrs == {"formaction": "javascript:x"}
        ok = {"placeholder": "p"}
        assert passthrough_attrs(ok) is not ok

    def test_the_error_names_the_attribute_and_never_prints_the_value(self):
        with pytest.raises(ValueError) as caught:
            passthrough_attrs({"onfocus": "SECRET-1"})
        assert "SECRET" not in str(caught.value) and "onfocus" in str(caught.value)

    def test_no_value_can_make_it_raise(self):
        """Only a NAME raises. Every URL / type value, however hostile, returns."""
        for name in RAISING + QUIET + ["type"]:
            for value in (
                SCRIPT_URLS + OTHER_URLS + REFUSED_TYPES + HOSTILE_NAMES + [mark_safe("<x>")]
            ):
                passthrough_attrs({name: value})
                passthrough_attrs({name: mark_safe(value)})

    def test_a_script_url_is_logged_without_its_value(self, caplog):
        with caplog.at_level("WARNING", logger="djust._attr_policy"):
            passthrough_attrs({"formaction": "javascript:SECRET-2"})
        assert caplog.records and "SECRET" not in caplog.text and "formaction" in caplog.text

    def test_a_rejected_scheme_is_logged_without_its_value(self, caplog):
        with caplog.at_level("WARNING", logger="djust._attr_policy"):
            passthrough_attrs({"poster": "blob:SECRET-3"})
        assert caplog.records and "SECRET" not in caplog.text


# ---------------------------------------------------------------------------
# {% live_input %} on both engines
# ---------------------------------------------------------------------------

PREFIX = "{% load live_tags %}<div dj-root>"


def _django(body, context):
    return engines["django"].from_string(PREFIX + body + "</div>").render(context)


def _native(body, context):
    from djust._rust import RustLiveView
    from djust.template_libraries import library_render_scope

    rv = RustLiveView(PREFIX + body + "</div>")
    rv.update_state(context)
    with library_render_scope():
        return rv.render()


ENGINES = [("django", _django), ("native", _native)]


@pytest.fixture(params=[e[0] for e in ENGINES])
def render(request):
    return dict(ENGINES)[request.param]


def _outcome(fn, body, context):
    try:
        return fn(body, context)
    except ValueError as exc:
        return f"ValueError:{exc}"


def _tag(attr):
    return '{% live_input "text" handler="h" ' + attr + "=u %}"


class TestLiveInputOnBothEngines:
    @pytest.mark.parametrize("attr", ["formaction", "src", "action", "href"])
    @pytest.mark.parametrize("url", SCRIPT_URLS)
    def test_a_script_url_in_a_navigation_attribute_renders_a_hash(self, render, attr, url):
        out = render(_tag(attr), {"u": url})
        assert f' {attr}="#"' in out
        assert "javascript" not in out.lower() and "script:" not in out.lower()

    def test_a_url_taken_from_data_never_fails_the_page(self, render):
        """The reported case: ``formaction=profile.website`` with a stored hostile URL."""

        class Profile:
            website = "javascript:alert(document.cookie)"

        out = render(
            '{% live_input "text" handler="h" formaction=profile.website %}', {"profile": Profile}
        )
        assert 'formaction="#"' in out
        out = render(
            '{% live_input "text" handler="h" formaction=profile.website %}',
            {"profile": {"website": "java\tscript:x"}},
        )
        assert 'formaction="#"' in out

    @pytest.mark.parametrize(
        "attr", ["onfocus", "onFocus", "ONFOCUS", "onclick", "on_focus", "onerror"]
    )
    def test_an_event_handler_attribute_raises(self, render, attr):
        with pytest.raises(ValueError):
            render(_tag(attr), {"u": "alert(1)"})

    @pytest.mark.parametrize("value", REFUSED_TYPES)
    def test_a_submitting_type_is_left_out_and_the_field_keeps_its_own(self, render, value):
        out = render(_tag("type"), {"u": value})
        assert out.startswith('<div dj-root><input type="text" class=')
        assert out.count("type=") == 1

    def test_the_issue_exploit_type_image_with_a_script_src_is_neutralised(self, render):
        out = render(
            '{% live_input "text" handler="h" type=t src=u %}',
            {"t": "image", "u": "javascript:alert(1)"},
        )
        assert 'type="text"' in out and 'type="image"' not in out
        assert 'src="#"' in out and "javascript" not in out

    @pytest.mark.parametrize("attr", ["poster", "srcset", "ping"])
    @pytest.mark.parametrize("url", SCRIPT_URLS + OTHER_URLS)
    def test_a_quiet_url_attribute_renders_a_hash(self, render, attr, url):
        if attr in ("srcset", "ping") and re.search(r"[\t\n\r]", url):
            pytest.skip("a list attribute splits on that whitespace: no single URL is a script URL")
        out = render(_tag(attr), {"u": url})
        assert f'{attr}="#' in out and "script" not in out.lower().replace("<script", "")
        assert "javascript" not in out.lower()

    @pytest.mark.parametrize(
        ("attr", "value"),
        [
            ("formaction", "/save/"),
            ("formaction", "https://ok.example/p?q=1&r=2"),
            ("href", "#frag"),
            ("type", "date"),
            ("type", "color"),
            ("placeholder", 'a "q" <b>'),
            ("aria_label", "Search"),
            ("maxlength", 20),
            ("autofocus", True),
            ("data_x", "1"),
            ("x_model", "foo"),
            ("style", "color:red"),
            ("dj_navigate", "/x/"),
        ],
    )
    def test_ordinary_attributes_render_exactly_as_before(self, render, attr, value):
        # The expected markup is what ``main`` rendered for the same call (measured before the
        # change); ``build_tag`` escaping is unchanged, so these are the bytes users have today.
        out = render(_tag(attr), {"u": value})
        name = attr.replace("_", "-")
        if value is True:
            assert f' {name}="{name}"' in out
        elif attr == "type":
            assert out.startswith(f'<div dj-root><input type="{value}" class=')
        else:
            assert (
                f' {name}="{html.escape(str(value), quote=True).replace("&#x27;", "&#x27;")}"'
                in out
            )

    def test_exact_bytes_of_the_documented_examples(self, render):
        assert render(
            '{% live_input "text" handler="search" value=q placeholder="Search..." debounce="300" %}',
            {"q": "django"},
        ) == (
            '<div dj-root><input type="text" class="form-control" value="django" name="search" '
            'dj-input="search" dj-debounce="300" placeholder="Search..." /></div>'
        )
        assert render(
            '{% live_input "textarea" handler="set_body" value=body rows=5 placeholder="Your note..." %}',
            {"body": "hi"},
        ) == (
            '<div dj-root><textarea class="form-control" name="set_body" dj-input="set_body" '
            'rows="5" placeholder="Your note...">hi</textarea></div>'
        )

    def test_every_other_field_type_judges_its_passthrough(self, render):
        for field_type in (
            "textarea",
            "select",
            "checkbox",
            "radio",
            "password",
            "email",
            "hidden",
        ):
            body = '{% live_input "' + field_type + '" handler="h" choices=c onfocus=u %}'
            with pytest.raises(ValueError):
                render(body, {"c": ["a"], "u": "x"})
            body = '{% live_input "' + field_type + '" handler="h" choices=c formaction=u %}'
            assert 'formaction="#"' in render(body, {"c": ["a"], "u": "javascript:x"})

    def test_the_two_engines_agree_on_every_payload(self):
        cells = []
        for attr in ["formaction", "src", "action", "href", "poster", "srcset", "ping"]:
            for url in SCRIPT_URLS + OTHER_URLS + GOOD_URLS:
                cells.append((_tag(attr), {"u": url}))
        for attr in ["onfocus", "onclick", "on_x"]:
            cells.append((_tag(attr), {"u": "alert(1)"}))
        for t in REFUSED_TYPES + OK_TYPES:
            cells.append((_tag("type"), {"u": t}))
        for attr, value in [("placeholder", "p"), ("aria_label", "a"), ("maxlength", 3)]:
            cells.append((_tag(attr), {"u": value}))
        disagreements = []
        for body, ctx in cells:
            d, n = _outcome(_django, body, ctx), _outcome(_native, body, ctx)
            if d != n:
                disagreements.append((body, ctx, d, n))
        assert not disagreements, disagreements[:3]
        assert len(cells) > 150

    def test_a_python_call_with_a_hostile_name_is_refused(self):
        from djust.templatetags.live_tags import live_input

        for name in HOSTILE_NAMES[:6]:
            with pytest.raises(ValueError):
                live_input("text", handler="h", **{name: "v"})

    def test_xlink_href_from_python_is_judged(self):
        from djust.templatetags.live_tags import live_input

        for url in ("blob:x", "javascript:x"):
            out = live_input("text", handler="h", **{"xlink:href": url})
            assert 'xlink:href="#"' in out

    def test_an_owned_attribute_is_still_overridable_by_an_ordinary_passthrough(self):
        # Unchanged behaviour (not a hole): a passthrough ``class`` / ``id`` / ``name`` wins.
        from djust.templatetags.live_tags import live_input

        out = live_input("text", handler="h", **{"class": "mine", "id": "x"})
        assert 'class="mine"' in out and 'id="x"' in out


# ---------------------------------------------------------------------------
# The other writers
# ---------------------------------------------------------------------------


class _F(forms.Form):
    def __init__(self, attrs, kind=forms.TextInput):
        super().__init__()
        self.fields["f"] = forms.CharField(widget=kind(attrs=attrs))


def _render_field(attrs, kind=forms.TextInput, adapter=None):
    from djust.frameworks import Bootstrap5Adapter

    form = _F(attrs, kind)
    return (adapter or Bootstrap5Adapter()).render_field(form.fields["f"], "f", "v", [])


class TestAsLiveWidgetAttrs:
    @pytest.mark.parametrize("name", ["onclick", "onchange", "onFocus", "ONFOCUS"])
    def test_an_inline_handler_in_widget_attrs_raises(self, name):
        with pytest.raises(ValueError):
            _render_field({name: "this.form.submit()"})

    @pytest.mark.parametrize("name", ["formaction", "href", "src", "action"])
    def test_a_script_url_in_widget_attrs_renders_a_hash(self, name):
        out = _render_field({name: "javascript:alert(1)"})
        assert f'{name}="#"' in out and "javascript" not in out

    def test_other_rejected_schemes_render_a_hash(self):
        assert 'formaction="#"' in _render_field({"formaction": "blob:x"})
        assert 'poster="#"' in _render_field({"poster": "file:///x"})

    @pytest.mark.parametrize("kind", [forms.Textarea, forms.Select])
    def test_every_field_type_judges_its_widget_attrs(self, kind):
        with pytest.raises(ValueError):
            _render_field({"onclick": "x"}, kind)

    @pytest.mark.parametrize("value", ["image", "submit", "reset", "button", "Image"])
    def test_a_widget_type_that_submits_is_ignored(self, value):
        # Django moves ``attrs={"type": ...}`` onto ``widget.input_type`` and the adapter takes
        # the tag's own type from there, so this is the route a widget ``type`` reaches the tag.
        # It does not fail the render: the field keeps its class's own type.
        out = _render_field({"type": value})
        assert 'type="text"' in out and value.lower() not in out.split("<input")[1].lower().replace(
            "validate_field", ""
        )

    @pytest.mark.parametrize("value", ["tel", "date", "color", "range"])
    def test_another_widget_type_still_applies(self, value):
        assert f'type="{value}"' in _render_field({"type": value})

    def test_a_type_that_would_be_written_is_judged(self):
        out = _render_field({"type": "image"}, forms.Textarea)
        assert "<textarea" in out and 'type="image"' not in out

    def test_ordinary_widget_attrs_are_byte_identical(self):
        out = _render_field(
            {
                "placeholder": 'a "q"',
                "maxlength": 5,
                "pattern": "[a-z]+",
                "data-x": "1",
                "autofocus": True,
            }
        )
        assert 'placeholder="a &quot;q&quot;"' in out
        assert 'maxlength="5"' in out and 'pattern="[a-z]+"' in out
        assert 'data-x="1"' in out and 'autofocus="True"' in out

    def test_none_and_false_widget_attrs_are_still_skipped(self):
        out = _render_field({"onclick": None, "formaction": False, "placeholder": "p"})
        assert "onclick" not in out and "formaction" not in out and 'placeholder="p"' in out


class TestIcons:
    def test_render_icon_refuses_an_event_handler(self):
        from djust.components.icons import render_icon

        with pytest.raises(ValueError):
            render_icon("check", onclick="alert(1)")

    def test_render_icon_neutralises_a_script_url(self):
        from djust.components.icons import render_icon

        assert 'href="#"' in render_icon("check", href="javascript:alert(1)")
        assert 'poster="#"' in render_icon("check", poster="blob:x")

    def test_render_icon_ordinary_extras_are_byte_identical(self):
        from djust.components.icons import render_icon

        out = render_icon("check", aria_label='Close "x"', title="t")
        assert 'aria-label="Close &quot;x&quot;"' in out and 'title="t"' in out

    def test_the_rust_engine_applies_the_same_policy(self):
        """The Rust engine renders ``{% icon %}``'s own node (one render path per tag)."""
        from djust import _rust
        from djust.components.rust_handlers import register_with_rust_engine

        register_with_rust_engine()
        with pytest.raises(Exception, match="onclick"):
            _rust.render_template('{% icon name="check" onclick="x" %}', {})
        out = _rust.render_template('{% icon name="check" aria_label="Close" %}', {})
        assert 'aria-label="Close"' in out


# ---------------------------------------------------------------------------
# Parity with djust.theming's implementation of the same policy
# ---------------------------------------------------------------------------


def _theming(attrs):
    from djust.theming.templatetags.theme_components import _passthrough_attrs

    return str(_passthrough_attrs(attrs, ()))


def _theming_outcome(name, value):
    try:
        out = _theming({name: value})
    except ValueError:
        return "raises"
    match = re.fullmatch(r'[A-Za-z-]+="(.*)"', out, flags=re.S)
    assert match, out
    return html.unescape(match.group(1))


def _shared_outcome(name, value):
    try:
        out = passthrough_attrs({name: value})[name]
    except ValueError:
        return "raises"
    return str(out)


def _same_modulo_list_whitespace(name, a, b):
    """``srcset`` / ``ping`` are lists a browser splits on ASCII whitespace: theming re-joins
    the entries with single spaces, the shared policy leaves an all-allowed list as written."""
    if name in ("srcset", "ping") and "raises" not in (a, b):
        return re.sub(r"[\t\n\f\r ]+", " ", a) == re.sub(r"[\t\n\f\r ]+", " ", b)
    return a == b


class TestParityWithTheming:
    def test_the_sets_are_the_same_sets(self):
        from djust.theming.templatetags import theme_components as tc

        assert policy.URL_ATTRS_NAVIGATION == tc._URL_ATTRS
        assert policy.URL_ATTRS_OTHER - {"xlink:href"} == tc._QUIET_URL_ATTRS
        assert policy.IMAGE_URL_ATTRS == tc._IMAGE_URL_ATTRS

    def test_every_url_payload_gets_the_same_verdict_except_the_documented_one(self):
        """The one deliberate difference: theming RAISES for a script-bearing value in
        ``href`` / ``src`` / ``action`` / ``formaction``; the shared policy renders ``#``
        (a value can come from data and must not fail the page)."""
        mismatches = []
        raising_differences = 0
        names = sorted(policy.URL_ATTRS_NAVIGATION | (policy.URL_ATTRS_OTHER - {"xlink:href"}))
        for name in names:
            values = SCRIPT_URLS + OTHER_URLS + GOOD_URLS + [mark_safe("&#x6A;avascript:x")]
            if name == "srcset":
                values = [v + " 2x" if v else v for v in values]
            for value in values:
                theirs, ours = _theming_outcome(name, value), _shared_outcome(name, value)
                if theirs == "raises" and ours == "#" and name in policy.URL_ATTRS_NAVIGATION:
                    raising_differences += 1
                    continue
                if not _same_modulo_list_whitespace(name, theirs, ours):
                    mismatches.append((name, value, theirs, ours))
        assert not mismatches, mismatches[:5]
        assert raising_differences > 40, "theming no longer raises: update the docstring"

    def test_the_other_documented_differences_exist_and_are_the_only_ones(self):
        from djust.theming.templatetags.theme_components import _passthrough_attrs

        # ``type``: judged by the shared policy only.
        assert passthrough_attrs({"type": "image"}) == {}
        assert 'type="image"' in str(_passthrough_attrs({"type": "image"}, ()))
        # ``hx-on-click``: refused by the shared policy only.
        with pytest.raises(ValueError):
            passthrough_attrs({"hx-on-click": "x"})
        assert 'hx-on-click="x"' in str(_passthrough_attrs({"hx-on-click": "x"}, ()))
        # ``xlink:href``: a URL attribute (and a legal name) in the shared policy only.
        assert passthrough_attrs({"xlink:href": "javascript:x"}) == {"xlink:href": "#"}
        with pytest.raises(ValueError):
            _passthrough_attrs({"xlink:href": "x"}, ())
        # ``srcset`` / ``ping``: an all-allowed list is printed as written here, re-joined there.
        assert passthrough_attrs({"srcset": "/a 1x,  /b 2x"}) == {"srcset": "/a 1x,  /b 2x"}
        assert 'srcset="/a 1x, /b 2x"' in str(_passthrough_attrs({"srcset": "/a 1x,  /b 2x"}, ()))
        assert passthrough_attrs({"ping": "/a  /b "}) == {"ping": "/a  /b "}
        assert 'ping="/a /b"' in str(_passthrough_attrs({"ping": "/a  /b "}, ()))

    @pytest.mark.parametrize("name", [n for n in ON_NAMES if n.lower().startswith("on")])
    def test_an_event_handler_is_refused_by_both(self, name):
        # The shared policy also refuses the Vue / Alpine / htmx spellings (``@click``,
        # ``x-on:``, ``v-on:``, ``hx-on``); theming's name rule already rejects ``@`` and ``:``.
        assert _theming_outcome(name, "x") == _shared_outcome(name, "x") == "raises"


# ---------------------------------------------------------------------------
# The writers are a decided set
# ---------------------------------------------------------------------------

#: Every place in ``python/djust`` (outside ``djust/theming``, which has the same policy
#: implemented in ``_passthrough_attrs``) that takes attribute NAMES from its caller and prints
#: them into a tag, with the decision. A tripwire, not a guard: it cannot see a writer that
#: spells its parameter differently, but it makes the obvious new one a deliberate decision.
DECIDED_WRITERS = {
    "components/icons.py": "render_icon: calls passthrough_attrs",
    "templatetags/live_tags.py": "_collect_passthrough_attrs: calls passthrough_attrs",
    "frameworks.py": "BaseAdapter._merge_widget_attrs: calls passthrough_attrs",
}
#: Functions with a ``**attrs``-style parameter, decided not to be passthrough writers.
NOT_WRITERS = {
    # (relative path, function): why
}


def _functions_with_attr_kwargs():
    found = {}
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(PKG).as_posix()
        if rel.startswith(("theming/", "tests/")) or "/tests/" in rel or rel.startswith("tests"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.args.kwarg:
                if node.args.kwarg.arg in {
                    "attrs",
                    "extra_attrs",
                    "html_attrs",
                    "attributes",
                    "extra",
                }:
                    found[(rel, node.name)] = node.args.kwarg.arg
    return found


class TestEveryWriterUsesThePolicy:
    def test_the_attrs_kwargs_writers_are_the_decided_ones(self):
        found = _functions_with_attr_kwargs()
        assert set(found) == {("components/icons.py", "render_icon")} | set(NOT_WRITERS), found

    def test_the_policy_is_called_from_exactly_the_decided_files(self):
        callers = set()
        for path in PKG.rglob("*.py"):
            rel = path.relative_to(PKG).as_posix()
            if (
                rel.startswith(("theming/", "tests/"))
                or "/tests/" in rel
                or rel == "_attr_policy.py"
            ):
                continue
            if re.search(r"(?<![\w.])passthrough_attrs\(", path.read_text()):
                callers.add(rel)
        assert callers == set(DECIDED_WRITERS), callers

    def test_the_live_input_collector_cannot_skip_the_policy(self):
        src = (PKG / "templatetags" / "live_tags.py").read_text()
        body = src.split("def _collect_passthrough_attrs", 1)[1].split("\ndef ", 1)[0]
        assert "return passthrough_attrs(out)" in body

    def test_no_native_rust_component_spreads_caller_attributes(self):
        """The Rust crates build elements from typed fields; none takes a map of attributes from
        Python. If one starts to, its attributes need this policy (or a Rust twin of it)."""
        offenders = []
        for path in (ROOT / "crates").rglob("*.rs"):
            if "target" in path.parts or "markup5ever" in str(path):
                continue
            text = path.read_text()
            if re.search(
                r"HashMap<String,\s*String>|IndexMap<String,\s*String>", text
            ) and re.search(r"\.attr\(\s*k|\.attr\(\s*key|for \(k, v\) in .*attr", text):
                offenders.append(path.relative_to(ROOT).as_posix())
        assert offenders == [], offenders
