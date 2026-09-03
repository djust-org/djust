"""``{% load app_tags %}`` imports the Django template library and bridges it (#2547).

Every parity assertion below renders the SAME source through live Django in
process and through djust — the plain backend (``DjustTemplateBackend`` →
``DjustTemplate.render`` → ``render_template_with_dirs``) AND the real
LiveView entry (a ``LiveView`` subclass driven by ``LiveViewTestClient``,
#1650) — and compares bytes. The scratch library under ``lib2547/`` carries
every registration shape: ``simple_tag`` (positional / variable /
``takes_context`` / keyword defaults / ``*args **kwargs`` / keyword-only /
``as var`` / the three escaping stances), ``simple_block_tag`` (default kw,
custom ``end_name``, nesting, ``takes_context``, ``as var``),
``inclusion_tag`` (with and without ``takes_context``), ``filter``
(``is_safe`` / ``needs_autoescape`` / ``@stringfilter`` / ``@mark_safe``), and
raw ``@register.tag`` compile functions that build their node from their own
token.

Mechanisms and the gate-off split (#1468 / #2129 / #2135)
---------------------------------------------------------
* **loader callback** (``parser.rs`` ``load`` arm →
  ``template_libraries.load_libraries``): gate it off and EVERY library row
  is red — nothing is bridged (measured: 129 red; the 7 survivors do not go
  through ``{% load %}`` — the ``OPTIONS['builtins']`` row, the structural
  pins, the no-load refusal).
* **``mark_safe`` on node output** (``template_libraries._render_node``): gate
  it off and ONLY the ``raw-inline-plain-str-markup`` rows go red (measured:
  3). Django's decorator-made nodes already return ``SafeString``
  (``conditional_escape`` / ``format_html`` / ``NodeList.render``), so the
  mechanism is load-bearing exactly for a node that returns markup as a
  plain ``str`` — the ``BlockTranslateNode`` shape the #2558 amendment
  measured; ``RawMarkupNode`` is that shape in the scratch library.
* **bindings diff** (``_render_node``'s ``ctx.dicts[-1]`` diff): gate it off
  and ONLY the ``as var`` / ``set_two`` rows go red (measured: 16).
* **block-tag refusal** (``_bridge_tag``'s ``_consumes_body`` branch →
  ``RefusedTagHandler`` → the parser's ``REFUSE_AT_PARSE`` check): gate it
  off and ONLY ``test_raw_block_consuming_tag_is_refused_loudly`` and the
  mixed-library case go red — the raw tag then bridges as an ordinary
  handler and dies in Django's ``unclosed_block_tag`` at render.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import TemplateSyntaxError  # noqa: E402
from django.template.backends.django import DjangoTemplates  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust import template_libraries  # noqa: E402
from djust.live_view import LiveView  # noqa: E402
from djust.template.backend import DjustTemplateBackend  # noqa: E402
from djust.testing import LiveViewTestClient  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

TEMPLATE_DIR = HERE / "lib2547" / "templates"
LIB_TAGS = "lib2547.templatetags.lib2547_tags"
LIB_EXTRA = "lib2547.templatetags.lib2547_extra"
LIB_RAW = "lib2547.templatetags.lib2547_rawblock"
LIB_BUILTIN = "lib2547.templatetags.lib2547_builtin"
LIBRARIES = {
    "lib2547_tags": LIB_TAGS,
    "lib2547_extra": LIB_EXTRA,
    "lib2547_rawblock": LIB_RAW,
}

DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*)</div>", re.S)

CTX = {
    "value": 42,
    "name": "Jack & Jill",
    "string": "abcdefghijklmnopqrstuvwxyz",
    "n": 5,
    "hostile": "<img src=x onerror=alert(1)>",
    "safe": mark_safe("<b>ok</b>"),
    "items": ["1", "2"],
}


# ---------------------------------------------------------------------------
# The three render paths
# ---------------------------------------------------------------------------


def _django_backend(**options):
    return DjangoTemplates(
        {
            "NAME": "django2547",
            "DIRS": [str(TEMPLATE_DIR)],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": LIBRARIES, **options},
        }
    )


def _djust_backend(**options):
    return DjustTemplateBackend(
        {
            "NAME": "djust2547",
            "DIRS": [str(TEMPLATE_DIR)],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": LIBRARIES, **options},
        }
    )


DJANGO = _django_backend()
DJUST = _djust_backend()


def django_render(source: str, context: dict) -> str:
    return str(DJANGO.from_string(source).render(dict(context)))


def plain_render(source: str, context: dict) -> str:
    return str(DJUST.from_string(source).render(dict(context)))


def liveview_render(source: str, context: dict) -> str:
    """The REAL LiveView entry: ``LiveViewTestClient.mount()`` + ``.render()``
    → ``_sync_state_to_rust`` → ``RustLiveView.render`` (#1650)."""

    class _V(LiveView):
        def mount(self, request, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    html = client.render()
    match = DJ_ROOT.search(html)
    assert match is not None, html
    return match.group(1)


RENDER = {"plain": plain_render, "liveview": liveview_render}


# ---------------------------------------------------------------------------
# Every registration shape, byte-for-byte against Django, on both paths
# ---------------------------------------------------------------------------

L = "{% load lib2547_tags %}"

SHAPES = {
    # simple_tag
    "simple-no-params": L + "{% no_params2547 %}",
    "simple-positional-literal": L + "{% one_param2547 37 %}",
    "simple-positional-variable": L + "{% one_param2547 value %}",
    "simple-takes-context": L + "{% with_context2547 37 %}",
    "simple-kw-default-absent": L + "{% one_default2547 37 %}",
    "simple-kw-default-given": L + '{% one_default2547 37 two="hello" %}',
    "simple-kw-both": L + '{% one_default2547 one=99 two="hello" %}',
    "simple-args-kwargs-with-filters": (
        L + '{% unlimited2547 37 40|add:2 56 eggs="scrambled" four=1|add:3 %}'
    ),
    "simple-kwonly-default": L + "{% kwonly2547 %}",
    "simple-kwonly-given": L + "{% kwonly2547 kwarg=37 %}",
    "simple-renamed": L + "{% minustwo2547 5 %}|{% minusone2547 n %}",
    "simple-arg-types": L + "{% types2547 5 1.5 'x' value name %}",
    "simple-in-for": L + "{% for i in items %}{% one_param2547 i %};{% endfor %}",
    # escaping — the `mark_safe`-on-output rows
    "escape-naive": L + "{% escape_naive2547 %}",
    "escape-explicit": L + "{% escape_explicit2547 %}",
    "escape-format-html": L + "{% escape_format_html2547 %}",
    "escape-user-text-return": L + "{% echo_arg2547 hostile %}",
    "escape-safe-context-value": L + "{% echo_arg2547 safe %}",
    "escape-quoted-literal": L + "{% echo_arg2547 '<b>lit</b>' %}",
    # `as var` — the bindings rows
    "asvar-simple": L + "{% one_param2547 37 as out %}Result: {{ out }}",
    "asvar-simple-hostile": L + "{% echo_arg2547 hostile as v %}{{ v }}",
    "asvar-simple-safe": L + "{% escape_format_html2547 as v %}{{ v }}",
    "asvar-in-for": L + "{% for i in items %}{% one_param2547 i as o %}{{ o }};{% endfor %}",
    "asvar-then-if": L + "{% one_param2547 1 as o %}{% if o %}yes:{{ o }}{% endif %}",
    "asvar-block": L + "{% div2547 as d %}{{ name }}{% enddiv2547 %}My div is: {{ d }}",
    "bindings-two-keys": L + "{% set_two2547 %}{{ k1_2547 }}|{{ k2_2547 }}",
    # simple_block_tag
    "block-default-kw": L + "{% div2547 %}content{% enddiv2547 %}",
    "block-kw-literal": L + "{% div2547 id='outer' %}x{% enddiv2547 %}",
    "block-kw-variable": L + "{% div2547 id=name %}x{% enddiv2547 %}",
    "block-body-variable": L + "{% div2547 %}{{ name }}{% enddiv2547 %}",
    "block-nested-same": (
        L + "Start{% div2547 id='outer' %}Before{% div2547 id='inner' %}{{ name }}"
        "{% enddiv2547 %}After{% enddiv2547 %}End"
    ),
    "block-nested-different": (
        L
        + "{% div2547 %}A{% kwonly_block2547 kwarg=7 %}B{% endkwonly_block2547 %}C{% enddiv2547 %}"
    ),
    "block-custom-end-name": L + "{% div_custom_end2547 %}{{ name }}{% divend2547 %}",
    "block-takes-context-naive": (
        L + "{% escape_naive_block2547 %}{{ name }} again{% endescape_naive_block2547 %}"
    ),
    "block-kwonly-default": L + "{% kwonly_block2547 %}forty two{% endkwonly_block2547 %}",
    # inclusion_tag
    "inclusion-positional": L + "{% incl_one2547 37 %}",
    "inclusion-takes-context": L + "{% incl_ctx2547 %}",
    # filters
    "filter-stringfilter-numeric-arg": L + "{{ string|trim2547:5 }}",
    "filter-mark-safe-decorator": L + "{{ name|data_div2547 }}",
    "filter-is-safe-hostile-input": L + "{{ hostile|shout2547 }}",
    "filter-is-safe-safe-input": L + "{{ safe|shout2547 }}",
    "filter-needs-autoescape": L + "{{ name|initial2547 }}",
    "filter-in-tag-arg-cross-library": (
        "{% load lib2547_tags lib2547_extra %}{% one_param2547 name|twice2547 %}"
    ),
    # raw @register.tag building its node from its own token
    "raw-inline-echo": L + "{% echo2547 a b c %}",
    "raw-inline-aliased": L + "{% other_echo2547 x y %}",
    # a node returning MARKUP as a plain `str` — Django never re-escapes a
    # node's output (the `mark_safe`-on-output mechanism's own row)
    "raw-inline-plain-str-markup": L + "{% raw_markup2547 %}",
    # load forms
    "load-two-libraries": "{% load lib2547_tags lib2547_extra %}{% extra_tag2547 1 %}",
    "load-same-twice": "{% load lib2547_tags lib2547_tags %}{% no_params2547 %}",
    "load-from-library": "{% load echo2547 from lib2547_tags %}{% echo2547 x y %}",
    "load-two-from-library": (
        "{% load echo2547 other_echo2547 from lib2547_tags %}"
        "{% echo2547 this %} {% other_echo2547 that %}"
    ),
    "load-filter-from-library": "{% load trim2547 from lib2547_tags %}{{ string|trim2547:3 }}",
    "load-inside-included-file": '{% include "lib2547_inc.html" %}',
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_plain_backend_matches_django(shape):
    source = SHAPES[shape]
    assert plain_render(source, CTX) == django_render(source, CTX)


#: Rows that need the scratch ``templates/`` directory: an ``inclusion_tag``
#: template or an ``{% include %}``. The LiveView entry's loader searches the
#: PROJECT's template dirs, so these render through the enclosing backend
#: below rather than through the fallback.
NEEDS_TEMPLATE_DIR = {
    "inclusion-positional",
    "inclusion-takes-context",
    "load-inside-included-file",
}


@pytest.mark.django_db
@pytest.mark.parametrize("shape", sorted(set(SHAPES) - NEEDS_TEMPLATE_DIR))
def test_liveview_entry_matches_django(shape):
    source = SHAPES[shape]
    assert liveview_render(source, CTX) == django_render(source, CTX)


@pytest.mark.django_db
@pytest.mark.parametrize("shape", sorted(NEEDS_TEMPLATE_DIR - {"load-inside-included-file"}))
def test_liveview_inclusion_tag_renders_through_the_enclosing_backend(shape):
    """An `inclusion_tag`'s template resolves through the backend of the
    enclosing render (the ContextVar `DjustTemplate.render` sets); the LiveView
    entry sets none and falls back to the project's djust backend, which does
    not know the scratch dir — so the test supplies the backend explicitly."""
    source = SHAPES[shape]
    with template_libraries.rendering_with_backend(DJUST):
        assert liveview_render(source, CTX) == django_render(source, CTX)


def test_load_inside_a_block_of_an_extended_template():
    expected = str(DJANGO.get_template("lib2547_child.html").render({}))
    assert expected.strip() == "[one_param - Expected result: 9]"
    assert str(DJUST.get_template("lib2547_child.html").render({})) == expected


# ---------------------------------------------------------------------------
# The escaping pins — Django's own stance, byte-equal
# ---------------------------------------------------------------------------


def test_simple_tag_returning_user_text_unescaped_renders_exactly_what_django_renders():
    """`SimpleNode.render` conditional-escapes a plain-`str` return; the
    bridge's `mark_safe` on node OUTPUT does not undo that."""
    source = L + "{% echo_arg2547 hostile %}"
    out = plain_render(source, CTX)
    assert out == django_render(source, CTX)
    assert out == "&lt;img src=x onerror=alert(1)&gt;"


def test_simple_tag_explicit_escape_is_not_double_escaped():
    source = L + "{% escape_explicit2547 %}"
    out = plain_render(source, CTX)
    assert out == django_render(source, CTX) == "Hello Jack &amp; Jill!"


def test_block_tag_body_is_escaped_once_by_the_body_and_once_by_the_naive_tag():
    source = L + "{% escape_naive_block2547 %}{{ name }} again{% endescape_naive_block2547 %}"
    out = plain_render(source, CTX)
    assert out == django_render(source, CTX)
    assert out == "Hello Jack &amp; Jill: Jack &amp;amp; Jill again!"


def test_node_output_is_never_re_escaped_even_when_it_is_a_plain_str():
    """Django never re-escapes a node's output. A raw node returning markup
    as a plain `str` (the `BlockTranslateNode` shape) renders raw on Django;
    the bridge's `mark_safe` on node output keeps `escape_handler_return`
    (#2379) from escaping it a second time. The node's own inputs were
    resolved by Django inside the node — here the naive node emits the
    context value RAW, on both engines, which is that node's bug and not the
    bridge's business."""
    source = L + "{% raw_markup2547 %}"
    out = plain_render(source, CTX)
    assert out == django_render(source, CTX) == "<em>raw</em> Jack & Jill"


def test_mark_safe_on_output_does_not_extend_to_a_bound_variable():
    """`as var` stores the RAW return; `{{ var }}` escapes it unless SafeData."""
    hostile = L + "{% echo_arg2547 hostile as v %}{{ v }}"
    assert plain_render(hostile, CTX) == django_render(hostile, CTX)
    assert plain_render(hostile, CTX) == "&lt;img src=x onerror=alert(1)&gt;"
    safe = L + "{% escape_format_html2547 as v %}{{ v }}"
    assert plain_render(safe, CTX) == django_render(safe, CTX) == "Hello Jack &amp; Jill!"


@pytest.mark.parametrize(
    "value",
    [
        "<img src=x onerror=alert(1)>",
        "&lt;img src=x&gt;",
        "%3Cimg%20src%3Dx%3E",
        "&#60;script&#62;",
        "<IMG SRC=x>",
        "<img src=x>",
    ],
)
def test_library_filter_is_safe_true_on_hostile_input_still_escapes(value):
    """The #2548 rule, applied to a `{% load %}`-bridged filter: `is_safe=True`
    keeps a SAFE input safe and never makes a hostile one safe — including
    every encoded / alternate representation (#1825)."""
    source = L + "{{ p|shout2547 }}"
    ctx = {"p": value}
    out = plain_render(source, ctx)
    assert out == django_render(source, ctx)
    assert "<img" not in out and "<IMG" not in out and "<script" not in out
    marked = {"p": mark_safe(value)}
    assert plain_render(source, marked) == django_render(source, marked) == "<b>%s</b>" % value


def test_safe_context_value_reaches_the_library_tag_as_safedata():
    """`{% echo_arg safe %}` renders raw on Django because the resolved operand
    is a `SafeString`; the bridge re-mints the renderer's grant on the dict."""
    source = L + "{% echo_arg2547 safe %}"
    assert plain_render(source, CTX) == django_render(source, CTX) == "<b>ok</b>"
    unmarked = dict(CTX, safe="<b>ok</b>")
    assert (
        plain_render(source, unmarked) == django_render(source, unmarked) == "&lt;b&gt;ok&lt;/b&gt;"
    )


# ---------------------------------------------------------------------------
# Loader contract: only what Django would import; Django's messages
# ---------------------------------------------------------------------------


def _message(fn, source):
    with pytest.raises(TemplateSyntaxError) as info:
        fn(source, CTX)
    return str(info.value)


def test_unknown_library_raises_djangos_exact_message():
    source = "{% load nope2547 %}x"
    ours = _message(plain_render, source)
    theirs = _message(django_render, source)
    assert ours.startswith("'nope2547' is not a registered tag library. Must be one of:\n")
    assert ours == theirs


def test_a_dotted_module_path_is_not_a_library_name():
    """The loader resolves NAMES through Django's map, never an arbitrary
    dotted path written in a template."""
    source = "{% load lib2547.templatetags.lib2547_tags %}x"
    assert _message(plain_render, source) == _message(django_render, source)


def test_load_from_library_unknown_name_raises_djangos_message():
    source = "{% load nope2547 from lib2547_tags %}x"
    ours = _message(plain_render, source)
    assert ours == "'nope2547' is not a valid tag or filter in tag library 'lib2547_tags'"
    assert ours == _message(django_render, source)


@pytest.mark.parametrize(
    "source",
    ["{% load from lib2547_tags %}x", "{% load echo2547 other_echo2547 nope from %}x"],
)
def test_malformed_from_forms_take_djangos_branch(source):
    """Django: `len(bits) >= 4 and bits[-2] == "from"` — anything else loads
    every word as a library name, `from` included."""
    assert _message(plain_render, source) == _message(django_render, source)


def test_library_exception_crosses_whole_with_its_type():
    source = L + "{% badtag2547 %}"
    with pytest.raises(RuntimeError, match="I am a bad tag \\(2547\\)"):
        plain_render(source, CTX)
    with pytest.raises(RuntimeError):
        django_render(source, CTX)


def test_parse_bits_errors_are_djangos_template_syntax_errors():
    source = L + '{% one_default2547 99 two="hello" three="foo" %}'
    ours = _message(plain_render, source)
    assert ours == "'one_default2547' received unexpected keyword argument 'three'"
    assert ours == _message(django_render, source)


def test_unknown_tag_without_load_is_refused():
    with pytest.raises(Exception, match="unknown_tag2547"):
        plain_render("{% unknown_tag2547 %}", CTX)


def test_raw_block_consuming_tag_is_refused_loudly():
    """Per TAG, at parse time, when a template USES it — not at `{% load %}`."""
    assert plain_render("{% load lib2547_rawblock %}loaded-ok", CTX) == "loaded-ok"
    with pytest.raises(TemplateSyntaxError) as info:
        plain_render("{% load lib2547_rawblock %}{% wrapblock2547 %}x{% endwrapblock2547 %}", CTX)
    message = str(info.value)
    assert "'wrapblock2547' from library 'lib2547_rawblock'" in message
    assert "consumes a block" in message
    assert "#2558" in message
    # Django, for the record, renders it — the refusal is the documented gap.
    assert (
        django_render("{% load lib2547_rawblock %}{% wrapblock2547 %}x{% endwrapblock2547 %}", CTX)
        == "[x]"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("path", sorted(RENDER))
def test_a_library_with_one_raw_block_tag_still_bridges_its_other_entries(path):
    """The refusal is scoped to the ONE tag: the sibling simple tag and the
    filter from the same library render byte-equal to Django."""
    source = "{% load lib2547_rawblock %}{% sibling2547 %}|{{ name|sibling_filter2547 }}"
    assert RENDER[path](source, CTX) == django_render(source, CTX)
    assert django_render(source, CTX) == "sibling - Expected result|[Jack &amp; Jill]"
    with pytest.raises(TemplateSyntaxError, match="wrapblock2547"):
        RENDER[path]("{% load lib2547_rawblock %}{% wrapblock2547 %}y{% endwrapblock2547 %}", CTX)


def test_djangos_own_libraries_resolve_but_are_not_bridged():
    """`{% load static %}` keeps parsing (separate rows own its tags) and
    registers nothing here."""
    assert plain_render("{% load static %}x-2547", {}) == "x-2547"
    assert not any(label == "static" for label in template_libraries.owned_tags().values())


# ---------------------------------------------------------------------------
# `OPTIONS['builtins']`, both engine paths share one registry, stateful nodes
# ---------------------------------------------------------------------------


def test_options_builtins_need_no_load():
    """`lib2547_builtin` is never `{% load %}`ed by any test; only the
    `OPTIONS['builtins']` path can have registered its tag."""
    assert "builtin_only2547" not in template_libraries.owned_tags()
    source = "{% builtin_only2547 %}"
    theirs = str(_django_backend(builtins=[LIB_BUILTIN]).from_string(source).render({}))
    ours = str(_djust_backend(builtins=[LIB_BUILTIN]).from_string(source).render({}))
    assert ours == theirs == "builtin_only - Expected result"


@pytest.mark.django_db
def test_both_paths_share_one_registry():
    """Load on the plain path, render (a different source, no `{% load %}`)
    on the LiveView path: the registration is process-global (#1051)."""
    assert plain_render(L + "shared-2547-a", {}) == "shared-2547-a"
    assert liveview_render("{% one_param2547 7 %}shared-2547-b", {}) == (
        "one_param - Expected result: 7shared-2547-b"
    )


def test_stateful_raw_node_keeps_state_within_a_template():
    """Django's `CounterNode` is compiled once per template; the bridge
    compiles once per argument list, so within one template the count
    advances exactly as on Django. (Across templates it keeps advancing —
    the documented process-global divergence.)"""
    source = L + "{% for i in '123' %}{% counter2547 %}{% endfor %}"
    assert plain_render(source, {}) == django_render(source, {}) == "012"


def test_reassert_restores_bridged_tags_after_a_registry_clear():
    """The #1928 class for library tags: a registry clear + a source the Rust
    TEMPLATE_CACHE already holds. Without `reassert()` the cached template's
    `CustomTag` nodes resolve to nothing and its `{% load %}` never re-runs."""
    source = L + "{% no_params2547 %}{% div2547 %}b{% enddiv2547 %}!reassert"
    expected = django_render(source, {})
    assert plain_render(source, {}) == expected  # parsed + cached now
    owned = template_libraries.owned_tags()
    assert owned["no_params2547"] == "lib2547_tags" and owned["div2547"] == "lib2547_tags"
    _rust.clear_tag_handlers()
    _rust.clear_block_tag_handlers()
    assert not _rust.has_tag_handler("no_params2547")
    assert not _rust.has_block_tag_handler("div2547")
    with pytest.raises(Exception, match="No handler registered"):
        plain_render(source, {})
    template_libraries.reassert()
    assert _rust.has_tag_handler("no_params2547")
    assert _rust.has_block_tag_handler("div2547")
    assert plain_render(source, {}) == expected
    from djust.template_tags import reregister_builtins

    reregister_builtins()  # restore djust's own built-ins for the tests that follow


def test_library_tag_never_displaces_a_djust_builtin():
    """A library registering `url` (djust's own handler) is skipped with a
    warning; the built-in wins process-wide."""
    from django import template as dj_template

    lib = dj_template.Library()

    @lib.simple_tag(name="url")
    def _url_impostor():
        return "impostor"

    from djust.template_tags import url as _  # noqa: F401 — the built-in

    assert _rust.has_tag_handler("url")
    before = _rust.get_registered_tags()
    template_libraries._bridge_library("impostor2547", lib)
    assert "url" not in template_libraries.owned_tags()
    assert sorted(_rust.get_registered_tags()) == sorted(before)


# ---------------------------------------------------------------------------
# Structural pins against the installed Django
# ---------------------------------------------------------------------------


def test_kind_classification_pins_djangos_compile_function_shapes():
    """`@wraps(func)` copies the USER function's `__qualname__` onto
    `compile_func`; the code object's own qualname is what discriminates.
    A Django rename fails here, loudly, rather than reclassifying every tag
    as raw."""
    from django import template as dj_template

    lib = dj_template.Library()

    @lib.simple_tag
    def s():
        return ""

    @lib.simple_block_tag(end_name="stop_b")
    def b(content):
        return content

    @lib.inclusion_tag("lib2547_incl.html")
    def i():
        return {}

    @lib.tag
    def r(parser, token):
        return dj_template.base.TextNode("")

    assert template_libraries._classify(lib.tags["s"]) == "simple_tag"
    assert template_libraries._classify(lib.tags["b"]) == "simple_block_tag"
    assert template_libraries._classify(lib.tags["i"]) == "inclusion_tag"
    assert template_libraries._classify(lib.tags["r"]) == "raw"
    assert template_libraries._end_name(lib.tags["b"], "b") == "stop_b"
    assert lib.tags["s"].__qualname__.endswith(".s"), "wraps() copies the user qualname"


def test_block_registry_honours_resolve_arg_positions():
    """The #1646 drift the plan measured: the block registry alone resolved
    every position. `{% div2547 id=name %}` must hand Django's parser the
    TOKEN `id=name` (which Django resolves to `Jack & Jill`), not the value."""
    source = L + "{% div2547 id=name %}x{% enddiv2547 %}"
    out = plain_render(source, CTX)
    assert out == django_render(source, CTX)
    assert out == "<div id='Jack &amp; Jill'>x</div>"


def test_loader_hook_is_installed_and_reinstalled_after_clear():
    assert _rust.has_library_loader()
    _rust.clear_library_loader()
    assert not _rust.has_library_loader()
    from djust.template_tags import reregister_builtins

    reregister_builtins()
    assert _rust.has_library_loader()


# ---------------------------------------------------------------------------
# Randomized differential against Django (≥ 300 cases)
# ---------------------------------------------------------------------------

_POSITIONAL = [
    "5",
    "-2",
    "1.5",
    "0",
    "'x'",
    '"a b"',
    "'<b>'",
    '"&amp;"',
    "value",
    "name",
    "hostile",
    "safe",
    "n|add:3",
    "name|upper",
    "value|add:'1'",
    "string|trim2547:4",
]
_KW = ["eggs", "four", "two"]


def _random_case(rng: random.Random) -> str:
    tag = rng.choice(["unlimited2547", "types2547", "echo_arg2547", "one_param2547", "div2547"])
    if tag == "echo_arg2547" or tag == "one_param2547":
        args = [rng.choice(_POSITIONAL)]
    elif tag == "div2547":
        args = [] if rng.random() < 0.5 else ["id=%s" % rng.choice(_POSITIONAL)]
    else:
        n = rng.randint(1, 4) if tag == "unlimited2547" else rng.randint(0, 4)
        args = [rng.choice(_POSITIONAL) for _ in range(n)]
        if tag == "unlimited2547" and rng.random() < 0.5:
            for kw in rng.sample(_KW, rng.randint(1, 2)):
                args.append("%s=%s" % (kw, rng.choice(_POSITIONAL)))
    asvar = rng.random() < 0.3
    if tag == "div2547":
        body = rng.choice(["x", "{{ name }}", "{{ hostile }}", "{{ safe }}", ""])
        head = "{%% div2547 %s %%}" % " ".join(args + (["as", "v"] if asvar else []))
        tpl = head + body + "{% enddiv2547 %}"
    else:
        tpl = "{%% %s %s %%}" % (tag, " ".join(args + (["as", "v"] if asvar else [])))
    if asvar:
        tpl += rng.choice(["[{{ v }}]", "[{{ v|upper }}]", "{% if v %}[{{ v }}]{% endif %}"])
    return L + tpl


def _outcome(fn, source):
    try:
        return "OK:" + fn(source, CTX)
    except TemplateSyntaxError as e:
        return "TSE:" + str(e)
    except Exception as e:  # noqa: BLE001 — the differential compares classes
        return "EXC:" + type(e).__name__


def test_randomized_differential_against_django():
    rng = random.Random(2547)
    cases = {_random_case(rng) for _ in range(900)}
    assert len(cases) >= 300
    mismatches = []
    for source in sorted(cases):
        theirs = _outcome(django_render, source)
        ours = _outcome(plain_render, source)
        if ours != theirs:
            mismatches.append((source, theirs, ours))
    assert not mismatches, "\n".join("%s\n  django: %r\n  djust : %r" % m for m in mismatches[:15])


def test_the_randomized_differential_is_not_vacuous():
    """Gate-off of the harness itself (#2135): a wrong render must be visible
    to `_outcome`."""
    rng = random.Random(1)
    source = _random_case(rng)
    assert _outcome(django_render, source) != _outcome(lambda s, c: "nonsense", source)
