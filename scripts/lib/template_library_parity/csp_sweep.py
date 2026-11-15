"""The strict-CSP inline sweep over djust's own template libraries and components (#4139).

A ``Content-Security-Policy`` without ``'unsafe-inline'`` refuses what a page carries inline: a
``style="..."`` attribute, a ``<style>`` element and an executable ``<script>`` element without a
``src`` (the last two run anyway when they carry the request's nonce), an inline event-handler
attribute (``onclick=``) and a ``javascript:`` URL (neither can carry a nonce). This module
renders everything djust draws from a template tag or a component, and reads the FINAL markup
for all five:

1. **Every tag** of :data:`SWEPT_LIBRARIES`, in every call ``variants`` makes for it (the parity
   harness's inventory), through BOTH real engines: Django's (the Python tag) and
   ``DjustTemplateBackend`` (the Rust engine; a native handler wins).
2. **Every component class** the URL sweep drives (``url_sweep.component_classes``), under each
   of the three ``css_framework`` renderers.
3. **Each in both modes** of ``LIVEVIEW_CONFIG["csp_strict_styles"]`` (#3979), with a request
   that carries a CSP nonce (:data:`NONCE`).

What it reports (:func:`findings`) is ``(kind, detail)`` per output:

* ``style-attr``: a ``style`` attribute, with its value;
* ``style-element``: a ``<style>`` without the nonce;
* ``script-inline``: an executable ``<script>`` with no ``src`` and without the nonce.
  Executable is what the HTML standard runs as a classic or module script: no ``type`` or an
  empty one, ``module``, or a JavaScript MIME type (:func:`is_javascript_type`). A script whose
  ``type`` is data (``application/json``, ``application/ld+json``) or not JavaScript at all
  (``djust/hook``) is never run by the browser, so it is not a finding;
* ``handler``: an ``on*`` event-handler attribute, as ``<element attribute>``;
* ``js-url``: a ``javascript:`` URL in ``href`` / ``src`` / ``action`` / ``formaction`` /
  ``xlink:href``, as ``<element attribute>``.

The judgement of what is allowed in each mode is the test's
(``python/djust/tests/test_csp_constant_styles_4139.py``), not this module's.

Like ``harness`` this configures Django: it only ever runs in a FRESH process
(``scripts/template-library-csp-sweep.py``).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from . import harness, url_sweep, variants

#: The libraries the URL sweep drives, and the two that write inline blocks of their own.
SWEPT_LIBRARIES = {
    **url_sweep.SWEPT_LIBRARIES,
    "live_tags": "djust.templatetags.live_tags",
    "djust_pwa": "djust.templatetags.djust_pwa",
}

#: The nonce the sweep's request carries (django-csp's ``request.csp_nonce``).
NONCE = "sweep4139nonce"

MODES = ("default", "strict")

#: The JavaScript MIME type essences of the HTML standard ("JavaScript MIME type", WHATWG MIME
#: Sniffing): a ``<script type>`` whose essence is one of these runs as a classic script.
JS_MIME_ESSENCES = frozenset(
    {
        "application/ecmascript",
        "application/javascript",
        "application/x-ecmascript",
        "application/x-javascript",
        "text/ecmascript",
        "text/javascript",
        "text/javascript1.0",
        "text/javascript1.1",
        "text/javascript1.2",
        "text/javascript1.3",
        "text/javascript1.4",
        "text/javascript1.5",
        "text/jscript",
        "text/livescript",
        "text/x-ecmascript",
        "text/x-javascript",
    }
)


def is_javascript_type(value: str) -> bool:
    """Whether a ``<script type>`` value runs: missing or empty, ``module``, or a JavaScript
    MIME type matched on its essence (parameters stripped, ASCII case-insensitive)."""
    text = value.strip().lower()
    if text in ("", "module"):
        return True
    essence = text.split(";", 1)[0].strip()
    return essence in JS_MIME_ESSENCES


#: The attributes whose ``javascript:`` URL a browser runs (``xlink:href`` on SVG ``<a>``).
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "xlink:href"})

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_OPEN = re.compile(r"<(style|script)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.I)


def _attr_map(attrs: str) -> dict[str, str]:
    return {
        a.name: a.value
        for a in url_sweep.attributes("<x %s>" % attrs)
        if a.element == "x"
    }


def findings(markup: str) -> list[tuple[str, str]]:
    """``[(kind, detail)]`` for everything in ``markup`` a strict CSP refuses (see the module)."""
    out: list[tuple[str, str]] = []
    for attr in url_sweep.attributes(markup):
        if attr.name == "style":
            out.append(("style-attr", "<%s style=%r>" % (attr.element, attr.value)))
        elif attr.name.startswith("on") and len(attr.name) > 2:
            out.append(("handler", "<%s %s>" % (attr.element, attr.name)))
        elif attr.name in _URL_ATTRS and url_sweep.scheme_of(attr.value) == "javascript":
            out.append(("js-url", "<%s %s>" % (attr.element, attr.name)))
    text = _COMMENT.sub("", markup)
    for m in _OPEN.finditer(text):
        element = m.group(1).lower()
        attrs = _attr_map(m.group(2))
        if attrs.get("nonce") == NONCE:
            continue
        if element == "style":
            body = text[m.end() : text.find("</style", m.end())]
            out.append(("style-element", body.strip()[:120]))
            continue
        if not is_javascript_type(attrs.get("type", "")) or "src" in attrs:
            continue
        body = text[m.end() : text.find("</script", m.end())]
        out.append(("script-inline", body.strip()[:120]))
    return out


@dataclass
class Render:
    mode: str
    #: ``"django"`` / ``"djust"`` for a tag, ``"class:<framework>"`` for a component class
    engine: str
    #: ``"<lib>:<tag>"`` or ``"<module>.<Class>"``
    key: str
    variant: str
    findings: list[tuple[str, str]]


@dataclass
class Sweep:
    renders: list[Render] = field(default_factory=list)
    #: what was rendered at least once, per mode and engine: ``{(mode, engine): {key}}``
    rendered: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    #: every tag and component class the sweep set out to render
    inventory: list[str] = field(default_factory=list)
    #: what it did not render, and why: :data:`NOT_RENDERED` and the closing tags
    skipped: dict[str, str] = field(default_factory=dict)
    probe: dict[str, str] = field(default_factory=dict)


def inventory() -> dict[str, variants.TagInfo]:
    """:func:`harness.inventory` over :data:`SWEPT_LIBRARIES`."""
    saved = dict(harness.LIBRARIES)
    harness.LIBRARIES.clear()
    harness.LIBRARIES.update(SWEPT_LIBRARIES)
    try:
        return harness.inventory()
    finally:
        harness.LIBRARIES.clear()
        harness.LIBRARIES.update(saved)


@contextlib.contextmanager
def mode(name: str) -> Iterator[None]:
    from djust.config import config

    before = config.get("csp_strict_styles", False)
    config.set("csp_strict_styles", name == "strict")
    try:
        yield
    finally:
        config.set("csp_strict_styles", before)


@contextlib.contextmanager
def framework(name: str) -> Iterator[None]:
    from djust.config import config

    before = config.get("css_framework", "bootstrap5")
    config.set("css_framework", name)
    try:
        yield
    finally:
        config.set("css_framework", before)


def request() -> Any:
    """A GET request carrying :data:`NONCE` as django-csp sets it."""
    from django.test import RequestFactory

    req = RequestFactory().get("/")
    req.csp_nonce = NONCE
    return req


def _render_tag(engine: Any, src: str, extra: Optional[dict[str, Any]] = None) -> Optional[str]:
    harness.reset_ids()
    req = request()
    # In the context too, as ``django.template.context_processors.request`` puts it: the tags
    # read their nonce from ``context["request"]``.
    ctx = {**harness.make_context("bound_form" in src), "request": req, **(extra or {})}
    try:
        return str(engine.from_string(src).render(ctx, req))
    except Exception:  # noqa: BLE001 - a call the tag rejects draws nothing to read
        return None


#: Calls the generic variants cannot make, per tag: ``(source, extra context)``. A tag that
#: needs an object (a bound form, the audio manifest) or a body the variants do not write.
EXTRA_CALLS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "live_tags:dj_activity": [
        ('{% load live_tags %}{% dj_activity "panel" visible=False %}x{% enddj_activity %}', {}),
        ('{% load live_tags %}{% dj_activity "panel" visible=True %}x{% enddj_activity %}', {}),
    ],
    "live_tags:djust_audio": [
        ("{% load live_tags %}{% djust_audio %}", {"djust_audio_manifest": "/audio.json"}),
    ],
    "theme_form_tags:theme_form": [
        ("{% load theme_form_tags %}{% theme_form form=bound_form %}", {}),
        ('{% load theme_form_tags %}{% theme_form form=bound_form layout="horizontal" %}', {}),
    ],
    "theme_form_tags:theme_form_errors": [
        ("{% load theme_form_tags %}{% theme_form_errors form=bound_form %}", {}),
    ],
    # The caller's own ``onclick=`` (#4146): the one handler djust writes because it was asked
    # to. :data:`CALLER_HANDLER_VARIANTS` names this call; a handler in any other call is djust's.
    "theme_components:theme_button": [
        ('{% load theme_components %}{% theme_button "Go" onclick="go()" %}', {}),
    ],
}

#: The calls that pass a caller's ``onclick=`` (#4146), per key: the variant names the test
#: excuses a ``<button onclick>`` in. The tag's is its ``EXTRA_CALLS`` entry; the component
#: class's is a second render with :data:`CALLER_ONCLICK_KWARGS`.
CALLER_HANDLER_VARIANTS: dict[str, str] = {
    "theme_components:theme_button": "extra_0",
    "djust.components.components.button.Button": "caller_onclick",
}

#: What the ``caller_onclick`` render of a component class in :data:`CALLER_HANDLER_VARIANTS` adds.
CALLER_ONCLICK_KWARGS = {"onclick": "go()"}

#: The arguments a ``*_block`` tag requires (its inline twin's, less the one the body fills).
BLOCK_ARGS = {
    "theme_components:theme_dropdown_block": 'id="d1" label="Menu"',
    "theme_components:theme_modal_block": 'id="m1"',
    "theme_components:theme_nav_group_block": 'label="Group"',
    "theme_components:theme_tooltip_block": 'text="Tip"',
}

#: Tags the sweep does not render, and why. Derived reasons only; a new tag is rendered.
NOT_RENDERED = {
    "live_tags:live_render": (
        "embeds a whole child LiveView: its markup is that view's template, not the tag's"
    ),
}

#: Component classes that need more than their required arguments to construct.
EXTRA_KWARGS: dict[str, dict[str, Any]] = {
    "djust.components.components.stat_card.StatCard": {"label": "x", "value": "1"},
}


def _is_closer(info: variants.TagInfo, infos: dict[str, variants.TagInfo]) -> bool:
    """``end_theme_card`` and the like: a closing tag, rendered by its opening tag."""
    return info.name.startswith("end") and any(
        i.lib == info.lib and i.name != info.name and info.name.endswith(i.name.removesuffix("_block"))
        for i in infos.values()
    )


def _calls(info: variants.TagInfo, infos: dict[str, variants.TagInfo]) -> list[variants.Variant]:
    key = "%s:%s" % (info.lib, info.name)
    if info.name.endswith("_block"):
        # ``theme_card_block``: a body tag the variants do not close; as the URL sweep writes it,
        # with the arguments the inline form requires besides the one the body fills.
        args = " ".join([*url_sweep.typical_args(info, ""), BLOCK_ARGS.get(key, "")]).strip()
        return [variants.Variant("block", url_sweep.wrap(info, args, None, ""))]
    blockness = {i.name: "block" in i.native or i.is_block for i in infos.values()}
    calls = variants.make_variants(
        info, lambda child: infos.get("%s:%s" % (info.lib, child)), blockness
    )
    return calls + [
        variants.Variant("extra_%d" % i, src) for i, (src, _ctx) in enumerate(EXTRA_CALLS.get(key, []))
    ]


def _extra_context(key: str, call: variants.Variant) -> dict[str, Any]:
    if call.name.startswith("extra_"):
        return EXTRA_CALLS[key][int(call.name.split("_")[1])][1]
    return {}


def _passes_nonce(info: variants.TagInfo, call: variants.Variant) -> bool:
    """Whether ``call`` hands the tag a nonce of its own, by name or by position."""
    if "nonce=" in call.src:
        return True
    # As ``variants.make_variants`` builds its ``positional_N`` calls.
    positional = [s[0] for s in info.sig if s[2] == "POSITIONAL_OR_KEYWORD" and s[0] != "context"]
    return call.name.startswith("positional_") and "nonce" in positional[: int(call.name[-1])]


#: A component class gets what a typical call passes, besides its required arguments.
COMPONENT_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")


def run_sweep(only: Optional[set[str]] = None, plant: Optional[str] = None) -> Sweep:
    """Render every tag and component class in both modes and read each output.

    ``plant`` is ``"<lib>:<tag>"``: the PYTHON tag's output gets a constant inline style
    appended (gate-off: the sweep must report it, in both modes)."""
    from django.template import engines

    python_engine = engines["django"]
    rust_engine = engines["djust"]
    infos = inventory()
    classes = url_sweep.component_classes()
    sweep = Sweep(probe=harness._probe())
    sweep.inventory = sorted(
        k for k in [*infos, *classes] if not only or k in only or k.split(":")[-1] in only
    )
    for mode_name in MODES:
        with mode(mode_name):
            for key, info in infos.items():
                if only and key not in only and info.name not in only:
                    continue
                if key in NOT_RENDERED:
                    sweep.skipped[key] = NOT_RENDERED[key]
                    continue
                if _is_closer(info, infos):
                    sweep.skipped[key] = "a closing tag, rendered by its opening tag"
                    continue
                for call in _calls(info, infos):
                    if _passes_nonce(info, call):
                        # The author's own nonce (or an explicitly empty one) wins over the
                        # request's: that call says nothing about what the tag does by itself.
                        continue
                    for engine_name, engine in (("django", python_engine), ("djust", rust_engine)):
                        planting = (
                            url_sweep.planted_hole(key, markup='<i style="display:none"></i>')
                            if plant == key and engine_name == "django"
                            else contextlib.nullcontext()
                        )
                        with planting:
                            out = _render_tag(engine, call.src, _extra_context(key, call))
                        if out is None:
                            continue
                        sweep.rendered.setdefault((mode_name, engine_name), set()).add(key)
                        sweep.renders.append(
                            Render(mode_name, engine_name, key, call.name, findings(out))
                        )
            for key, cls in classes.items():
                if only and key not in only and cls.__name__ not in only:
                    continue
                _names, required = url_sweep.component_params(cls)
                required = {**required, **EXTRA_KWARGS.get(key, {})}
                calls = [("required", required)]
                if CALLER_HANDLER_VARIANTS.get(key) == "caller_onclick":
                    calls.append(("caller_onclick", {**required, **CALLER_ONCLICK_KWARGS}))
                for fw in COMPONENT_FRAMEWORKS:
                    for variant_name, kwargs in calls:
                        with framework(fw):
                            harness.reset_ids()
                            try:
                                out = str(cls(**kwargs).render())  # type: ignore[call-arg]
                            except Exception:  # noqa: BLE001 - a class that needs more draws nothing
                                continue
                        engine_name = "class:" + fw
                        sweep.rendered.setdefault((mode_name, engine_name), set()).add(key)
                        sweep.renders.append(
                            Render(mode_name, engine_name, key, variant_name, findings(out))
                        )
    return sweep
