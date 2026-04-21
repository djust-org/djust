// ============================================================================
// dj-ignore-attrs — Client-owned attributes skipped by VDOM SetAttr patches
// ============================================================================
//
// Mark HTML attributes as client-owned so VDOM SetAttr patches skip them.
// Essential for browser-native elements (<dialog open>, <details open>) and
// third-party JS libraries that manage attributes the server doesn't know
// about.
//
// Phoenix 1.1 parity: JS.ignore_attributes/1.
//
// Usage (template):
//   <dialog dj-ignore-attrs="open">
//   <div dj-ignore-attrs="data-lib-state, aria-expanded">
//
// Format: comma-separated attribute names, whitespace-tolerant.
// ============================================================================

(function initIgnoreAttrs() {
    globalThis.djust = globalThis.djust || {};

    /**
     * Return true when the element opts out of SetAttr updates for attrName.
     *
     * @param {Element|null} el - DOM element
     * @param {string} attrName - attribute key to check
     * @returns {boolean}
     */
    globalThis.djust.isIgnoredAttr = function(el, attrName) {
        if (!el || typeof el.getAttribute !== 'function') return false;
        const raw = el.getAttribute('dj-ignore-attrs');
        if (!raw) return false;
        // CSV with whitespace tolerance. Exact match on attribute name.
        for (const item of raw.split(',')) {
            if (item.trim() === attrName) return true;
        }
        return false;
    };
})();
