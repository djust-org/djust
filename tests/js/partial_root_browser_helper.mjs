// Replay a real server patch against the browser-parsed HTTP page.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from 'jsdom';
import { normalizeSignificantTree } from './_significant_tree.js';

const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(fixture.page, { runScripts: 'outside-only', url: 'http://localhost/' });
const { window } = dom;
if (!window.CSS) window.CSS = {};
if (!window.CSS.escape) window.CSS.escape = (s) => String(s).replace(/([^\w-])/g, '\\$1');
const errors = [];
window.console = {
    log() {}, info() {}, debug() {},
    warn: (...args) => errors.push(args.join(' ')),
    error: (...args) => errors.push(args.join(' ')),
};
window.DJUST_USE_WEBSOCKET = false;
window.WebSocket = class MockWebSocket {
    static OPEN = 1;
    constructor() { this.readyState = 1; }
    send() {}
    close() {}
};
window.eval(fs.readFileSync('./python/djust/static/djust/client.js', 'utf8'));
if (fixture.requireReplacement) {
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
}

// Mirrors findPageViewContainer() in the real websocket client. Some direct
// render tests omit dj-view stamping; fall back to the client's dj-root path.
const candidateSelector = '[dj-view]:not(html):not(head):not([dj-sticky-root]):not([data-djust-embedded]):not([dj-lazy]):not([data-djust-target])';
const root = window.document.querySelector(candidateSelector)
    || window.document.querySelector('[dj-root]:not(html):not(head)');
assert.ok(root, 'browser has no live root');
assert.match(root.textContent, /before/, 'browser selected a different root');
// Mirror the skipMountHtml branch's WS mount morph before applying events:
// the initial HTTP document intentionally does not carry server dj-ids.
const mount = window.djust._parseRootHtmlLive(fixture.mount, root);
window.djust.morphChildren(root, mount);
const originalView = root.getAttribute('dj-view');
await window.djust.applyPatches(fixture.patches, root);
const activeRoot = window.document.querySelector(candidateSelector)
    || window.document.querySelector('[dj-root]:not(html):not(head)');
assert.ok(activeRoot, 'browser lost the live root after patch');
if (fixture.requireReplacement) {
    assert.notEqual(activeRoot, root, 'expected the root element to be replaced');
    assert.equal(activeRoot.tagName.toLowerCase(), 'section');
    assert.equal(activeRoot.getAttribute('dj-view'), originalView, 'root replacement lost mount identity');
    const events = [];
    assert.ok(window.djust.liveViewInstance, 'client transport did not initialize');
    window.djust.liveViewInstance.sendEvent = (name, params, element) => {
        events.push({ name, view: element.closest('[dj-view]')?.getAttribute('dj-view') });
        return Promise.resolve();
    };
    window.djust.reinitAfterDOMUpdate(activeRoot);
    activeRoot.querySelector('[dj-click="advance"]').click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(events, [{ name: 'advance', view: originalView }], 'event lost its view route');
    await window.djust.applyPatches(fixture.nextPatches, activeRoot);
}
const expectedContainer = window.document.createElement('div');
expectedContainer.innerHTML = fixture.nextExpected || fixture.expected;
const expectedRoot = expectedContainer.querySelector('[dj-root]');
assert.ok(expectedRoot, 'fresh render has no root');
assert.equal(activeRoot.textContent, expectedRoot.textContent);
assert.deepEqual(normalizeSignificantTree(activeRoot, window), normalizeSignificantTree(expectedRoot, window));
assert.deepEqual(errors, []);
dom.window.close();
