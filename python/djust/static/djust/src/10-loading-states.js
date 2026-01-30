
// Global Loading Manager (Phase 5)
// Handles dj-loading.disable, dj-loading.class, dj-loading.show, dj-loading.hide attributes
const globalLoadingManager = {
    // Map of element -> { originalState, modifiers }
    registeredElements: new Map(),
    pendingEvents: new Set(),

    // Register an element with dj-loading attributes
    register(element, eventName) {
        const modifiers = [];
        const originalState = {};

        // Parse dj-loading.* attributes
        Array.from(element.attributes).forEach(attr => {
            const match = attr.name.match(/^dj-loading\.(.+)$/);
            if (match) {
                const modifier = match[1];
                if (modifier === 'disable') {
                    modifiers.push({ type: 'disable' });
                    originalState.disabled = element.disabled;
                } else if (modifier === 'show') {
                    modifiers.push({ type: 'show' });
                    // Store original inline display to restore when loading stops
                    originalState.display = element.style.display;
                    // Determine display value to use when showing:
                    // 1. Use attribute value if specified (e.g., dj-loading.show="flex")
                    // 2. Otherwise default to 'block'
                    originalState.visibleDisplay = attr.value || 'block';
                } else if (modifier === 'hide') {
                    modifiers.push({ type: 'hide' });
                    // Store computed display to properly restore when loading stops
                    const computedDisplay = getComputedStyle(element).display;
                    originalState.display = computedDisplay !== 'none' ? computedDisplay : (element.style.display || 'block');
                } else if (modifier === 'class') {
                    const className = attr.value;
                    if (className) {
                        modifiers.push({ type: 'class', value: className });
                    }
                }
            }
        });

        if (modifiers.length > 0) {
            this.registeredElements.set(element, { eventName, modifiers, originalState });
            if (globalThis.djustDebug) {
                console.log(`[Loading] Registered element for "${eventName}":`, modifiers);
            }
        }
    },

    // Scan and register all elements with dj-loading attributes
    scanAndRegister() {
        // Use targeted attribute selectors for better performance on large pages
        const selectors = [
            '[dj-loading\\.disable]',
            '[dj-loading\\.show]',
            '[dj-loading\\.hide]',
            '[dj-loading\\.class]'
        ].join(',');

        const loadingElements = document.querySelectorAll(selectors);
        loadingElements.forEach(element => {
            // Find the associated event from dj-click, dj-submit, etc.
            const eventAttr = Array.from(element.attributes).find(
                attr => attr.name.startsWith('dj-') && !attr.name.startsWith('dj-loading')
            );
            const eventName = eventAttr ? eventAttr.value : null;
            if (eventName) {
                this.register(element, eventName);
            }
        });
        if (globalThis.djustDebug) {
            console.log(`[Loading] Scanned ${this.registeredElements.size} elements with dj-loading attributes`);
        }
    },

    startLoading(eventName, triggerElement) {
        this.pendingEvents.add(eventName);

        // Apply loading state to trigger element
        if (triggerElement) {
            triggerElement.classList.add('djust-loading');

            // Check if trigger element has dj-loading.disable
            const hasDisable = triggerElement.hasAttribute('dj-loading.disable');
            if (globalThis.djustDebug) {
                console.log(`[Loading] triggerElement:`, triggerElement);
                console.log(`[Loading] hasAttribute('dj-loading.disable'):`, hasDisable);
            }
            if (hasDisable) {
                triggerElement.disabled = true;
            }
        }

        // Apply loading state to all registered elements watching this event
        this.registeredElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                this.applyLoadingState(element, config);
            }
        });

        document.body.classList.add('djust-global-loading');

        if (globalThis.djustDebug) {
            console.log(`[Loading] Started: ${eventName}`);
        }
    },

    stopLoading(eventName, triggerElement) {
        this.pendingEvents.delete(eventName);

        // Remove loading state from trigger element
        if (triggerElement) {
            triggerElement.classList.remove('djust-loading');
            // Check if trigger element has dj-loading.disable
            const hasDisable = triggerElement.hasAttribute('dj-loading.disable');
            if (hasDisable) {
                triggerElement.disabled = false;
            }
        }

        // Remove loading state from all registered elements watching this event
        this.registeredElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                this.removeLoadingState(element, config);
            }
        });

        document.body.classList.remove('djust-global-loading');

        if (globalThis.djustDebug) {
            console.log(`[Loading] Stopped: ${eventName}`);
        }
    },

    applyLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = true;
            } else if (modifier.type === 'show') {
                // Use stored visible display value (supports dj-loading.show="flex" etc.)
                element.style.display = config.originalState.visibleDisplay || 'block';
            } else if (modifier.type === 'hide') {
                element.style.display = 'none';
            } else if (modifier.type === 'class') {
                element.classList.add(modifier.value);
            }
        });
    },

    removeLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = config.originalState.disabled || false;
            } else if (modifier.type === 'show') {
                element.style.display = config.originalState.display || 'none';
            } else if (modifier.type === 'hide') {
                element.style.display = config.originalState.display || '';
            } else if (modifier.type === 'class') {
                element.classList.remove(modifier.value);
            }
        });
    }
};

// Expose globalLoadingManager under djust namespace
window.djust.globalLoadingManager = globalLoadingManager;
// Backward compatibility
window.globalLoadingManager = globalLoadingManager;

// Generate unique request ID for cache tracking
let cacheRequestCounter = 0;
function generateCacheRequestId() {
    return `cache_${Date.now()}_${++cacheRequestCounter}`;
}
