
// ============================================================================
// Flash Messages — Server-to-client transient notifications (put_flash)
// ============================================================================

(function () {

    var CONTAINER_ID = 'dj-flash-container';
    var DEFAULT_AUTO_DISMISS = 5000;
    var REMOVE_TRANSITION_MS = 300;

    /**
     * Get or create the flash container element.
     * Returns null if the container doesn't exist in the DOM (tag not used).
     */
    function getContainer() {
        return document.getElementById(CONTAINER_ID);
    }

    /**
     * Handle a flash message from the server.
     *
     * data.action === 'put':   render a new flash message
     * data.action === 'clear': remove existing messages (optionally by level)
     */
    function handleFlash(data) {
        if (globalThis.djustDebug) console.log('[LiveView] flash: %o', data);

        if (data.action === 'clear') {
            clearFlash(data.level);
            return;
        }

        if (data.action === 'put') {
            showFlash(data.level, data.message);
        }
    }

    /**
     * Render a flash message into the container.
     */
    function showFlash(level, message) {
        var container = getContainer();
        if (!container) {
            if (globalThis.djustDebug) console.log('[LiveView] flash: no #dj-flash-container found, skipping');
            return;
        }

        var el = document.createElement('div');
        el.className = 'dj-flash dj-flash-' + level;
        el.setAttribute('role', 'alert');
        el.setAttribute('data-dj-flash-level', level);
        el.textContent = message;

        container.appendChild(el);

        // Auto-dismiss
        var timeout = parseInt(container.getAttribute('data-dj-auto-dismiss'), 10);
        if (isNaN(timeout)) {
            timeout = DEFAULT_AUTO_DISMISS;
        }
        if (timeout > 0) {
            setTimeout(function () {
                dismissFlash(el);
            }, timeout);
        }
    }

    /**
     * Dismiss a single flash element with a removal animation.
     */
    function dismissFlash(el) {
        if (!el || !el.parentNode) return;
        el.classList.add('dj-flash-removing');
        setTimeout(function () {
            if (el.parentNode) {
                el.parentNode.removeChild(el);
            }
        }, REMOVE_TRANSITION_MS);
    }

    /**
     * Clear flash messages from the container.
     * If level is provided, only clear messages with that level.
     */
    function clearFlash(level) {
        var container = getContainer();
        if (!container) return;

        var selector = level
            ? '.dj-flash[data-dj-flash-level="' + level + '"]'
            : '.dj-flash';
        var elements = container.querySelectorAll(selector);
        for (var i = 0; i < elements.length; i++) {
            dismissFlash(elements[i]);
        }
    }

    // Expose to djust namespace
    window.djust.flash = {
        handleFlash: handleFlash,
        show: showFlash,
        clear: clearFlash,
        dismiss: dismissFlash,
    };

})();
