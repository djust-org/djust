/**
 * A stricter tree comparison than `normalizeSignificantTree` (which records only
 * tag names, `dj-key`, whether a `dj-id` exists, and text). This one records
 * EVERY attribute, `dj-id` values included, so a row that ends up carrying
 * another row's `id`, `data-*` payload, class or `dj-id` fails the comparison
 * (#3639: `dj-update="prepend"` once left `<li id=rows-2 data-pk=...>` twice and
 * the first row with the second's attributes, while the tag/text shape was right).
 *
 * A separate helper on purpose: other suites rely on the looser one.
 */

export function strictTree(node, window) {
    const getSig = window.djust.getSignificantChildren;

    function norm(n) {
        if (n.nodeType === 1) {
            const attrs = {};
            for (const name of n.getAttributeNames().sort()) attrs[name] = n.getAttribute(name);
            return { tag: n.tagName.toLowerCase(), attrs, children: getSig(n).map(norm) };
        }
        if (n.nodeType === 8) return { comment: (n.textContent || '').trim() };
        return { text: (n.textContent || '').trim() };
    }

    return norm(node);
}

/** `dj-id` values that appear on more than one element under `root`. */
export function duplicateDjIds(root) {
    const seen = new Map();
    for (const el of [root, ...root.querySelectorAll('[dj-id]')]) {
        const id = el.getAttribute && el.getAttribute('dj-id');
        if (id !== null && id !== undefined) seen.set(id, (seen.get(id) || 0) + 1);
    }
    return [...seen].filter(([, n]) => n > 1).map(([id]) => id);
}
