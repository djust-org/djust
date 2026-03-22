
        /**
         * Debug Warning Interceptor
         *
         * When DEBUG_MODE is true, intercepts console.warn calls matching
         * [LiveView] prefix and surfaces them in the debug panel.
         */
        _initWarningInterceptor() {
            if (!window.DEBUG_MODE) return;

            const panel = this;
            const originalWarn = console.warn;

            console.warn = function(...args) {
                originalWarn.apply(console, args);

                // Check if this is a LiveView warning
                const msg = args.length > 0 ? String(args[0]) : '';
                if (msg.indexOf('[LiveView]') === 0) {
                    panel.warningCount++;
                    panel.updateCounter('warning-count', panel.warningCount);
                    panel._updateWarningBadge();

                    // Auto-open on first error if configured
                    if (panel.warningCount === 1 && panel._shouldAutoOpen()) {
                        panel.open();
                    }
                }
            };
        }

        _shouldAutoOpen() {
            // Check LIVEVIEW_CONFIG.debug_auto_open_on_error
            if (typeof window.LIVEVIEW_CONFIG === 'object' &&
                window.LIVEVIEW_CONFIG.debug_auto_open_on_error) {
                return true;
            }
            return false;
        }

        _updateWarningBadge() {
            if (!this.button) return;
            let badge = this.button.querySelector('.warning-badge');
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'warning-badge';
                badge.setAttribute('style',
                    'position: absolute; top: -2px; left: -2px; ' +
                    'background: #f59e0b; color: #000; font-size: 10px; ' +
                    'font-weight: bold; border-radius: 50%; width: 18px; ' +
                    'height: 18px; display: flex; align-items: center; ' +
                    'justify-content: center; pointer-events: none;'
                );
                this.button.appendChild(badge);
            }
            if (this.warningCount > 0) {
                badge.textContent = this.warningCount > 99 ? '99+' : this.warningCount;
                badge.style.display = 'flex';
            } else {
                badge.style.display = 'none';
            }
        }
