// ============================================================================
// Accessibility Module (WCAG Compliance)
// ============================================================================
//
// Provides screen reader support, focus management, and keyboard navigation
// for djust LiveViews.
//
// Features:
// - ARIA live regions for screen reader announcements
// - Auto-injection of aria-live on patched DOM regions
// - Focus management after DOM updates
// - Keyboard accessibility for dj-click elements
// - Loading state announcements
//
// ============================================================================

(function() {
    'use strict';

    // Configuration (can be overridden by server during mount)
    const config = {
        ariaLiveDefault: 'polite',
        autoFocusErrors: true,
        announceLoading: true,
    };

    // ========================================================================
    // ARIA Live Region Management
    // ========================================================================

    /**
     * Get or create the announcement container for screen readers.
     * Uses a visually-hidden but screen-reader-accessible element.
     */
    function getAnnouncementContainer(priority) {
        const id = `djust-announcements-${priority}`;
        let container = document.getElementById(id);
        
        if (!container) {
            container = document.createElement('div');
            container.id = id;
            container.setAttribute('role', 'status');
            container.setAttribute('aria-live', priority);
            container.setAttribute('aria-atomic', 'true');
            // Visually hidden but accessible to screen readers
            container.style.cssText = `
                position: absolute !important;
                width: 1px !important;
                height: 1px !important;
                padding: 0 !important;
                margin: -1px !important;
                overflow: hidden !important;
                clip: rect(0, 0, 0, 0) !important;
                white-space: nowrap !important;
                border: 0 !important;
            `;
            document.body.appendChild(container);
        }
        
        return container;
    }

    /**
     * Announce a message to screen readers.
     * @param {string} message - Text to announce
     * @param {string} priority - "polite" or "assertive"
     */
    function announce(message, priority = 'polite') {
        if (!message) return;
        
        const container = getAnnouncementContainer(priority);
        
        // Clear and re-add to trigger announcement (some screen readers need this)
        container.textContent = '';
        
        // Use requestAnimationFrame to ensure the clear is processed
        requestAnimationFrame(() => {
            container.textContent = message;
            
            if (globalThis.djustDebug) {
                console.log(`[Accessibility] Announced (${priority}): "${message}"`);
            }
        });
    }

    /**
     * Process announcements from server response.
     * @param {Array} announcements - Array of [message, priority] tuples
     */
    function processAnnouncements(announcements) {
        if (!Array.isArray(announcements)) return;
        
        announcements.forEach(([message, priority]) => {
            announce(message, priority);
        });
    }

    // ========================================================================
    // ARIA Live Region Auto-Injection
    // ========================================================================

    /**
     * Auto-inject aria-live on elements being patched.
     * Called before DOM patches are applied.
     * @param {Element} element - Element being patched
     */
    function injectAriaLive(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) return;
        
        // Check for explicit dj-aria-live directive
        const explicitLive = element.getAttribute('dj-aria-live');
        if (explicitLive === 'off') {
            // Explicitly disabled
            element.removeAttribute('aria-live');
            return;
        }
        
        if (explicitLive) {
            element.setAttribute('aria-live', explicitLive);
            return;
        }
        
        // Don't auto-inject if aria-live is already set
        if (element.hasAttribute('aria-live')) return;
        
        // Check for error indicators - use assertive
        const isError = element.classList.contains('error') ||
                       element.classList.contains('is-invalid') ||
                       element.classList.contains('field-error') ||
                       element.classList.contains('form-error') ||
                       element.getAttribute('role') === 'alert' ||
                       element.getAttribute('aria-invalid') === 'true';
        
        if (isError) {
            element.setAttribute('aria-live', 'assertive');
            return;
        }
        
        // Use default for other patched regions
        if (config.ariaLiveDefault && config.ariaLiveDefault !== 'off') {
            element.setAttribute('aria-live', config.ariaLiveDefault);
        }
    }

    /**
     * Process all elements that will be affected by patches.
     * Called before applyPatches.
     * @param {Array} patches - Array of VDOM patches
     */
    function beforePatches(patches) {
        if (!Array.isArray(patches)) return;
        
        patches.forEach(patch => {
            // Try to find the target element by djust ID
            if (patch.djustId) {
                const el = document.querySelector(`[data-dj-id="${CSS.escape(patch.djustId)}"]`);
                if (el) {
                    injectAriaLive(el);
                }
            }
        });
    }

    // ========================================================================
    // Focus Management
    // ========================================================================

    // Store focus state for preservation during patches
    let savedFocusState = null;

    /**
     * Save current focus state before DOM patches.
     * Allows restoring focus position after updates.
     */
    function saveFocusState() {
        const active = document.activeElement;
        if (!active || active === document.body) {
            savedFocusState = null;
            return;
        }
        
        savedFocusState = {
            // Try multiple identification strategies
            id: active.id,
            djustId: active.getAttribute('data-dj-id'),
            name: active.getAttribute('name'),
            // For inputs, save selection state
            selectionStart: active.selectionStart,
            selectionEnd: active.selectionEnd,
            // For contenteditable
            isContentEditable: active.isContentEditable,
        };
        
        if (globalThis.djustDebug) {
            console.log('[Accessibility] Saved focus state:', savedFocusState);
        }
    }

    /**
     * Restore focus state after DOM patches.
     */
    function restoreFocusState() {
        if (!savedFocusState) return;
        
        let element = null;
        
        // Try to find element by various identifiers
        if (savedFocusState.id) {
            element = document.getElementById(savedFocusState.id);
        }
        if (!element && savedFocusState.djustId) {
            element = document.querySelector(`[data-dj-id="${CSS.escape(savedFocusState.djustId)}"]`);
        }
        if (!element && savedFocusState.name) {
            element = document.querySelector(`[name="${CSS.escape(savedFocusState.name)}"]`);
        }
        
        if (element && element !== document.activeElement) {
            try {
                element.focus({ preventScroll: true });
                
                // Restore selection state for inputs
                if (savedFocusState.selectionStart !== undefined && 
                    element.setSelectionRange) {
                    element.setSelectionRange(
                        savedFocusState.selectionStart,
                        savedFocusState.selectionEnd
                    );
                }
                
                if (globalThis.djustDebug) {
                    console.log('[Accessibility] Restored focus to:', element);
                }
            } catch (e) {
                // Some elements can't receive focus
                if (globalThis.djustDebug) {
                    console.warn('[Accessibility] Could not restore focus:', e);
                }
            }
        }
        
        savedFocusState = null;
    }

    /**
     * Set focus to an element.
     * @param {string} selector - CSS selector or special value
     * @param {Object} options - Focus options
     */
    function setFocus(selector, options = {}) {
        if (!selector) return;
        
        const doFocus = () => {
            let element = null;
            
            // Handle special selectors
            if (selector === '__djust_first_error__') {
                element = findFirstError();
            } else {
                element = document.querySelector(selector);
            }
            
            if (!element) {
                if (globalThis.djustDebug) {
                    console.warn(`[Accessibility] Focus target not found: ${selector}`);
                }
                return;
            }
            
            // Make element focusable if it isn't
            if (!isFocusable(element)) {
                element.setAttribute('tabindex', '-1');
            }
            
            try {
                element.focus({
                    preventScroll: options.preventScroll || false,
                });
                
                // Scroll into view unless prevented
                if (options.scroll !== false && !options.preventScroll) {
                    element.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                    });
                }
                
                if (globalThis.djustDebug) {
                    console.log('[Accessibility] Focused:', element);
                }
            } catch (e) {
                if (globalThis.djustDebug) {
                    console.warn('[Accessibility] Could not focus element:', e);
                }
            }
        };
        
        // Apply delay if specified
        if (options.delayMs && options.delayMs > 0) {
            setTimeout(doFocus, options.delayMs);
        } else {
            // Use requestAnimationFrame to ensure DOM is ready
            requestAnimationFrame(doFocus);
        }
    }

    /**
     * Find the first error element on the page.
     */
    function findFirstError() {
        const errorSelectors = [
            '[aria-invalid="true"]',
            '.is-invalid',
            '.error:not(.error-summary)',
            '.field-error',
            '.form-error',
            '[role="alert"]',
        ];
        
        for (const selector of errorSelectors) {
            const el = document.querySelector(selector);
            if (el) return el;
        }
        
        return null;
    }

    /**
     * Check if an element is focusable.
     */
    function isFocusable(element) {
        if (element.disabled) return false;
        if (element.hasAttribute('tabindex')) return true;
        
        const focusableTags = ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA'];
        if (focusableTags.includes(element.tagName)) return true;
        
        if (element.isContentEditable) return true;
        
        return false;
    }

    /**
     * Process focus command from server.
     * @param {Object} focusCmd - {selector, options}
     */
    function processFocus(focusCmd) {
        if (!focusCmd) return;
        
        const [selector, options] = Array.isArray(focusCmd) ? focusCmd : [focusCmd.selector, focusCmd.options || {}];
        setFocus(selector, options);
    }

    // ========================================================================
    // Keyboard Navigation
    // ========================================================================

    /**
     * Make dj-click elements keyboard accessible.
     * Called after event binding.
     */
    function enhanceKeyboardAccessibility() {
        // Find all elements with dj-click that aren't natively interactive
        const clickables = document.querySelectorAll('[dj-click]:not(button):not(a):not(input):not(select):not(textarea)');
        
        clickables.forEach(element => {
            // Skip if already enhanced
            if (element.dataset.djustKeyboardEnhanced) return;
            element.dataset.djustKeyboardEnhanced = 'true';
            
            // Add role="button" if no role is set
            if (!element.hasAttribute('role')) {
                element.setAttribute('role', 'button');
            }
            
            // Make focusable if not already
            if (!element.hasAttribute('tabindex')) {
                element.setAttribute('tabindex', '0');
            }
            
            // Handle keyboard events (Enter and Space)
            element.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    element.click();
                }
            });
            
            // Handle dj-keyboard directive for custom key handlers
            const keyboardHandler = element.getAttribute('dj-keyboard');
            if (keyboardHandler) {
                bindCustomKeyHandler(element, keyboardHandler);
            }
        });
        
        if (globalThis.djustDebug) {
            console.log(`[Accessibility] Enhanced ${clickables.length} elements for keyboard access`);
        }
    }

    /**
     * Bind custom keyboard handler from dj-keyboard directive.
     * Format: dj-keyboard="handler_name" or dj-keyboard.key="handler_name"
     * @param {Element} element 
     * @param {string} handler 
     */
    function bindCustomKeyHandler(element, handler) {
        // Parse handler for key modifiers
        const parts = handler.split('.');
        const handlerName = parts[0];
        const keyFilter = parts.length > 1 ? parts[1].toLowerCase() : null;
        
        element.addEventListener('keydown', async (e) => {
            // Apply key filter if specified
            if (keyFilter) {
                const keyMap = {
                    'enter': 'Enter',
                    'escape': 'Escape',
                    'space': ' ',
                    'tab': 'Tab',
                    'up': 'ArrowUp',
                    'down': 'ArrowDown',
                    'left': 'ArrowLeft',
                    'right': 'ArrowRight',
                };
                
                const requiredKey = keyMap[keyFilter] || keyFilter;
                if (e.key !== requiredKey) return;
            }
            
            // Prevent default for handled keys
            e.preventDefault();
            
            // Send event to server
            if (window.handleEvent) {
                await window.handleEvent(handlerName, {
                    key: e.key,
                    code: e.code,
                    ctrlKey: e.ctrlKey,
                    shiftKey: e.shiftKey,
                    altKey: e.altKey,
                    metaKey: e.metaKey,
                });
            }
        });
    }

    // ========================================================================
    // Loading State Announcements
    // ========================================================================

    let loadingAnnouncementTimeout = null;

    /**
     * Announce loading start to screen readers.
     */
    function announceLoadingStart() {
        if (!config.announceLoading) return;
        
        // Debounce rapid loading states
        if (loadingAnnouncementTimeout) {
            clearTimeout(loadingAnnouncementTimeout);
        }
        
        loadingAnnouncementTimeout = setTimeout(() => {
            announce('Loading...', 'polite');
        }, 200);  // Only announce if loading takes more than 200ms
    }

    /**
     * Announce loading complete to screen readers.
     */
    function announceLoadingComplete() {
        if (!config.announceLoading) return;
        
        // Cancel pending "Loading..." announcement
        if (loadingAnnouncementTimeout) {
            clearTimeout(loadingAnnouncementTimeout);
            loadingAnnouncementTimeout = null;
        }
        
        // Don't announce completion if loading was very fast
        // The actual content update will be announced via live regions
    }

    // ========================================================================
    // dj-focus Directive
    // ========================================================================

    /**
     * Process dj-focus directives on elements.
     * Called after DOM patches.
     */
    function processDjFocusDirectives() {
        const focusElements = document.querySelectorAll('[dj-focus]');
        
        focusElements.forEach(element => {
            const focusValue = element.getAttribute('dj-focus');
            
            // dj-focus without value means focus this element
            if (!focusValue || focusValue === 'true' || focusValue === '') {
                setFocus(getElementSelector(element), { scroll: true });
                // Remove the directive after processing (one-time focus)
                element.removeAttribute('dj-focus');
            }
        });
    }

    /**
     * Get a selector for an element.
     */
    function getElementSelector(element) {
        if (element.id) return `#${element.id}`;
        const djustId = element.getAttribute('data-dj-id');
        if (djustId) return `[data-dj-id="${djustId}"]`;
        return null;
    }

    // ========================================================================
    // Integration with djust Core
    // ========================================================================

    /**
     * Initialize accessibility features.
     * Called after djust core initializes.
     */
    function init() {
        // Enhance keyboard accessibility on initial load
        enhanceKeyboardAccessibility();
        
        if (globalThis.djustDebug) {
            console.log('[Accessibility] Initialized');
        }
    }

    /**
     * Update accessibility configuration from server.
     * @param {Object} newConfig 
     */
    function setConfig(newConfig) {
        if (newConfig) {
            Object.assign(config, newConfig);
            if (globalThis.djustDebug) {
                console.log('[Accessibility] Config updated:', config);
            }
        }
    }

    /**
     * Called after DOM patches are applied.
     */
    function afterPatches() {
        // Re-enhance any new clickable elements
        enhanceKeyboardAccessibility();
        
        // Process dj-focus directives
        processDjFocusDirectives();
        
        // Restore focus if it was saved
        restoreFocusState();
    }

    // ========================================================================
    // Expose API
    // ========================================================================

    window.djust = window.djust || {};
    window.djust.accessibility = {
        // Core functions
        announce,
        setFocus,
        setConfig,
        
        // Lifecycle hooks for core integration
        init,
        beforePatches,
        afterPatches,
        saveFocusState,
        restoreFocusState,
        
        // Processing server responses
        processAnnouncements,
        processFocus,
        
        // Loading state
        announceLoadingStart,
        announceLoadingComplete,
        
        // Keyboard enhancement
        enhanceKeyboardAccessibility,
        
        // ARIA injection
        injectAriaLive,
        
        // Utilities
        findFirstError,
        isFocusable,
        
        // Config access
        getConfig: () => ({ ...config }),
    };

    // Auto-init when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
