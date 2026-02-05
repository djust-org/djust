"""
Django template tags for PWA/offline support.

Provides tags for generating PWA manifest, service worker registration,
and offline-aware UI elements.

Usage:
    {% load djust_pwa %}

    <!-- Generate PWA manifest link -->
    {% djust_pwa_manifest name="My App" theme_color="#007bff" %}

    <!-- Register service worker -->
    {% djust_sw_register %}

    <!-- Offline status indicator -->
    {% djust_offline_indicator %}
"""

import json
from django import template
from django.conf import settings
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def djust_pwa_manifest(
    name=None,
    short_name=None,
    description=None,
    theme_color=None,
    background_color=None,
    display="standalone",
    start_url="/",
    icons=None,
):
    """
    Generate a PWA manifest as an inline <link> tag with data URI.

    This creates a manifest.json inline, so you don't need a separate file.
    For more control, use the generate_sw command which creates manifest.json.

    Args:
        name: Full app name (default: from settings or "djust App")
        short_name: Short app name (default: from settings or "djust")
        description: App description
        theme_color: Theme color for browser chrome (default: "#007bff")
        background_color: Background color for splash screen (default: "#ffffff")
        display: Display mode (standalone, fullscreen, minimal-ui, browser)
        start_url: Starting URL when launched (default: "/")
        icons: List of icon dicts with src, sizes, type keys

    Returns:
        HTML link tag for the manifest

    Example:
        {% load djust_pwa %}
        <head>
            {% djust_pwa_manifest name="My App" theme_color="#007bff" %}
        </head>
    """
    manifest = {
        "name": name or getattr(settings, "DJUST_PWA_NAME", "djust App"),
        "short_name": short_name or getattr(settings, "DJUST_PWA_SHORT_NAME", "djust"),
        "description": description
        or getattr(settings, "DJUST_PWA_DESCRIPTION", "A djust-powered app"),
        "start_url": start_url,
        "display": display,
        "background_color": background_color
        or getattr(settings, "DJUST_PWA_BACKGROUND_COLOR", "#ffffff"),
        "theme_color": theme_color or getattr(settings, "DJUST_PWA_THEME_COLOR", "#007bff"),
    }

    if icons:
        manifest["icons"] = icons
    else:
        # Default icons
        static_url = getattr(settings, "STATIC_URL", "/static/")
        manifest["icons"] = [
            {
                "src": f"{static_url}icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": f"{static_url}icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
        ]

    # Generate data URI
    manifest_json = json.dumps(manifest)
    data_uri = f"data:application/manifest+json,{manifest_json}"

    # Also add theme-color meta tag - escape the theme color for HTML attribute safety
    theme_meta = format_html(
        '<meta name="theme-color" content="{}">',
        manifest["theme_color"],
    )

    # The data URI contains JSON which is safe (no user-controlled raw HTML),
    # but we still escape the manifest JSON for the href attribute
    return mark_safe("%s\n<link rel=\"manifest\" href='%s'>" % (theme_meta, escape(data_uri)))


@register.simple_tag
def djust_sw_register(sw_url=None, scope="/"):
    """
    Generate JavaScript to register the service worker.

    Args:
        sw_url: URL to the service worker (default: "/sw.js" or STATIC_URL/sw.js)
        scope: Service worker scope (default: "/")

    Returns:
        Script tag with service worker registration code

    Example:
        {% load djust_pwa %}
        {% djust_sw_register %}
    """
    if not sw_url:
        static_url = getattr(settings, "STATIC_URL", "/static/")
        sw_url = "%ssw.js" % static_url

    # Use json.dumps for safe injection into JavaScript contexts
    # (HTML escape() is insufficient for JS strings — doesn't handle \, newlines, etc.)
    safe_sw_url = json.dumps(sw_url)
    safe_scope = json.dumps(scope)

    script = """<script>
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
        navigator.serviceWorker.register(%s, { scope: %s })
            .then(function(registration) {
                // Check for updates periodically
                setInterval(function() {
                    registration.update();
                }, 60 * 60 * 1000); // Every hour

                // Handle updates
                registration.addEventListener('updatefound', function() {
                    var newWorker = registration.installing;
                    newWorker.addEventListener('statechange', function() {
                        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                            // New version available
                            if (window.djust && window.djust.offline) {
                                window.djust.offline.onUpdateAvailable(registration);
                            }
                        }
                    });
                });
            })
            .catch(function(error) {
                if (window.djust && window.djust.reportError) {
                    window.djust.reportError('ServiceWorker registration failed', error);
                }
            });
    });
}
</script>""" % (safe_sw_url, safe_scope)
    return mark_safe(script)


@register.simple_tag
def djust_offline_indicator(
    online_text="Online",
    offline_text="Offline",
    online_class="djust-status-online",
    offline_class="djust-status-offline",
    show_when="offline",
):
    """
    Render an offline status indicator element.

    The indicator automatically shows/hides based on connection status.

    Args:
        online_text: Text to show when online
        offline_text: Text to show when offline
        online_class: CSS class when online
        offline_class: CSS class when offline
        show_when: When to show the indicator ("always", "offline", "online")

    Returns:
        HTML for the status indicator

    Example:
        {% load djust_pwa %}
        {% djust_offline_indicator offline_text="You are offline" %}
    """
    # Determine visibility attributes based on show_when
    if show_when == "offline":
        visibility = 'dj-offline-show style="display: none;"'
    elif show_when == "online":
        visibility = "dj-offline-hide"
    else:
        visibility = ""

    # Escape all user-provided values for safe HTML injection
    safe_online_text = escape(online_text)
    safe_offline_text = escape(offline_text)
    safe_online_class = escape(online_class)
    safe_offline_class = escape(offline_class)
    display_text = safe_offline_text if show_when == "offline" else safe_online_text

    indicator_html = """<div class="djust-offline-indicator" %s
     data-online-text="%s"
     data-offline-text="%s"
     data-online-class="%s"
     data-offline-class="%s">
    <span class="djust-indicator-dot"></span>
    <span class="djust-indicator-text">%s</span>
</div>""" % (
        visibility,
        safe_online_text,
        safe_offline_text,
        safe_online_class,
        safe_offline_class,
        display_text,
    )

    indicator_css = """<style>
.djust-offline-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 9999px;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.3s ease;
}
.djust-indicator-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    transition: background-color 0.3s ease;
}
.djust-status-online {
    background-color: #d1fae5;
    color: #065f46;
}
.djust-status-online .djust-indicator-dot {
    background-color: #10b981;
}
.djust-status-offline {
    background-color: #fee2e2;
    color: #991b1b;
}
.djust-status-offline .djust-indicator-dot {
    background-color: #ef4444;
}
</style>"""

    return mark_safe(indicator_html + "\n" + indicator_css)


@register.simple_tag
def djust_offline_styles():
    """
    Include CSS styles for offline-related directives.

    Adds styles for:
    - .djust-online / .djust-offline body classes
    - dj-offline-show / dj-offline-hide / dj-offline-disable directives

    Example:
        {% load djust_pwa %}
        <head>
            {% djust_offline_styles %}
        </head>
    """
    styles = """<style>
/* Offline/Online body classes */
body.djust-offline [dj-offline-hide],
body:not(.djust-online) [dj-offline-hide] {
    display: none !important;
}

body.djust-online [dj-offline-show] {
    display: none !important;
}

body.djust-offline [dj-offline-disable],
body:not(.djust-online) [dj-offline-disable] {
    pointer-events: none !important;
    opacity: 0.5 !important;
    cursor: not-allowed !important;
}

/* Offline indicator animations */
@keyframes djust-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

body.djust-offline .djust-indicator-dot {
    animation: djust-pulse 2s ease-in-out infinite;
}

/* Queued event indicator */
.djust-queued-indicator {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: #fef3c7;
    color: #92400e;
    padding: 12px 16px;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    font-size: 14px;
    z-index: 9999;
    display: none;
}

body.djust-offline .djust-queued-indicator.has-queued {
    display: block;
}

/* Syncing state */
.djust-syncing {
    position: relative;
}

.djust-syncing::after {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(255, 255, 255, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
}
</style>"""
    return mark_safe(styles)


@register.inclusion_tag("djust/pwa_head.html", takes_context=True)
def djust_pwa_head(context, name=None, theme_color=None):
    """
    Include all PWA-related head tags at once.

    This is a convenience tag that includes:
    - PWA manifest
    - Service worker registration
    - Offline styles
    - Meta tags for mobile web app

    Args:
        name: App name
        theme_color: Theme color

    Example:
        {% load djust_pwa %}
        <head>
            {% djust_pwa_head name="My App" theme_color="#007bff" %}
        </head>
    """
    return {
        "name": name or getattr(settings, "DJUST_PWA_NAME", "djust App"),
        "theme_color": theme_color or getattr(settings, "DJUST_PWA_THEME_COLOR", "#007bff"),
        "static_url": getattr(settings, "STATIC_URL", "/static/"),
    }


@register.filter
def offline_fallback(value, fallback):
    """
    Template filter to provide a fallback value for offline mode.

    In templates, you can use this to show placeholder content when
    dynamic data might not be available offline.

    Args:
        value: The primary value to display
        fallback: Fallback value to use if primary is empty/None

    Example:
        {{ user.name|offline_fallback:"Guest" }}
    """
    if value is None or value == "":
        return fallback
    return value
