/**
 * `<body>` as the LiveView root (#3302): the client keeps the body children the
 * server did not render (injected scripts, debug UI, extension nodes) wherever
 * it replaces or morphs the container's content.
 *
 * "Foreign" = a direct child element of `<body>` with no `dj-id`, once the
 * server's elements have been stamped (a WS/SSE mount or a content replacement).
 * Before that, and on an HTTP-only page (which never stamps), nothing is
 * foreign by that test, and a first-mount morph keeps unmatched elements.
 *
 * The helpers under test are loaded from the built client bundle.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function load(bodyAttrs, bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body ${bodyAttrs}>${bodyHtml}</body></html>`, {
        runScripts: 'dangerously',
        pretendToBeVisual: true,
    });
    const w = dom.window;
    if (!w.CSS) w.CSS = {};
    if (!w.CSS.escape) w.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    w.eval(clientCode);
    return w;
}

function document_for(s) {
    const d = new JSDOM('<!DOCTYPE html><body></body>').window.document;
    const div = d.createElement('div');
    div.innerHTML = s;
    return div;
}

const ids = (w) => Array.from(w.document.body.children).map((e) => e.id || e.tagName.toLowerCase());

describe('body root helpers', () => {
    it('only a body that is a root has foreign children', () => {
        const w = load('', '<header id="h" dj-id="1"></header><div id="x"></div>');
        w.djust.markBodyStamped();
        expect(w.djust.isForeignBodyChild(w.document.getElementById('x'))).toBe(false);
        const r = load('dj-root', '<header id="h" dj-id="1"></header><div id="x"></div>');
        r.djust.markBodyStamped();
        expect(r.djust.isForeignBodyChild(r.document.getElementById('x'))).toBe(true);
        expect(r.djust.isForeignBodyChild(r.document.getElementById('h'))).toBe(false);
    });

    it('a root inside the body wins: the page is not a body root (the T005 shape keeps its behaviour)', () => {
        for (const inner of ['<div dj-root></div>', '<div dj-view="a.B"></div>']) {
            const r = load('dj-view="p.P"', '<header id="h" dj-id="1"></header>' + inner + '<div id="x"></div>');
            r.djust.markBodyStamped();
            expect(r.djust.isForeignBodyChild(r.document.getElementById('x'))).toBe(false);
        }
    });

    it("other views' containers do not stop the body being the root", () => {
        const body =
            '<header id="h" dj-id="1"></header>' +
            '<div dj-view="a.W" dj-lazy="idle"></div>' +
            '<div dj-view="a.W" data-djust-target="w2"></div>' +
            '<div dj-view data-djust-embedded="c1"><div dj-root></div></div>' +
            '<div id="x"></div>';
        const r = load('dj-root', body);
        r.djust.markBodyStamped();
        expect(r.djust.isForeignBodyChild(r.document.getElementById('x'))).toBe(true);
    });

    it('nothing is foreign before the first mount stamps the page (HTTP-only too)', () => {
        const r = load('dj-root', '<header id="h"></header><div id="x"></div>');
        expect(r.djust.isForeignBodyChild(r.document.getElementById('x'))).toBe(false);
        // A patch inserting one stamped node must not turn every other node foreign.
        const stamped = r.document.createElement('aside');
        stamped.setAttribute('dj-id', 'b');
        r.document.body.appendChild(stamped);
        expect(r.djust.isForeignBodyChild(r.document.getElementById('x'))).toBe(false);
    });

    it('foreign children do not count toward patch indices', () => {
        const r = load(
            'dj-root',
            '<div id="lead"></div><header dj-id="1"></header><main dj-id="2"></main><script id="t"></script>',
        );
        r.djust.markBodyStamped();
        const kids = r.djust.getSignificantChildren(r.document.body);
        expect(kids.map((n) => n.tagName)).toEqual(['HEADER', 'MAIN']);
    });
});

describe('replaceContainerHtml', () => {
    it('keeps foreign body children in place and puts the server nodes before the trailing ones', () => {
        const r = load(
            'dj-root',
            '<div id="lead"></div><header id="old" dj-id="1">old</header><script id="s1"></script><div id="dbg"></div>',
        );
        r.djust.markBodyStamped();
        const lead = r.document.getElementById('lead');
        const s1 = r.document.getElementById('s1');
        r.djust.replaceContainerHtml(r.document.body, '<header id="new" dj-id="9">new</header><main dj-id="a">m</main>');
        expect(ids(r)).toEqual(['lead', 'new', 'main', 's1', 'dbg']);
        // The very same nodes: nothing was detached and re-created.
        expect(r.document.getElementById('lead')).toBe(lead);
        expect(r.document.getElementById('s1')).toBe(s1);
    });

    it('does not run inserted scripts (innerHTML semantics)', () => {
        const r = load('dj-root', '<p dj-id="1"></p>');
        r.djust.markBodyStamped();
        r.djust.replaceContainerHtml(r.document.body, '<p dj-id="2">x</p><script>window.__ran = 1</script>');
        expect(r.__ran).toBeUndefined();
    });

    it('is plain innerHTML for any other container', () => {
        const r = load('', '<div id="c" dj-root><p>old</p></div><div id="keep"></div>');
        r.djust.replaceContainerHtml(r.document.getElementById('c'), '<p>new</p>');
        expect(r.document.getElementById('c').innerHTML).toBe('<p>new</p>');
        expect(r.document.getElementById('keep')).not.toBeNull();
    });

    it('a body that is not a root is plain innerHTML', () => {
        const r = load('', '<p>old</p><div id="x"></div>');
        r.djust.replaceContainerHtml(r.document.body, '<p>new</p>');
        expect(r.document.body.innerHTML).toBe('<p>new</p>');
    });
});

describe('morphChildren on a body root', () => {
    it('first mount: aligns the prerendered page and keeps every node it did not render', () => {
        const r = load(
            'dj-root',
            '<div id="ext-lead"></div><header id="hd">H</header><main id="mn">M</main><footer id="ft">F</footer>' +
                '<script id="client"></script><div id="dbg"></div>',
        );
        const lead = r.document.getElementById('ext-lead');
        const header = r.document.getElementById('hd');
        const desired = document_for(
            '<header id="hd" dj-id="1">H</header><main id="mn" dj-id="2">M2</main><footer id="ft" dj-id="3">F</footer>',
        );
        r.djust.morphChildren(r.document.body, desired);
        expect(ids(r)).toEqual(['ext-lead', 'hd', 'mn', 'ft', 'client', 'dbg']);
        expect(r.document.getElementById('ext-lead')).toBe(lead);
        expect(r.document.getElementById('hd')).toBe(header); // morphed in place
        expect(r.document.getElementById('mn').textContent).toBe('M2');
        expect(r.document.getElementById('mn').getAttribute('dj-id')).toBe('2');
    });

    it('first mount: an injected node with the same tag as a template node survives', () => {
        // The template renders one <script>; the HTTP response also carries
        // djust's injected ones after it. The first aligns, the rest are kept.
        const r = load('dj-root', '<main id="mn">M</main><script>1</script><script src="/a.js"></script><script src="/b.js"></script>');
        const own = r.document.querySelector('script');
        const desired = document_for('<main id="mn" dj-id="1">M</main><script dj-id="2">1</script>');
        r.djust.morphChildren(r.document.body, desired);
        const scripts = Array.from(r.document.querySelectorAll('script'));
        expect(scripts.map((n) => n.getAttribute('src'))).toEqual([null, '/a.js', '/b.js']);
        expect(scripts[0]).toBe(own);
        expect(own.getAttribute('dj-id')).toBe('2');
    });

    it('stamped page: a body child without dj-id is never aligned with the server nodes or removed', () => {
        const r = load(
            'dj-root',
            '<div class="ext"></div><div dj-id="1" class="a">A</div><div dj-id="2" class="b">B</div><span class="trail"></span>',
        );
        r.djust.markBodyStamped();
        const ext = r.document.querySelector('.ext');
        const desired = document_for('<div dj-id="1" class="a">A2</div><div dj-id="2" class="b">B2</div><div dj-id="3" class="c">C</div>');
        r.djust.morphChildren(r.document.body, desired);
        expect(r.document.querySelector('.ext')).toBe(ext);
        expect(ext.textContent).toBe('');
        expect(r.document.querySelector('.a').textContent).toBe('A2');
        expect(r.document.querySelector('.c')).not.toBeNull();
        // the new server node lands before the trailing foreign one
        const order = Array.from(r.document.body.children).map((e) => e.className);
        expect(order).toEqual(['ext', 'a', 'b', 'c', 'trail']);
    });

    it('stamped page: a server node the server no longer renders is removed', () => {
        const r = load('dj-root', '<div dj-id="1" class="a"></div><div dj-id="2" class="gone"></div><i class="ext"></i>');
        r.djust.markBodyStamped();
        r.djust.morphChildren(r.document.body, document_for('<div dj-id="1" class="a"></div>'));
        expect(r.document.querySelector('.gone')).toBeNull();
        expect(r.document.querySelector('.ext')).not.toBeNull();
    });

    it('a container other than a root body still removes what the server dropped', () => {
        const r = load('', '<div id="c" dj-root><p>a</p><p>b</p></div><div id="ext"></div>');
        r.djust.morphChildren(r.document.getElementById('c'), document_for('<p>a</p>'));
        expect(r.document.getElementById('c').children.length).toBe(1);
        expect(r.document.getElementById('ext')).not.toBeNull();
    });
});

describe('_runInsertedScripts on a body root', () => {
    it('does not re-run foreign scripts, nor the ones that already ran', () => {
        const r = load(
            'dj-root',
            '<p dj-id="1"></p><script id="own" dj-id="2">window.__own = (window.__own || 0) + 1</script>' +
                '<script id="foreign">window.__foreign = (window.__foreign || 0) + 1</script>',
        );
        r.djust.markBodyStamped();
        const own = r.document.getElementById('own');
        // Both ran once when the page loaded (JSDOM executes parsed scripts).
        expect(r.__own).toBe(1);
        expect(r.__foreign).toBe(1);
        r.__own = undefined;
        r.__foreign = undefined;
        // `own` ran when the page loaded: it is in the "already ran" set.
        r.djust._runInsertedScripts(r.document.body, new Set([own]));
        expect(r.__foreign).toBeUndefined();
        expect(r.__own).toBeUndefined();
        // A script the morph created (not in the set) does run.
        const fresh = r.document.createElement('script');
        fresh.id = 'fresh';
        fresh.setAttribute('dj-id', '9');
        fresh.textContent = 'window.__fresh = 1';
        r.document.body.insertBefore(fresh, r.document.getElementById('foreign'));
        r.djust._runInsertedScripts(r.document.body, new Set([own]));
        expect(r.__fresh).toBe(1);
        expect(r.__foreign).toBeUndefined();
    });
});

describe('a view container keeps the address the client gave it', () => {
    it('morphElement does not strip data-djust-target from a dj-view container', () => {
        const r = load('dj-root', '<div id="w1" dj-view="a.B" dj-lazy="idle" data-djust-target="w1"></div>');
        const el = r.document.getElementById('w1');
        const desired = document_for('<div id="w1" dj-view="a.B" dj-lazy="idle"></div>').firstChild;
        r.djust.morphElement(el, desired);
        expect(el.getAttribute('data-djust-target')).toBe('w1');
    });

    it('other attributes the server dropped are still removed', () => {
        const r = load('', '<div id="w" class="x" data-djust-target="t"></div>');
        const el = r.document.getElementById('w');
        r.djust.morphElement(el, document_for('<div id="w"></div>').firstChild);
        expect(el.hasAttribute('class')).toBe(false);
        // not a view container: the ordinary rule applies
        expect(el.hasAttribute('data-djust-target')).toBe(false);
    });
});


describe('prerendered script diagnostics', () => {
    it('does not warn or rerun a script that ran during page parsing', () => {
        const w = load('dj-root', '<script dj-id="1">window.__parsedRuns = (window.__parsedRuns || 0) + 1</script>');
        const own = w.document.querySelector('script');
        w.djust.markBodyStamped();
        w.DEBUG_MODE = true;
        const errors = vi.spyOn(w.console, 'error').mockImplementation(() => {});
        const warnings = vi.spyOn(w.console, 'warn').mockImplementation(() => {});
        w.djust._runInsertedScripts(w.document.body, new Set([own]));
        w.djust._warnDeadScripts(w.document.body);
        expect(w.__parsedRuns).toBe(1);
        expect(errors).not.toHaveBeenCalled();
        expect(own.hasAttribute('data-djust-script-ran')).toBe(false);
        expect(warnings.mock.calls.some(args => String(args[0]).includes('execution status is unknown'))).toBe(true);
        warnings.mockRestore();
        errors.mockRestore();
        w.close();
    });
});

describe('retained script execution provenance', () => {
    for (const kind of ['LiveViewWebSocket', 'LiveViewSSE']) {
        for (const inert of [false, true]) {
            it(`${kind}: retained ${inert ? 'inert' : 'parsed'} script has unknown provenance without rerunning`, async () => {
                const w = load('dj-root dj-view="review.View"', '<header id="h">old</header><script id="parsed">window.__parsedRuns = (window.__parsedRuns || 0) + 1</script>');
                w.DEBUG_MODE = true;
                w.DJUST_USE_WEBSOCKET = false;
                const errors = vi.spyOn(w.console, 'error').mockImplementation(() => {});
                const warnings = vi.spyOn(w.console, 'warn').mockImplementation(() => {});
                try {
                    if (inert) {
                        const host = w.document.createElement('div');
                        host.id = 'inert-host';
                        host.innerHTML = '<script id="inert">window.__inertRuns = (window.__inertRuns || 0) + 1</script>';
                        w.document.body.appendChild(host);
                    }
                    const retained = w.document.querySelector(inert ? '#inert' : '#parsed');
                    let html = '<header id="h" dj-id="1">new</header><script id="parsed" dj-id="2">window.__parsedRuns = (window.__parsedRuns || 0) + 1</script>';
                    if (inert) html += '<div id="inert-host" dj-id="3"><script id="inert" dj-id="4">window.__inertRuns = (window.__inertRuns || 0) + 1</script></div>';
                    const transport = new w.djust[kind]();
                    transport.skipMountHtml = true;
                    transport.primaryViewPath = 'review.View';
                    await transport.handleMessage({type: 'mount', view: 'review.View', html, has_ids: true, version: 1});
                    expect(w.document.querySelector(inert ? '#inert' : '#parsed')).toBe(retained);
                    expect(w.__parsedRuns).toBe(1);
                    expect(w.__inertRuns || 0).toBe(0);
                    expect(retained.hasAttribute('data-djust-script-ran')).toBe(false);
                    expect(warnings.mock.calls.some(args => String(args[0]).includes('execution status is unknown'))).toBe(true);
                    expect(errors).not.toHaveBeenCalled();
                    // A later diagnostic/run pass must not turn the retained inert node executable.
                    w.djust._runInsertedScripts(w.document.body);
                    w.djust._warnDeadScripts(w.document.body);
                    expect(w.__parsedRuns).toBe(1);
                    expect(w.__inertRuns || 0).toBe(0);
                    expect(errors).not.toHaveBeenCalled();
                } finally {
                    warnings.mockRestore(); errors.mockRestore(); w.close();
                }
            });
        }
    }
});
