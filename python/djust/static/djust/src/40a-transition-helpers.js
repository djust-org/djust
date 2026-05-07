
// transition-helpers — shared CSS-timing helpers for dj-transition + dj-remove
//
// Both `41-dj-transition.js` and `42-dj-remove.js` need to inspect the
// computed `transition-property`, `transition-duration`, and
// `transition-delay` of an element to size their fallback timeouts and
// count expected `transitionend` events. They previously kept identical
// copies of these helpers because the source files are concatenated as
// separate modules (no cross-file imports). The bundle ended up with
// duplicate top-level function declarations — flagged by CodeQL
// (`js/duplicate-function`) — so this module hosts the canonical copy.
//
// Because the build is a concat (`scripts/build-client.sh:74`,
// `cat src/[0-9]*.js > client.js`), this file only needs to sort
// LEXICOGRAPHICALLY before its consumers. `40a-` slots between
// `40-dj-layout.js` and `41-dj-transition.js`.
//
// Closes #1360.

function _parseTimeMs(s) {
    // CSS time tokens: "550ms", "0.55s", "0s". Returns 0 on parse failure.
    const t = (s || '').trim();
    if (!t) return 0;
    if (t.endsWith('ms')) return parseFloat(t) || 0;
    if (t.endsWith('s')) return (parseFloat(t) || 0) * 1000;
    return 0;
}

function _computeTransitionTiming(el) {
    // Inspect `transition-property`, `transition-duration`, `transition-delay`
    // and return {maxMs, propsCount}. CSS spec: when *-duration / *-delay
    // have fewer comma-separated values than -property, they cycle. When
    // they have more, extras are ignored.
    const cs = (typeof getComputedStyle === 'function') ? getComputedStyle(el) : null;
    if (!cs) return { maxMs: 0, propsCount: 0 };
    const props = (cs.transitionProperty || '')
        .split(',').map(s => s.trim()).filter(s => s && s !== 'none');
    const durations = (cs.transitionDuration || '')
        .split(',').map(s => _parseTimeMs(s));
    const delays = (cs.transitionDelay || '')
        .split(',').map(s => _parseTimeMs(s));
    if (props.length === 0) return { maxMs: 0, propsCount: 0 };
    let maxMs = 0;
    for (let i = 0; i < props.length; i++) {
        const dur = durations[i % durations.length] || 0;
        const del = delays[i % delays.length] || 0;
        const total = dur + del;
        if (total > maxMs) maxMs = total;
    }
    return { maxMs: maxMs, propsCount: props.length };
}
