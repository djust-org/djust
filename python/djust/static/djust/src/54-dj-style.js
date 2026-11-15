// dj-style — free-form style values under a strict CSP (#3979)
//
// By default a component writes a free-form value (a column width, a custom
// colour, an item height) as an inline `style`, and this module does nothing.
// A `style-src` without 'unsafe-inline' refuses every `style="..."`, so with
// LIVEVIEW_CONFIG["csp_strict_styles"] the server writes
// `data-dj-style-<property>="<value>"` (checked by
// djust.components.utils.style_attrs), marks the element `data-dj-style` and
// the page `<meta name="djust-styles" content="attrs">`. Only on such a page
// this module applies the values with `el.style.setProperty`, which a strict
// CSP allows and which cannot add a second declaration; on any other page
// injected `data-dj-style-*` attributes are inert. It does not trust the
// attribute text either: only the properties listed here (the server's
// STYLE_PROPERTY_NAMES, pinned equal by a test), never a value that could load
// or reach anything (`url(`, `image`, `attr(`, `@`, `\`, comments, `;{}`,
// quotes, `expression`), and a custom property only of its kind. Removing the
// attribute removes the property.
//
// `data-dj-css="skeleton"` / `"shimmer"` asks for a small shared rule set
// that is an inline <style> block in the default mode; it is added once,
// through the CSSOM (a constructed stylesheet). Its selectors have zero
// specificity (`:where`), so any rule of the page's own stylesheet wins.
//
// Lifted from 35-dj-dialog.js / 41-dj-transition.js: one document observer
// filtered to these attributes, plus a pass over every added subtree (one
// marker selector), so a patch, a morph, a navigation and a lazily mounted
// child are covered. `style` is watched too: a morph that drops the
// client-set `style` attribute (the server's HTML has none) gets the values
// back.

const _DJ_STYLE_PROPS = ('width min-width max-width height min-height max-height top left ' +
    'margin-right padding-left gap font-size --dj-masonry-gap background-color color ' +
    'border-top-color --dj-calendar-event-color --dj-collab-sel-color z-index -webkit-line-clamp ' +
    '--dj-masonry-columns --dj-data-card-grid-cols flex aspect-ratio animation-delay grid-column ' +
    'grid-row grid-template-columns grid-auto-rows position display overflow overflow-y ' +
    '-webkit-box-orient font-weight').split(' ');
const _DJ_STYLE_ATTRS = _DJ_STYLE_PROPS.map(function (p) { return 'data-dj-style-' + p; });
const _DJ_STYLE_ALLOWED = new Set(_DJ_STYLE_ATTRS);
// One marker attribute on every element this module serves (a `data-dj-css`
// one included): a single-attribute selector.
const _DJ_STYLE_SEL = '[data-dj-style]';
// Nothing that loads, reads or ends a declaration, whatever the property.
const _DJ_STYLE_DENY = /url\(|image|attr\(|expression|[\\@;{}"'`<>]|\/\*/i;
const _DJ_INT = /^\d{1,4}$/;
const _DJ_LEN = /^[\d.]{1,12}(px|rem|em|%|ch|vh|vw|)$/;
const _DJ_COLOR = /^(#[\da-f]{3,8}|[a-z]{3,20}|(rgba?|hsla?)\([\w\s.,%/()-]*\)|var\(--[\w-]+\))$/i;
// A positive check by kind where the property needs one (custom properties
// reach shorthands in the stylesheet; position and stacking stay bounded).
const _DJ_STYLE_KIND = new Map([
    ['position', /^(sticky|relative)$/],
    ['z-index', /^-?\d{1,4}$/],
    ['--dj-calendar-event-color', _DJ_COLOR],
    ['--dj-collab-sel-color', _DJ_COLOR],
    ['--dj-masonry-columns', _DJ_INT],
    ['--dj-data-card-grid-cols', _DJ_INT],
    ['--dj-masonry-gap', _DJ_LEN],
]);

function _djStyleOk(prop, value) {
    const kind = _DJ_STYLE_KIND.get(prop);
    return !_DJ_STYLE_DENY.test(value) && (!kind || kind.test(value.trim()));
}

const _DJ_CSS_RULES = {
    skeleton: [
        '@keyframes djust-skeleton-shimmer{from{background-position:200% 0}to{background-position:-200% 0}}',
        ':where(.djust-skeleton){display:inline-block;background:linear-gradient(90deg,#e5e7eb 25%,#f3f4f6 50%,#e5e7eb 75%);' +
            'background-size:200% 100%;animation:djust-skeleton-shimmer 1.5s ease-in-out infinite;' +
            'border-radius:4px;vertical-align:middle}',
        ':where(.djust-skeleton-line){display:block;margin-bottom:0.5em}',
        ':where(.djust-skeleton-circle){border-radius:50%}',
        '@media (prefers-reduced-motion: reduce){:where(.djust-skeleton){animation:none}}',
    ],
    shimmer: ['@keyframes shimmer{0%{background-position:-100% 0}100%{background-position:100% 0}}'],
};
const _djCssDone = new Set();
let _djCssSheet = null;

function _djCss(name) {
    if (_djCssDone.has(name) || !Object.prototype.hasOwnProperty.call(_DJ_CSS_RULES, name)) return;
    _djCssDone.add(name);
    if (!_djCssSheet) {
        try {
            const sheet = new CSSStyleSheet();
            document.adoptedStyleSheets = document.adoptedStyleSheets.concat([sheet]);
            _djCssSheet = sheet;
        } catch (_e) {
            return; // no constructed stylesheets (an old browser): no shimmer
        }
    }
    // eslint-disable-next-line security/detect-object-injection
    _DJ_CSS_RULES[name].forEach(function (rule) {
        try { _djCssSheet.insertRule(rule, _djCssSheet.cssRules.length); } catch (_e) { /* unsupported rule */ }
    });
}

// The elements whose `style` this module wrote in the current pass: their
// `style` records are its own (anything else is handled).
let _djWrote = new Set();

function _djStyleSet(el, attr, onlyMissing) {
    const prop = attr.slice(14);
    const value = el.getAttribute(attr);
    _djWrote.add(el);
    if (value === null || !_djStyleOk(prop, value)) el.style.removeProperty(prop);
    else if (!onlyMissing || !el.style.getPropertyValue(prop)) el.style.setProperty(prop, value);
}

function _djStyleApply(el, onlyMissing) {
    const attrs = el.attributes;
    for (let i = 0; i < attrs.length; i++) {
        // eslint-disable-next-line security/detect-object-injection
        if (_DJ_STYLE_ALLOWED.has(attrs[i].name)) _djStyleSet(el, attrs[i].name, onlyMissing);
    }
}

function _djStyleScan(root) {
    if (root.nodeType !== 1) return;
    const found = Array.from(root.querySelectorAll(_DJ_STYLE_SEL));
    if (root.matches(_DJ_STYLE_SEL)) found.push(root);
    found.forEach(function (el) {
        _djStyleApply(el, false);
        const css = el.getAttribute('data-dj-css');
        if (css) _djCss(css);
    });
}

function _djStyleHandle(mutations) {
    // A record that adds many nodes (an `innerHTML` swap) is one scan of
    // its parent, not one per node.
    const parents = new Set();
    mutations.forEach(function (m) {
        if (m.type === 'childList') {
            if (m.addedNodes.length > 8) parents.add(m.target);
            else m.addedNodes.forEach(_djStyleScan);
        } else if (m.attributeName === 'style') {
            const el = m.target;
            if (el.hasAttribute('data-dj-style')) _djStyleApply(el, true);
        } else if (m.attributeName === 'data-dj-css') {
            _djStyleScan(m.target);
        } else {
            _djStyleSet(m.target, m.attributeName, false);
        }
    });
    parents.forEach(_djStyleScan);
}

function _installDjStyleObserver() {
    _djStyleScan(document.documentElement);
    const observer = new MutationObserver(function (mutations) {
        // Records queued while this runs: the module's own `style` writes are
        // dropped; anything else (code reacting synchronously to a write) is
        // handled in a further pass.
        for (let pass = 0; mutations.length && pass < 8; pass++) {
            _djWrote = new Set();
            _djStyleHandle(mutations);
            const wrote = _djWrote;
            mutations = observer.takeRecords().filter(function (m) {
                return !(m.attributeName === 'style' && wrote.has(m.target));
            });
        }
    });
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: _DJ_STYLE_ATTRS.concat(['style', 'data-dj-css']),
        subtree: true,
        childList: true,
    });
}

// The bundle is a `defer`red script: it runs once the document is parsed, so
// the first scan sees the whole page. Installed at once (not on
// DOMContentLoaded) so the values are set before the init pass and the hooks
// it mounts run. Nothing at all unless the page opted in: the meta in the
// server-rendered <head>, read once here (one in the body, which markup a
// page renders could carry, does not count). Strict mode is a project-wide
// setting, so every page of a strict project has it, and live navigation
// keeps the document head.
if (typeof document !== 'undefined' && document.head &&
        document.head.querySelector('meta[name="djust-styles"][content="attrs"]')) {
    _installDjStyleObserver();
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djStyle = { props: _DJ_STYLE_PROPS, scan: _djStyleScan, ok: _djStyleOk };
