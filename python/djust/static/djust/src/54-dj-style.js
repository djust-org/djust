
// dj-style — free-form style values under a strict CSP (#3979)
//
// A `style-src` without 'unsafe-inline' refuses every `style="..."` attribute,
// so a component writes a value no class can stand for (a column width, a
// custom colour, an item height) as `data-dj-style-<property>="<value>"`.
// The server checks the value (djust.components.utils.style_attrs); this
// module applies it with `el.style.setProperty`, which a strict CSP allows
// and which cannot add a second declaration. Only the properties listed here
// are applied (the same list as the server's STYLE_PROPERTIES, pinned equal
// by a test); any other `data-dj-style-*` is ignored. Removing the attribute
// removes the property.
//
// `data-dj-css="skeleton"` / `"shimmer"` asks for a small shared rule set
// that used to be an inline <style> block; it is added once, through the
// CSSOM (a constructed stylesheet). Its selectors have zero specificity
// (`:where`), so any rule of the page's own stylesheet overrides them.
//
// Lifted from 35-dj-dialog.js / 41-dj-transition.js: one document observer
// filtered to these attributes, plus a pass over every added subtree, so a
// first paint, a patch, a morph, a navigation and a lazily mounted child are
// all covered. `style` is watched too: a morph that drops the client-set
// `style` attribute (the server's HTML has none) gets the values back.

const _DJ_STYLE_PROPS = ('width min-width max-width height min-height max-height top left inset ' +
    'margin margin-right padding-left gap font-size --dj-masonry-gap background-color color ' +
    'border-top-color --dj-calendar-event-color --dj-collab-sel-color z-index -webkit-line-clamp ' +
    '--dj-masonry-columns --dj-data-card-grid-cols flex aspect-ratio animation-delay grid-column ' +
    'grid-row grid-template-columns grid-auto-rows position display overflow overflow-y ' +
    '-webkit-box-orient font-weight').split(' ');
const _DJ_STYLE_ATTRS = _DJ_STYLE_PROPS.map(function (p) { return 'data-dj-style-' + p; });
const _DJ_STYLE_SEL = _DJ_STYLE_ATTRS.map(function (a) { return '[' + a + ']'; }).join(',');

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
            // No constructed stylesheets (an old browser): a <style> element,
            // which only a page without a strict CSP will apply.
            const el = document.createElement('style');
            el.id = 'djust-css';
            document.head.appendChild(el);
            _djCssSheet = el.sheet;
        }
    }
    // eslint-disable-next-line security/detect-object-injection
    _DJ_CSS_RULES[name].forEach(function (rule) {
        try { _djCssSheet.insertRule(rule, _djCssSheet.cssRules.length); } catch (_e) { /* unsupported rule */ }
    });
}

function _djStyleSet(el, attr, onlyMissing) {
    const prop = attr.slice(14);
    const value = el.getAttribute(attr);
    if (value === null) el.style.removeProperty(prop);
    else if (!onlyMissing || !el.style.getPropertyValue(prop)) el.style.setProperty(prop, value);
}

function _djStyleApply(el, onlyMissing) {
    _DJ_STYLE_ATTRS.forEach(function (a) {
        if (el.hasAttribute(a)) _djStyleSet(el, a, onlyMissing);
    });
}

function _djStyleScan(root) {
    if (root.nodeType !== 1) return;
    [root].concat(Array.from(root.querySelectorAll(_DJ_STYLE_SEL + ',[data-dj-css]'))).forEach(function (el) {
        _djStyleApply(el, false);
        const css = el.getAttribute('data-dj-css');
        if (css) _djCss(css);
    });
}

function _installDjStyleObserver() {
    _djStyleScan(document.documentElement);
    new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'childList') {
                m.addedNodes.forEach(_djStyleScan);
            } else if (m.attributeName === 'style') {
                if (m.target.matches(_DJ_STYLE_SEL)) _djStyleApply(m.target, true);
            } else if (m.attributeName === 'data-dj-css') {
                _djStyleScan(m.target);
            } else {
                _djStyleSet(m.target, m.attributeName, false);
            }
        });
    }).observe(document.documentElement, {
        attributes: true,
        attributeFilter: _DJ_STYLE_ATTRS.concat(['style', 'data-dj-css']),
        subtree: true,
        childList: true,
    });
}

// Installed at once, not on DOMContentLoaded: the observer sees what the
// parser adds from here on, so the values are set before the init pass (and
// the hooks it mounts) runs.
if (typeof document !== 'undefined') _installDjStyleObserver();

globalThis.djust = globalThis.djust || {};
globalThis.djust.djStyle = { props: _DJ_STYLE_PROPS, scan: _djStyleScan };
