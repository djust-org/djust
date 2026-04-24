// ============================================================================
// {% dj_activity %} — activity visibility tracker (v0.7.0)
// ============================================================================
// React 19.2 <Activity> parity. The server is the canonical source of
// visibility; this module observes the DOM for ``[data-djust-activity]``
// wrappers, tracks their current ``hidden`` state, and dispatches a
// bubbling ``djust:activity-shown`` CustomEvent when an activity flips
// from hidden → visible. Consumers (user code, the event-dispatch gate in
// 11-event-handler.js, and the VDOM-patch gate in 12-vdom-patch.js) read
// this state via ``window.djust.activityVisible(name)``.
//
// No autocomplete-style polling loop: one MutationObserver filtered to
// the ``hidden`` attribute on activity roots keeps overhead low. A
// re-scan runs on ``djust:morph-complete`` so activities added by VDOM
// patches get tracked.

(function () {
    // name → { node, visible, eager } — authoritative in-memory mirror of
    // every activity wrapper currently in the document. Keyed by the
    // ``data-djust-activity`` attribute; when the same name appears more
    // than once (a case the A071 system check flags at build time), the
    // LAST-scanned node wins so the map size matches DOM reality.
    const _activities = new Map();

    function _isHidden(node) {
        // Treat explicit ``hidden`` attribute OR computed display:none as
        // hidden. The attribute is the primary signal (that's what the
        // template tag emits); the computed check is defensive for apps
        // that override with CSS.
        if (!node || node.nodeType !== 1) return false;
        if (node.hasAttribute('hidden')) return true;
        try {
            const cs = node.ownerDocument.defaultView.getComputedStyle(node);
            if (cs && cs.display === 'none') return true;
        } catch (_) {
            // getComputedStyle can throw in exotic environments — fall
            // through to the attribute check result.
        }
        return false;
    }

    function _scan(root) {
        const scope = root || document;
        const nodes = scope.querySelectorAll
            ? scope.querySelectorAll('[data-djust-activity]')
            : [];
        // Build a fresh view from what's actually in the DOM so stale
        // entries don't linger after a VDOM patch removes a wrapper.
        const fresh = new Map();
        for (const node of nodes) {
            const name = node.getAttribute('data-djust-activity') || '';
            if (!name) continue;
            fresh.set(name, {
                node: node,
                visible: !_isHidden(node),
                eager: node.getAttribute('data-djust-eager') === 'true',
            });
        }
        // Replace contents of the live map so external references still
        // resolve the latest state.
        _activities.clear();
        for (const [k, v] of fresh) _activities.set(k, v);
        return _activities;
    }

    function activityVisible(name) {
        // Accept a falsy ``name`` as "no activity context" — behave
        // identically to an unknown name (defaults to visible so callers
        // don't suppress legitimate events against a typo).
        if (!name) return true;
        const entry = _activities.get(name);
        if (!entry) return true;
        return entry.visible !== false;
    }

    function _dispatchShown(name, node) {
        try {
            const ev = new CustomEvent('djust:activity-shown', {
                bubbles: true,
                detail: { name: name, node: node },
            });
            node.dispatchEvent(ev);
            if (globalThis.djustDebug) {
                console.log('[djust:activity] shown:', name);
            }
        } catch (_) {
            // CustomEvent may not be available in some legacy envs; safe
            // to swallow — the state map stays in sync regardless.
        }
    }

    // One observer watches the whole document for ``hidden`` attribute
    // flips on nodes carrying ``data-djust-activity``. attributeOldValue
    // lets us distinguish a hidden→visible flip (dispatch) from a
    // visible→hidden flip (state update only, no event).
    let _observer = null;

    function _installObserver() {
        if (_observer || typeof MutationObserver === 'undefined') return;
        _observer = new MutationObserver(function (mutations) {
            for (const m of mutations) {
                if (m.type !== 'attributes' || m.attributeName !== 'hidden') continue;
                const node = m.target;
                if (!node || !node.getAttribute) continue;
                const name = node.getAttribute('data-djust-activity');
                if (!name) continue;
                const wasHidden = m.oldValue !== null; // oldValue === null iff attr was absent
                const isHidden = node.hasAttribute('hidden');
                const entry = _activities.get(name) || {
                    node: node,
                    visible: true,
                    eager: node.getAttribute('data-djust-eager') === 'true',
                };
                entry.node = node;
                entry.visible = !isHidden;
                _activities.set(name, entry);
                if (wasHidden && !isHidden) {
                    _dispatchShown(name, node);
                }
            }
        });
        _observer.observe(document.body || document.documentElement, {
            attributes: true,
            attributeOldValue: true,
            attributeFilter: ['hidden'],
            subtree: true,
        });
    }

    // Initial scan + observer install. On document-ready (or immediately
    // if we're already past DOMContentLoaded).
    function _boot() {
        _scan(document);
        _installObserver();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _boot, { once: true });
    } else {
        _boot();
    }

    // Re-scan when VDOM patches add / remove activity wrappers. The morph
    // pipeline emits ``djust:morph-complete`` after every successful
    // patch batch; a lightweight re-scan catches newly-introduced
    // wrappers and drops entries for removed ones.
    window.addEventListener('djust:morph-complete', function () {
        _scan(document);
    });

    // Expose to the namespace. The top-level ``activityVisible`` helper
    // is what user code, the event-handler gate, and the VDOM-patch gate
    // all call; ``_activity`` carries the implementation-detail handles
    // for tests.
    window.djust = window.djust || {};
    window.djust._activity = {
        _activities: _activities,
        _scan: _scan,
        _isHidden: _isHidden,
        activityVisible: activityVisible,
    };
    window.djust.activityVisible = activityVisible;
})();
