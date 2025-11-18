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

    sendEvent(eventName, params = {}) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        if (!this.viewMounted) {
            console.warn('[LiveView] View not mounted. Event ignored:', eventName);
            return false;
        }

        // Phase 5: Track event name for loading state
        this.lastEventName = eventName;

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
        } catch(e) {}

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
                const params = {};

                // For checkboxes/radios, send the checked state
                if (e.target.type === 'checkbox' || e.target.type === 'radio') {
                    params.checked = e.target.checked;
                    params.value = e.target.checked;
                } else {
                    // For other inputs, send the value
                    params.value = e.target.value;
                }

                // Extract all data-* attributes
                Array.from(e.target.attributes).forEach(attr => {
                    if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                        // Convert data-foo-bar to foo_bar
                        const key = attr.name.substring(5).replace(/-/g, '_');
                        params[key] = attr.value;
                    }
                });

                // Auto-extract field name from element attributes
                const fieldName = getFieldName(e.target);
                if (fieldName) {
                    params.field_name = fieldName;
                    // Also set the parameter with the field name for cache key generation
                    // This allows @cache(key_params=["query"]) to work correctly
                    params[fieldName] = params.value;
                }

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

                await handleEvent(changeHandler, params);
            });
        }

        // Handle @input events (real-time typing)
        const inputHandler = element.getAttribute('@input');
        if (inputHandler && !element.dataset.liveviewInputBound) {
            element.dataset.liveviewInputBound = 'true';

            // Create the base handler function
            const baseHandler = async (e) => {
                const params = {};

                // For checkboxes, send the checked state
                if (e.target.type === 'checkbox') {
                    params.value = e.target.checked;
                } else {
                    // For other inputs, send the value
                    params.value = e.target.value;
                }

                // Extract all data-* attributes
                Array.from(e.target.attributes).forEach(attr => {
                    if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                        // Convert data-foo-bar to foo_bar
                        const key = attr.name.substring(5).replace(/-/g, '_');
                        params[key] = attr.value;
                    }
                });

                // Auto-extract field name from element attributes
                const fieldName = getFieldName(e.target);
                if (fieldName) {
                    params.field_name = fieldName;
                    // Also set the parameter with the field name for cache key generation
                    // This allows @cache(key_params=["query"]) to work correctly
                    params[fieldName] = params.value;
                }

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

                await handleEvent(inputHandler, params);
            };

            // Determine rate limiting strategy (priority order):
            // 1. Explicit data-throttle or data-debounce attribute
            // 2. Smart default based on input type
            // 3. No rate limiting (immediate)
            let rateLimitedHandler = baseHandler;

            if (element.dataset.throttle) {
                // Explicit throttle attribute
                const ms = parseInt(element.dataset.throttle);
                rateLimitedHandler = throttle(baseHandler, ms);
            } else if (element.dataset.debounce) {
                // Explicit debounce attribute
                const ms = parseInt(element.dataset.debounce);
                rateLimitedHandler = debounce(baseHandler, ms);
            } else {
                // Check for smart defaults
                const inputType = element.tagName.toLowerCase() === 'textarea'
                    ? 'textarea'
                    : (element.type || 'text');
                const defaultLimit = DEFAULT_RATE_LIMITS[inputType];

                if (defaultLimit) {
                    if (defaultLimit.type === 'throttle') {
                        rateLimitedHandler = throttle(baseHandler, defaultLimit.ms);
                    } else if (defaultLimit.type === 'debounce') {
                        rateLimitedHandler = debounce(baseHandler, defaultLimit.ms);
                    }
                }
            }

            // Create optimistic UI handler for immediate feedback
            // This runs immediately (not rate-limited) to update display
            const optimisticHandler = (e) => {
                // Optimistic UI update for high-frequency inputs
                if (e.target.type === 'range' || e.target.type === 'number' || e.target.type === 'color') {
                    updateDisplayValue(e.target);
                }

                // Then call rate-limited server update
                rateLimitedHandler(e);
            };

            element.addEventListener('input', optimisticHandler);
        }

        // Phase 5: Register elements with @loading attributes
        // This enables Phoenix LiveView-style loading indicators (disable, show, hide, class)
        const hasLoadingAttr = Array.from(element.attributes).some(attr =>
            attr.name.startsWith('@loading.')
        );

        if (hasLoadingAttr && !element.dataset.loadingRegistered) {
            element.dataset.loadingRegistered = 'true';

            // ============================================================
            // Event Name Discovery: Two Strategies
            // ============================================================
            // We need to associate this @loading element with an event name
            // so we know when to apply/remove loading state.
            //
            // Strategy 1: Element has its own event handler
            //   Example: <button @click="save" @loading.disable>
            //   → Register for "save" event
            //
            // Strategy 2: Sibling has event handler (for spinners/indicators)
            //   Example:
            //     <div class="d-flex">
            //       <button @click="save">Save</button>
            //       <div @loading.show>Spinner</div>
            //     </div>
            //   → Spinner registers for "save" event from sibling button
            //
            // This allows flexible UI patterns without requiring the event
            // handler to be on the loading element itself.
            // ============================================================

            let eventName = element.getAttribute('@click') ||
                           element.getAttribute('@submit') ||
                           element.getAttribute('@change') ||
                           element.getAttribute('@input');

            // Strategy 2: Sibling event detection
            if (!eventName && element.parentElement) {
                const siblings = Array.from(element.parentElement.children);
                for (const sibling of siblings) {
                    const siblingEvent = sibling.getAttribute('@click') ||
                                       sibling.getAttribute('@submit') ||
                                       sibling.getAttribute('@change') ||
                                       sibling.getAttribute('@input');
                    if (siblingEvent) {
                        eventName = siblingEvent;
                        break; // Use first event found in siblings
                    }
                }
            }

            if (eventName) {
                globalLoadingManager.register(element, eventName);

                if (globalThis.djustDebug) {
                    console.log(`[Loading] Registered element for "${eventName}"`, element);
                }
            }
        }
    });
}

// DOM patching utilities
// Find the root LiveView container
function getLiveViewRoot() {
    // First, look for explicit LiveView container (used with template inheritance)
    const liveviewContainer = document.querySelector('[data-liveview-root]');
    if (liveviewContainer) {
        return liveviewContainer;
    }

    // Fallback: first content element, skipping <link>, <style>, <script>
    const NON_CONTENT_TAGS = ['LINK', 'STYLE', 'SCRIPT'];
    for (const child of document.body.childNodes) {
        if (child.nodeType === Node.ELEMENT_NODE && !NON_CONTENT_TAGS.includes(child.tagName)) {
            return child;
        }
    }
    return document.body;
}

function getNodeByPath(path) {
    // The Rust VDOM root matches getLiveViewRoot() (first content element)
    // Path [0] = first child of root, [0,0] = first child of that, etc.
    let node = getLiveViewRoot();

    // DEBUG: Log root info
    if (path.length > 0) {
        console.log(`[DEBUG-ROOT] Starting traversal from <${node.tagName}>, path length=${path.length}, path=${JSON.stringify(path)}`);
    }

    // Empty path means the root itself
    if (path.length === 0) {
        return node;
    }

    // Traverse the path directly - no adjustment needed since roots are aligned
    for (let i = 0; i < path.length; i++) {
        const index = path[i];

        // Get children - Rust DOES filter whitespace-only text nodes
        // The parser.rs code filters with: if !text.trim().is_empty()
        // So we should do the same here
        const children = Array.from(node.childNodes).filter(child => {
            // Keep element nodes
            if (child.nodeType === Node.ELEMENT_NODE) return true;
            // Keep text nodes that have non-whitespace content
            if (child.nodeType === Node.TEXT_NODE) {
                return child.textContent.trim().length > 0;
            }
            return false;
        });

        // DEBUG: Log at first few iterations
        if (i < 3) {
            console.log(`[DEBUG-PATH] Step ${i}: index=${index}, children=${children.length}, node=<${node.tagName || node.nodeName}>`);
            if (children.length === 0) {
                console.log(`[DEBUG-PATH] ZERO CHILDREN! node.childNodes.length=${node.childNodes.length}`);
                for (let j = 0; j < node.childNodes.length; j++) {
                    const child = node.childNodes[j];
                    console.log(`  Raw child[${j}]: type=${child.nodeType}, tag=${child.tagName || child.nodeName}`);
                }
            }
        }

        // DEBUG: Log when accessing the form's parent (card-body)
        if (i === 4 && path[0] === 0 && path[1] === 0 && path[2] === 0 && path[3] === 1 && path[4] === 2 && path.length > 5) {
            console.log(`[DEBUG] About to descend into child[${index}] of card-body`);
            console.log(`[DEBUG] card-body has ${children.length} children, about to access index ${index}`);
            if (index < children.length && children[index].nodeType === Node.ELEMENT_NODE) {
                const target = children[index];
                console.log(`[DEBUG] Target is <${target.tagName.toLowerCase()}>`);
                // Now show what THIS element's children are
                const targetChildren = Array.from(target.childNodes).filter(child => {
                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                    if (child.nodeType === Node.TEXT_NODE) {
                        return child.textContent.trim().length > 0;
                    }
                    return false;
                });
                console.log(`[DEBUG] This element has ${targetChildren.length} filtered children`);
                targetChildren.forEach((child, idx) => {
                    if (child.nodeType === Node.ELEMENT_NODE) {
                        console.log(`  [${idx}] <${child.tagName.toLowerCase()} class="${child.className || ''}">`);
                    }
                });
            }
        }

        if (index >= children.length) {
            console.warn(`[LiveView] Index ${index} out of bounds, only ${children.length} children at path`, path.slice(0, i+1));
            return null;
        }

        node = children[index];
    }
    return node;
}

function createNodeFromVNode(vnode) {
    // VNode structure from Rust: { tag, text?, attrs, children, key? }
    // Text nodes have tag === "#text" and a text field
    if (vnode.tag === '#text') {
        return document.createTextNode(vnode.text || '');
    }

    // Element nodes
    const elem = document.createElement(vnode.tag);

    // Set attributes and handle events
    if (vnode.attrs) {
        for (const [key, value] of Object.entries(vnode.attrs)) {
            // Handle LiveView event attributes (@click, @change, etc.)
            if (key.startsWith('@')) {
                const eventName = key.substring(1);
                elem.addEventListener(eventName, (e) => {
                    e.preventDefault();

                    // Extract data-* attributes just like bindLiveViewEvents() does
                    const params = {};
                    Array.from(elem.attributes).forEach(attr => {
                        if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                            const key = attr.name.substring(5).replace(/-/g, '_');
                            params[key] = attr.value;
                        }
                    });

                    // Phase 4: Check if event is from a component (walk up DOM tree)
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
                // Special handling for input value property
                if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                    elem.value = value;
                }
                // Regular HTML attributes
                elem.setAttribute(key, value);
            }
        }
    }

    // Add children recursively
    if (vnode.children && vnode.children.length > 0) {
        for (const child of vnode.children) {
            const childNode = createNodeFromVNode(child);
            if (childNode) {
                elem.appendChild(childNode);
            }
        }
    }

    return elem;
}

// Debug helper: log DOM structure
function debugNode(node, prefix = '') {
    const children = Array.from(node.childNodes).filter(child => {
        if (child.nodeType === Node.ELEMENT_NODE) return true;
        if (child.nodeType === Node.TEXT_NODE) {
            return child.textContent.trim().length > 0;
        }
        return false;
    });

    return {
        tag: node.tagName || 'TEXT',
        text: node.nodeType === Node.TEXT_NODE ? node.textContent.substring(0, 20) : null,
        childCount: children.length,
        children: children.slice(0, 3).map((c, i) => `[${i}]: ${c.tagName || 'TEXT'}`)
    };
}

function applyPatches(patches) {
    // patches is already a JavaScript array (parsed by response.json())
    if (window.DJUST_DEBUG_VDOM) {
        console.log('[LiveView] Applying', patches.length, 'patches');
    }

    // Sort patches to ensure RemoveChild operations are applied in descending index order
    // within the same path. This prevents index invalidation when removing multiple children.
    patches.sort((a, b) => {
        // RemoveChild patches should come before other types at the same path
        if (a.type === 'RemoveChild' && b.type === 'RemoveChild') {
            // Same path? Sort by index descending (higher indices first)
            const pathA = JSON.stringify(a.path);
            const pathB = JSON.stringify(b.path);
            if (pathA === pathB) {
                return b.index - a.index; // Descending order
            }
        }
        return 0; // Maintain relative order for other patches
    });

    // Debug: log patch types for small patch sets
    if (window.DJUST_DEBUG_VDOM && patches.length > 0 && patches.length <= 20) {
        console.log('[LiveView] Patch count:', patches.length);
        for (let i = 0; i < Math.min(5, patches.length); i++) {
            console.log('  Patch', i, ':', patches[i].type, 'at', patches[i].path);
        }
    }

    let failedCount = 0;
    let successCount = 0;
    for (const patch of patches) {
        const node = getNodeByPath(patch.path);
        if (!node) {
            failedCount++;
            if (failedCount <= 3) {
                // Log first 3 failures in detail
                const patchType = Object.keys(patch).filter(k => k !== 'path')[0];
                console.warn(`[LiveView] Failed patch #${failedCount} at path:`, patch.path);
                console.warn('Patch type:', patchType, patch[patchType]);

                // Try to traverse as far as we can to see where it breaks
                let debugNode = getLiveViewRoot();

                for (let i = 0; i < patch.path.length; i++) {
                    const children = Array.from(debugNode.childNodes).filter(child => {
                        if (child.nodeType === Node.ELEMENT_NODE) return true;
                        if (child.nodeType === Node.TEXT_NODE) return child.textContent.trim().length > 0;
                        return false;
                    });

                    console.warn(`  Path[${i}] = ${patch.path[i]}, available children:`, children.length,
                        children.map((c,idx) => `[${idx}]=${c.tagName||'TEXT'}`).join(', '));

                    if (patch.path[i] >= children.length) {
                        console.warn(`  FAILED: Index ${patch.path[i]} out of bounds (only ${children.length} children)`);
                        console.warn(`  Current node HTML (first 500 chars):`);
                        console.warn(debugNode.outerHTML ? debugNode.outerHTML.substring(0, 500) : 'N/A');
                        break;
                    }

                    debugNode = children[patch.path[i]];

                    // After moving to the next node, show what it contains
                    if (i === 3) { // At the critical failing level [3, 0, 2, 1]
                        console.warn(`  >>> At critical path[3], showing node details:`);
                        console.warn(`      Tag: ${debugNode.tagName}, ID: ${debugNode.id || 'none'}, Class: ${debugNode.className || 'none'}`);
                        console.warn(`      HTML (first 800 chars): ${debugNode.outerHTML ? debugNode.outerHTML.substring(0, 800) : 'N/A'}`);
                    }
                }
            }
            continue;
        }

        successCount++;

        if (patch.type === 'Replace') {
            const newNode = createNodeFromVNode(patch.node);
            node.parentNode.replaceChild(newNode, node);
        } else if (patch.type === 'SetText') {
            node.textContent = patch.text;
        } else if (patch.type === 'SetAttr') {
            // Special handling for input value to preserve user input
            if (patch.key === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                // Only update if the element is not currently focused (user is typing)
                if (document.activeElement !== node) {
                    node.value = patch.value;
                }
                // Always update the attribute for consistency
                node.setAttribute(patch.key, patch.value);
            } else {
                node.setAttribute(patch.key, patch.value);
            }
        } else if (patch.type === 'RemoveAttr') {
            if (window.DJUST_DEBUG_VDOM) {
                console.log(`[LiveView] Patch RemoveAttr '${patch.key}' at path (${patch.path.length}) [${patch.path.join(', ')}]`);
                console.log(`[LiveView]   Target element: <${node.tagName || 'unknown'}> with ${(node.children || []).length} children`);
                if (node.id) console.log(`[LiveView]     id="${node.id}"`);
                if (node.className) console.log(`[LiveView]     class="${node.className}"`);
            }
            node.removeAttribute(patch.key);
        } else if (patch.type === 'InsertChild') {
            const newChild = createNodeFromVNode(patch.node);
            // Use filtered children to match path traversal
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
            // Use filtered children to match path traversal
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
            // Use filtered children to match path traversal
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
    }

    if (window.DJUST_DEBUG_VDOM) {
        console.log(`[LiveView] Patch summary: ${successCount} succeeded, ${failedCount} failed`);
    }

    // If any patches failed, return false to trigger page reload
    if (failedCount > 0) {
        console.error(`[LiveView] ${failedCount} patches failed, page will reload`);
        return false;
    }

    return true; // All patches applied successfully
}

// Throttle utility: Limits function calls to at most once per interval
// Guarantees the last call will always execute (trailing edge)
function throttle(func, interval) {
    let lastCall = 0;
    let timeout = null;

    return function(...args) {
        const now = Date.now();
        const timeSinceLastCall = now - lastCall;

        if (timeSinceLastCall >= interval) {
            // Enough time has passed, execute immediately
            lastCall = now;
            func.apply(this, args);
        } else {
            // Too soon, schedule for later (ensures last value is sent)
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                lastCall = Date.now();
                func.apply(this, args);
            }, interval - timeSinceLastCall);
        }
    };
}

// Debounce utility: Delays function call until after wait period of inactivity
// Resets timer on each call (only executes after user stops)
function debounce(func, wait) {
    let timeout = null;

    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => {
            func.apply(this, args);
        }, wait);
    };
}

// Optimistic UI update: Immediately update display elements
// This provides instant visual feedback before server responds
function updateDisplayValue(inputElement) {
    const value = inputElement.value;
    let updated = false;

    // Strategy 1: Check for explicit data-display-target attribute
    const targetId = inputElement.dataset.displayTarget;
    if (targetId) {
        const targetElement = document.getElementById(targetId);
        if (targetElement) {
            targetElement.textContent = value;
            updated = true;
        }
    }

    // Strategy 2: Find nearby <output> element (semantic HTML)
    const outputElement = inputElement.parentElement?.querySelector('output');
    if (outputElement) {
        outputElement.textContent = value;
        updated = true;
    }

    // Strategy 3: Find badge in sibling label (Range component pattern)
    // Range structure: <label>Volume <span class="badge">50</span></label><input>
    const parentDiv = inputElement.parentElement;
    if (parentDiv) {
        // Find label sibling
        const label = parentDiv.querySelector('label');
        if (label) {
            const badge = label.querySelector('.badge');
            if (badge) {
                badge.textContent = value;
                updated = true;
            }
        }
    }

    // Strategy 4: Find nearby <strong> element (percentage display)
    // Look in parent and grandparent for <strong> elements
    const searchContainers = [
        inputElement.parentElement,
        inputElement.parentElement?.parentElement
    ];

    for (const container of searchContainers) {
        if (container) {
            const strongElements = container.querySelectorAll('strong');
            strongElements.forEach(strong => {
                // Check if it contains a number (likely our display)
                if (strong.textContent.match(/\d+/)) {
                    // Update with or without % suffix based on original content
                    if (strong.textContent.includes('%')) {
                        strong.textContent = `${value}%`;
                    } else {
                        strong.textContent = value;
                    }
                    updated = true;
                }
            });
        }
    }

    // Strategy 5: Find sibling <span> or <div> with .value or .display class
    if (!updated && parentDiv) {
        const displayElement =
            parentDiv.querySelector('.value') ||
            parentDiv.querySelector('.display') ||
            parentDiv.querySelector('span[class*="value"]');

        if (displayElement) {
            displayElement.textContent = value;
            updated = true;
        }
    }
}

// ============================================================================
// Decorator Functions (Phase 2: Debounce/Throttle, Phase 3: Optimistic)
// ============================================================================

/**
 * Debounce an event - delay until user stops triggering events
 */
function debounceEvent(eventName, eventData, config) {
    const { wait, max_wait } = config;
    const now = Date.now();

    // Get or create state
    let state = debounceTimers.get(eventName);
    if (!state) {
        state = { timerId: null, firstCallTime: now };
        debounceTimers.set(eventName, state);
    }

    // Clear existing timer
    if (state.timerId) {
        clearTimeout(state.timerId);
    }

    // Check if we've exceeded max_wait
    if (max_wait && (now - state.firstCallTime) >= (max_wait * 1000)) {
        // Force execution - max wait exceeded
        sendEventImmediate(eventName, eventData);
        debounceTimers.delete(eventName);
        if (window.djustDebug) {
            console.log(`[LiveView:debounce] Force executing ${eventName} (max_wait exceeded)`);
        }
        return;
    }

    // Set new timer
    state.timerId = setTimeout(() => {
        sendEventImmediate(eventName, eventData);
        debounceTimers.delete(eventName);
        if (window.djustDebug) {
            console.log(`[LiveView:debounce] Executing ${eventName} after ${wait}s wait`);
        }
    }, wait * 1000);

    if (window.djustDebug) {
        console.log(`[LiveView:debounce] Debouncing ${eventName} (wait: ${wait}s, max_wait: ${max_wait || 'none'})`);
    }
}

/**
 * Throttle an event - limit execution frequency
 */
function throttleEvent(eventName, eventData, config) {
    const { interval, leading, trailing } = config;
    const now = Date.now();

    if (!throttleState.has(eventName)) {
        // First call - execute immediately if leading=true
        if (leading) {
            sendEventImmediate(eventName, eventData);
            if (window.djustDebug) {
                console.log(`[LiveView:throttle] Executing ${eventName} (leading edge)`);
            }
        }

        // Set up state
        const state = {
            lastCall: leading ? now : 0,
            timeoutId: null,
            pendingData: null
        };

        throttleState.set(eventName, state);

        // Schedule trailing call if needed
        if (trailing && !leading) {
            state.pendingData = eventData;
            state.timeoutId = setTimeout(() => {
                sendEventImmediate(eventName, state.pendingData);
                throttleState.delete(eventName);
                if (window.djustDebug) {
                    console.log(`[LiveView:throttle] Executing ${eventName} (trailing edge - no leading)`);
                }
            }, interval * 1000);
        }

        return;
    }

    const state = throttleState.get(eventName);
    const elapsed = now - state.lastCall;

    if (elapsed >= (interval * 1000)) {
        // Enough time has passed - execute now
        sendEventImmediate(eventName, eventData);
        state.lastCall = now;
        state.pendingData = null;

        // Clear any pending trailing call
        if (state.timeoutId) {
            clearTimeout(state.timeoutId);
            state.timeoutId = null;
        }

        if (window.djustDebug) {
            console.log(`[LiveView:throttle] Executing ${eventName} (interval elapsed: ${elapsed}ms)`);
        }
    } else if (trailing) {
        // Update pending data and reschedule trailing call
        state.pendingData = eventData;

        if (state.timeoutId) {
            clearTimeout(state.timeoutId);
        }

        const remaining = (interval * 1000) - elapsed;
        state.timeoutId = setTimeout(() => {
            if (state.pendingData) {
                sendEventImmediate(eventName, state.pendingData);
                if (window.djustDebug) {
                    console.log(`[LiveView:throttle] Executing ${eventName} (trailing edge)`);
                }
            }
            throttleState.delete(eventName);
        }, remaining);

        if (window.djustDebug) {
            console.log(`[LiveView:throttle] Throttled ${eventName} (${remaining}ms until trailing)`);
        }
    } else {
        if (window.djustDebug) {
            console.log(`[LiveView:throttle] Dropped ${eventName} (within interval, no trailing)`);
        }
    }
}

/**
 * Send event immediately (bypassing decorators)
 */
async function sendEventImmediate(eventName, params) {
    // This function delegates to the main handleEvent flow
    // Called after debounce/throttle timers fire

    // Check for cache decorator before sending
    // Cache should work even with debounce/throttle
    const metadata = window.handlerMetadata?.[eventName];
    if (metadata?.cache) {
        // Apply cache - it will handle sending if needed
        await cacheEvent(eventName, params, metadata.cache, async (evName, evParams) => {
            evParams._skipDecorators = true;
            await handleEvent(evName, evParams);
        });
        return;
    }

    // No cache - send immediately
    params._skipDecorators = true;
    await handleEvent(eventName, params);
}

/**
 * Apply optimistic DOM updates before server validation
 */
function applyOptimisticUpdate(eventName, params, targetElement) {
    if (!targetElement) {
        return;
    }

    // Save original state before update
    saveOptimisticState(eventName, targetElement);

    // Apply update based on element type
    if (targetElement.type === 'checkbox' || targetElement.type === 'radio') {
        optimisticToggle(targetElement, params);
    } else if (targetElement.tagName === 'INPUT' || targetElement.tagName === 'TEXTAREA') {
        optimisticInputUpdate(targetElement, params);
    } else if (targetElement.tagName === 'SELECT') {
        optimisticSelectUpdate(targetElement, params);
    } else if (targetElement.tagName === 'BUTTON') {
        optimisticButtonUpdate(targetElement, params);
    }

    // Add loading indicator
    targetElement.classList.add('optimistic-pending');
    pendingEvents.add(eventName);

    if (window.djustDebug) {
        console.log('[LiveView:optimistic] Applied optimistic update:', eventName);
    }
}

function saveOptimisticState(eventName, element) {
    const originalState = {};

    if (element.type === 'checkbox' || element.type === 'radio') {
        originalState.checked = element.checked;
    }
    if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') {
        originalState.value = element.value;
    }
    if (element.tagName === 'BUTTON') {
        originalState.disabled = element.disabled;
        originalState.text = element.textContent;
    }

    optimisticUpdates.set(eventName, {
        element: element,
        originalState: originalState
    });
}

function clearOptimisticState(eventName) {
    if (optimisticUpdates.has(eventName)) {
        const { element, originalState } = optimisticUpdates.get(eventName);

        // Remove loading indicator
        element.classList.remove('optimistic-pending');

        // Restore original button state (if button)
        if (element.tagName === 'BUTTON') {
            if (originalState.disabled !== undefined) {
                element.disabled = originalState.disabled;
            }
            if (originalState.text !== undefined) {
                element.textContent = originalState.text;
            }
        }

        optimisticUpdates.delete(eventName);
    }
    pendingEvents.delete(eventName);
}

function revertOptimisticUpdate(eventName) {
    if (!optimisticUpdates.has(eventName)) {
        return;
    }

    const { element, originalState } = optimisticUpdates.get(eventName);

    // Restore original state
    if (originalState.checked !== undefined) {
        element.checked = originalState.checked;
    }
    if (originalState.value !== undefined) {
        element.value = originalState.value;
    }
    if (originalState.disabled !== undefined) {
        element.disabled = originalState.disabled;
    }
    if (originalState.text !== undefined) {
        element.textContent = originalState.text;
    }

    // Add error indicator
    element.classList.remove('optimistic-pending');
    element.classList.add('optimistic-error');
    setTimeout(() => element.classList.remove('optimistic-error'), 2000);

    clearOptimisticState(eventName);

    if (window.djustDebug) {
        console.log('[LiveView:optimistic] Reverted optimistic update:', eventName);
    }
}

function optimisticToggle(element, params) {
    if (params.checked !== undefined) {
        element.checked = params.checked;
    } else {
        element.checked = !element.checked;
    }
}

function optimisticInputUpdate(element, params) {
    if (params.value !== undefined) {
        element.value = params.value;
    }
}

function optimisticSelectUpdate(element, params) {
    if (params.value !== undefined) {
        element.value = params.value;
    }
}

function optimisticButtonUpdate(element, params) {
    element.disabled = true;
    if (element.hasAttribute('data-loading-text')) {
        element.textContent = element.getAttribute('data-loading-text');
    }
}

// ============================================================================
// Cache Decorator (Phase 5)
// ============================================================================

async function cacheEvent(eventName, eventData, config, sendFn) {
    const { ttl = 60, key_params = [] } = config;

    // Generate cache key from handler + params
    const cacheKey = generateCacheKey(eventName, eventData, key_params);

    // Check cache
    const cached = resultCache.get(cacheKey);
    if (cached && Date.now() < cached.expiresAt) {
        // Cache hit - apply cached patches without server call
        if (globalThis.djustDebug) {
            const remainingTtl = Math.round((cached.expiresAt - Date.now()) / 1000);
            console.log(`[LiveView:cache] Cache hit: ${cacheKey} (TTL: ${remainingTtl}s remaining)`);
        }
        // Apply cached patches directly
        applyPatches(cached.patches);
        return;
    }

    // Cache miss - send event to server and cache the patches
    if (globalThis.djustDebug) {
        console.log(`[LiveView:cache] Cache miss: ${cacheKey} (calling server)`);
    }

    // Generate unique request ID to track this cache request
    const requestId = `${eventName}_${Date.now()}_${Math.random()}`;

    // Mark this request as pending cache
    pendingCacheRequests.set(requestId, { cacheKey, ttl });

    // Add request ID to event data so we can match patches to this request
    eventData._cacheRequestId = requestId;

    // Send event to server
    await sendFn(eventName, eventData);
}

function generateCacheKey(eventName, eventData, keyParams) {
    if (keyParams.length === 0) {
        return eventName;
    }

    const paramValues = keyParams.map(param => {
        const value = eventData[param];
        if (value === undefined || value === null) {
            return '';
        }
        return String(value);
    }).join(':');

    return `${eventName}:${paramValues}`;
}

function clearCache(eventName) {
    if (!eventName) {
        // Clear all cache
        resultCache.clear();
        if (globalThis.djustDebug) {
            console.log('[LiveView:cache] Cleared all cache entries');
        }
        return;
    }

    // Clear specific event (all keys starting with eventName)
    let cleared = 0;
    for (const key of resultCache.keys()) {
        if (key === eventName || key.startsWith(eventName + ':')) {
            resultCache.delete(key);
            cleared++;
        }
    }

    if (globalThis.djustDebug) {
        console.log(`[LiveView:cache] Cleared ${cleared} cache entries for ${eventName}`);
    }
}

function cleanupExpiredCache() {
    const now = Date.now();
    let cleaned = 0;

    for (const [key, entry] of resultCache.entries()) {
        if (now >= entry.expiresAt) {
            resultCache.delete(key);
            cleaned++;
        }
    }

    if (globalThis.djustDebug && cleaned > 0) {
        console.log(`[LiveView:cache] Cleaned up ${cleaned} expired cache entries`);
    }

    return cleaned;
}

// ============================================================================
// Client State Decorator (Phase 5)
// ============================================================================

function clientStateEvent(eventName, eventData, config, sendFn) {
    const { state_key } = config;

    if (!state_key) {
        console.error('[LiveView:client_state] Missing state_key in config');
        return sendFn(eventName, eventData);
    }

    // Extract state value from event data
    const stateValue = eventData.value !== undefined ? eventData.value :
                       eventData.checked !== undefined ? eventData.checked :
                       eventData;

    if (globalThis.djustDebug) {
        console.log(`[LiveView:client_state] Setting ${state_key} =`, stateValue);
    }

    // Update StateBus (this will notify all subscribers)
    globalStateBus.set(state_key, stateValue);

    // Call server to persist state
    return sendFn(eventName, eventData);
}

// ============================================================================
// Loading Attribute Support (Phase 5)
// ============================================================================

class LoadingManager {
    constructor() {
        this.loadingElements = new Map();
        this.pendingEvents = new Set();
    }

    register(element, eventName) {
        // Check if element is already registered - preserve original state if so
        const existingConfig = this.loadingElements.get(element);
        const preservedOriginalState = existingConfig?.originalState || {};

        const attributes = element.attributes;
        const loadingConfig = {
            eventName,
            modifiers: [],
            originalState: {}
        };

        for (let i = 0; i < attributes.length; i++) {
            const attr = attributes[i];
            const match = attr.name.match(/^@loading\.(.+)$/);
            if (match) {
                const modifier = match[1];

                if (modifier === 'disable') {
                    loadingConfig.modifiers.push({ type: 'disable' });
                    // Preserve original state if already registered, otherwise capture current
                    loadingConfig.originalState.disabled = preservedOriginalState.disabled !== undefined
                        ? preservedOriginalState.disabled
                        : element.disabled;
                } else if (modifier === 'show') {
                    loadingConfig.modifiers.push({ type: 'show' });
                    loadingConfig.originalState.display = preservedOriginalState.display !== undefined
                        ? preservedOriginalState.display
                        : element.style.display;
                } else if (modifier === 'hide') {
                    loadingConfig.modifiers.push({ type: 'hide' });
                    loadingConfig.originalState.display = preservedOriginalState.display !== undefined
                        ? preservedOriginalState.display
                        : element.style.display;
                } else if (modifier === 'class') {
                    const className = attr.value;
                    if (className) {
                        loadingConfig.modifiers.push({ type: 'class', value: className });
                    }
                }
            }
        }

        if (loadingConfig.modifiers.length > 0) {
            this.loadingElements.set(element, loadingConfig);

            if (globalThis.djustDebug) {
                console.log(`[Loading] Registered modifiers for "${eventName}":`,
                    loadingConfig.modifiers, element);
            }
        }
    }

    startLoading(eventName, triggerElement = null) {
        this.pendingEvents.add(eventName);

        if (globalThis.djustDebug) {
            console.log(`[Loading] Started: ${eventName}`, triggerElement);
        }

        this.loadingElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                // Only apply loading state if element is related to the trigger
                if (this.isRelatedElement(element, triggerElement)) {
                    this.applyLoadingState(element, config);
                }
            }
        });
    }

    stopLoading(eventName, triggerElement = null) {
        this.pendingEvents.delete(eventName);

        if (globalThis.djustDebug) {
            console.log(`[Loading] Stopped: ${eventName}`, triggerElement);
        }

        this.loadingElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                // Only remove loading state if element is related to the trigger
                if (this.isRelatedElement(element, triggerElement)) {
                    this.removeLoadingState(element, config);
                }
            }
        });
    }
    /**
     * Check if an element is related to the trigger element.
     *
     * This method implements scoped loading state to prevent cross-button
     * contamination when multiple buttons share the same event handler.
     *
     * Scoping Rules:
     * 1. The trigger element itself is ALWAYS affected
     * 2. Siblings are ONLY affected if they're in an explicit grouping container
     *
     * Grouping Containers (Bootstrap-oriented):
     * - d-flex: Flexbox layouts (common for button + spinner)
     * - btn-group: Button groups
     * - input-group: Input groups with buttons
     * - form-group: Form field groups
     * - btn-toolbar: Button toolbars
     *
     * Why these specific classes?
     * - They indicate intentional UI grouping in Bootstrap (most common framework)
     * - They're semantically meaningful (not just styling classes)
     * - They provide a clear developer signal that elements should coordinate
     *
     * Examples:
     *
     * ✅ INDEPENDENT (no grouping):
     *   <div class="card-body">
     *     <button @click="save" @loading.disable>Save A</button>
     *     <button @click="save" @loading.disable>Save B</button>
     *   </div>
     *   → Clicking Save A only affects Save A
     *
     * ✅ GROUPED (d-flex):
     *   <div class="d-flex gap-2">
     *     <button @click="save">Save</button>
     *     <div @loading.show>Spinner</div>
     *   </div>
     *   → Clicking Save affects both button and spinner
     *
     * Note: This list is hardcoded for Bootstrap compatibility.
     * Future enhancement: Make configurable via config.
     *
     * @param {HTMLElement} element - Element with @loading attribute
     * @param {HTMLElement} triggerElement - Element that triggered the event
     * @returns {boolean} True if element should receive loading state
     */
    isRelatedElement(element, triggerElement) {
        if (!triggerElement) {
            // No trigger specified, apply to all (legacy behavior)
            return true;
        }

        // Always match the trigger element itself
        if (element === triggerElement) {
            return true;
        }

        // Only match siblings if they're in an explicit grouping container
        if (element.parentElement && triggerElement.parentElement &&
            element.parentElement === triggerElement.parentElement) {


            // Check if parent has a grouping class (configurable via LIVEVIEW_CONFIG)
            const groupingClasses = window.DJUST_LOADING_GROUPING_CLASSES || ['d-flex', 'btn-group', 'input-group', 'form-group', 'btn-toolbar'];
            const parentClasses = element.parentElement.className || '';

            // Allow sibling matching only for explicitly grouped containers
            return groupingClasses.some(cls => parentClasses.includes(cls));
        }

        return false;
    }

    applyLoadingState(element, config) {
        if (globalThis.djustDebug) {
            console.log(`[Loading] Applying state to element:`, element, 'modifiers:', config.modifiers);
        }

        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = true;
                if (globalThis.djustDebug) {
                    console.log(`[Loading] Applied disable to element`);
                }
            } else if (modifier.type === 'show') {
                element.style.display = element.getAttribute('data-loading-display') || 'block';
                if (globalThis.djustDebug) {
                    console.log(`[Loading] Applied show to element`);
                }
            } else if (modifier.type === 'hide') {
                element.style.display = 'none';
                if (globalThis.djustDebug) {
                    console.log(`[Loading] Applied hide to element`);
                }
            } else if (modifier.type === 'class') {
                element.classList.add(modifier.value);
                if (globalThis.djustDebug) {
                    console.log(`[Loading] Applied class "${modifier.value}" to element`);
                }
            }
        });
    }

    removeLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = config.originalState.disabled || false;
            } else if (modifier.type === 'show' || modifier.type === 'hide') {
                element.style.display = config.originalState.display || '';
            } else if (modifier.type === 'class') {
                element.classList.remove(modifier.value);
            }
        });

        // Also remove optimistic-pending class (from @optimistic decorator)
        // This is needed because VDOM patches may replace elements before clearOptimisticState runs
        element.classList.remove('optimistic-pending');
    }

    isLoading(eventName) {
        return this.pendingEvents.has(eventName);
    }

    clear() {
        this.loadingElements.clear();
        this.pendingEvents.clear();
    }
}

const globalLoadingManager = new LoadingManager();

// ============================================================================
// Event Handling
// ============================================================================

async function handleEvent(eventName, params) {
    console.log('[LiveView] Event:', eventName, params);

    // Skip decorator checks if this is an immediate send (from decorator)
    if (!params._skipDecorators) {
        // Check for handler metadata (decorators)
        const metadata = window.handlerMetadata?.[eventName];

        // Warn if multiple decorators present (ambiguous behavior)
        if (metadata?.debounce && metadata?.throttle) {
            console.warn(
                `[LiveView] Handler '${eventName}' has both @debounce and @throttle decorators. ` +
                `Applying @debounce only. Use one decorator per handler.`
            );
        }

        // Apply optimistic update FIRST (Phase 3)
        // This happens immediately, regardless of debounce/throttle
        if (metadata?.optimistic && params._targetElement) {
            applyOptimisticUpdate(eventName, params, params._targetElement);
        }

        // Apply debounce if configured (Phase 2)
        if (metadata?.debounce) {
            debounceEvent(eventName, params, metadata.debounce);
            return; // Don't send immediately
        }

        // Apply throttle if configured (Phase 2)
        if (metadata?.throttle) {
            throttleEvent(eventName, params, metadata.throttle);
            return; // Don't send immediately
        }

        // Apply cache if configured (Phase 5)
        if (metadata?.cache) {
            // Cache wraps the actual send - check cache first, call server on miss
            await cacheEvent(eventName, params, metadata.cache, async (evName, evParams) => {
                evParams._skipDecorators = true;
                await handleEvent(evName, evParams);
            });
            return; // Cache handled it
        }
    }

    // Phase 5: Start loading state (pass trigger element for scoping)
    const triggerElement = params._targetElement;
    globalLoadingManager.startLoading(eventName, triggerElement);

    // Store trigger element for WebSocket flow (stopLoading will need it)
    if (liveViewWS) {
        liveViewWS.lastTriggerElement = triggerElement;
    }

    // Remove internal flags before sending
    delete params._skipDecorators;
    delete params._targetElement;

    // Try WebSocket first
    if (liveViewWS && liveViewWS.sendEvent(eventName, params)) {
        console.log('[LiveView] Event sent via WebSocket');
        return;  // WebSocket handled it
    }

    // Fallback to HTTP
    console.log('[LiveView] Using HTTP fallback');
    try {
        const response = await fetch(window.location.href.split('?')[0], {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({
                event: eventName,
                params: params
            })
        });

        if (response.ok) {
            const data = await response.json();

            // Use centralized response handler
            handleServerResponse(data, eventName, triggerElement);
        } else {
            // Handle HTTP errors (500, 404, etc.)
            const errorData = await response.json().catch(() => ({}));

            console.error('[LiveView] Server Error:', {
                status: response.status,
                statusText: response.statusText,
                error: errorData
            });

            // Revert optimistic update on error (Phase 3)
            revertOptimisticUpdate(eventName);

            // Phase 5: Stop loading state on error
            globalLoadingManager.stopLoading(eventName, triggerElement);

            // Show user-friendly error notification
            showErrorNotification(errorData, response.status);
        }
    } catch (error) {
        console.error('[LiveView] Network Error:', error);

        // Revert optimistic update on network error (Phase 3)
        revertOptimisticUpdate(eventName);

        // Phase 5: Stop loading state on network error
        globalLoadingManager.stopLoading(eventName, triggerElement);

        showErrorNotification({ error: 'Network error occurred. Please check your connection.' }, 0);
    }
}

function showErrorNotification(errorData, statusCode) {
    // Create error notification element
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        max-width: 500px;
        background: #dc3545;
        color: white;
        padding: 16px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
    `;

    // Build error message
    let message = '';
    if (errorData.error) {
        message = `<strong>Error:</strong> ${escapeHtml(errorData.error)}`;

        // Show detailed error info if available (DEBUG mode)
        if (errorData.type) {
            message += `<br><small><strong>${escapeHtml(errorData.type)}</strong></small>`;
        }
        if (errorData.event) {
            message += `<br><small>Event: <code style="background: rgba(0,0,0,0.2); padding: 2px 4px; border-radius: 3px;">${escapeHtml(errorData.event)}</code></small>`;
        }
        if (errorData.traceback) {
            message += `<br><details style="margin-top: 8px; cursor: pointer;">
                <summary style="font-size: 12px;">Show Traceback</summary>
                <pre style="font-size: 11px; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 4px; overflow-x: auto; margin-top: 4px;">${escapeHtml(errorData.traceback)}</pre>
            </details>`;
        }
    } else {
        message = `<strong>Error ${statusCode}:</strong> An error occurred while processing your request.`;
    }

    notification.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px;">
            <div style="flex: 1;">${message}</div>
            <button onclick="this.parentElement.parentElement.remove()"
                    style="background: none; border: none; color: white; font-size: 20px;
                           cursor: pointer; padding: 0; line-height: 1; opacity: 0.8;"
                    title="Close">×</button>
        </div>
    `;

    // Add animation styles
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(400px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
    `;
    if (!document.querySelector('style[data-liveview-error-styles]')) {
        style.setAttribute('data-liveview-error-styles', '');
        document.head.appendChild(style);
    }

    document.body.appendChild(notification);

    // Auto-remove after 10 seconds
    setTimeout(() => {
        notification.style.animation = 'slideIn 0.3s ease-out reverse';
        setTimeout(() => notification.remove(), 300);
    }, 10000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// Client-side TodoItem interactions (only for React demo TodoItems)
function initTodoItems() {
    // Handle checkbox changes for .todo-checkbox (React demo TodoItems)
    document.querySelectorAll('.todo-checkbox').forEach(checkbox => {
        if (!checkbox.dataset.reactInitialized) {
            checkbox.dataset.reactInitialized = 'true';
            checkbox.addEventListener('change', function() {
                const todoItem = this.closest('.todo-item');
                if (this.checked) {
                    todoItem.classList.add('completed');
                } else {
                    todoItem.classList.remove('completed');
                }
            });
        }
    });

    // Handle delete button clicks for React demo TodoItems
    document.querySelectorAll('.todo-delete').forEach(button => {
        if (!button.dataset.reactInitialized) {
            button.dataset.reactInitialized = 'true';
            button.addEventListener('click', async function() {
                const todoText = this.dataset.todoText;
                await handleEvent('delete_todo_item', { text: todoText });
            });
        }
    });
}

// Inject CSS styles for optimistic updates (Phase 3)
function injectOptimisticStyles() {
    if (document.getElementById('djust-optimistic-styles')) {
        return; // Already injected
    }

    const styles = `
        /* Optimistic update loading indicators */
        .optimistic-pending {
            opacity: 0.6;
            cursor: wait !important;
            position: relative;
        }

        .optimistic-pending::after {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(255, 255, 255, 0.1);
            pointer-events: none;
        }

        .optimistic-error {
            animation: djust-shake 0.5s;
            border-color: #dc3545 !important;
        }

        @keyframes djust-shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-5px); }
            75% { transform: translateX(5px); }
        }
    `;

    const styleElement = document.createElement('style');
    styleElement.id = 'djust-optimistic-styles';
    styleElement.textContent = styles;
    document.head.appendChild(styleElement);
}

document.addEventListener('DOMContentLoaded', function() {
    console.log('[LiveView] Initialized with Rust-powered rendering');

    // Expose handleEvent globally for custom scripts (e.g., throttle/debounce demos)
    window.djustHandleEvent = handleEvent;

    // Inject optimistic update CSS (Phase 3)
    injectOptimisticStyles();

    // Check if WebSocket is enabled (can be disabled via config)
    const useWebSocket = window.DJUST_USE_WEBSOCKET !== false;

    if (useWebSocket) {
        // Initialize WebSocket connection
        console.log('[LiveView] Using WebSocket mode');
        liveViewWS = new LiveViewWebSocket();
        liveViewWS.connect();
        liveViewWS.startHeartbeat();
    } else {
        // HTTP-only mode (no WebSocket)
        console.log('[LiveView] Using HTTP-only mode (WebSocket disabled)');
    }

    initReactCounters();  // Initialize client-side React components
    initTodoItems();      // Initialize todo item checkboxes
    bindLiveViewEvents();
    initDraftMode();      // Initialize DraftModeMixin (Phase 5)
    initTurboNavigation(); // Initialize Turbo-like navigation

    // Expose sendEvent globally for manual event handling (e.g., optimistic updates)
    window.sendEvent = handleEvent;
});

// ==============================================================================
// TURBO-LIKE NAVIGATION
// ==============================================================================
// Provides instant page navigation without full page reloads, while maintaining
// multi-page architecture benefits (SEO, deep linking, browser history).
//
// Features:
// - Intercepts clicks on links with data-djust-navigate attribute
// - Fetches pages via AJAX and replaces main content
// - Updates browser history with pushState/popState
// - Shows loading indicator during navigation
// - Preserves scroll position
// - Falls back to normal navigation on errors
//
// Usage:
//   <a href="/rentals/properties/" data-djust-navigate>Properties</a>
//
// Configuration:
//   data-djust-navigate="main" - Replace element with id="main"
//   data-djust-navigate - Use default selector (main, [data-liveview-root], or body)
// ==============================================================================

function initTurboNavigation() {
    let isNavigating = false;
    const loadingBar = createLoadingBar();

    // Intercept link clicks
    document.addEventListener('click', function(e) {
        // Find the link element (might be nested in other elements)
        const link = e.target.closest('a[data-djust-navigate]');
        if (!link) return;

        // Ignore if:
        // - Already navigating
        // - Link has target="_blank" or similar
        // - Modifier keys are pressed (ctrl/cmd/shift for new tab/window)
        // - Not left click
        if (isNavigating ||
            link.target ||
            e.ctrlKey ||
            e.metaKey ||
            e.shiftKey ||
            e.button !== 0) {
            return;
        }

        // Get the URL
        const url = link.href;
        if (!url || url === window.location.href) return;

        // Same origin check
        const linkUrl = new URL(url);
        if (linkUrl.origin !== window.location.origin) return;

        // Prevent default navigation
        e.preventDefault();

        // Perform turbo navigation
        navigateTo(url, link.getAttribute('data-djust-navigate') || null);
    });

    // Handle browser back/forward buttons
    window.addEventListener('popstate', function(e) {
        if (e.state && e.state.turboNavigated) {
            navigateTo(window.location.href, null, true);
        }
    });

    async function navigateTo(url, targetSelector, skipHistory = false) {
        if (isNavigating) return;

        isNavigating = true;
        showLoadingBar();

        try {
            // Fetch the new page
            const response = await fetch(url, {
                headers: {
                    'X-Djust-Turbo': 'true' // Server can detect turbo navigation
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const html = await response.text();

            // Parse the response
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            // Determine what to replace
            let newContent, oldContent;

            if (targetSelector) {
                // Use specific selector
                newContent = doc.querySelector(targetSelector);
                oldContent = document.querySelector(targetSelector);
            } else {
                // Auto-detect: try main, then [data-liveview-root], then body
                const selectors = ['main', '[data-liveview-root]', 'body'];
                for (const selector of selectors) {
                    newContent = doc.querySelector(selector);
                    oldContent = document.querySelector(selector);
                    if (newContent && oldContent) break;
                }
            }

            if (!newContent || !oldContent) {
                throw new Error('Could not find content to replace');
            }

            // Save scroll position
            const scrollPos = window.scrollY;

            // Replace content
            oldContent.innerHTML = newContent.innerHTML;

            // Update title
            if (doc.title) {
                document.title = doc.title;
            }

            // Re-initialize LiveView events on new content
            bindLiveViewEvents();

            // Update browser history
            if (!skipHistory) {
                window.history.pushState(
                    { turboNavigated: true },
                    '',
                    url
                );
            }

            // Restore scroll position (or scroll to top for new pages)
            if (skipHistory) {
                window.scrollTo(0, scrollPos);
            } else {
                window.scrollTo(0, 0);
            }

            // Dispatch custom event for tracking/analytics
            window.dispatchEvent(new CustomEvent('djust:navigate', {
                detail: { url, turboNavigated: true }
            }));

        } catch (error) {
            console.error('[Turbo] Navigation failed:', error);

            // Fall back to normal navigation
            window.location.href = url;
        } finally {
            isNavigating = false;
            hideLoadingBar();
        }
    }

    function createLoadingBar() {
        const bar = document.createElement('div');
        bar.id = 'djust-loading-bar';
        bar.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #3b82f6, #8b5cf6);
            transform: scaleX(0);
            transform-origin: left;
            transition: transform 0.3s ease;
            z-index: 9999;
            opacity: 0;
        `;
        document.body.appendChild(bar);
        return bar;
    }

    function showLoadingBar() {
        loadingBar.style.opacity = '1';
        loadingBar.style.transform = 'scaleX(0.3)';

        // Animate to 70% over 2 seconds
        setTimeout(() => {
            loadingBar.style.transition = 'transform 2s ease';
            loadingBar.style.transform = 'scaleX(0.7)';
        }, 100);
    }

    function hideLoadingBar() {
        loadingBar.style.transition = 'transform 0.2s ease';
        loadingBar.style.transform = 'scaleX(1)';

        // Fade out
        setTimeout(() => {
            loadingBar.style.opacity = '0';
            setTimeout(() => {
                loadingBar.style.transform = 'scaleX(0)';
            }, 200);
        }, 200);
    }

    if (window.djustDebug) {
        console.log('[Turbo] Navigation initialized');
    }
}

// ============================================================================
// Developer Debug Panel
// ============================================================================

class DjustDebugPanel {
    constructor() {
        this.visible = false;
        this.currentTab = 'handlers';
        this.eventHistory = [];
        this.patchHistory = [];

        // Get max history from config (default: 50)
        const debugInfo = window.DJUST_DEBUG_INFO || {};
        this.maxHistory = (debugInfo.config && debugInfo.config.maxHistory) || 50;

        this.createUI();
        this.setupKeyboardShortcut();
    }

    createUI() {
        // Create floating button (only visible in DEBUG mode)
        if (typeof window.DJUST_DEBUG_INFO === 'undefined') {
            return; // Not in debug mode
        }

        // Floating button
        this.button = document.createElement('button');
        this.button.className = 'djust-debug-button';
        this.button.innerHTML = '🔧';
        this.button.title = 'Open djust Debug Panel (Ctrl+Shift+D)';
        this.button.onclick = () => this.toggle();
        document.body.appendChild(this.button);

        // Panel container
        this.panel = document.createElement('div');
        this.panel.className = 'djust-debug-panel';
        this.panel.style.display = 'none';
        document.body.appendChild(this.panel);
    }

    setupKeyboardShortcut() {
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'D') {
                e.preventDefault();
                this.toggle();
            }
        });
    }

    toggle() {
        this.visible = !this.visible;

        if (this.visible) {
            this.render();
            this.panel.style.display = 'block';
            this.button.classList.add('active');
        } else {
            this.panel.style.display = 'none';
            this.button.classList.remove('active');
        }
    }

    render() {
        const debugInfo = window.DJUST_DEBUG_INFO || {};

        this.panel.innerHTML = `
            <div class="djust-debug-header">
                <h3>djust Debug Panel</h3>
                <button onclick="window.djustDebugPanel.toggle()">✕</button>
            </div>

            <div class="djust-debug-tabs">
                <button class="${this.currentTab === 'handlers' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('handlers')">
                    Event Handlers
                </button>
                <button class="${this.currentTab === 'history' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('history')">
                    Event History (${this.eventHistory.length})
                </button>
                <button class="${this.currentTab === 'patches' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('patches')">
                    VDOM Patches
                </button>
                <button class="${this.currentTab === 'variables' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('variables')">
                    Variables
                </button>
            </div>

            <div class="djust-debug-content">
                ${this.renderTabContent()}
            </div>
        `;
    }

    renderTabContent() {
        const debugInfo = window.DJUST_DEBUG_INFO || {};

        switch (this.currentTab) {
            case 'handlers':
                return this.renderHandlers(debugInfo.handlers || {});
            case 'history':
                return this.renderEventHistory();
            case 'patches':
                return this.renderPatchHistory();
            case 'variables':
                return this.renderVariables(debugInfo.variables || {});
            default:
                return '';
        }
    }

    renderHandlers(handlers) {
        if (Object.keys(handlers).length === 0) {
            return '<p class="empty">No event handlers found</p>';
        }

        let html = '<div class="handler-list">';

        for (const [name, info] of Object.entries(handlers)) {
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
                                ${!p.required ? ` = <span class="default">${p.default}</span>` : '<span class="required">*required</span>'}
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

        html += '</div>';
        return html;
    }

    renderEventHistory() {
        if (this.eventHistory.length === 0) {
            return '<p class="empty">No events captured yet. Interact with the page to see events here.</p>';
        }

        let html = `
            <div class="event-history">
                <div class="history-toolbar">
                    <button onclick="window.djustDebugPanel.exportEventHistory()" class="export-btn">
                        📥 Export All Events
                    </button>
                    <span class="history-count">${this.eventHistory.length} event(s)</span>
                </div>
        `;

        // Reverse to show newest first
        for (let i = 0; i < this.eventHistory.length; i++) {
            const event = this.eventHistory[this.eventHistory.length - 1 - i];
            const timestamp = new Date(event.timestamp).toLocaleTimeString();
            const success = event.error ? 'error' : 'success';
            const eventId = `event-${i}`;

            // Format duration
            const durationText = event.duration !== undefined
                ? `${event.duration.toFixed(1)}ms`
                : 'N/A';

            html += `
                <div class="event-item ${success} collapsed" data-event-id="${eventId}">
                    <div class="event-header" onclick="window.djustDebugPanel.toggleEvent('${eventId}')">
                        <span class="event-toggle">▶</span>
                        <span class="event-name">${event.name}</span>
                        <span class="event-time">${timestamp} • ${durationText}</span>
                    </div>

                    <div class="event-body">
                        <div class="event-params">
                            <strong>Parameters:</strong>
                            <pre>${JSON.stringify(event.params, null, 2)}</pre>
                        </div>

                        ${event.error ? `
                            <div class="event-error">
                                <strong>Error:</strong> ${event.error}
                            </div>
                        ` : ''}

                        <div class="event-actions">
                            <button onclick="navigator.clipboard.writeText('${JSON.stringify(event).replace(/'/g, "\\'")}')">
                                Copy JSON
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    renderPatchHistory() {
        if (this.patchHistory.length === 0) {
            return '<p class="empty">No patches captured yet.</p>';
        }

        let html = '<div class="patch-history">';

        for (let i = 0; i < this.patchHistory.length; i++) {
            const entry = this.patchHistory[this.patchHistory.length - 1 - i];
            const patchId = `patch-${i}`;

            // Format duration
            const durationText = entry.duration !== undefined
                ? `${entry.duration.toFixed(2)}ms`
                : 'N/A';

            html += `
                <div class="patch-item collapsed" data-patch-id="${patchId}">
                    <div class="patch-header" onclick="window.djustDebugPanel.togglePatch('${patchId}')">
                        <span class="patch-toggle">▶</span>
                        <span>${entry.count} patch${entry.count !== 1 ? 'es' : ''}</span>
                        <span>${new Date(entry.timestamp).toLocaleTimeString()} • ${durationText}</span>
                    </div>
                    <div class="patch-body">
                        <pre>${JSON.stringify(entry.patches, null, 2)}</pre>
                        ${entry.duration !== undefined ? `<p class="patch-stats">Applied in ${durationText}</p>` : ''}
                    </div>
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    togglePatch(patchId) {
        const patchItem = this.panel.querySelector(`[data-patch-id="${patchId}"]`);
        if (patchItem) {
            patchItem.classList.toggle('collapsed');
            const toggle = patchItem.querySelector('.patch-toggle');
            if (toggle) {
                toggle.textContent = patchItem.classList.contains('collapsed') ? '▶' : '▼';
            }
        }
    }

    toggleEvent(eventId) {
        const eventItem = this.panel.querySelector(`[data-event-id="${eventId}"]`);
        if (eventItem) {
            eventItem.classList.toggle('collapsed');
            const toggle = eventItem.querySelector('.event-toggle');
            if (toggle) {
                toggle.textContent = eventItem.classList.contains('collapsed') ? '▶' : '▼';
            }
        }
    }

    renderVariables(variables) {
        if (Object.keys(variables).length === 0) {
            return '<p class="empty">No public variables found</p>';
        }

        let html = '<div class="variable-list">';

        for (const [name, info] of Object.entries(variables)) {
            html += `
                <div class="variable-item">
                    <div class="variable-name">${name}</div>
                    <div class="variable-type">${info.type}</div>
                    <div class="variable-value"><code>${info.value}</code></div>
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    switchTab(tab) {
        this.currentTab = tab;
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

        // Keep last 50 events
        if (this.eventHistory.length > this.maxHistory) {
            this.eventHistory.shift();
        }

        if (this.visible && this.currentTab === 'history') {
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

        if (this.visible && this.currentTab === 'patches') {
            this.render();
        }
    }

    exportEventHistory() {
        // Create export data
        const exportData = {
            exported_at: new Date().toISOString(),
            view_class: (window.DJUST_DEBUG_INFO || {}).view_class || 'Unknown',
            event_count: this.eventHistory.length,
            events: this.eventHistory
        };

        // Convert to JSON
        const jsonString = JSON.stringify(exportData, null, 2);

        // Create download link
        const blob = new Blob([jsonString], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `djust-events-${Date.now()}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    }
}

// Initialize global debug panel
if (typeof window.DJUST_DEBUG_INFO !== 'undefined') {
    window.djustDebugPanel = new DjustDebugPanel();

    // Hook into event sending to log events
    // Store original handleEvent function
    const originalHandleEvent = handleEvent;

    // Replace with wrapper that logs events with timing
    window.handleEvent = handleEvent = async function(eventName, params) {
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
    const originalApplyPatches = window.applyPatches || applyPatches;

    // Replace with wrapper that logs patches with timing
    window.applyPatches = applyPatches = function(patches) {
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
}
