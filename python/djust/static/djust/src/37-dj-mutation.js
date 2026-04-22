
// dj-mutation — declarative DOM mutation → server event (v0.6.0)
//
// Fires a server event when attributes or children of the marked element
// change. Replaces the custom dj-hook authors had to write to bridge
// third-party widgets (charts, maps, rich-text editors) that mutate the
// DOM outside djust's control.
//
// Usage:
//   <div dj-mutation="handle_change" dj-mutation-attr="class,style">
//   <div dj-mutation="on_children_update">
//   <div dj-mutation="on_change" dj-mutation-debounce="300">
//
// Semantics:
//   - If dj-mutation-attr="a,b,c" is set, observe attribute changes on
//     those attrs and dispatch {mutation: "attributes", attrs: [...]}.
//   - Otherwise observe childList changes and dispatch
//     {mutation: "childList", added: N, removed: N}.
//   - dj-mutation-debounce (ms, default 150) coalesces bursts so a chart
//     library re-rendering 50 times in 10ms produces one server event.
//
// Dispatch path:
//   1. A local cancelable `dj-mutation-fire` CustomEvent bubbles from
//      the element, carrying detail={handler, payload}. Application
//      code can preventDefault() to short-circuit the server call.
//   2. If not cancelled, the payload is forwarded to the server via
//      the standard djust event pipeline (window.djust.handleEvent),
//      invoking the @event_handler method named in dj-mutation=.
//
// Don't list sensitive attributes (e.g. password field `value`) in
// dj-mutation-attr: the attribute name is included in the server
// payload, which is noisy for audit logs.

const _djMutationObservers = new WeakMap();

function _parseAttrList(raw) {
    return (raw || '')
        .split(',')
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; });
}

function _installDjMutationFor(el) {
    if (_djMutationObservers.has(el)) return;
    const handlerName = el.getAttribute('dj-mutation');
    if (!handlerName) return;

    const attrList = _parseAttrList(el.getAttribute('dj-mutation-attr'));
    const rawDebounce = parseInt(el.getAttribute('dj-mutation-debounce') || '150', 10);
    const debounceMs = Number.isFinite(rawDebounce) && rawDebounce >= 0 ? rawDebounce : 150;

    let timer = null;
    let pending = null;

    function _dispatch() {
        const payload = pending;
        pending = null;
        timer = null;
        if (!payload) return;
        // Local CustomEvent first — lets application code intercept or
        // short-circuit via preventDefault before the server roundtrip.
        const ev = new CustomEvent('dj-mutation-fire', {
            bubbles: true,
            cancelable: true,
            detail: { handler: handlerName, payload: payload },
        });
        const proceed = el.dispatchEvent(ev);
        if (!proceed) return;
        // Route to the standard djust event pipeline so the server-side
        // handler named in dj-mutation="..." actually runs.
        if (globalThis.djust && typeof globalThis.djust.handleEvent === 'function') {
            globalThis.djust.handleEvent(handlerName, payload);
        }
    }

    const observer = new MutationObserver(function (mutations) {
        let attrsChanged = null;
        let added = 0, removed = 0;
        mutations.forEach(function (m) {
            if (m.type === 'attributes') {
                attrsChanged = attrsChanged || new Set();
                attrsChanged.add(m.attributeName);
            } else if (m.type === 'childList') {
                added += m.addedNodes.length;
                removed += m.removedNodes.length;
            }
        });
        if (attrsChanged) {
            pending = { mutation: 'attributes', attrs: Array.from(attrsChanged) };
        } else if (added || removed) {
            pending = { mutation: 'childList', added: added, removed: removed };
        }
        if (timer) clearTimeout(timer);
        timer = setTimeout(_dispatch, debounceMs);
    });

    const opts = attrList.length > 0
        ? { attributes: true, attributeFilter: attrList }
        : { childList: true };
    observer.observe(el, opts);
    _djMutationObservers.set(el, { observer: observer, clear: function () {
        if (timer) { clearTimeout(timer); timer = null; pending = null; }
    }});
}

function _tearDownDjMutation(el) {
    const entry = _djMutationObservers.get(el);
    if (entry) {
        entry.observer.disconnect();
        // Cancel any in-flight debounced dispatch so a setTimeout
        // doesn't fire on a detached element after removal.
        entry.clear();
        _djMutationObservers.delete(el);
    }
}

function _installDjMutationObserver() {
    document.querySelectorAll('[dj-mutation]').forEach(_installDjMutationFor);

    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            m.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (node.hasAttribute && node.hasAttribute('dj-mutation')) {
                    _installDjMutationFor(node);
                }
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-mutation]').forEach(_installDjMutationFor);
                }
            });
            m.removedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (_djMutationObservers.has(node)) _tearDownDjMutation(node);
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-mutation]').forEach(_tearDownDjMutation);
                }
            });
        });
    });
    rootObserver.observe(document.documentElement, { subtree: true, childList: true });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjMutationObserver);
    } else {
        _installDjMutationObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djMutation = {
    _installDjMutationFor,
    _tearDownDjMutation,
};
