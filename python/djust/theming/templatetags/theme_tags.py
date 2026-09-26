"""
Template tags for djust_theming.

Usage:
    {% load theme_tags %}

    <!-- In <head> -->
    {% theme_head %}

    <!-- Theme switcher component -->
    {% theme_switcher %}

    <!-- Simple mode toggle -->
    {% theme_mode_toggle %}

    <!-- Preset selector -->
    {% theme_preset_selector layout="dropdown" %}
"""

import functools
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django import template
from django.http import HttpRequest
from django.template import Context
from django.template.loader import render_to_string
from django.urls import reverse, NoReverseMatch
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.http import urlencode

from ..components import PresetSelector, ThemeModeButton, ThemeSwitcher, ThemeSwitcherConfig
from ..component_css_generator import generate_component_css
from ..manager import (
    generate_critical_css_for_state,
    generate_css_for_state,
    get_css_prefix,
    get_direction,
    get_theme_config,
    get_theme_manager,
)
from ..template_resolver import resolve_theme_template

if TYPE_CHECKING:
    from ..manager import ThemeManager

register = template.Library()


def build_theme_head_context(
    request: HttpRequest | None,
    include_js: bool = True,
    link_css: bool = False,
    loading_class: bool = True,
    manager: "ThemeManager | None" = None,
) -> dict[str, Any]:
    """Build the full context dict consumed by ``djust_theming/theme_head.html``.

    Single source of truth for the ``theme_head`` render context. Both the
    ``{% theme_head %}`` simple tag and ``ThemeMixin._setup_theme_context()``
    call this so the two render paths cannot drift (#1531 — the #1452 drift,
    repeated for the ThemeMixin path). ``theme_head.html`` consumes nine
    variables; a hand-built sub-dict silently drops the rest.

    Args:
        request: The current request (used to resolve the theme manager when
            ``manager`` is not supplied). May be ``None``.
        include_js: Emit the ``theme.js`` / ``components.js`` script tags.
        link_css: Use a ``<link>`` to the theme CSS endpoint instead of the
            critical-CSS inline split.
        loading_class: Add the ``loading`` class to ``documentElement`` in the
            anti-FOUC script. The ``{% theme_head %}`` tag passes ``True``
            (cold page load); ``ThemeMixin`` passes ``False`` — a LiveView
            mount is a reactive render, not a cold load, so no loading-class
            flash. This *intentional* divergence is a parameter precisely so
            it stays explicit while the *unintended* missing-key drift cannot
            recur.
        manager: An already-resolved ``ThemeManager``. When ``None`` (the tag
            path), the manager is resolved via ``get_theme_manager(request)``.

    Returns:
        A dict with keys: ``loading_class``, ``css_block``,
        ``deferred_css_block``, ``component_css_block``,
        ``include_component_link``, ``include_components_app_link``,
        ``include_js``, ``direction``,
        ``cookie_prefix_js``, ``resolved_mode_js`` — exactly the variables
        ``theme_head.html``
        consumes.
    """
    # Get current theme state
    if manager is None:
        manager = get_theme_manager(request)
    state = manager.get_state()
    config = get_theme_config()
    critical_css_enabled = config.get("critical_css", True)

    css_block = ""
    deferred_css_block = ""

    # Get css_prefix — needed for both CSS generation and component CSS
    css_prefix = get_css_prefix()

    if critical_css_enabled and not link_css:
        # Critical CSS split: inline critical, async-load deferred
        critical_css = generate_critical_css_for_state(state, css_prefix=css_prefix)
        css_block = f"<style data-djust-theme-critical>{critical_css}</style>"

        # Build deferred CSS URL
        try:
            deferred_url = reverse("djust_theming:deferred_theme_css")
            query_params = {"t": state.theme, "p": state.preset, "m": state.mode}
            if state.pack:
                query_params["pk"] = state.pack
            deferred_href = f"{deferred_url}?{urlencode(query_params)}"
            deferred_css_block = (
                f'<link rel="preload" href="{deferred_href}" as="style" '
                f"onload=\"this.onload=null;this.rel='stylesheet'\" data-djust-theme-deferred>"
                f'\n<noscript><link rel="stylesheet" href="{deferred_href}"></noscript>'
            )
        except NoReverseMatch:
            # Cannot resolve deferred URL — fall back to inlining everything
            css = generate_css_for_state(state, css_prefix=css_prefix)
            css_block = f"<style data-djust-theme>{css}</style>"
            deferred_css_block = ""
    elif link_css:
        try:
            url = reverse("djust_theming:theme_css")
            # Add cache buster based on state
            query_params = {"t": state.theme, "p": state.preset, "m": state.mode}
            if state.pack:
                query_params["pk"] = state.pack

            css_block = (
                f'<link rel="stylesheet" href="{url}?{urlencode(query_params)}" data-djust-theme>'
            )
        except NoReverseMatch:
            # Fallback to inline if URL not configured
            pass

    if not css_block:
        # Generate CSS inline (legacy behavior or fallback)
        css = generate_css_for_state(state, css_prefix=css_prefix)
        css_block = f"<style data-djust-theme>{css}</style>"

    # Component CSS: inline when prefix is set, static link otherwise
    component_css_block = ""
    include_component_link = True

    if css_prefix:
        # Generate prefixed component CSS inline
        component_css = generate_component_css(css_prefix)
        component_css_block = f"<style data-djust-components>{component_css}</style>"
        include_component_link = False

    # #1624: auto-include djust-components's components.css when the app is
    # installed. Layout rules for {% code_block %}, {% card %}, {% dj_button %}
    # spinners and other component tags live there. Detection is defensive —
    # apps.is_installed() raises if the app registry isn't populated (e.g.
    # before Django setup), so the call is wrapped in try/except and falls
    # back to no link.
    try:
        from django.apps import apps as django_apps

        include_components_app_link = django_apps.is_installed("djust.components")
    except Exception:  # noqa: BLE001 — defensive: never break theme_head
        include_components_app_link = False

    # Resolve text direction
    direction = get_direction()

    # #1158 — namespace prefix for theming cookies (cross-project isolation
    # on shared domains like localhost). JSON-encoded for safe inlining in a
    # <script> context (json.dumps gives a valid JS string literal).
    ns = (config.get("cookie_namespace") or "").strip()
    cookie_prefix = f"{ns}_" if ns else ""
    cookie_prefix_js = json.dumps(cookie_prefix)

    return {
        "loading_class": loading_class,
        "css_block": css_block,
        "deferred_css_block": deferred_css_block,
        "component_css_block": component_css_block,
        "include_component_link": include_component_link,
        "include_components_app_link": include_components_app_link,
        "include_js": include_js,
        "direction": direction,
        "cookie_prefix_js": cookie_prefix_js,
        # The mode the *server* resolved — config default, session, or cookie.
        # The anti-FOUC script used to hardcode `'system'` as its fallback, so
        # a project that configured `default_mode: "dark"` still rendered in
        # whatever the OS preferred until the user clicked a toggle. Handing
        # the resolved mode to the script is what makes the configured default
        # actually the default. JSON-encoded like cookie_prefix_js, since it
        # is interpolated into a <script> literal.
        "resolved_mode_js": json.dumps(state.mode),
        # Cache-buster for the asset tags below. Without one a browser keeps the
        # `components.js` / `components.css` it already downloaded: Django's
        # static server sends no `Cache-Control`, so browsers fall back to
        # heuristic freshness — a fraction of the file's age — and revalidate
        # only once that elapses. A fix to either file is then invisible on the
        # page it was made for, which reads as "the fix didn't work". Production
        # avoids this with hashed filenames (`ManifestStaticFilesStorage`); a
        # dev server serving the source does not.
        "asset_version": _theme_asset_version(),
    }


def _theme_asset_version() -> str:
    """A short token that changes whenever a theming static asset does.

    Derived from the newest mtime under the theming static tree rather than
    from the package version, because the case that bites is an edit — someone
    changes `components.css`, reloads, and sees the old stylesheet. A
    release-keyed token would not move until the next release, which is exactly
    when it is not needed.

    Both static trees are scanned, because `theme_head.html` links assets from
    each: the theming package's own, and `djust_components/components.css` from
    the optional components app. Scanning only the first would leave a fix to
    the second uncached — which is exactly the file whose spinner rules were
    written but never reaching the page.

    Cheap enough to compute per render: the trees are a handful of files.
    """
    app_static = Path(__file__).resolve().parent.parent / "static"
    bases = [app_static / "djust_theming"]
    # djust/components/static/djust_components — a sibling app, not a parent.
    components_base = app_static.parent.parent / "components" / "static" / "djust_components"
    if components_base.is_dir():
        bases.append(components_base)

    newest = 0.0
    for base in bases:
        for path in base.rglob("*"):
            if path.suffix not in (".js", ".css"):
                continue
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:  # pragma: no cover — a file that vanished mid-scan
                continue
    return f"{int(newest):x}"


@register.simple_tag(takes_context=True)
def theme_head(context: Context, include_js: bool = True, link_css: bool = False) -> SafeString:
    """
    Render theme CSS and anti-FOUC script in the <head>.

    Usage:
        {% theme_head %}
        {% theme_head include_js=False %}
        {% theme_head link_css=True %}

    Renders via the shared ``djust_theming/theme_head.html`` template:

    - Anti-flash script (runs before page render to set correct theme)
    - Theme CSS (either inline <style> or <link> tag)
    - Component CSS (``components.css`` via <link> tag)
    - Optionally, the theme.js script tag

    The component CSS file contains styles for all template-tag components
    (alert, badge, button, card, input, theme-switcher). It is loaded once
    regardless of how many components are rendered on the page.

    The render context is built by :func:`build_theme_head_context`, the
    shared builder also used by ``ThemeMixin._setup_theme_context()`` so
    the two paths cannot drift (#1531).
    """
    request = context.get("request")
    head_ctx = build_theme_head_context(request, include_js=include_js, link_css=link_css)
    html = render_to_string("djust_theming/theme_head.html", head_ctx)
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def theme_css(context: Context) -> SafeString:
    """
    Render only the theme CSS (no scripts).

    Useful when you want more control over script placement.

    Usage:
        {% theme_css %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()

    css = generate_css_for_state(state, css_prefix=get_css_prefix())

    return format_html("<style data-djust-theme>{}</style>", mark_safe(css))


@register.simple_tag(takes_context=True)
def theme_css_link(context: Context) -> SafeString:
    """
    Render a ``<link>`` to ``/_theming/theme.css`` with cache-busting URL params.

    Chrome's ``Vary: Cookie`` handling is unreliable for per-cookie dynamic
    content: after a pack switch, the browser often serves the prior pack's
    CSS from its own HTTP cache and the page renders with the stale palette
    until manual cache clear. The fix is to make different pack/mode produce
    a different URL — the browser then can't re-use the cached body. (#1012)

    Usage::

        <link rel="stylesheet" href="{% theme_css_link %}">

    Or directly drop the tag where you'd put the URL — it returns the URL
    string when used inside an ``href=""``. The tag reads the same
    ``ThemeManager.get_state()`` the view itself reads, so the link URL and
    the served body stay in lockstep.
    """
    from django.urls import NoReverseMatch, reverse

    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()

    try:
        base_url = reverse("djust_theming:theme_css")
    except NoReverseMatch:
        # URL not mounted (e.g. test environment that doesn't include
        # djust_theming.urls). Fall back to a stable path so templates
        # that include the tag don't crash.
        base_url = "/_theming/theme.css"

    # ThemeState is a dataclass, not a dict — use attribute access.
    pack = (getattr(state, "pack", None) or "").strip()
    mode = (getattr(state, "resolved_mode", None) or getattr(state, "mode", None) or "").strip()
    preset = (getattr(state, "preset", None) or "").strip()

    params = {}
    if pack:
        params["p"] = pack
    if mode:
        params["m"] = mode
    if preset:
        params["r"] = preset

    # Query values are URL-encoded and the whole URL is HTML-escaped, so the
    # result is safe to drop into an ``href="..."`` attribute as-is.
    full = f"{base_url}?{urlencode(params)}" if params else base_url
    return escape(full)


@register.simple_tag(takes_context=True)
def theme_framework_overrides(context: Context) -> str:
    """
    Render theme-aware CSS overrides for the active CSS framework.

    Maps djust theme variables (--primary, --border, --ring, etc.) onto the
    framework's form, button, badge, and alert selectors. Place this tag
    AFTER your framework's CSS file so the theme-based rules take precedence.

    Usage:
        <link rel="stylesheet" href="bootstrap4.css">
        {% theme_framework_overrides %}
        <link rel="stylesheet" href="base.css">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()

    if not state.pack:
        return ""

    try:
        from ..pack_css_generator import ThemePackCSSGenerator

        gen = ThemePackCSSGenerator(pack_name=state.pack)
        fw_css = gen._generate_framework_css()
        if fw_css:
            # format_html returns a SafeString; the local annotation narrows the
            # untyped-boundary Any (django.utils.html is unstubbed here) to str.
            overrides: str = format_html(
                "<style data-djust-framework-overrides>{}</style>", mark_safe(fw_css)
            )
            return overrides
    except (ValueError, ImportError):
        # Pack CSS generator is optional (older installs / missing pack); emit nothing.
        pass

    return ""


@register.simple_tag(takes_context=True)
def theme_switcher(
    context: Context,
    show_presets: bool = True,
    show_mode_toggle: bool = True,
    show_labels: bool = True,
    dropdown_position: str = "bottom-end",
    button_class: str = "",
    dropdown_class: str = "",
) -> SafeString:
    """
    Render the full theme switcher component.

    Usage:
        {% theme_switcher %}
        {% theme_switcher show_presets=False %}
        {% theme_switcher show_labels=False button_class="btn btn-sm" %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)

    config = ThemeSwitcherConfig(
        show_presets=show_presets,
        show_mode_toggle=show_mode_toggle,
        show_labels=show_labels,
        dropdown_position=dropdown_position,
        button_class=button_class,
        dropdown_class=dropdown_class,
    )

    switcher = ThemeSwitcher(theme_manager=manager, config=config)
    tmpl = resolve_theme_template(request, "theme_switcher")
    html = tmpl.render(switcher.get_context())
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def theme_mode_toggle(
    context: Context, button_class: str = "", show_label: bool = False
) -> SafeString:
    """
    Render a simple theme mode toggle button.

    Usage:
        {% theme_mode_toggle %}
        {% theme_mode_toggle button_class="btn btn-outline-secondary" %}
        {% theme_mode_toggle show_label=True %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)

    button = ThemeModeButton(
        theme_manager=manager,
        button_class=button_class,
        show_label=show_label,
    )
    return mark_safe(button.render())


@register.simple_tag(takes_context=True)
def theme_preset_selector(
    context: Context,
    layout: str = "dropdown",
    show_descriptions: bool = True,
    dropdown_class: str = "",
) -> SafeString:
    """
    Render theme preset selector.

    Usage:
        {% theme_preset_selector %}
        {% theme_preset_selector layout="grid" %}
        {% theme_preset_selector layout="list" show_descriptions=True %}
    """
    request = context.get("request")
    manager = get_theme_manager(request)

    selector = PresetSelector(
        theme_manager=manager,
        show_descriptions=show_descriptions,
        layout=layout,
        dropdown_class=dropdown_class,
    )
    return mark_safe(selector.render())


@register.simple_tag(takes_context=True)
def theme_panel(
    context: Context,
    show_mode: bool = True,
    show_packs: bool = True,
    show_presets: bool = True,
    show_design: bool = True,
    show_layout: bool = True,
) -> SafeString:
    """
    Render a combined theme settings panel in a single dropdown.

    Includes mode toggle, theme pack selector, color preset, and design
    system — all in one compact dropdown behind a gear icon.

    Usage:
        {% theme_panel %}
        {% theme_panel show_packs=False %}
        {% theme_panel show_design=False %}
    """
    from ..theme_packs import get_all_design_systems, get_all_theme_packs

    request = context.get("request")
    manager = get_theme_manager(request)
    state = manager.get_state()
    presets = manager.get_available_presets()

    # Build design system list with display names
    designs = [
        {"name": name, "display_name": name.replace("_", " ").title()}
        for name in sorted(get_all_design_systems().keys())
    ]

    # Build theme pack list
    packs = [
        {"name": name, "display_name": pack.display_name, "description": pack.description}
        for name, pack in sorted(get_all_theme_packs().items())
    ]

    # Build layout list
    layouts = [
        {"name": "", "display_name": "Base"},
        {"name": "sidebar", "display_name": "Sidebar"},
        {"name": "topbar", "display_name": "Top Bar"},
        {"name": "sidebar-topbar", "display_name": "Sidebar + Top Bar"},
        {"name": "dashboard", "display_name": "Dashboard Grid"},
        {"name": "centered", "display_name": "Centered"},
    ]

    tmpl = resolve_theme_template(request, "components/theme_panel")
    return mark_safe(
        tmpl.render(
            {
                "show_mode": show_mode,
                "show_packs": show_packs,
                "show_presets": show_presets,
                "show_design": show_design,
                "show_layout": show_layout,
                "presets": presets,
                "designs": designs,
                "packs": packs,
                "layouts": layouts,
                "current_pack": state.pack or "",
                "current_design": getattr(state, "theme", "") or "ios",
                "current_layout": getattr(state, "layout", "") or "",
                "theme_mode": state.mode,
            }
        )
    )


@register.simple_tag(takes_context=True)
def theme_preset(context: Context) -> str:
    """
    Get current theme preset name.

    Usage:
        <body class="theme-{% theme_preset %}">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    return manager.get_state().preset


@register.simple_tag(takes_context=True)
def theme_mode(context: Context) -> str:
    """
    Get current theme mode setting.

    Returns 'light', 'dark', or 'system'.

    Usage:
        <body data-theme-setting="{% theme_mode %}">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    return manager.get_state().mode


@register.simple_tag(takes_context=True)
def theme_resolved_mode(context: Context) -> str:
    """
    Get resolved theme mode (always 'light' or 'dark').

    Usage:
        <body class="{% theme_resolved_mode %}">
    """
    request = context.get("request")
    manager = get_theme_manager(request)
    return manager.get_state().resolved_mode


@register.simple_tag
def theme_asset_version() -> str:
    """The cache-buster token, for templates that link assets outside `theme_head`.

    `theme_head` stamps its own links itself. A page that adds a stylesheet of
    its own has no way to reach that token, and so links it bare — which is how
    the catalogue came to load `djust_components/components.css` twice, once
    versioned and once not, with the unversioned copy second and therefore
    winning. A bare link is a link that goes stale on the next edit.

    Usage:
        <link rel="stylesheet" href="{% static 'djust_components/components.css' %}?v={% theme_asset_version %}">
    """
    return _theme_asset_version()


# ---------------------------------------------------------------------------
# Catalogue preview (ADR-032)
# ---------------------------------------------------------------------------

# Compiled lazily through a private ``Engine``: ``django.template.Template``
# needs a configured ``DjangoTemplates`` backend, and a ``djust new`` project
# configures only ``DjustTemplateBackend`` — a module-level ``Template(...)``
# here broke the import of every theme tag in such a project.
_PREVIEW_SOURCE = """{% load djust_components %}<section class="dc-section" id="dc-preview">
{% card title="Preview" %}
  {% if options %}
  <div class="dc-options">
    {% for opt in options %}
    <div class="dc-option-row">
      <span class="dc-option-key">{{ opt.key }}</span>
      {% toggle_group name=opt.key options=opt.choices value=opt.current event="set_option" size="sm" %}
    </div>
    {% endfor %}
  </div>
  <div class="dc-preview">{{ playground_html|safe }}</div>
  {{ playground_feedback|safe }}
  {{ playground_code_html }}
  {% else %}
    {% for ex in examples_html %}
    <div class="dc-example">
      <div class="dc-preview">{{ ex.html|safe }}</div>
      {{ ex.feedback|safe }}
      {% if ex.kwargs_display %}<details class="dc-example-args"><summary>Arguments</summary><pre>{{ name }}({{ ex.kwargs_display }})</pre></details>{% endif %}
    </div>
    {% empty %}
    <div class="dc-preview dc-preview--empty">{{ empty_reason }}</div>
    {% endfor %}
  {% endif %}
  {% if more_examples %}
  <div class="dc-subtitle">More examples</div>
  {% for ex in more_examples %}
  <div class="dc-example">
    <div class="dc-preview">{{ ex.html|safe }}</div>
    {% if ex.kwargs_display %}<details class="dc-example-args"><summary>Arguments</summary><pre>{{ name }}({{ ex.kwargs_display }})</pre></details>{% endif %}
  </div>
  {% endfor %}
  {% endif %}
{% if preview_note %}<p class="dc-note">{{ preview_note }}</p>{% endif %}
{% endcard %}
</section>"""


@functools.lru_cache(maxsize=1)
def _preview_template() -> Any:
    from django.template import Engine

    return Engine(
        autoescape=True,
        libraries={"djust_components": "djust.components.templatetags.djust_components"},
    ).from_string(_PREVIEW_SOURCE)


def _highlighted_python(code: str) -> str:
    if not code:
        return ""
    from djust.components.components.code_snippet import CodeSnippet

    return str(CodeSnippet(code=code, language="python").render())


@register.simple_tag
def component_preview(
    component_name: str,
    component_type: str,
    examples: Any,
    values: Any,
    playground: Any = None,
) -> SafeString:
    """The catalogue page's preview, rendered from the preview component's
    State (`live_views.Preview`, ADR-032), out of the components it shows.

    One Preview card. When the examples expose enumerable kwargs (a token
    string with two or more values across the examples, or a bool) the card
    is a playground: a `toggle_group` per kwarg, the first example rendered
    with the reader's choices, and the call that produces it in a
    `code_snippet`. Examples the chips can reproduce are not repeated; the
    ones that show something else (a slot, an icon, different content) follow
    as "More examples". Without options the examples are the preview.
    """
    from ..gallery.live_views import render_preview_examples
    from ..gallery.catalogue import playground_options
    from ..gallery.component_registry import empty_preview_reason, preview_note

    examples = list(examples or [])
    values = dict(values or {})
    playground = dict(playground or {})
    rendered = render_preview_examples(component_name, component_type, examples, values)

    options: list = []
    playground_html = ""
    playground_feedback = ""
    playground_call = ""
    more_examples: list = []
    if examples:
        base = {**examples[0], **values}
        for opt in playground_options(examples):
            current = playground.get(opt["key"], base.get(opt["key"]))
            options.append(
                {
                    "key": opt["key"],
                    "choices": [
                        {"value": f"{opt['key']}:{str(v).lower()}", "label": str(v)}
                        for v in opt["values"]
                    ],
                    "current": f"{opt['key']}:{str(current).lower()}",
                }
            )
        if options:
            chosen = {**base, **playground}
            shown = render_preview_examples(component_name, component_type, [chosen], {})
            playground_html = shown[0]["html"] if shown else ""
            playground_feedback = shown[0].get("feedback", "") if shown else ""
            playground_call = (
                f"{component_name}("
                + ", ".join(
                    f"{k}={v!r}"
                    for k, v in chosen.items()
                    if not k.startswith(("slot_", "__preview_"))
                )
                + ")"
            )
            # An example is "more" only if it shows something the chips cannot:
            # a slot, a structural kwarg (items, columns …), a bool the chips
            # do not cover. A different label or message alongside a different
            # variant is the same example with other words.
            option_keys = {o["key"] for o in options}
            first = examples[0]

            def shows_more(key: str, value: Any) -> bool:
                if key in option_keys:
                    return False
                if key.startswith("slot_"):
                    return True
                return not isinstance(value, str)

            for example, html in zip(examples, rendered):
                keys = set(example) | set(first)
                if any(
                    example.get(k) != first.get(k) and shows_more(k, example.get(k, first.get(k)))
                    for k in keys
                ):
                    more_examples.append(html)
    return mark_safe(
        _preview_template().render(
            Context(
                {
                    "name": component_name,
                    "component_type": component_type,
                    "examples_html": rendered,
                    "options": options,
                    "playground_html": playground_html,
                    "playground_feedback": playground_feedback,
                    "playground_call": playground_call,
                    # The Python component, not the ``{% code_snippet %}`` tag:
                    # the Rust engine renders the tag natively without the
                    # highlighting or the ``dj-copy`` the usage card has.
                    "playground_code_html": _highlighted_python(playground_call),
                    "more_examples": more_examples,
                    "empty_reason": empty_preview_reason(component_name),
                    "preview_note": preview_note(component_name),
                }
            )
        )
    )


#: Tags that must not survive into a card thumbnail. An ``<a>`` inside the
#: card's own ``<a>`` is INVALID HTML: the parser closes the outer anchor
#: before the inner one, which lifts the card out of its link and leaves an
#: empty anchor holding a grid cell — a visible hole in the index. Thirteen
#: components render links (breadcrumb, nav, pagination, a table of
#: contents…), so thirteen cells were empty.
#:
#: The same substitution answers an accessibility fault: the thumbnail is
#: ``aria-hidden``, and a focusable control inside an aria-hidden region is
#: reachable by keyboard but invisible to a screen reader. Classes are kept,
#: so a ``span.dj-btn`` still looks exactly like the button it previews.
_INERT_TAG_RE = re.compile(r"<(/?)(?:a|button)(\s[^>]*)?>", re.IGNORECASE)


#: ``href`` and ``type`` mean nothing on a ``span`` and are not valid there,
#: so they go with the tag. Everything else stays: the classes are what make
#: the preview look like the component.
_DEAD_ATTR_RE = re.compile(r"""\s(?:href|type)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)


def _inert_markup(html: str) -> str:
    """A component's markup with its interactive tags turned into spans."""
    if not html:
        return ""

    def to_span(match: "re.Match[str]") -> str:
        attrs = _DEAD_ATTR_RE.sub("", match.group(2) or "")
        return f"<{match.group(1)}span{attrs}>"

    return _INERT_TAG_RE.sub(to_span, html)


@functools.lru_cache(maxsize=256)
def _thumbnail_html(component_name: str) -> str:
    """A component's first example, rendered once per process, for the cards.

    Both kinds: a contracted component through its template, a python one
    through its class. This used to answer "" for anything not in
    ``COMPONENT_CONTRACTS``, on the grounds that a python component's
    examples "need runtime context the card cannot supply" — true when the
    registry had examples for the contracted 24 only. It now carries them for
    149 python components as well, and they render, so that rule was hiding a
    preview on 9 cards out of 10.

    Still "" when there is genuinely nothing to show: no example, or a
    component that renders nothing until opened (a modal, a tour). A blank
    beats a broken box, and a card without a preview still carries its name,
    type, category and description.
    """
    from djust.theming.contracts import COMPONENT_CONTRACTS

    from ..gallery.catalogue import _render_template_examples, build_catalogue_detail_context
    from ..gallery.component_registry import (
        PYTHON_COMPONENT_EXAMPLES,
        render_python_component_example,
    )

    from ..gallery.catalogue import INTERACTIVE_ENTRIES, interactive_preview_html

    try:
        if component_name in INTERACTIVE_ENTRIES:
            # Its preview is its canonical example view, rendered as-is.
            return _inert_markup(interactive_preview_html(component_name))
        if component_name in COMPONENT_CONTRACTS:
            examples = build_catalogue_detail_context(component_name, render_examples=False).get(
                "examples"
            )
            if not examples:
                return ""
            rendered = _render_template_examples(component_name, [examples[0]])
            return _inert_markup(rendered[0]["html"]) if rendered else ""

        examples = PYTHON_COMPONENT_EXAMPLES.get(component_name)
        if not examples:
            return ""
        return _inert_markup(render_python_component_example(component_name, dict(examples[0])))
    except Exception:  # noqa: BLE001 — a card thumbnail is never worth a 500
        return ""


@register.simple_tag
def component_thumbnail(component_name: str) -> SafeString:
    """`{% component_thumbnail comp.name as thumb %}` — the card's preview."""
    return mark_safe(_thumbnail_html(str(component_name)))
