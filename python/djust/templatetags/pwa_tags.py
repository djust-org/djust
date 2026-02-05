"""
Template tags for djust PWA support.
"""

from django import template
from django.utils.safestring import mark_safe
from django.conf import settings
import json

register = template.Library()


@register.simple_tag
def pwa_manifest(manifest_url="/manifest.json"):
    """
    Generate PWA manifest link tag.

    Usage:
        {% pwa_manifest %}
        {% pwa_manifest '/custom-manifest.json' %}
    """
    return mark_safe(f'<link rel="manifest" href="{manifest_url}">')


@register.simple_tag
def pwa_service_worker(sw_url="/sw.js", register_immediately=True):
    """
    Generate service worker registration script.

    Usage:
        {% pwa_service_worker %}
        {% pwa_service_worker '/custom-sw.js' %}
    """
    if not register_immediately:
        return mark_safe("")

    script = f"""
<script>
if ('serviceWorker' in navigator) {{
    window.addEventListener('load', function() {{
        navigator.serviceWorker.register('{sw_url}')
            .then(function(registration) {{
                console.log('SW registered: ', registration);

                // Check for updates
                registration.addEventListener('updatefound', function() {{
                    const newWorker = registration.installing;
                    newWorker.addEventListener('statechange', function() {{
                        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {{
                            // New service worker available
                            window.dispatchEvent(new CustomEvent('sw-update-available', {{
                                detail: {{ registration: registration }}
                            }}));
                        }}
                    }});
                }});
            }})
            .catch(function(registrationError) {{
                console.log('SW registration failed: ', registrationError);
            }});
    }});
}}
</script>
"""

    return mark_safe(script)


@register.simple_tag
def pwa_theme_color(color=None):
    """
    Generate theme-color meta tag.

    Usage:
        {% pwa_theme_color %}
        {% pwa_theme_color '#ff0000' %}
    """
    if not color:
        try:
            djust_config = getattr(settings, "DJUST_CONFIG", {})
            color = djust_config.get("PWA_THEME_COLOR", "#000000")
        except Exception:
            color = "#000000"

    return mark_safe(f'<meta name="theme-color" content="{color}">')


@register.simple_tag
def pwa_apple_touch_icon(icon_url="/static/icons/apple-touch-icon.png", sizes="180x180"):
    """
    Generate Apple touch icon link tag.

    Usage:
        {% pwa_apple_touch_icon %}
        {% pwa_apple_touch_icon '/custom-icon.png' '192x192' %}
    """
    return mark_safe(f'<link rel="apple-touch-icon" sizes="{sizes}" href="{icon_url}">')


@register.simple_tag
def pwa_offline_indicator(show_class="offline", hide_class="online"):
    """
    Generate offline status indicator script.

    Usage:
        {% pwa_offline_indicator %}
        {% pwa_offline_indicator 'show-offline' 'show-online' %}
    """
    script = f"""
<script>
(function() {{
    function updateOnlineStatus() {{
        const isOnline = navigator.onLine;
        const indicators = document.querySelectorAll('[dj-offline]');

        indicators.forEach(function(element) {{
            const behavior = element.getAttribute('dj-offline');

            switch(behavior) {{
                case 'show':
                    element.style.display = isOnline ? 'none' : '';
                    break;
                case 'hide':
                    element.style.display = isOnline ? '' : 'none';
                    break;
                case 'disable':
                    element.disabled = !isOnline;
                    break;
                case 'enable':
                    element.disabled = isOnline;
                    break;
            }}
        }});

        // Add/remove classes on body
        document.body.classList.toggle('{hide_class}', isOnline);
        document.body.classList.toggle('{show_class}', !isOnline);

        // Dispatch custom event
        window.dispatchEvent(new CustomEvent('connection-change', {{
            detail: {{ online: isOnline }}
        }}));
    }}

    window.addEventListener('online', updateOnlineStatus);
    window.addEventListener('offline', updateOnlineStatus);
    window.addEventListener('load', updateOnlineStatus);
}})();
</script>
"""

    return mark_safe(script)


@register.simple_tag
def pwa_install_prompt(button_selector=".pwa-install-btn"):
    """
    Generate PWA install prompt handling script.

    Usage:
        {% pwa_install_prompt %}
        {% pwa_install_prompt '#install-button' %}
    """
    script = f"""
<script>
(function() {{
    let deferredPrompt;

    window.addEventListener('beforeinstallprompt', function(e) {{
        console.log('PWA install prompt available');
        e.preventDefault();
        deferredPrompt = e;

        // Show install button
        const installButtons = document.querySelectorAll('{button_selector}');
        installButtons.forEach(function(button) {{
            button.style.display = 'block';
        }});

        // Dispatch custom event
        window.dispatchEvent(new CustomEvent('pwa-installable'));
    }});

    // Handle install button clicks
    document.addEventListener('click', function(e) {{
        if (e.target.matches('{button_selector}')) {{
            if (deferredPrompt) {{
                deferredPrompt.prompt();
                deferredPrompt.userChoice.then(function(choiceResult) {{
                    if (choiceResult.outcome === 'accepted') {{
                        console.log('PWA installation accepted');
                        window.dispatchEvent(new CustomEvent('pwa-installed'));
                    }} else {{
                        console.log('PWA installation declined');
                        window.dispatchEvent(new CustomEvent('pwa-install-declined'));
                    }}
                    deferredPrompt = null;
                }});
            }}
        }}
    }});

    window.addEventListener('appinstalled', function(e) {{
        console.log('PWA was installed');
        window.dispatchEvent(new CustomEvent('pwa-installed'));

        // Hide install buttons
        const installButtons = document.querySelectorAll('{button_selector}');
        installButtons.forEach(function(button) {{
            button.style.display = 'none';
        }});
    }});
}})();
</script>
"""

    return mark_safe(script)


@register.simple_tag
def pwa_update_notification(notification_selector=".pwa-update-notification"):
    """
    Generate PWA update notification handling script.

    Usage:
        {% pwa_update_notification %}
        {% pwa_update_notification '#update-banner' %}
    """
    script = f"""
<script>
window.addEventListener('sw-update-available', function(e) {{
    console.log('Service worker update available');

    // Show update notification
    const notifications = document.querySelectorAll('{notification_selector}');
    notifications.forEach(function(notification) {{
        notification.style.display = 'block';
    }});

    // Handle update button clicks
    document.addEventListener('click', function(clickEvent) {{
        if (clickEvent.target.matches('{notification_selector} .update-btn')) {{
            const registration = e.detail.registration;
            if (registration.waiting) {{
                registration.waiting.postMessage({{ type: 'SKIP_WAITING' }});

                navigator.serviceWorker.addEventListener('controllerchange', function() {{
                    window.location.reload();
                }});
            }}
        }}

        if (clickEvent.target.matches('{notification_selector} .dismiss-btn')) {{
            const notifications = document.querySelectorAll('{notification_selector}');
            notifications.forEach(function(notification) {{
                notification.style.display = 'none';
            }});
        }}
    }});
}});
</script>
"""

    return mark_safe(script)


@register.simple_tag
def pwa_config_json():
    """
    Generate PWA configuration as JSON for JavaScript access.

    Usage:
        <script>
        const pwaConfig = {% pwa_config_json %};
        </script>
    """
    try:
        djust_config = getattr(settings, "DJUST_CONFIG", {})

        pwa_config = {
            "name": djust_config.get("PWA_NAME", "djust App"),
            "short_name": djust_config.get("PWA_SHORT_NAME", "djust"),
            "theme_color": djust_config.get("PWA_THEME_COLOR", "#000000"),
            "background_color": djust_config.get("PWA_BACKGROUND_COLOR", "#ffffff"),
            "cache_strategy": djust_config.get("PWA_CACHE_STRATEGY", "cache_first"),
            "offline_storage": djust_config.get("PWA_OFFLINE_STORAGE", "indexeddb"),
            "sync_endpoint": djust_config.get("PWA_SYNC_ENDPOINT", "/api/sync/"),
            "connection_timeout": djust_config.get("PWA_CONNECTION_TIMEOUT", 5000),
            "retry_interval": djust_config.get("PWA_RETRY_INTERVAL", 30000),
        }

        return mark_safe(json.dumps(pwa_config))
    except Exception:
        return mark_safe("{}")


@register.inclusion_tag("djust/pwa/offline_banner.html", takes_context=True)
def pwa_offline_banner(context, message="You're offline. Changes will sync when connected."):
    """
    Include offline status banner template.

    Usage:
        {% pwa_offline_banner %}
        {% pwa_offline_banner "Custom offline message" %}
    """
    return {
        "message": message,
        "request": context.get("request"),
    }


@register.inclusion_tag("djust/pwa/install_prompt.html", takes_context=True)
def pwa_install_button(context, text="Install App", class_name="pwa-install-btn"):
    """
    Include PWA install button template.

    Usage:
        {% pwa_install_button %}
        {% pwa_install_button "Add to Home Screen" "btn btn-primary" %}
    """
    return {
        "text": text,
        "class_name": class_name,
        "request": context.get("request"),
    }


@register.inclusion_tag("djust/pwa/update_notification.html", takes_context=True)
def pwa_update_banner(context, message="App updated! Refresh to see changes."):
    """
    Include PWA update notification template.

    Usage:
        {% pwa_update_banner %}
        {% pwa_update_banner "New version available!" %}
    """
    return {
        "message": message,
        "request": context.get("request"),
    }


@register.filter
def is_pwa_enabled(request):
    """
    Check if PWA is enabled for the current request.

    Usage:
        {% if request|is_pwa_enabled %}
            <!-- PWA-specific content -->
        {% endif %}
    """
    try:
        djust_config = getattr(settings, "DJUST_CONFIG", {})
        return djust_config.get("PWA_ENABLED", False)
    except Exception:
        return False


@register.filter
def is_standalone(request):
    """
    Check if the app is running in standalone mode.

    Usage:
        {% if request|is_standalone %}
            <!-- Standalone app content -->
        {% endif %}
    """
    # Check for PWA standalone mode indicators
    user_agent = request.META.get("HTTP_USER_AGENT", "").lower()

    # Check for various PWA/standalone indicators
    standalone_indicators = [
        "wv",  # WebView
        "standalone",
        "fullscreen",
    ]

    for indicator in standalone_indicators:
        if indicator in user_agent:
            return True

    # Check for custom header
    return request.META.get("HTTP_X_REQUESTED_WITH") == "PWA"
