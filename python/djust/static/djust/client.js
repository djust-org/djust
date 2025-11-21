// djust - WebSocket + HTTP Fallback Client

// ============================================================================
// Centralized Response Handler (WebSocket + HTTP)
// ============================================================================

/**
 * Centralized server response handler for both WebSocket and HTTP fallback.
 * Eliminates code duplication and ensures consistent behavior.
 *
 * @param {Object} data - Server response data
 * @param {string} eventName - Name of the event that triggered this response
 * @param {HTMLElement} triggerElement - Element that triggered the event
 * @returns {boolean} - True if handled successfully, false otherwise
 */
function handleServerResponse(data, eventName, triggerElement) {
    try {
        // Handle cache storage (from @cache decorator)
        if (data.cache_request_id && pendingCacheRequests.has(data.cache_request_id)) {
            const { cacheKey, ttl } = pendingCacheRequests.get(data.cache_request_id);
            const expiresAt = Date.now() + (ttl * 1000);
            resultCache.set(cacheKey, {
                patches: data.patches,
                expiresAt
            });
            if (globalThis.djustDebug) {
                console.log(`[LiveView:cache] Cached patches: ${cacheKey} (TTL: ${ttl}s)`);
            }
            pendingCacheRequests.delete(data.cache_request_id);
        }

        // Handle version tracking and mismatch
        if (data.version !== undefined) {
            if (clientVdomVersion === null) {
                clientVdomVersion = data.version;
                console.log('[LiveView] Initialized VDOM version:', clientVdomVersion);
            } else if (clientVdomVersion !== data.version - 1) {
                // Version mismatch - force full reload
                console.warn('[LiveView] VDOM version mismatch!');
                console.warn(`  Expected v${clientVdomVersion + 1}, got v${data.version}`);

                clearOptimisticState(eventName);

                if (data.html) {
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(data.html, 'text/html');
                    const liveviewRoot = getLiveViewRoot();
                    const newRoot = doc.querySelector('[data-liveview-root]') || doc.body;
                    liveviewRoot.innerHTML = newRoot.innerHTML;
                    clientVdomVersion = data.version;
                    initReactCounters();
                    initTodoItems();
                    bindLiveViewEvents();
                }

                globalLoadingManager.stopLoading(eventName, triggerElement);
                return true;
            }
            clientVdomVersion = data.version;
        }

        // Clear optimistic state BEFORE applying changes
        clearOptimisticState(eventName);

        // Global cleanup of lingering optimistic-pending classes
        document.querySelectorAll('.optimistic-pending').forEach(el => {
            el.classList.remove('optimistic-pending');
        });

        // Apply patches (efficient incremental updates)
        if (data.patches && Array.isArray(data.patches) && data.patches.length > 0) {
            console.log('[LiveView] Applying', data.patches.length, 'patches');

            const success = applyPatches(data.patches);
            if (success === false) {
                console.error('[LiveView] Patches failed, reloading page...');
                globalLoadingManager.stopLoading(eventName, triggerElement);
                window.location.reload();
                return false;
            }

            console.log('[LiveView] Patches applied successfully');

            // Final cleanup
            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            initReactCounters();
            initTodoItems();
            bindLiveViewEvents();
        }
        // Apply full HTML update (fallback)
        else if (data.html) {
            console.log('[LiveView] Applying full HTML update');
            const parser = new DOMParser();
            const doc = parser.parseFromString(data.html, 'text/html');
            const liveviewRoot = getLiveViewRoot();
            const newRoot = doc.querySelector('[data-liveview-root]') || doc.body;
            liveviewRoot.innerHTML = newRoot.innerHTML;

            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            initReactCounters();
            initTodoItems();
            bindLiveViewEvents();
        } else {
            console.warn('[LiveView] Response has neither patches nor html!', data);
        }

        // Handle form reset
        if (data.reset_form) {
            console.log('[LiveView] Resetting form');
            const form = document.querySelector('[data-liveview-root] form');
            if (form) form.reset();
        }

        // Stop loading state
        globalLoadingManager.stopLoading(eventName, triggerElement);
        return true;

    } catch (error) {
        console.error('[LiveView] Error in handleServerResponse:', error);
        globalLoadingManager.stopLoading(eventName, triggerElement);
        return false;
    }
}

// ============================================================================
// WebSocket LiveView Client
// ============================================================================

class LiveViewWebSocket {
    constructor() {
        this.ws = null;
        this.sessionId = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.viewMounted = false;
        this.enabled = true;  // Can be disabled to use HTTP fallback
        this.lastEventName = null;  // Phase 5: Track last event for loading state
        this.lastTriggerElement = null;  // Phase 5: Track trigger element for scoped loading
    }

    connect(url = null) {
        if (!this.enabled) return;

        if (!url) {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;
            url = `${protocol}//${host}/ws/live/`;
        }

        console.log('[LiveView] Connecting to WebSocket:', url);
        this.ws = new WebSocket(url);

        this.ws.onopen = (event) => {
            console.log('[LiveView] WebSocket connected');
            this.reconnectAttempts = 0;
        };

        this.ws.onclose = (event) => {
            console.log('[LiveView] WebSocket disconnected');
            this.viewMounted = false;

            // Clear all decorator state on disconnect
            // Phase 2: Debounce timers
            debounceTimers.forEach(state => {
                if (state.timerId) {
                    clearTimeout(state.timerId);
                }
            });
            debounceTimers.clear();

            // Phase 2: Throttle timers
            throttleState.forEach(state => {
                if (state.timeoutId) {
                    clearTimeout(state.timeoutId);
                }
            });
            throttleState.clear();

            // Phase 3: Optimistic updates
            optimisticUpdates.clear();
            pendingEvents.clear();

            // Remove loading indicators from DOM
            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                console.log(`[LiveView] Reconnecting in ${delay}ms...`);
                setTimeout(() => this.connect(url), delay);
            } else {
                console.warn('[LiveView] Max reconnection attempts reached. Falling back to HTTP mode.');
                this.enabled = false;
            }
        };

        this.ws.onerror = (error) => {
            console.error('[LiveView] WebSocket error:', error);
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (error) {
                console.error('[LiveView] Failed to parse message:', error);
            }
        };
    }

    handleMessage(data) {
        console.log('[LiveView] Received:', data.type, data);

        switch (data.type) {
            case 'connected':
                this.sessionId = data.session_id;
                console.log('[LiveView] Session ID:', this.sessionId);
                this.autoMount();
                break;

            case 'mounted':
                this.viewMounted = true;
                console.log('[LiveView] View mounted:', data.view);

                // Initialize VDOM version from mount response (critical for patch generation)
                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                    console.log('[LiveView] VDOM version initialized:', clientVdomVersion);
                }

                // OPTIMIZATION: Skip HTML replacement if content was pre-rendered via HTTP GET
                if (this.skipMountHtml) {
                    console.log('[LiveView] Skipping mount HTML - using pre-rendered content');
                    this.skipMountHtml = false; // Reset flag
                    bindLiveViewEvents(); // Bind events to existing content
                } else if (data.html) {
                    // Replace page content with server-rendered HTML
                    let container = document.querySelector('[data-live-view]');
                    if (!container) {
                        container = document.querySelector('[data-liveview-root]');
                    }
                    if (container) {
                        container.innerHTML = data.html;
                        bindLiveViewEvents();
                    }
                }
                break;

            case 'patch':
                // Use centralized response handler
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_update':
                // Use centralized response handler
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'error':
                console.error('[LiveView] Server error:', data.error);
                if (data.traceback) {
                    console.error('Traceback:', data.traceback);
                }

                // Phase 5: Stop loading state on error
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'pong':
                // Heartbeat response
                break;
        }
    }

    mount(viewPath, params = {}) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        console.log('[LiveView] Mounting view:', viewPath);
        this.ws.send(JSON.stringify({
            type: 'mount',
            view: viewPath,
            params: params
        }));
        return true;
    }

    autoMount() {
        // Look for container with view path
        let container = document.querySelector('[data-live-view]');
        if (!container) {
            // Fallback: look for data-liveview-root with data-live-view attribute
            container = document.querySelector('[data-liveview-root][data-live-view]');
        }

        if (container) {
            const viewPath = container.dataset.liveView;
            if (viewPath) {
                // OPTIMIZATION: Check if content was already rendered by HTTP GET
                // We still send mount message (server needs to initialize session),
                // but we'll skip applying the HTML response
                const hasContent = container.innerHTML && container.innerHTML.trim().length > 0;

                if (hasContent) {
                    console.log('[LiveView] Content pre-rendered via HTTP - will skip HTML in mount response');
                    this.skipMountHtml = true;
                }

                // Always send mount message to initialize server-side session
                this.mount(viewPath);
            } else {
                console.warn('[LiveView] Container found but no view path specified');
            }
        } else {
            console.warn('[LiveView] No LiveView container found for auto-mounting');
        }
    }

    sendEvent(eventName, params = {}, triggerElement = null) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        if (!this.viewMounted) {
            console.warn('[LiveView] View not mounted. Event ignored:', eventName);
            return false;
        }

        // Phase 5: Track event name and trigger element for loading state
        this.lastEventName = eventName;
        this.lastTriggerElement = triggerElement;

        this.ws.send(JSON.stringify({
            type: 'event',
            event: eventName,
            params: params
        }));
        return true;
    }

    // Removed duplicate applyPatches and patch helper methods
    // Now using centralized handleServerResponse() -> applyPatches()

    startHeartbeat(interval = 30000) {
        setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, interval);
    }
}

// Global WebSocket instance
let liveViewWS = null;

// === HTTP Fallback LiveView Client ===

// Track VDOM version for synchronization
let clientVdomVersion = null;

// State management for decorators
const debounceTimers = new Map(); // Map<handlerName, {timerId, firstCallTime}>
const throttleState = new Map();  // Map<handlerName, {lastCall, timeoutId, pendingData}>
const optimisticUpdates = new Map(); // Map<eventName, {element, originalState}>
const pendingEvents = new Set(); // Set<eventName> (for loading indicators)
const resultCache = new Map(); // Map<cacheKey, {patches, expiresAt}>
const pendingCacheRequests = new Map(); // Map<requestId, {cacheKey, ttl}>

// StateBus - Client-side State Coordination (Phase 5)
class StateBus {
    constructor() {
        this.state = new Map();
        this.subscribers = new Map();
    }

    set(key, value) {
        const oldValue = this.state.get(key);
        this.state.set(key, value);
        if (globalThis.djustDebug) {
            console.log(`[StateBus] Set: ${key} =`, value, `(was:`, oldValue, `)`);
        }
        this.notify(key, value, oldValue);
    }

    get(key) {
        return this.state.get(key);
    }

    subscribe(key, callback) {
        if (!this.subscribers.has(key)) {
            this.subscribers.set(key, new Set());
        }
        this.subscribers.get(key).add(callback);
        if (globalThis.djustDebug) {
            console.log(`[StateBus] Subscribed to: ${key} (${this.subscribers.get(key).size} subscribers)`);
        }
        return () => {
            const subs = this.subscribers.get(key);
            if (subs) {
                subs.delete(callback);
                if (globalThis.djustDebug) {
                    console.log(`[StateBus] Unsubscribed from: ${key} (${subs.size} remaining)`);
                }
            }
        };
    }

    notify(key, newValue, oldValue) {
        const callbacks = this.subscribers.get(key) || new Set();
        if (callbacks.size > 0 && globalThis.djustDebug) {
            console.log(`[StateBus] Notifying ${callbacks.size} subscribers of: ${key}`);
        }
        callbacks.forEach(callback => {
            try {
                callback(newValue, oldValue);
            } catch (error) {
                console.error(`[StateBus] Subscriber error for ${key}:`, error);
            }
        });
    }

    clear() {
        this.state.clear();
        this.subscribers.clear();
        if (globalThis.djustDebug) {
            console.log('[StateBus] Cleared all state');
        }
    }

    getAll() {
        return Object.fromEntries(this.state.entries());
    }
}

const globalStateBus = new StateBus();

// DraftManager for localStorage-based draft saving
class DraftManager {
    constructor() {
        this.saveTimers = new Map();
        this.saveDelay = 500;
    }

    saveDraft(draftKey, data) {
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
        }

        const timerId = setTimeout(() => {
            try {
                const draftData = {
                    data,
                    timestamp: Date.now()
                };
                localStorage.setItem(`djust_draft_${draftKey}`, JSON.stringify(draftData));

                if (globalThis.djustDebug) {
                    console.log(`[DraftMode] Saved draft: ${draftKey}`, data);
                }
            } catch (error) {
                console.error(`[DraftMode] Failed to save draft ${draftKey}:`, error);
            }
            this.saveTimers.delete(draftKey);
        }, this.saveDelay);

        this.saveTimers.set(draftKey, timerId);
    }

    loadDraft(draftKey) {
        try {
            const stored = localStorage.getItem(`djust_draft_${draftKey}`);
            if (!stored) {
                return null;
            }

            const draftData = JSON.parse(stored);

            if (globalThis.djustDebug) {
                const age = Math.round((Date.now() - draftData.timestamp) / 1000);
                console.log(`[DraftMode] Loaded draft: ${draftKey} (${age}s old)`, draftData.data);
            }

            return draftData.data;
        } catch (error) {
            console.error(`[DraftMode] Failed to load draft ${draftKey}:`, error);
            return null;
        }
    }

    clearDraft(draftKey) {
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
            this.saveTimers.delete(draftKey);
        }

        try {
            localStorage.removeItem(`djust_draft_${draftKey}`);

            if (globalThis.djustDebug) {
                console.log(`[DraftMode] Cleared draft: ${draftKey}`);
            }
        } catch (error) {
            console.error(`[DraftMode] Failed to clear draft ${draftKey}:`, error);
        }
    }

    getAllDraftKeys() {
        const keys = [];
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith('djust_draft_')) {
                    keys.push(key.replace('djust_draft_', ''));
                }
            }
        } catch (error) {
            console.error('[DraftMode] Failed to get draft keys:', error);
        }
        return keys;
    }

    clearAllDrafts() {
        const keys = this.getAllDraftKeys();
        keys.forEach(key => this.clearDraft(key));

        if (globalThis.djustDebug) {
            console.log(`[DraftMode] Cleared all ${keys.length} drafts`);
        }
    }
}

const globalDraftManager = new DraftManager();

function initDraftMode() {
    // Check if draft mode is enabled on this page
    const draftRoot = document.querySelector('[data-draft-enabled]');
    if (!draftRoot) return;

    const draftKey = draftRoot.getAttribute('data-draft-key');
    if (!draftKey) {
        console.warn('[DraftMode] Draft enabled but no draft-key found');
        return;
    }

    console.log(`[DraftMode] Initializing draft mode with key: ${draftKey}`);

    // Load existing draft on page load
    const savedDraft = globalDraftManager.loadDraft(draftKey);
    if (savedDraft) {
        // Restore field values from draft
        Object.keys(savedDraft).forEach(fieldName => {
            const field = document.querySelector(`[name="${fieldName}"]`);
            if (field) {
                if (field.type === 'checkbox') {
                    field.checked = savedDraft[fieldName];
                } else {
                    field.value = savedDraft[fieldName];
                }
            }
        });
    }

    // Monitor all fields with data-draft="true" for changes
    const draftFields = document.querySelectorAll('[data-draft="true"]');
    draftFields.forEach(field => {
        const saveDraft = () => {
            // Collect all draft field values
            const draftData = {};
            draftFields.forEach(f => {
                if (f.name) {
                    if (f.type === 'checkbox') {
                        draftData[f.name] = f.checked;
                    } else {
                        draftData[f.name] = f.value;
                    }
                }
            });
            globalDraftManager.saveDraft(draftKey, draftData);
        };

        // Attach input listeners with debouncing built into DraftManager
        field.addEventListener('input', saveDraft);
        field.addEventListener('change', saveDraft);
    });

    // Check for draft clear flag
    if (draftRoot.hasAttribute('data-draft-clear')) {
        console.log('[DraftMode] Draft clear flag detected, clearing draft...');
        globalDraftManager.clearDraft(draftKey);
        draftRoot.removeAttribute('data-draft-clear');
    }
}

function collectFormData(container) {
    const data = {};

    const fields = container.querySelectorAll('input, textarea, select');
    fields.forEach(field => {
        if (field.name) {
            if (field.type === 'checkbox') {
                data[field.name] = field.checked;
            } else if (field.type === 'radio') {
                if (field.checked) {
                    data[field.name] = field.value;
                }
            } else {
                data[field.name] = field.value;
            }
        }
    });

    const editables = container.querySelectorAll('[contenteditable="true"]');
    editables.forEach(editable => {
        const name = editable.getAttribute('name') || editable.id;
        if (name) {
            data[name] = editable.innerHTML;
        }
    });

    return data;
}

function restoreFormData(container, data) {
    if (!data) return;

    Object.entries(data).forEach(([name, value]) => {
        let field = container.querySelector(`[name="${name}"]`);

        if (!field) {
            field = container.querySelector(`#${name}`);
        }

        if (!field) return;

        if (field.tagName === 'INPUT') {
            if (field.type === 'checkbox') {
                field.checked = value;
            } else if (field.type === 'radio') {
                if (field.value === value) {
                    field.checked = true;
                }
            } else {
                field.value = value;
            }
        } else if (field.tagName === 'TEXTAREA') {
            field.value = value;
        } else if (field.tagName === 'SELECT') {
            field.value = value;
        } else if (field.getAttribute('contenteditable') === 'true') {
            field.innerHTML = value;
        }
    });

    if (globalThis.djustDebug) {
        console.log('[DraftMode] Restored form data:', data);
    }
}

// Client-side React Counter component (vanilla JS implementation)
function initReactCounters() {
    document.querySelectorAll('[data-react-component="Counter"]').forEach(container => {
        const propsJson = container.dataset.reactProps;
        let props = {};
        try {
            props = JSON.parse(propsJson.replace(/&quot;/g, '"'));
        } catch (e) { }

        let count = props.initialCount || 0;
        const display = container.querySelector('.counter-display');
        const minusBtn = container.querySelectorAll('.btn-sm')[0];
        const plusBtn = container.querySelectorAll('.btn-sm')[1];

        if (display && minusBtn && plusBtn) {
            minusBtn.addEventListener('click', () => {
                count--;
                display.textContent = count;
            });
            plusBtn.addEventListener('click', () => {
                count++;
                display.textContent = count;
            });
        }
    });
}

// Stub function for todo items initialization (reserved for future use)
function initTodoItems() {
    // Currently no-op - todo functionality handled via LiveView events
}

// Smart default rate limiting by input type
// Prevents VDOM version mismatches from high-frequency events
const DEFAULT_RATE_LIMITS = {
    'range': { type: 'throttle', ms: 150 },      // Sliders
    'number': { type: 'throttle', ms: 100 },     // Number spinners
    'color': { type: 'throttle', ms: 150 },      // Color pickers
    'text': { type: 'debounce', ms: 300 },       // Text inputs
    'search': { type: 'debounce', ms: 300 },     // Search boxes
    'email': { type: 'debounce', ms: 300 },      // Email inputs
    'url': { type: 'debounce', ms: 300 },        // URL inputs
    'tel': { type: 'debounce', ms: 300 },        // Phone inputs
    'password': { type: 'debounce', ms: 300 },   // Password inputs
    'textarea': { type: 'debounce', ms: 300 }    // Multi-line text
};

function bindLiveViewEvents() {
    // Find all interactive elements
    const allElements = document.querySelectorAll('*');
    allElements.forEach(element => {
        // Handle @click events
        const clickHandler = element.getAttribute('@click');
        if (clickHandler && !element.dataset.liveviewClickBound) {
            element.dataset.liveviewClickBound = 'true';
            element.addEventListener('click', async (e) => {
                e.preventDefault();

                // Extract all data-* attributes
                const params = {};
                Array.from(element.attributes).forEach(attr => {
                    if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                        // Convert data-foo-bar to foo_bar
                        const key = attr.name.substring(5).replace(/-/g, '_');
                        params[key] = attr.value;
                    }
                });

                // Phase 4: Check if event is from a component (walk up DOM tree)
                let currentElement = e.currentTarget;
                while (currentElement && currentElement !== document.body) {
                    if (currentElement.dataset.componentId) {
                        params.component_id = currentElement.dataset.componentId;
                        break;
                    }
                    currentElement = currentElement.parentElement;
                }

                // Pass target element for optimistic updates (Phase 3)
                params._targetElement = e.currentTarget;

                await handleEvent(clickHandler, params);
            });
        }

        // Handle @submit events on forms
        const submitHandler = element.getAttribute('@submit');
        if (submitHandler && !element.dataset.liveviewSubmitBound) {
            element.dataset.liveviewSubmitBound = 'true';
            element.addEventListener('submit', async (e) => {
                e.preventDefault();
                const formData = new FormData(e.target);
                const params = Object.fromEntries(formData.entries());

                // Phase 4: Check if event is from a component (walk up DOM tree)
                let currentElement = e.target;
                while (currentElement && currentElement !== document.body) {
                    if (currentElement.dataset.componentId) {
                        params.component_id = currentElement.dataset.componentId;
                        break;
                    }
                    currentElement = currentElement.parentElement;
                }

                // Pass target element for optimistic updates (Phase 3)
                params._targetElement = e.target;

                await handleEvent(submitHandler, params);
                e.target.reset();
            });
        }

        // Helper: Extract field name from element attributes
        // Priority: data-field (explicit) > name (standard) > id (fallback)
        function getFieldName(element) {
            if (element.dataset.field) {
                return element.dataset.field;
            }
            if (element.name) {
                return element.name;
            }
            if (element.id) {
                // Strip common prefixes like 'id_' (Django convention)
                return element.id.replace(/^id_/, '');
            }
            return null;
        }

        // Handle @change events
        const changeHandler = element.getAttribute('@change');
        if (changeHandler && !element.dataset.liveviewChangeBound) {
            element.dataset.liveviewChangeBound = 'true';
            element.addEventListener('change', async (e) => {
                const fieldName = getFieldName(e.target);
                const params = {
                    value: e.target.type === 'checkbox' ? e.target.checked : e.target.value,
                    field: fieldName
                };

                // Phase 4: Check if event is from a component (walk up DOM tree)
                let currentElement = e.target;
                while (currentElement && currentElement !== document.body) {
                    if (currentElement.dataset.componentId) {
                        params.component_id = currentElement.dataset.componentId;
                        break;
                    }
                    currentElement = currentElement.parentElement;
                }

                await handleEvent(changeHandler, params);
            });
        }

        // Handle @input events (with smart debouncing/throttling)
        const inputHandler = element.getAttribute('@input');
        if (inputHandler && !element.dataset.liveviewInputBound) {
            element.dataset.liveviewInputBound = 'true';

            // Determine rate limit strategy
            const inputType = element.type || element.tagName.toLowerCase();
            const rateLimit = DEFAULT_RATE_LIMITS[inputType] || { type: 'debounce', ms: 300 };

            // Check for explicit overrides
            if (element.hasAttribute('data-debounce')) {
                rateLimit.type = 'debounce';
                rateLimit.ms = parseInt(element.getAttribute('data-debounce'));
            } else if (element.hasAttribute('data-throttle')) {
                rateLimit.type = 'throttle';
                rateLimit.ms = parseInt(element.getAttribute('data-throttle'));
            }

            const handler = async (e) => {
                const fieldName = getFieldName(e.target);
                const params = {
                    value: e.target.value,
                    field: fieldName
                };

                // Phase 4: Check if event is from a component (walk up DOM tree)
                let currentElement = e.target;
                while (currentElement && currentElement !== document.body) {
                    if (currentElement.dataset.componentId) {
                        params.component_id = currentElement.dataset.componentId;
                        break;
                    }
                    currentElement = currentElement.parentElement;
                }

                await handleEvent(inputHandler, params);
            };

            // Apply rate limiting wrapper
            let wrappedHandler;
            if (rateLimit.type === 'throttle') {
                wrappedHandler = throttle(handler, rateLimit.ms);
            } else {
                wrappedHandler = debounce(handler, rateLimit.ms);
            }

            element.addEventListener('input', wrappedHandler);
        }

        // Handle @blur events
        const blurHandler = element.getAttribute('@blur');
        if (blurHandler && !element.dataset.liveviewBlurBound) {
            element.dataset.liveviewBlurBound = 'true';
            element.addEventListener('blur', async (e) => {
                const fieldName = getFieldName(e.target);
                const params = {
                    value: e.target.value,
                    field: fieldName
                };

                // Phase 4: Check if event is from a component (walk up DOM tree)
                let currentElement = e.target;
                while (currentElement && currentElement !== document.body) {
                    if (currentElement.dataset.componentId) {
                        params.component_id = currentElement.dataset.componentId;
                        break;
                    }
                    currentElement = currentElement.parentElement;
                }

                await handleEvent(blurHandler, params);
            });
        }

        // Handle @focus events
        const focusHandler = element.getAttribute('@focus');
        if (focusHandler && !element.dataset.liveviewFocusBound) {
            element.dataset.liveviewFocusBound = 'true';
            element.addEventListener('focus', async (e) => {
                const fieldName = getFieldName(e.target);
                const params = {
                    value: e.target.value,
                    field: fieldName
                };

                // Phase 4: Check if event is from a component (walk up DOM tree)
                let currentElement = e.target;
                while (currentElement && currentElement !== document.body) {
                    if (currentElement.dataset.componentId) {
                        params.component_id = currentElement.dataset.componentId;
                        break;
                    }
                    currentElement = currentElement.parentElement;
                }

                await handleEvent(focusHandler, params);
            });
        }

        // Handle @keydown / @keyup events
        ['keydown', 'keyup'].forEach(eventType => {
            const keyHandler = element.getAttribute(`@${eventType}`);
            if (keyHandler && !element.dataset[`liveview${eventType}Bound`]) {
                element.dataset[`liveview${eventType}Bound`] = 'true';
                element.addEventListener(eventType, async (e) => {
                    // Check for key modifiers (e.g. @keydown.enter)
                    const modifiers = keyHandler.split('.');
                    const handlerName = modifiers[0];
                    const requiredKey = modifiers.length > 1 ? modifiers[1] : null;

                    if (requiredKey) {
                        if (requiredKey === 'enter' && e.key !== 'Enter') return;
                        if (requiredKey === 'escape' && e.key !== 'Escape') return;
                        if (requiredKey === 'space' && e.key !== ' ') return;
                        // Add more key mappings as needed
                    }

                    const fieldName = getFieldName(e.target);
                    const params = {
                        key: e.key,
                        code: e.code,
                        value: e.target.value,
                        field: fieldName
                    };

                    // Phase 4: Check if event is from a component (walk up DOM tree)
                    let currentElement = e.target;
                    while (currentElement && currentElement !== document.body) {
                        if (currentElement.dataset.componentId) {
                            params.component_id = currentElement.dataset.componentId;
                            break;
                        }
                        currentElement = currentElement.parentElement;
                    }

                    await handleEvent(handlerName, params);
                });
            }
        });
    });
}

// Helper: Debounce function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Helper: Throttle function
function throttle(func, limit) {
    let inThrottle;
    return function (...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    }
}

// Helper: Get LiveView root element
function getLiveViewRoot() {
    return document.querySelector('[data-liveview-root]') || document.body;
}

// Helper: Clear optimistic state
function clearOptimisticState(eventName) {
    if (eventName && optimisticUpdates.has(eventName)) {
        const { element, originalState } = optimisticUpdates.get(eventName);
        // Restore original state if needed (e.g. on error)
        // For now, we just clear the tracking
        optimisticUpdates.delete(eventName);
    }
}

// Global Loading Manager (Phase 5)
const globalLoadingManager = {
    startLoading(eventName, triggerElement) {
        if (triggerElement) {
            triggerElement.classList.add('djust-loading');
            triggerElement.disabled = true;
        }
        document.body.classList.add('djust-global-loading');
    },

    stopLoading(eventName, triggerElement) {
        if (triggerElement) {
            triggerElement.classList.remove('djust-loading');
            triggerElement.disabled = false;
        }
        document.body.classList.remove('djust-global-loading');
    }
};

// Main Event Handler
async function handleEvent(eventName, params = {}) {
    console.log(`[LiveView] Handling event: ${eventName}`, params);

    // Start loading state
    const triggerElement = params._targetElement;
    globalLoadingManager.startLoading(eventName, triggerElement);

    // Try WebSocket first
    if (liveViewWS && liveViewWS.sendEvent(eventName, params, triggerElement)) {
        return;
    }

    // Fallback to HTTP
    console.log('[LiveView] WebSocket unavailable, falling back to HTTP');

    try {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
        const response = await fetch(window.location.href, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Djust-Event': eventName
            },
            body: JSON.stringify(params)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        handleServerResponse(data, eventName, triggerElement);

    } catch (error) {
        console.error('[LiveView] HTTP fallback failed:', error);
        globalLoadingManager.stopLoading(eventName, triggerElement);
    }
}

// === VDOM Patch Application ===

function getNodeByPath(path) {
    let node = getLiveViewRoot();

    if (path.length === 0) {
        return node;
    }

    for (let i = 0; i < path.length; i++) {
        const index = path[i];
        const children = Array.from(node.childNodes).filter(child => {
            if (child.nodeType === Node.ELEMENT_NODE) return true;
            if (child.nodeType === Node.TEXT_NODE) {
                return child.textContent.trim().length > 0;
            }
            return false;
        });

        if (index >= children.length) {
            console.warn(`[LiveView] Path traversal failed at index ${index}, only ${children.length} children`);
            return null;
        }

        node = children[index];
    }

    return node;
}

function createNodeFromVNode(vnode) {
    if (vnode.tag === '#text') {
        return document.createTextNode(vnode.text || '');
    }

    const elem = document.createElement(vnode.tag);

    if (vnode.attrs) {
        for (const [key, value] of Object.entries(vnode.attrs)) {
            if (key.startsWith('@')) {
                const eventName = key.substring(1);
                elem.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    const params = {};
                    Array.from(elem.attributes).forEach(attr => {
                        if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                            const key = attr.name.substring(5).replace(/-/g, '_');
                            params[key] = attr.value;
                        }
                    });

                    let currentElement = elem;
                    while (currentElement && currentElement !== document.body) {
                        if (currentElement.dataset.componentId) {
                            params.component_id = currentElement.dataset.componentId;
                            break;
                        }
                        currentElement = currentElement.parentElement;
                    }

                    handleEvent(value, params);
                });
            } else {
                if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                    elem.value = value;
                }
                elem.setAttribute(key, value);
            }
        }
    }

    if (vnode.children) {
        for (const child of vnode.children) {
            elem.appendChild(createNodeFromVNode(child));
        }
    }

    return elem;
}

function applyPatches(patches) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Sort patches to ensure RemoveChild operations are applied in descending order
    patches.sort((a, b) => {
        if (a.type === 'RemoveChild' && b.type === 'RemoveChild') {
            const pathA = JSON.stringify(a.path);
            const pathB = JSON.stringify(b.path);
            if (pathA === pathB) {
                return b.index - a.index;
            }
        }
        return 0;
    });

    let failedCount = 0;
    let successCount = 0;

    for (const patch of patches) {
        const node = getNodeByPath(patch.path);
        if (!node) {
            failedCount++;
            console.warn(`[LiveView] Failed to find node at path:`, patch.path);
            continue;
        }

        successCount++;

        try {
            if (patch.type === 'Replace') {
                const newNode = createNodeFromVNode(patch.node);
                node.parentNode.replaceChild(newNode, node);
            } else if (patch.type === 'SetText') {
                node.textContent = patch.text;
            } else if (patch.type === 'SetAttr') {
                if (patch.key === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                    if (document.activeElement !== node) {
                        node.value = patch.value;
                    }
                    node.setAttribute(patch.key, patch.value);
                } else {
                    node.setAttribute(patch.key, patch.value);
                }
            } else if (patch.type === 'RemoveAttr') {
                node.removeAttribute(patch.key);
            } else if (patch.type === 'InsertChild') {
                const newChild = createNodeFromVNode(patch.node);
                const children = Array.from(node.childNodes).filter(child => {
                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                    if (child.nodeType === Node.TEXT_NODE) {
                        return child.textContent.trim().length > 0;
                    }
                    return false;
                });
                const refChild = children[patch.index];
                if (refChild) {
                    node.insertBefore(newChild, refChild);
                } else {
                    node.appendChild(newChild);
                }
            } else if (patch.type === 'RemoveChild') {
                const children = Array.from(node.childNodes).filter(child => {
                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                    if (child.nodeType === Node.TEXT_NODE) {
                        return child.textContent.trim().length > 0;
                    }
                    return false;
                });
                const child = children[patch.index];
                if (child) {
                    node.removeChild(child);
                }
            } else if (patch.type === 'MoveChild') {
                const children = Array.from(node.childNodes).filter(child => {
                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                    if (child.nodeType === Node.TEXT_NODE) {
                        return child.textContent.trim().length > 0;
                    }
                    return false;
                });
                const child = children[patch.from];
                if (child) {
                    const refChild = children[patch.to];
                    if (refChild) {
                        node.insertBefore(child, refChild);
                    } else {
                        node.appendChild(child);
                    }
                }
            }
        } catch (error) {
            failedCount++;
            console.error(`[LiveView] Error applying patch:`, patch, error);
        }
    }

    if (failedCount > 0) {
        console.error(`[LiveView] ${failedCount}/${patches.length} patches failed`);
        return false;
    }

    return true;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    console.log('[LiveView] Initializing...');

    // Initialize WebSocket
    liveViewWS = new LiveViewWebSocket();
    liveViewWS.connect();

    // Initialize React counters (if any)
    initReactCounters();

    // Bind initial events
    bindLiveViewEvents();

    // Initialize Draft Mode
    initDraftMode();

    // Start heartbeat
    liveViewWS.startHeartbeat();
});

// === Debug Panel Implementation ===

// === Debug Panel Implementation ===

class DjustDebugPanel {
    constructor() {
        this.panel = null;
        this.isVisible = false;
        this.activeTab = 'history';
        this.eventHistory = [];
        this.patchHistory = [];

        // Initialize from server-provided debug info
        const debugInfo = window.DJUST_DEBUG_INFO || {};
        this.handlers = debugInfo.handlers || {};
        this.variables = debugInfo.variables || {};

        this.maxHistory = 50; // Configurable limit

        this.init();
    }

    init() {
        // Create container
        this.container = document.createElement('div');
        this.container.id = 'djust-debug-root';
        document.body.appendChild(this.container);
        this.render();

        // Bind keyboard shortcut (Ctrl+Shift+D)
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'D') {
                this.toggle();
            }
        });
    }

    toggle() {
        this.isVisible = !this.isVisible;
        this.render();
    }

    setTab(tab) {
        this.activeTab = tab;
        this.render();
    }

    setLimit(limit) {
        this.maxHistory = parseInt(limit);
        // Trim existing history if needed
        if (this.eventHistory.length > this.maxHistory) {
            this.eventHistory = this.eventHistory.slice(this.eventHistory.length - this.maxHistory);
        }
        if (this.patchHistory.length > this.maxHistory) {
            this.patchHistory = this.patchHistory.slice(this.patchHistory.length - this.maxHistory);
        }
        this.render();
    }

    getErrorCount() {
        return this.eventHistory.filter(e => e.error).length;
    }

    exportData() {
        const data = {
            timestamp: new Date().toISOString(),
            events: this.eventHistory,
            patches: this.patchHistory,
            handlers: this.handlers,
            variables: this.variables
        };

        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `djust-debug-${new Date().toISOString()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    render() {
        const errorCount = this.getErrorCount();
        const errorBadge = errorCount > 0 ? `<span class="tab-badge error">${errorCount}</span>` : '';
        const handlerCount = Object.keys(this.handlers).length;

        const html = `
            <button class="djust-debug-button ${this.isVisible ? 'active' : ''}" onclick="window.djustDebugPanel.toggle()">
                🐞
            </button>

            <div class="djust-debug-panel ${this.isVisible ? 'visible' : ''}">
                <div class="djust-debug-sidebar">
                    <div class="djust-debug-header">
                        <h3>DJUST TOOLS</h3>
                    </div>
                    <div class="djust-debug-tabs">
                        <button class="djust-debug-tab ${this.activeTab === 'history' ? 'active' : ''}" onclick="window.djustDebugPanel.setTab('history')">
                            <span>Event History</span>
                            ${errorBadge}
                        </button>
                        <button class="djust-debug-tab ${this.activeTab === 'patches' ? 'active' : ''}" onclick="window.djustDebugPanel.setTab('patches')">
                            <span>VDOM Patches</span>
                            <span class="tab-badge">${this.patchHistory.length}</span>
                        </button>
                        <button class="djust-debug-tab ${this.activeTab === 'handlers' ? 'active' : ''}" onclick="window.djustDebugPanel.setTab('handlers')">
                            <span>Handlers</span>
                            <span class="tab-badge">${handlerCount}</span>
                        </button>
                        <button class="djust-debug-tab ${this.activeTab === 'variables' ? 'active' : ''}" onclick="window.djustDebugPanel.setTab('variables')">
                            <span>Variables</span>
                        </button>
                    </div>
                </div>

                <div class="djust-debug-content">
                    ${this.renderContent()}
                </div>

                <div class="djust-status-bar">
                    <div class="status-item">
                        <div class="status-dot connected"></div>
                        <span>Connected</span>
                    </div>
                    <div class="status-item">
                        <span>v${clientVdomVersion || '0.0.0'}</span>
                    </div>
                    <div style="flex:1"></div>
                    <div class="status-item">
                        <span>${navigator.userAgent.includes('Mac') ? 'Cmd+Shift+D' : 'Ctrl+Shift+D'} to toggle</span>
                    </div>
                </div>
            </div>
        `;

        this.container.innerHTML = html;
        this.panel = this.container.querySelector('.djust-debug-panel');
    }

    renderContent() {
        switch (this.activeTab) {
            case 'history':
                return this.renderHistory();
            case 'patches':
                return this.renderPatches();
            case 'handlers':
                return this.renderHandlers();
            case 'variables':
                return this.renderVariables(this.variables);
            default:
                return '';
        }
    }

    renderHistory() {
        let html = `
            <div class="djust-toolbar">
                <span class="toolbar-title">Event Log</span>
                <div class="toolbar-actions">
                    <span class="history-count">Last ${this.maxHistory}</span>
                    <button class="btn-xs" onclick="window.djustDebugPanel.exportData()">Export</button>
                    <button class="btn-xs" onclick="window.djustDebugPanel.clearHistory()">Clear</button>
                </div>
            </div>
            <div class="debug-list">
        `;

        if (this.eventHistory.length === 0) {
            return html + '<p class="empty">No events recorded yet</p></div>';
        }

        for (let i = 0; i < this.eventHistory.length; i++) {
            const entry = this.eventHistory[this.eventHistory.length - 1 - i];
            const eventId = `evt-${i}`;
            const isError = !!entry.error;

            // Format duration
            const durationText = entry.duration !== undefined
                ? `${entry.duration.toFixed(2)}ms`
                : 'N/A';

            html += `
                <div class="event-item ${isError ? 'error' : ''}" id="${eventId}">
                    <div class="event-header" onclick="window.djustDebugPanel.toggleItem('${eventId}')">
                        <span style="margin-right: 8px; font-size: 10px;">▶</span>
                        <span class="event-name">${entry.name}</span>
                        <span class="event-meta">${durationText} • ${new Date(entry.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="event-body" style="display: none;">
                        <div class="event-params">
                            <strong>Params:</strong>
                            <pre>${JSON.stringify(entry.params, null, 2)}</pre>
                        </div>
                        ${isError ? `<div class="event-error"><strong>Error:</strong> ${entry.error}</div>` : ''}
                    </div>
                </div>
            `;
        }
        return html + '</div>';
    }

    renderPatches() {
        let html = `
            <div class="djust-toolbar">
                <span class="toolbar-title">VDOM Patches</span>
                <div class="toolbar-actions">
                    <button class="btn-xs" onclick="window.djustDebugPanel.clearPatches()">Clear</button>
                </div>
            </div>
            <div class="debug-list">
        `;

        if (this.patchHistory.length === 0) {
            return html + '<p class="empty">No VDOM patches applied yet</p></div>';
        }

        for (let i = 0; i < this.patchHistory.length; i++) {
            const entry = this.patchHistory[this.patchHistory.length - 1 - i];
            const patchId = `patch-${i}`;

            // Format duration
            const durationText = entry.duration !== undefined
                ? `${entry.duration.toFixed(2)}ms`
                : 'N/A';

            html += `
                <div class="patch-item" id="${patchId}">
                    <div class="patch-header" onclick="window.djustDebugPanel.toggleItem('${patchId}')">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 10px;">▶</span>
                            <span class="patch-count">${entry.count} patch${entry.count !== 1 ? 'es' : ''}</span>
                        </div>
                        <span>${durationText} • ${new Date(entry.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="event-body" style="display: none;">
                        <pre>${JSON.stringify(entry.patches, null, 2)}</pre>
                    </div>
                </div>
            `;
        }
        return html + '</div>';
    }

    renderHandlers() {
        let html = `
            <div class="djust-toolbar">
                <span class="toolbar-title">Registered Handlers</span>
            </div>
            <div class="debug-list">
        `;

        const handlerNames = Object.keys(this.handlers);
        if (handlerNames.length === 0) {
            return html + '<p class="empty">No event handlers found in debug info</p></div>';
        }

        for (const [name, info] of Object.entries(this.handlers)) {
            const params = info.params || [];
            const decorators = Object.keys(info.decorators || {}).filter(d => d !== 'event_handler');

            html += `
                <div class="handler-item">
                    <div class="handler-name">${name}</div>
                    ${info.description ? `<div class="handler-desc">${info.description}</div>` : ''}

                    <div class="handler-signature">
                        <strong>Parameters:</strong>
                        ${params.length > 0 ? params.map(p => `
                            <div class="param">
                                <code>${p.name}</code>:
                                <span class="type">${p.type}</span>
                                ${!p.required ? ` = <span class="default">${p.default}</span>` : '<span class="required">*</span>'}
                            </div>
                        `).join('') : '<span class="empty">No parameters</span>'}
                        ${info.accepts_kwargs ? '<div class="param"><code>**kwargs</code></div>' : ''}
                    </div>

                    ${decorators.length > 0 ? `
                        <div class="handler-decorators">
                            <strong>Decorators:</strong> ${decorators.map(d => `<code>@${d}</code>`).join(', ')}
                        </div>
                    ` : ''}
                </div>
            `;
        }

        return html + '</div>';
    }

    renderVariables(variables) {
        let html = `
            <div class="djust-toolbar">
                <span class="toolbar-title">Variables</span>
            </div>
            <div class="debug-list">
        `;

        if (Object.keys(variables).length === 0) {
            return html + '<p class="empty">No public variables found</p></div>';
        }

        for (const [name, info] of Object.entries(variables)) {
            html += `
                <div class="variable-item">
                    <div class="variable-name">${name}</div>
                    <div class="variable-type">${info.type}</div>
                    <div class="variable-value">
                        Current: <code>${info.value}</code>
                    </div>
                </div>
            `;
        }
        return html + '</div>';
    }

    toggleItem(id) {
        const el = document.getElementById(id);
        if (el) {
            const body = el.querySelector('.event-body');
            const header = el.querySelector('.event-header, .patch-header');
            const chevron = header ? header.querySelector('span:first-child') : null;

            if (body) {
                const isExpanded = body.style.display !== 'none';
                body.style.display = isExpanded ? 'none' : 'block';

                // Rotate chevron
                if (chevron && (chevron.textContent === '▶' || chevron.textContent === '▼')) {
                    chevron.textContent = isExpanded ? '▶' : '▼';
                }
            }
        }
    }

    clearHistory() {
        this.eventHistory = [];
        this.render();
    }

    clearPatches() {
        this.patchHistory = [];
        this.render();
    }

    logEvent(eventName, params, result, duration) {
        this.eventHistory.push({
            timestamp: Date.now(),
            name: eventName,
            params: params,
            error: result && result.type === 'error' ? result.error : null,
            duration: duration
        });

        // Keep last N events
        if (this.eventHistory.length > this.maxHistory) {
            this.eventHistory.shift();
        }

        if (this.isVisible) {
            this.render();
        }
    }

    logPatches(patches, duration) {
        this.patchHistory.push({
            timestamp: Date.now(),
            count: patches.length,
            patches: patches,
            duration: duration
        });

        if (this.patchHistory.length > this.maxHistory) {
            this.patchHistory.shift();
        }

        if (this.isVisible) {
            this.render();
        }
    }
}

// Initialize global debug panel
if (typeof window.DJUST_DEBUG_INFO !== 'undefined') {
    window.djustDebugPanel = new DjustDebugPanel();

    // Hook into event sending to log events
    // Store original handleEvent function
    const originalHandleEvent = handleEvent;

    // Replace with wrapper that logs events with timing
    window.handleEvent = handleEvent = async function (eventName, params) {
        const startTime = performance.now();
        let result = null;
        let error = null;

        try {
            // Call original implementation
            result = await originalHandleEvent(eventName, params);
        } catch (e) {
            error = e;
            throw e;
        } finally {
            // Calculate duration
            const duration = performance.now() - startTime;

            // Log event to debug panel (skip internal/decorator events)
            if (window.djustDebugPanel && !params._skipDecorators) {
                window.djustDebugPanel.logEvent(
                    eventName,
                    params,
                    error ? { type: 'error', error: error.message } : result,
                    duration
                );
            }
        }

        return result;
    };

    // Hook into patch application to log patches
    // Ensure applyPatches exists before hooking
    if (typeof applyPatches !== 'undefined' || typeof window.applyPatches !== 'undefined') {
        const originalApplyPatches = window.applyPatches || applyPatches;

        // Replace with wrapper that logs patches with timing
        window.applyPatches = applyPatches = function (patches) {
            const startTime = performance.now();

            // Call original implementation
            const result = originalApplyPatches(patches);

            // Calculate duration
            const duration = performance.now() - startTime;

            // Log patches to debug panel
            if (window.djustDebugPanel && patches && patches.length > 0) {
                window.djustDebugPanel.logPatches(patches, duration);
            }

            return result;
        };
    } else {
        console.warn('[djust] applyPatches not found - patch logging disabled');
    }
}
