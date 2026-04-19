/**
 * djust_theming - Client-side theme manager
 *
 * Handles:
 * - Theme mode switching (light/dark/system)
 * - System preference detection
 * - Persistence via localStorage
 * - Smooth transitions
 * - djust client command integration
 */

(function() {
    'use strict';

    const STORAGE_KEY_MODE = 'djust-theme-mode';
    const STORAGE_KEY_PRESET = 'djust-theme-preset';
    const STORAGE_KEY_THEME = 'djust-theme-design';
    const STORAGE_KEY_PACK = 'djust-theme-pack';
    const STORAGE_KEY_LAYOUT = 'djust-theme-layout';

    const COOKIE_KEY_PRESET = 'djust_theme_preset';
    const COOKIE_KEY_THEME = 'djust_theme';
    const COOKIE_KEY_PACK = 'djust_theme_pack';
    const COOKIE_KEY_LAYOUT = 'djust_theme_layout';

    class DjustThemeManager {
        constructor() {
            this.pendingUpdate = null;
            this.init();
        }

        init() {
            // Apply stored theme immediately (anti-FOUC should have already run)
            this.applyStoredTheme();

            // Listen for system preference changes
            this.setupSystemPreferenceListener();

            // Listen for djust client commands
            this.setupClientCommandListener();

            // Setup UI bindings for non-djust usage
            this.setupUIBindings();

            // Mark theme as ready after deferred CSS has loaded and all
            // pending DOM updates (applyMode rAF) have completed.
            // This prevents transitions from animating the initial paint.
            this._markReadyWhenStable();
        }

        /**
         * Get the current mode setting from storage
         */
        getMode() {
            return localStorage.getItem(STORAGE_KEY_MODE) || 'system';
        }

        /**
         * Set the mode and persist to storage
         */
        setMode(mode) {
            if (!['light', 'dark', 'system'].includes(mode)) {
                console.warn(`Invalid theme mode: ${mode}`);
                return;
            }

            localStorage.setItem(STORAGE_KEY_MODE, mode);
            this.applyMode(mode);
            this.updateUIState();
        }

        /**
         * Get the resolved mode (always 'light' or 'dark')
         */
        getResolvedMode() {
            const mode = this.getMode();
            if (mode === 'system') {
                return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            }
            return mode;
        }

        /**
         * Apply theme mode to the document (optimized with RAF)
         */
        applyMode(mode) {
            // Cancel any pending update
            if (this.pendingUpdate) {
                cancelAnimationFrame(this.pendingUpdate);
            }

            // Batch DOM updates in next frame
            this.pendingUpdate = requestAnimationFrame(() => {
                const resolvedMode = mode === 'system'
                    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
                    : mode;

                // Batch all DOM changes together
                const html = document.documentElement;
                html.setAttribute('data-theme', resolvedMode);
                html.setAttribute('data-theme-mode', mode);

                // Also toggle .dark class for compatibility
                if (resolvedMode === 'dark') {
                    html.classList.add('dark');
                } else {
                    html.classList.remove('dark');
                }

                // Dispatch event for other scripts to listen to
                window.dispatchEvent(new CustomEvent('djust-theme-changed', {
                    detail: { mode, resolvedMode }
                }));

                this.pendingUpdate = null;
            });
        }

        /**
         * Apply the stored theme settings
         */
        applyStoredTheme() {
            const mode = this.getMode();
            this.applyMode(mode);
        }

        /**
         * Wait for deferred CSS to load and pending rAFs to settle,
         * then enable transitions by adding 'theme-ready' class.
         */
        _markReadyWhenStable() {
            const enable = () => {
                // Double-rAF: first rAF waits for applyMode's rAF,
                // second ensures a full frame has painted with final values
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        document.documentElement.classList.remove('loading');
                        document.documentElement.classList.add('theme-ready');
                    });
                });
            };

            const deferred = document.querySelector('link[data-djust-theme-deferred]');
            if (deferred && deferred.rel === 'preload') {
                // Deferred CSS hasn't swapped in yet — wait for it
                deferred.addEventListener('load', enable, { once: true });
                // Fallback: if it takes too long, enable anyway after 2s
                setTimeout(enable, 2000);
            } else {
                // Already loaded or using blocking CSS — just wait for rAFs
                enable();
            }
        }

        /**
         * Toggle between light and dark mode
         */
        toggle() {
            const current = this.getResolvedMode();
            const newMode = current === 'dark' ? 'light' : 'dark';
            this.setMode(newMode);
            return newMode;
        }

        /**
         * Get current preset name from storage
         */
        getPreset() {
            return localStorage.getItem(STORAGE_KEY_PRESET) || 'default';
        }

        /**
         * Set preset and persist to storage
         */
        setPreset(preset) {
            localStorage.setItem(STORAGE_KEY_PRESET, preset);

            // Also set a cookie so the server can read it
            document.cookie = `${COOKIE_KEY_PRESET}=${preset};path=/;max-age=31536000;SameSite=Lax`;

            // Clear pack if setting preset manually
            this.clearPack();

            // Dispatch event - actual CSS update requires page reload
            window.dispatchEvent(new CustomEvent('djust-preset-changed', {
                detail: { preset }
            }));

            // Reload the page to apply the new theme CSS
            window.location.reload();
        }

        /**
         * Set design system theme and persist to storage
         */
        setTheme(theme) {
            localStorage.setItem(STORAGE_KEY_THEME, theme);

            // Set cookie for server
            document.cookie = `${COOKIE_KEY_THEME}=${theme};path=/;max-age=31536000;SameSite=Lax`;

            // Clear pack if setting theme manually
            this.clearPack();

            // Reload to apply
            window.location.reload();
        }

        /**
         * Set theme pack and persist to storage
         */
        setPack(pack) {
            localStorage.setItem(STORAGE_KEY_PACK, pack);

            // Set cookie for server
            document.cookie = `${COOKIE_KEY_PACK}=${pack};path=/;max-age=31536000;SameSite=Lax`;

            // Reload to apply
            window.location.reload();
        }

        /**
         * Clear theme pack
         */
        clearPack() {
            localStorage.removeItem(STORAGE_KEY_PACK);
            document.cookie = `${COOKIE_KEY_PACK}=;path=/;expires=Thu, 01 Jan 1970 00:00:00 GMT`;
        }

        /**
         * Set layout and apply via CSS class (no reload needed)
         */
        setLayout(layout) {
            localStorage.setItem(STORAGE_KEY_LAYOUT, layout);
            document.cookie = `${COOKIE_KEY_LAYOUT}=${layout};path=/;max-age=31536000;SameSite=Lax`;

            // Apply layout class immediately (CSS-only switching)
            const wrapper = document.querySelector('[data-layout]');
            if (wrapper) {
                wrapper.setAttribute('data-layout', layout);
            }
        }

        /**
         * Listen for system color scheme preference changes
         */
        setupSystemPreferenceListener() {
            const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

            mediaQuery.addEventListener('change', (e) => {
                if (this.getMode() === 'system') {
                    this.applyMode('system');
                }
            });
        }

        /**
         * Listen for djust push_event commands (djust-experimental compatibility)
         */
        setupClientCommandListener() {
            // djust-experimental sends push_event through 'djust:push_event' custom events
            window.addEventListener('djust:push_event', (event) => {
                const { event: eventName, payload } = event.detail || {};

                // Only handle theme_update events
                if (eventName !== 'theme_update') {
                    return;
                }

                const { mode, preset, css } = payload || {};

                // Update mode without reload (if provided)
                if (mode) {
                    this.setModeWithoutReload(mode);
                }

                // Update preset CSS without reload (if CSS is provided)
                if (preset && css) {
                    this.setPresetWithoutReload(preset, css);
                }
            });

            // Also support legacy 'djust:client-command' for backward compatibility
            window.addEventListener('djust:client-command', (event) => {
                const { type, mode, preset } = event.detail || {};

                switch (type) {
                    case 'set_theme_mode':
                        if (mode) {
                            this.setMode(mode);
                        }
                        break;

                    case 'set_theme_preset':
                        if (preset) {
                            this.setPreset(preset);
                        }
                        break;

                    case 'toggle_theme_mode':
                        this.toggle();
                        break;
                }
            });
        }

        /**
         * Set mode without reloading the page (for LiveView reactive updates)
         */
        setModeWithoutReload(mode) {
            if (!['light', 'dark', 'system'].includes(mode)) {
                console.warn(`Invalid theme mode: ${mode}`);
                return;
            }

            localStorage.setItem(STORAGE_KEY_MODE, mode);
            this.applyMode(mode);
            this.updateUIState();
        }

        /**
         * Update preset and CSS without reloading the page (for LiveView reactive updates)
         */
        setPresetWithoutReload(preset, css) {
            // Cancel any pending update
            if (this.pendingUpdate) {
                cancelAnimationFrame(this.pendingUpdate);
            }

            // Batch updates in next frame
            this.pendingUpdate = requestAnimationFrame(() => {
                localStorage.setItem(STORAGE_KEY_PRESET, preset);

                // Set cookie for server-side rendering
                document.cookie = `${COOKIE_KEY_PRESET}=${preset};path=/;max-age=31536000;SameSite=Lax`;

                // Update the CSS dynamically
                const styleElement = document.querySelector('#djust-theme-css');
                if (styleElement && css) {
                    if (styleElement.tagName === 'LINK') {
                        // Replace <link> with <style> so we can set textContent
                        const newStyle = document.createElement('style');
                        newStyle.id = 'djust-theme-css';
                        newStyle.setAttribute('data-djust-theme', '');
                        newStyle.textContent = css;
                        styleElement.replaceWith(newStyle);
                    } else {
                        styleElement.textContent = css;
                    }
                } else if (css) {
                    // Create style element if it doesn't exist
                    const newStyle = document.createElement('style');
                    newStyle.id = 'djust-theme-css';
                    newStyle.setAttribute('data-djust-theme', '');
                    newStyle.textContent = css;
                    document.head.appendChild(newStyle);
                }

                // Dispatch event
                window.dispatchEvent(new CustomEvent('djust-preset-changed', {
                    detail: { preset }
                }));

                // Update UI state (select values, etc.)
                this.updateUIState();

                this.pendingUpdate = null;
            });
        }

        /**
         * Setup UI bindings for standalone usage (without djust events)
         */
        setupUIBindings() {
            document.addEventListener('click', (e) => {
                const target = e.target.closest('[data-theme-toggle]');
                if (target) {
                    e.preventDefault();
                    this.toggle();
                }
            });

            document.addEventListener('click', (e) => {
                // Use a more specific selector to avoid matching the html element
                // which also has data-theme-mode attribute
                const target = e.target.closest('[data-theme-mode]:not(html)');
                if (target) {
                    e.preventDefault();
                    const mode = target.getAttribute('data-theme-mode');
                    if (mode) {
                        this.setMode(mode);
                    }
                }
            });

            document.addEventListener('change', (e) => {
                const target = e.target.closest('[data-theme-preset-select]');
                if (target) {
                    this.setPreset(target.value);
                }
            });
        }

        /**
         * Update UI elements to reflect current state (debounced for performance)
         */
        updateUIState() {
            // Debounce UI updates
            if (this.uiUpdateTimer) {
                clearTimeout(this.uiUpdateTimer);
            }

            this.uiUpdateTimer = setTimeout(() => {
                requestAnimationFrame(() => {
                    const mode = this.getMode();
                    const resolvedMode = this.getResolvedMode();

                    // Update mode toggle buttons
                    const modeButtons = document.querySelectorAll('.theme-mode-btn');
                    modeButtons.forEach(btn => {
                        const btnMode = btn.dataset.djustParams
                            ? JSON.parse(btn.dataset.djustParams).mode
                            : btn.getAttribute('data-theme-mode');

                        btn.classList.toggle('active', btnMode === mode);
                    });

                    // Update preset selects
                    const presetSelects = document.querySelectorAll('.theme-preset-select');
                    presetSelects.forEach(select => {
                        const preset = this.getPreset();
                        if (select.value !== preset) {
                            select.value = preset;
                        }
                    });
                });
            }, 16); // ~1 frame at 60fps for instant feel
        }
    }

    // Initialize and expose globally
    window.djustTheme = new DjustThemeManager();

    // Also expose constructor for manual instantiation
    window.DjustThemeManager = DjustThemeManager;

    // --- Theme Panel interaction ---
    function initThemePanel() {
        function getCookie(name) {
            var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
            return m ? m[1] : '';
        }

        // Toggle panel open/close
        document.addEventListener('click', function(e) {
            var trigger = e.target.closest('.theme-panel-trigger');
            if (trigger) {
                e.preventDefault();
                trigger.closest('.theme-panel').classList.toggle('open');
                return;
            }
            // Close when clicking outside
            if (!e.target.closest('.theme-panel')) {
                var open = document.querySelector('.theme-panel.open');
                if (open) open.classList.remove('open');
            }
            // Mode buttons
            var modeBtn = e.target.closest('.theme-panel-mode-btn[data-theme-mode]');
            if (modeBtn && window.djustTheme) {
                e.preventDefault();
                window.djustTheme.setMode(modeBtn.getAttribute('data-theme-mode'));
                return;
            }
            // Custom select toggle
            var selectBtn = e.target.closest('.tp-select-btn');
            if (selectBtn) {
                e.preventDefault();
                var sel = selectBtn.closest('.tp-select');
                // Close other open selects
                document.querySelectorAll('.tp-select.open').forEach(function(s) {
                    if (s !== sel) s.classList.remove('open');
                });
                sel.classList.toggle('open');
                return;
            }
            // Custom select option click
            var option = e.target.closest('.tp-select-option');
            if (option && window.djustTheme) {
                e.preventDefault();
                var sel = option.closest('[data-tp-select]');
                var type = sel.getAttribute('data-tp-select');
                var value = option.getAttribute('data-value');
                if (type === 'pack') window.djustTheme.setPack(value);
                else if (type === 'preset') window.djustTheme.setPreset(value);
                else if (type === 'design') window.djustTheme.setTheme(value);
                else if (type === 'layout') window.djustTheme.setLayout(value);
                return;
            }
            // Close custom selects when clicking elsewhere in panel
            document.querySelectorAll('.tp-select.open').forEach(function(s) {
                if (!e.target.closest('.tp-select')) s.classList.remove('open');
            });
            // Customize toggle
            var custToggle = e.target.closest('[data-theme-panel-customize]');
            if (custToggle) {
                e.preventDefault();
                var content = custToggle.closest('.theme-panel-menu').querySelector('[data-theme-panel-customize-content]');
                if (content) {
                    content.classList.toggle('open');
                    custToggle.classList.toggle('open');
                }
                return;
            }
        });
    }

    // Run after DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemePanel);
    } else {
        initThemePanel();
    }

})();
