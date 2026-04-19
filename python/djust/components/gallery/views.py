"""Django views for the component gallery.

Provides three views:
- ``gallery_index_view`` — landing page with category cards
- ``gallery_category_view`` — per-category page with rendered components
- ``gallery_view`` — legacy monolithic page with all components
"""

from django.http import HttpResponse, Http404
from django.template import Template, Context
from django.templatetags.static import static

from .registry import get_gallery_data


# ── Theme helpers ──


def _get_theme_css(preset="default", design_system="material", mode="light"):
    """Generate theme CSS from djust-theming, or return empty string if unavailable."""
    try:
        from djust_theming.manager import ThemeState, generate_css_for_state

        state = ThemeState(
            theme=design_system,
            preset=preset,
            mode=mode,
            resolved_mode=mode,
        )
        return generate_css_for_state(state)
    except Exception:
        return ""


def _get_theme_options():
    """Get available presets and design systems from djust-theming."""
    try:
        from djust_theming.presets import THEME_PRESETS
        from djust_theming.theme_packs import DESIGN_SYSTEMS

        presets = sorted(THEME_PRESETS.keys())
        systems = sorted(DESIGN_SYSTEMS.keys())
        return presets, systems
    except Exception:
        return ["default"], ["material"]


def _resolve_theme(request):
    """Read theme cookies, validate against allowlists, generate CSS.

    Returns (mode, theme_css, ds_options, preset_options).
    """
    presets, systems = _get_theme_options()

    design_system = request.COOKIES.get("gallery_ds", "material")
    if design_system not in systems:
        design_system = "material"

    preset = request.COOKIES.get("gallery_preset", "default")
    if preset not in presets:
        preset = "default"

    mode = request.COOKIES.get("gallery_mode", "light")
    if mode not in ("light", "dark"):
        mode = "light"

    theme_css = _get_theme_css(preset=preset, design_system=design_system, mode=mode)

    ds_options = "".join(
        f'<option value="{s}"{" selected" if s == design_system else ""}>'
        f"{s.replace('_', ' ').title()}</option>"
        for s in systems
    )
    preset_options = "".join(
        f'<option value="{p}"{" selected" if p == preset else ""}>'
        f"{p.replace('_', ' ').title()}</option>"
        for p in presets
    )

    return mode, theme_css, ds_options, preset_options


# ── Shared rendering functions ──


def _render_head(mode, theme_css, title="djust-components Gallery"):
    """Return the <head> block with theme CSS, component CSS, and gallery styles."""
    theming_base_link = ""
    try:
        theming_base_link = f'<link rel="stylesheet" href="{static("djust_theming/css/base.css")}">'
    except Exception:
        pass

    return f"""\
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {theming_base_link}
    <style data-djust-theme>{theme_css}</style>
    <link rel="stylesheet" href="{static("djust_components/components.css")}">
    <link rel="stylesheet" href="{static("djust_components/components-classes.css")}">
    <style>
        /* ── Reset — scoped to gallery layout only, not component internals ── */
        *, *::before, *::after {{ box-sizing: border-box; }}
        body {{ margin: 0; }}
        h1, h2, h3, p {{ margin: 0; }}
        .gallery-header, .gallery-sidebar, .gallery-content,
        .gallery-body, .category-section, .category-grid,
        .gallery-toolbar, .toolbar-group, .gallery-breadcrumb,
        .category-nav, .variant-section, .variant-label {{ margin: 0; padding: 0; }}

        body {{
            font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif);
            background: var(--color-bg);
            color: var(--color-text);
            line-height: 1.6;
        }}

        /* ── Layout ── */
        .gallery-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px 24px;
            border-bottom: 1px solid var(--color-border);
            position: sticky;
            top: 0;
            background: var(--color-bg);
            z-index: 100;
        }}

        .gallery-header h1 {{
            font-size: 1.25rem;
            font-weight: 600;
        }}

        .gallery-header h1 span {{
            color: var(--color-text-secondary);
            font-weight: 400;
            font-size: 0.875rem;
            margin-left: 8px;
        }}

        .gallery-header h1 a {{
            color: inherit;
            text-decoration: none;
        }}

        .gallery-toolbar {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .gallery-toolbar button {{
            padding: 6px 12px;
            border: 1px solid var(--color-border);
            background: var(--color-bg-subtle);
            color: var(--color-text);
            border-radius: var(--radius-md, 8px);
            cursor: pointer;
            font-size: 0.8rem;
        }}

        .gallery-toolbar button:hover {{
            border-color: var(--color-primary);
        }}

        .gallery-toolbar button.active {{
            background: var(--color-primary);
            color: hsl(var(--primary-foreground));
            border-color: var(--color-primary);
        }}

        .gallery-body {{
            display: flex;
            min-height: calc(100vh - 57px);
        }}

        /* ── Sidebar ── */
        .gallery-sidebar {{
            width: 220px;
            border-right: 1px solid var(--color-border);
            padding: 16px 0;
            position: sticky;
            top: 57px;
            height: calc(100vh - 57px);
            overflow-y: auto;
            flex-shrink: 0;
        }}

        .gallery-sidebar h3 {{
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--color-text-secondary);
            padding: 8px 16px 4px;
            margin-top: 8px;
        }}

        .gallery-sidebar a {{
            display: block;
            padding: 4px 16px;
            color: var(--color-text);
            text-decoration: none;
            font-size: 0.85rem;
        }}

        .gallery-sidebar a:hover {{
            background: var(--color-bg-subtle);
        }}

        .gallery-sidebar a.active {{
            color: var(--color-primary);
            font-weight: 600;
        }}

        /* ── Content ── */
        .gallery-content {{
            flex: 1;
            padding: 24px;
            max-width: 960px;
        }}

        .category-section {{
            margin-bottom: 48px;
        }}

        .category-section h2 {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--color-border);
        }}

        /* ── Component Card ── */
        .component-card {{
            border: 1px solid var(--color-border);
            border-radius: var(--radius-md, 8px);
            margin-bottom: 24px;
            overflow: hidden;
        }}

        .component-card-header {{
            padding: 12px 16px;
            background: var(--color-bg-subtle);
            border-bottom: 1px solid var(--color-border);
            font-weight: 600;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .component-card-header .tag-badge {{
            font-size: 0.65rem;
            padding: 2px 6px;
            border-radius: var(--radius-sm, 4px);
            background: var(--color-primary);
            color: hsl(var(--primary-foreground));
            font-weight: 500;
        }}

        .variant-section {{
            padding: 16px;
            border-bottom: 1px solid var(--color-border);
        }}

        .variant-section:last-child {{
            border-bottom: none;
        }}

        .variant-label {{
            font-size: 0.75rem;
            color: var(--color-text-secondary);
            margin-bottom: 8px;
            font-weight: 500;
        }}

        .variant-preview {{
            padding: 16px;
            background: var(--color-bg);
            border: 1px solid var(--color-border);
            border-radius: var(--radius-md, 8px);
            overflow: hidden;
            position: relative;
            transform: translateZ(0);
            min-height: 60px;
            max-height: 500px;
            overflow-y: auto;
            z-index: 0;
        }}

        .variant-preview .modal-overlay,
        .variant-preview .sheet-overlay,
        .variant-preview .sheet,
        .variant-preview .palette-overlay,
        .variant-preview .palette-dialog,
        .variant-preview .fab-container,
        .variant-preview .toast-container,
        .variant-preview .dj-connection-status,
        .variant-preview .dj-toast-container,
        .variant-preview .dj-confirm-dialog-backdrop,
        .variant-preview .dj-sidebar,
        .variant-preview .dj-sidebar__backdrop,
        .variant-preview .dj-bottom-sheet__backdrop,
        .variant-preview .dj-cookie-consent--bottom,
        .variant-preview .dj-cookie-consent--top,
        .variant-preview .dj-export-dialog__backdrop,
        .variant-preview .dj-lightbox,
        .variant-preview .dj-tour__overlay,
        .variant-preview .dj-tour__popover,
        .variant-preview .palette {{
            position: absolute !important;
        }}

        .preview-container {{
            transition: max-width 0.3s ease;
        }}

        .preview-container.mobile {{ max-width: 375px; }}
        .preview-container.tablet {{ max-width: 768px; }}
        .preview-container.desktop {{ max-width: none; }}

        .theme-toggle {{
            font-size: 1.1rem;
            background: none;
            border: 1px solid var(--color-border);
            border-radius: var(--radius-md, 8px);
            padding: 6px 10px;
            cursor: pointer;
            color: var(--color-text);
        }}

        .gallery-toolbar select {{
            padding: 6px 8px;
            border: 1px solid var(--color-border);
            background: var(--color-bg-subtle);
            color: var(--color-text);
            border-radius: var(--radius-md, 8px);
            font-size: 0.8rem;
            cursor: pointer;
        }}

        .gallery-toolbar label {{
            font-size: 0.7rem;
            color: var(--color-text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}

        .toolbar-group {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}

        /* ── Index Page ── */
        .category-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 16px;
        }}

        .category-card {{
            border: 1px solid var(--color-border);
            border-radius: var(--radius-md, 8px);
            padding: 20px;
            text-decoration: none;
            color: var(--color-text);
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }}

        .category-card:hover {{
            border-color: var(--color-primary);
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}

        .category-card h3 {{
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 4px;
        }}

        .category-card .count {{
            font-size: 0.85rem;
            color: var(--color-text-secondary);
        }}

        /* ── Breadcrumb ── */
        .gallery-breadcrumb {{
            font-size: 0.8rem;
            color: var(--color-text-secondary);
            margin-bottom: 16px;
        }}

        .gallery-breadcrumb a {{
            color: var(--color-primary);
            text-decoration: none;
        }}

        /* ── Prev/Next ── */
        .category-nav {{
            display: flex;
            justify-content: space-between;
            margin-top: 32px;
            padding-top: 16px;
            border-top: 1px solid var(--color-border);
        }}

        .category-nav a {{
            color: var(--color-primary);
            text-decoration: none;
            font-size: 0.9rem;
        }}
    </style>
</head>"""


def _render_header(ds_options, preset_options):
    """Return the sticky header HTML with theme/preview toolbar."""
    return f"""\
    <header class="gallery-header">
        <h1><a href="./">djust-components</a><span>Gallery</span></h1>
        <div class="gallery-toolbar">
            <div class="toolbar-group">
                <label>Design</label>
                <select id="design-system" onchange="changeTheme()">{ds_options}</select>
            </div>
            <div class="toolbar-group">
                <label>Preset</label>
                <select id="preset" onchange="changeTheme()">{preset_options}</select>
            </div>
            <button onclick="setPreview('mobile', event)">Mobile</button>
            <button onclick="setPreview('tablet', event)">Tablet</button>
            <button class="active" onclick="setPreview('desktop', event)">Desktop</button>
            <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" title="Toggle dark mode">&#127769;</button>
        </div>
    </header>"""


def _render_scripts():
    """Return the JS block for theme toggle, responsive preview, and dj-click shim."""
    return """\
    <script>
        function setCookie(name, value) {
            document.cookie = name + '=' + value + ';path=/;max-age=31536000;SameSite=Lax';
        }

        function toggleTheme() {
            const current = document.documentElement.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            setCookie('gallery_mode', next);
            window.location = window.location.pathname;
        }

        function changeTheme() {
            setCookie('gallery_ds', document.getElementById('design-system').value);
            setCookie('gallery_preset', document.getElementById('preset').value);
            setCookie('gallery_mode', document.documentElement.getAttribute('data-theme') || 'light');
            window.location = window.location.pathname;
        }

        (function() {
            const mode = document.documentElement.getAttribute('data-theme');
            document.getElementById('theme-toggle').textContent = mode === 'dark' ? '\\u2600\\uFE0F' : '\\uD83C\\uDF19';
        })();

        function setPreview(mode, e) {
            const main = document.querySelector('.gallery-content');
            main.classList.remove('mobile', 'tablet', 'desktop');
            main.classList.add(mode);
            document.querySelectorAll('.gallery-toolbar button:not(.theme-toggle)').forEach(b => b.classList.remove('active'));
            if (e && e.target) e.target.classList.add('active');
        }

        /* dj-click interactivity shim (capture phase) */
        document.addEventListener('click', function(e) {
            const el = e.target.closest('[dj-click]');
            if (!el) return;
            if (el.hasAttribute('onclick')) return;

            const action = el.getAttribute('dj-click');
            const value = el.getAttribute('data-value') || '';
            const preview = el.closest('.variant-preview');
            if (!preview) return;

            if (action === 'accordion_toggle') {
                const item = el.closest('.dj-accordion-item');
                if (item) {
                    item.classList.toggle('dj-accordion-item--open');
                    const chevron = el.querySelector('.dj-accordion__chevron');
                    if (chevron) chevron.classList.toggle('dj-accordion__chevron--open');
                }
            }

            if (action === 'set_tab') {
                const tabs = el.closest('.dj-tabs');
                if (tabs) {
                    tabs.querySelectorAll('.dj-tab').forEach(t => t.classList.remove('dj-tab--active'));
                    el.classList.add('dj-tab--active');
                    const pane = tabs.querySelector('.dj-tabs__pane');
                    if (pane) pane.textContent = value.charAt(0).toUpperCase() + value.slice(1) + ' content.';
                }
            }

            if (action === 'toggle_dropdown' || action === 'dropdown_toggle') {
                const dd = el.closest('.dj-dropdown, .dj-dropdown-menu');
                if (dd) dd.classList.toggle('dj-dropdown--open');
            }

            if (action === 'close_modal') {
                const backdrop = preview.querySelector('.dj-modal-backdrop, .modal-overlay');
                if (backdrop) backdrop.style.display = 'none';
            }

            if (action === 'close_sheet') {
                const overlay = preview.querySelector('.sheet-overlay, .dj-sheet-overlay');
                const sheet = preview.querySelector('.sheet');
                if (overlay) { overlay.style.opacity = '0'; overlay.style.visibility = 'hidden'; }
                if (sheet) sheet.style.transform = 'translateX(100%)';
            }

            if (action.startsWith('toggle_')) {
                const wrapper = el.closest('[class*="split-button"], [class*="context-menu"], [class*="notification-popover"], [class*="popconfirm"]');
                if (wrapper) wrapper.classList.toggle('is-open');
            }

            if (action.startsWith('dismiss_') || action === 'close') {
                const target = el.closest('[class*="toast"], [class*="alert"], [class*="banner"], [class*="announcement"]');
                if (target) target.style.display = 'none';
            }
        }, true);

        (function() {
            const style = document.createElement('style');
            style.textContent = `
                .dj-accordion-item .dj-accordion__content { max-height: 0; overflow: hidden; transition: max-height 0.25s ease, padding 0.25s ease; padding: 0 1rem; }
                .dj-accordion-item--open .dj-accordion__content { max-height: 500px; padding: 0.75rem 1rem; }
                .dj-accordion__chevron { transition: transform 0.2s ease; display: inline-block; }
                .dj-accordion__chevron--open { transform: rotate(0deg); }
                .dj-accordion-item:not(.dj-accordion-item--open) .dj-accordion__chevron { transform: rotate(-90deg); }
                .dj-dropdown--open .dj-dropdown__menu { display: block !important; }
                .dj-dropdown__menu { display: none; }
            `;
            document.head.appendChild(style);
        })();
    </script>"""


def _render_component_cards(components, extra_context=None):
    """Render component cards with variant previews. Returns HTML string.

    Args:
        components: List of component dicts with name, label, type, variants.
        extra_context: Optional dict merged into each template tag's render
            context.  Used by per-category gallery LiveViews to pass mixin state
            (e.g. ``{"active": "s1"}``) so interactive components reflect
            the current LiveView state.
    """
    parts = []
    for comp in components:
        anchor = comp["name"].lower().replace(" ", "-")
        comp_type = "tag" if comp["type"] == "tag" else "class"
        parts.append(f'<div class="component-card" id="{anchor}">')
        parts.append(
            f'<div class="component-card-header">'
            f"{comp['label']}"
            f'<span class="tag-badge">{comp_type}</span>'
            f"</div>"
        )

        for variant in comp["variants"]:
            rendered = ""
            if comp["type"] == "tag":
                try:
                    tpl_str = variant["template"]
                    t = Template("{% load djust_components %}" + tpl_str)
                    ctx = dict(variant.get("context", {}))
                    if extra_context:
                        ctx.update(extra_context)
                    rendered = t.render(Context(ctx))
                except Exception as exc:
                    rendered = f'<div style="color:red;">Render error: {exc}</div>'
            elif comp["type"] == "class":
                try:
                    rendered = variant["render"]()
                except Exception as exc:
                    rendered = f'<div style="color:red;">Render error: {exc}</div>'

            parts.append('<div class="variant-section">')
            parts.append(f'<div class="variant-label">{variant["name"]}</div>')
            parts.append(f'<div class="variant-preview">{rendered}</div>')
            parts.append("</div>")

        parts.append("</div>")
    return "\n".join(parts)


def _render_sidebar(categories, current_slug=None):
    """Build sidebar HTML. Shows all categories; expands component links for current."""
    from .examples import CATEGORIES, CATEGORY_ORDER

    parts = []
    for slug in CATEGORY_ORDER:
        cat_label = CATEGORIES.get(slug, slug.title())
        comps = categories.get(cat_label, [])
        count = len(comps)

        if current_slug:
            active = ' class="active"' if slug == current_slug else ""
            parts.append(f'<h3><a href="../{slug}/"{active}>{cat_label} ({count})</a></h3>')
            if slug == current_slug:
                for comp in comps:
                    anchor = comp["name"].lower().replace(" ", "-")
                    parts.append(f'<a href="#{anchor}">{comp["label"]}</a>')
        else:
            parts.append(f'<h3><a href="{slug}/">{cat_label} ({count})</a></h3>')

    return "\n".join(parts)


def _assemble_page(mode, head, header, sidebar, content, scripts):
    """Wrap sections into a full HTML document."""
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="{mode}">
{head}
<body>
{header}
    <div class="gallery-body">
        <nav class="gallery-sidebar">
            {sidebar}
        </nav>
        <main class="gallery-content preview-container desktop">
            {content}
        </main>
    </div>
{scripts}
</body>
</html>"""


# ── Views ──


def gallery_index_view(request):
    """Render the gallery landing page with category cards."""
    mode, theme_css, ds_options, preset_options = _resolve_theme(request)
    data = get_gallery_data()
    categories = data["categories"]

    head = _render_head(mode, theme_css)
    header = _render_header(ds_options, preset_options)
    sidebar = _render_sidebar(categories)
    scripts = _render_scripts()

    # Build category card grid
    from .examples import CATEGORIES, CATEGORY_ORDER

    cards = []
    for slug in CATEGORY_ORDER:
        cat_label = CATEGORIES.get(slug, slug.title())
        comps = categories.get(cat_label, [])
        count = len(comps)
        cards.append(
            f'<a class="category-card" href="{slug}/">'
            f"<h3>{cat_label}</h3>"
            f'<span class="count">{count} component{"s" if count != 1 else ""}</span>'
            f"</a>"
        )
    content = f'<h2>Categories</h2><div class="category-grid">{"".join(cards)}</div>'

    html = _assemble_page(mode, head, header, sidebar, content, scripts)
    return HttpResponse(html)


def gallery_category_view(request, category_slug):
    """Render a single category's components."""
    from .examples import CATEGORIES, CATEGORY_ORDER

    if category_slug not in CATEGORIES:
        raise Http404(f"Unknown category: {category_slug}")

    mode, theme_css, ds_options, preset_options = _resolve_theme(request)
    data = get_gallery_data()
    categories = data["categories"]

    cat_label = CATEGORIES[category_slug]
    components = categories.get(cat_label, [])

    head = _render_head(mode, theme_css, title=f"{cat_label} — djust-components Gallery")
    header = _render_header(ds_options, preset_options)
    sidebar = _render_sidebar(categories, current_slug=category_slug)
    scripts = _render_scripts()

    # Breadcrumb
    breadcrumb = (
        f'<div class="gallery-breadcrumb">'
        f'<a href="../">Gallery</a> &rsaquo; {cat_label} ({len(components)} components)'
        f"</div>"
    )

    # Component cards
    cards_html = _render_component_cards(components)

    # Prev/next navigation
    idx = CATEGORY_ORDER.index(category_slug)
    prev_link = ""
    next_link = ""
    if idx > 0:
        prev_slug = CATEGORY_ORDER[idx - 1]
        prev_label = CATEGORIES.get(prev_slug, prev_slug.title())
        prev_link = f'<a href="../{prev_slug}/">&larr; {prev_label}</a>'
    if idx < len(CATEGORY_ORDER) - 1:
        next_slug = CATEGORY_ORDER[idx + 1]
        next_label = CATEGORIES.get(next_slug, next_slug.title())
        next_link = f'<a href="../{next_slug}/">{next_label} &rarr;</a>'
    nav = f'<div class="category-nav">{prev_link}<span></span>{next_link}</div>'

    content = f'{breadcrumb}\n<section class="category-section">\n<h2>{cat_label}</h2>\n{cards_html}\n</section>\n{nav}'

    html = _assemble_page(mode, head, header, sidebar, content, scripts)
    return HttpResponse(html)


def gallery_view(request):
    """Render all components on a single page (legacy monolithic view).

    Kept for backward compatibility — use ``gallery_index_view`` and
    ``gallery_category_view`` for the per-category experience.
    """
    mode, theme_css, ds_options, preset_options = _resolve_theme(request)
    data = get_gallery_data()
    categories = data["categories"]

    head = _render_head(mode, theme_css)
    header = _render_header(ds_options, preset_options)
    scripts = _render_scripts()

    # Build sidebar with all component links
    sidebar_parts = []
    for cat_label, components in sorted(categories.items()):
        sidebar_parts.append(f"<h3>{cat_label}</h3>")
        for comp in components:
            anchor = comp["name"].lower().replace(" ", "-")
            sidebar_parts.append(f'<a href="#{anchor}">{comp["label"]}</a>')
    sidebar = "\n".join(sidebar_parts)

    # Build content with all categories
    content_parts = []
    for cat_label, components in sorted(categories.items()):
        content_parts.append(f'<section class="category-section" id="cat-{cat_label.lower()}">')
        content_parts.append(f"<h2>{cat_label}</h2>")
        content_parts.append(_render_component_cards(components))
        content_parts.append("</section>")
    content = "\n".join(content_parts)

    html = _assemble_page(mode, head, header, sidebar, content, scripts)
    return HttpResponse(html)
