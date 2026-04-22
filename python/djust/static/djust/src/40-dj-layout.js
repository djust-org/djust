
// dj-layout — runtime layout switching (v0.6.0)
//
// Handles the {"type": "layout", "path": ..., "html": ...} WebSocket
// frame emitted by LayoutMixin.set_layout(). Swaps the document body
// with the new layout while physically moving the live
// [dj-root] / [data-djust-root] element into the new layout's
// root-shaped slot — preserving all inner LiveView state (form values,
// scroll position, focused element, dj-hook bookkeeping, third-party
// widget instances hanging off live nodes).
//
// Use case: toggling between admin / public layouts, fullscreen mode
// for editors, minimal layout during onboarding.
//
// Known limitations (v1):
//   - <head> tags are NOT merged. If the new layout needs different
//     stylesheets or scripts, add them to the original layout's <head>.
//   - Dispatches djust:layout-changed on document for app-level hooks.

function _findRoot(scope) {
    return scope.querySelector('[data-djust-root]') || scope.querySelector('[dj-root]');
}

function _applyLayout(payload) {
    const html = payload && payload.html;
    if (!html || typeof html !== 'string') {
        if (globalThis.djustDebug) console.warn('[djust:layout] empty html payload; ignoring');
        return;
    }
    let newDoc;
    try {
        newDoc = new DOMParser().parseFromString(html, 'text/html');
    } catch (_e) {
        console.warn('[djust:layout] failed to parse incoming layout HTML');
        return;
    }
    const newRoot = _findRoot(newDoc);
    const currentRoot = _findRoot(document);
    if (!newRoot || !currentRoot) {
        console.warn(
            '[djust:layout] could not locate dj-root / data-djust-root in ' +
            (newRoot ? 'current document' : 'incoming layout') + '; ignoring swap'
        );
        return;
    }
    // Physically move the current (live) root into the new layout's body
    // in place of the new layout's root-shaped placeholder. This
    // preserves every inner DOM node, including focused elements and
    // third-party-widget references.
    newRoot.parentNode.replaceChild(currentRoot, newRoot);
    // Swap the live body with the reconstructed body. document.body
    // adoption is automatic across DOMParser documents in modern
    // browsers.
    const newBody = newDoc.body;
    if (newBody) {
        document.body.replaceWith(newBody);
    }
    document.dispatchEvent(new CustomEvent('djust:layout-changed', {
        detail: { path: payload.path || null },
    }));
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djLayout = {
    applyLayout: _applyLayout,
    _findRoot,
};
