// ============================================================================
// JS Commands — client-side interpreter + fluent chain API
// ============================================================================
//
// Exposes `window.djust.js` as both:
//   - a chain factory: djust.js.show('#modal').addClass('active', {to: '#overlay'}).exec()
//   - a dispatcher for JSON command lists built on the server via djust.js.JS
//
// Template binding integration: event handlers (dj-click, dj-change, etc.)
// detect when the attribute value starts with `[[` (a JSON command list) and
// execute the chain locally instead of sending a server event. The special
// `push` command in a chain DOES send a server event, so you can mix optimistic
// DOM updates with server round-trips in a single handler.
//
// All eleven commands from Phoenix LiveView 1.0 are supported: show, hide,
// toggle, add_class, remove_class, transition, dispatch, focus, set_attr,
// remove_attr, push. The public JS (camelCase) method names are addClass,
// removeClass, setAttr, removeAttr; the serialised ops use snake_case to match
// Phoenix and the Python djust.js.JS helper.
// ============================================================================

(function() {
    if (!window.djust) window.djust = {};

    // ------------------------------------------------------------------------
    // Target resolution
    // ------------------------------------------------------------------------

    /**
     * Resolve an operation's target into a NodeList-like array of elements.
     *
     * Targets (at most one):
     *   to=<selector>       absolute: document.querySelectorAll
     *   inner=<selector>    scoped to originEl's descendants
     *   closest=<selector>  walk up from originEl
     * (none of the above)   default to originEl itself
     */
    function resolveTargets(args, originEl) {
        args = args || {};
        if (args.to) {
            try {
                return Array.from(document.querySelectorAll(args.to));
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] bad to= selector', args.to, err);
                return [];
            }
        }
        if (args.inner && originEl) {
            try {
                return Array.from(originEl.querySelectorAll(args.inner));
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] bad inner= selector', args.inner, err);
                return [];
            }
        }
        if (args.closest && originEl) {
            try {
                const el = originEl.closest(args.closest);
                return el ? [el] : [];
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] bad closest= selector', args.closest, err);
                return [];
            }
        }
        return originEl ? [originEl] : [];
    }

    // ------------------------------------------------------------------------
    // Individual command executors
    // ------------------------------------------------------------------------

    function execShow(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const display = args.display || '';
        targets.forEach(el => {
            el.style.display = display;
            if (!display) el.style.removeProperty('display');
            el.removeAttribute('hidden');
            el.dispatchEvent(new CustomEvent('djust:show', { bubbles: true }));
        });
    }

    function execHide(args, originEl) {
        const targets = resolveTargets(args, originEl);
        targets.forEach(el => {
            el.style.display = 'none';
            el.dispatchEvent(new CustomEvent('djust:hide', { bubbles: true }));
        });
    }

    function execToggle(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const display = args.display || '';
        targets.forEach(el => {
            const cs = el.ownerDocument.defaultView.getComputedStyle(el);
            const hidden = cs.display === 'none' || el.hidden;
            if (hidden) {
                el.style.display = display;
                if (!display) el.style.removeProperty('display');
                el.removeAttribute('hidden');
            } else {
                el.style.display = 'none';
            }
        });
    }

    function execAddClass(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const classes = (args.names || '').split(/\s+/).filter(Boolean);
        targets.forEach(el => el.classList.add(...classes));
    }

    function execRemoveClass(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const classes = (args.names || '').split(/\s+/).filter(Boolean);
        targets.forEach(el => el.classList.remove(...classes));
    }

    function execTransition(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const classes = (args.names || '').split(/\s+/).filter(Boolean);
        const time = Number(args.time) || 200;
        targets.forEach(el => {
            el.classList.add(...classes);
            setTimeout(() => {
                el.classList.remove(...classes);
            }, time);
        });
    }

    function execSetAttr(args, originEl) {
        const targets = resolveTargets(args, originEl);
        // args.attr is a 2-tuple [name, value] (matches Phoenix/the Python helper)
        if (!Array.isArray(args.attr) || args.attr.length < 2) return;
        const [name, value] = args.attr;
        targets.forEach(el => el.setAttribute(name, value));
    }

    function execRemoveAttr(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const name = typeof args.attr === 'string' ? args.attr : (Array.isArray(args.attr) ? args.attr[0] : null);
        if (!name) return;
        targets.forEach(el => el.removeAttribute(name));
    }

    function execFocus(args, originEl) {
        const targets = resolveTargets(args, originEl);
        if (targets.length) {
            try {
                targets[0].focus();
            } catch (_err) { /* focus can throw on non-focusable elements */ }
        }
    }

    function execDispatch(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const name = args.event || 'djust:unnamed';
        const detail = args.detail || {};
        const bubbles = args.bubbles !== false;
        targets.forEach(el => {
            el.dispatchEvent(new CustomEvent(name, { bubbles, detail }));
        });
    }

    async function execPush(args, _originEl) {
        // push op: bridge a chain to a server event. Uses the existing
        // handleEvent() pipeline so debouncing, rate limiting, and the
        // HTTP/WebSocket fallback path all work identically.
        const event = args.event;
        if (!event) return;
        const params = Object.assign({}, args.value || {});
        if (args.target) params._target = args.target;
        if (args.page_loading && window.djust.pageLoading && window.djust.pageLoading.start) {
            try { window.djust.pageLoading.start(); } catch (_) {}
        }
        try {
            if (typeof window.djust.handleEvent === 'function') {
                await window.djust.handleEvent(event, params);
            }
        } finally {
            if (args.page_loading && window.djust.pageLoading && window.djust.pageLoading.stop) {
                try { window.djust.pageLoading.stop(); } catch (_) {}
            }
        }
    }

    const COMMAND_TABLE = {
        show: execShow,
        hide: execHide,
        toggle: execToggle,
        add_class: execAddClass,
        addClass: execAddClass,          // alias for chains built client-side
        remove_class: execRemoveClass,
        removeClass: execRemoveClass,
        transition: execTransition,
        set_attr: execSetAttr,
        setAttr: execSetAttr,
        remove_attr: execRemoveAttr,
        removeAttr: execRemoveAttr,
        focus: execFocus,
        dispatch: execDispatch,
        push: execPush,
    };

    // ------------------------------------------------------------------------
    // Chain execution
    // ------------------------------------------------------------------------

    /**
     * Execute a parsed op list against the event origin element.
     * ops: array of [opName, args] tuples.
     */
    async function executeOps(ops, originEl) {
        if (!Array.isArray(ops)) return;
        for (const entry of ops) {
            if (!Array.isArray(entry) || entry.length < 1) continue;
            const [opName, args] = entry;
            // eslint-disable-next-line security/detect-object-injection
            const fn = COMMAND_TABLE[opName];
            if (!fn) {
                if (globalThis.djustDebug) console.log('[js-commands] unknown op', opName);
                continue;
            }
            try {
                // Some ops are async (push); others are sync. Await handles both.
                await fn(args || {}, originEl);
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] op failed', opName, err);
            }
        }
    }

    /**
     * Parse an attribute value into an op list if it looks like a JSON array.
     * Returns null if the value is a plain event name (existing behavior).
     */
    function parseCommandValue(value) {
        if (typeof value !== 'string') return null;
        const trimmed = value.trim();
        if (!trimmed.startsWith('[')) return null;
        try {
            const parsed = JSON.parse(trimmed);
            if (Array.isArray(parsed) && parsed.length > 0 && Array.isArray(parsed[0])) {
                return parsed;
            }
        } catch (_) {
            return null;
        }
        return null;
    }

    /**
     * Public entry point for the event-binding layer: given an attribute
     * value and the element that fired the event, either execute a JS
     * Command chain (returning true) or bail out so the caller can fall
     * back to the legacy event-name behavior (returning false).
     */
    async function tryExecuteAttribute(value, originEl) {
        const ops = parseCommandValue(value);
        if (!ops) return false;
        await executeOps(ops, originEl);
        return true;
    }

    // ------------------------------------------------------------------------
    // Fluent chain API (mirrors the Python djust.js.JS helper)
    // ------------------------------------------------------------------------

    function JSChain(ops) {
        this.ops = ops ? ops.slice() : [];
    }

    function _chainAdd(chain, op, args) {
        const next = new JSChain(chain.ops);
        next.ops.push([op, args || {}]);
        return next;
    }

    JSChain.prototype.show = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'show', args);
    };

    JSChain.prototype.hide = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'hide', args);
    };

    JSChain.prototype.toggle = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'toggle', args);
    };

    JSChain.prototype.addClass = function(names, options) {
        const args = Object.assign({ names: names }, options || {});
        return _chainAdd(this, 'add_class', args);
    };

    JSChain.prototype.removeClass = function(names, options) {
        const args = Object.assign({ names: names }, options || {});
        return _chainAdd(this, 'remove_class', args);
    };

    JSChain.prototype.transition = function(names, options) {
        const args = Object.assign({ names: names, time: 200 }, options || {});
        return _chainAdd(this, 'transition', args);
    };

    JSChain.prototype.setAttr = function(name, value, options) {
        const args = Object.assign({ attr: [name, value] }, options || {});
        return _chainAdd(this, 'set_attr', args);
    };

    JSChain.prototype.removeAttr = function(name, options) {
        const args = Object.assign({ attr: name }, options || {});
        return _chainAdd(this, 'remove_attr', args);
    };

    JSChain.prototype.focus = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'focus', args);
    };

    JSChain.prototype.dispatch = function(event, options) {
        const args = Object.assign({ event: event, bubbles: true }, options || {});
        return _chainAdd(this, 'dispatch', args);
    };

    JSChain.prototype.push = function(event, options) {
        const args = Object.assign({ event: event }, options || {});
        return _chainAdd(this, 'push', args);
    };

    /**
     * Run the chain against ``originEl`` (or the document body if omitted).
     * Returns a promise that resolves when every op is complete — relevant
     * because `push` round-trips to the server.
     */
    JSChain.prototype.exec = async function(originEl) {
        return executeOps(this.ops, originEl || document.body);
    };

    JSChain.prototype.toString = function() {
        return JSON.stringify(this.ops);
    };

    // ------------------------------------------------------------------------
    // Factory: djust.js.show(...) starts a new chain; djust.js.chain() exposes
    // an empty chain for hooks that want to build one up.
    // ------------------------------------------------------------------------

    const factory = {
        chain: function() { return new JSChain(); },
        show: function(selector, options) { return new JSChain().show(selector, options); },
        hide: function(selector, options) { return new JSChain().hide(selector, options); },
        toggle: function(selector, options) { return new JSChain().toggle(selector, options); },
        addClass: function(names, options) { return new JSChain().addClass(names, options); },
        removeClass: function(names, options) { return new JSChain().removeClass(names, options); },
        transition: function(names, options) { return new JSChain().transition(names, options); },
        setAttr: function(name, value, options) { return new JSChain().setAttr(name, value, options); },
        removeAttr: function(name, options) { return new JSChain().removeAttr(name, options); },
        focus: function(selector, options) { return new JSChain().focus(selector, options); },
        dispatch: function(event, options) { return new JSChain().dispatch(event, options); },
        push: function(event, options) { return new JSChain().push(event, options); },

        // Internal hooks used by the event-binding layer:
        _executeOps: executeOps,
        _tryExecuteAttribute: tryExecuteAttribute,
        _parseCommandValue: parseCommandValue,
        _JSChain: JSChain,
    };

    window.djust.js = factory;
})();
