
// ============================================================================
// Lazy Hydration Support (Performance Optimization)
// ============================================================================

/**
 * Lazy LiveView Hydration Manager
 *
 * Defers WebSocket connection and LiveView mounting until elements enter the
 * viewport. This significantly reduces memory usage and WebSocket connections
 * for pages with below-fold LiveView components.
 *
 * Usage:
 *   <div data-djust-view="my_view" data-djust-lazy>
 *     <!-- Content loads when element scrolls into view -->
 *   </div>
 *
 *   <div data-djust-view="my_view" data-djust-lazy="click">
 *     <!-- Content loads on first user interaction -->
 *   </div>
 *
 * Supported lazy modes:
 *   - "viewport" (default): Mount when element enters viewport
 *   - "click": Mount on first click within the element
 *   - "hover": Mount on first mouse hover
 *   - "idle": Mount when browser is idle (requestIdleCallback)
 */
const lazyHydrationManager = {
    // Set of element IDs that have been hydrated
    hydratedElements: new Set(),

    // IntersectionObserver instance for viewport-based hydration
    viewportObserver: null,

    // Queue of elements waiting for WebSocket connection
    pendingMounts: [],

    // Initialize lazy hydration
    init() {
        // Clear pending mounts on reinit (e.g., TurboNav navigation)
        this.pendingMounts = [];
        this.hydratedElements.clear();

        // Inject CSS for lazy click elements (only once)
        if (!document.getElementById('djust-lazy-styles')) {
            const style = document.createElement('style');
            style.id = 'djust-lazy-styles';
            style.textContent = '.djust-lazy-click { cursor: pointer; }';
            document.head.appendChild(style);
        }

        // Create viewport observer if supported
        if ('IntersectionObserver' in window) {
            this.viewportObserver = new IntersectionObserver(
                (entries) => this.handleIntersection(entries),
                {
                    // Start loading slightly before element is visible
                    rootMargin: '50px',
                    threshold: 0
                }
            );
        }
    },

    // Handle viewport intersection
    handleIntersection(entries) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const element = entry.target;
                this.hydrateElement(element);
                this.viewportObserver.unobserve(element);
            }
        });
    },

    // Register an element for lazy hydration
    register(element) {
        const lazyMode = element.getAttribute('data-djust-lazy') || 'viewport';

        switch (lazyMode) {
            case 'click':
                element.addEventListener('click', () => this.hydrateElement(element), { once: true });
                // Add CSS class for styling (avoids overriding inline styles)
                element.classList.add('djust-lazy-click');
                break;

            case 'hover':
                element.addEventListener('mouseenter', () => this.hydrateElement(element), { once: true });
                break;

            case 'idle':
                if ('requestIdleCallback' in window) {
                    requestIdleCallback(() => this.hydrateElement(element), { timeout: 5000 });
                } else {
                    // Fallback: use setTimeout
                    setTimeout(() => this.hydrateElement(element), 2000);
                }
                break;

            case 'viewport':
            case '':
            default:
                if (this.viewportObserver) {
                    this.viewportObserver.observe(element);
                } else {
                    // Fallback for browsers without IntersectionObserver
                    this.hydrateElement(element);
                }
                break;
        }

        if (globalThis.djustDebug) {
            console.log(`[LiveView:lazy] Registered element for lazy hydration (mode: ${lazyMode})`, element);
        }
    },

    // Hydrate a single element
    hydrateElement(element) {
        const elementId = element.id || element.getAttribute('data-djust-view');

        // Prevent double hydration
        if (this.hydratedElements.has(elementId)) {
            return;
        }
        this.hydratedElements.add(elementId);

        const viewPath = element.getAttribute('data-djust-view');
        if (!viewPath) {
            console.warn('[LiveView:lazy] Element missing data-djust-view attribute', element);
            return;
        }

        console.log(`[LiveView:lazy] Hydrating: ${viewPath}`);

        // Ensure WebSocket is connected
        if (!liveViewWS || !liveViewWS.enabled) {
            liveViewWS = new LiveViewWebSocket();
            liveViewWS.connect();
        }

        // Wait for WebSocket connection then mount
        if (liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN) {
            this.mountElement(element, viewPath);
        } else {
            // Queue mount for when WebSocket connects (handles multiple lazy elements)
            this.pendingMounts.push({ element, viewPath });

            // Set up connection callback if not already done
            if (this.pendingMounts.length === 1 && liveViewWS.ws) {
                const originalOnOpen = liveViewWS.ws.onopen;
                liveViewWS.ws.onopen = (event) => {
                    if (originalOnOpen) originalOnOpen.call(liveViewWS.ws, event);
                    this.processPendingMounts();
                };
            }
        }
    },

    // Process all queued mounts when WebSocket connects
    processPendingMounts() {
        console.log(`[LiveView:lazy] Processing ${this.pendingMounts.length} pending mounts`);
        const mounts = this.pendingMounts.slice();
        this.pendingMounts = [];
        mounts.forEach(({ element, viewPath }) => {
            this.mountElement(element, viewPath);
        });
    },

    // Mount a specific element
    mountElement(element, viewPath) {
        // Check if content was already pre-rendered
        const hasContent = element.innerHTML && element.innerHTML.trim().length > 0;

        if (hasContent) {
            console.log('[LiveView:lazy] Using pre-rendered content');
            liveViewWS.skipMountHtml = true;
        }

        // Pass URL query params
        const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
        liveViewWS.mount(viewPath, urlParams);

        // Remove lazy attribute to indicate hydration complete
        element.removeAttribute('data-djust-lazy');
        element.setAttribute('data-live-hydrated', 'true');

        // Bind events to the newly hydrated content
        bindLiveViewEvents();
    },

    // Check if an element is lazily loaded
    isLazy(element) {
        return element.hasAttribute('data-djust-lazy');
    },

    // Force hydrate all lazy elements (useful for testing or SPA navigation)
    hydrateAll() {
        document.querySelectorAll('[data-djust-lazy]').forEach(el => {
            this.hydrateElement(el);
        });
    }
};

// Expose lazy hydration API
window.djust.lazyHydration = lazyHydrationManager;
