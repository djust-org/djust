/**
 * Form-field value preservation during VDOM patches / broadcasts.
 *
 * #1991 — the broadcast textarea sweep had no per-field opt-out: any
 *   push_to_view patch unconditionally reset EVERY <textarea>.value in the
 *   LiveView root, wiping unrelated drafts. Fix: a textarea marked
 *   dj-update="ignore" (already the "client-owned, don't update" convention
 *   honored by morphElement) is now SKIPPED by the sweep. Both sweep sites
 *   (02-response-handler.js's applyPatches path + 12-vdom-patch.js's
 *   preserveFormValues innerHTML path) route through ONE shared helper,
 *   syncBroadcastTextareas, so the opt-out lives in exactly one place
 *   (parallel-path-drift cure, #1646).
 *
 * #1990 — morphElement()'s skipValue heuristic (issue cites the old name
 *   "morphNode"; the real function is morphElement) skipped the server
 *   value sync for a FOCUSED field unless its name changed, so a handler
 *   that cleared a still-focused composer (Enter-to-send, no blur) could
 *   never take effect. Fix: an opt-in dj-force-value attribute applies the
 *   server value even while focused. Opposite polarity to #1991's opt-out:
 *   #1991 opts a field OUT of an unconditional overwrite; #1990 opts a
 *   focused field IN to server updates. Covers INPUT/SELECT/TEXTAREA.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// ---------------------------------------------------------------------------
// Shared JSDOM instance for the direct-call unit tests (helper + morphElement)
// ---------------------------------------------------------------------------
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function (value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}
dom.window.eval(clientCode);

const { syncBroadcastTextareas, morphElement } = dom.window.djust;
const document = dom.window.document;

// ---------------------------------------------------------------------------
// #1991 — broadcast textarea sweep opt-out
// ---------------------------------------------------------------------------
describe('#1991 broadcast textarea sweep — per-field opt-out (dj-update="ignore")', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    describe('syncBroadcastTextareas (shared helper — the logic BOTH sweep sites run)', () => {
        it('sweeps a normal textarea .value from its textContent (preserves #1601)', () => {
            const root = document.createElement('div');
            // Server-rendered content is "peer message"; the local .value has
            // diverged (this is what the sweep exists to reconcile for a
            // shared/collaborative textarea).
            root.innerHTML = '<textarea id="shared">peer message</textarea>';
            document.body.appendChild(root);
            const ta = root.querySelector('textarea');
            ta.value = 'stale local value';

            syncBroadcastTextareas(root);

            expect(ta.value).toBe('peer message');
        });

        it('does NOT wipe a textarea carrying dj-update="ignore" (the opt-out)', () => {
            const root = document.createElement('div');
            root.innerHTML =
                '<textarea id="draft" dj-update="ignore">old server content</textarea>';
            document.body.appendChild(root);
            const ta = root.querySelector('textarea');
            ta.value = 'my unsent draft';

            syncBroadcastTextareas(root);

            // Opt-out honored: the user's draft survives the broadcast sweep.
            expect(ta.value).toBe('my unsent draft');
        });

        it('sweeps opted-in siblings while skipping the opted-out one (mixed root)', () => {
            const root = document.createElement('div');
            root.innerHTML =
                '<textarea id="shared">peer message</textarea>' +
                '<textarea id="draft" dj-update="ignore">old server content</textarea>';
            document.body.appendChild(root);
            const shared = root.querySelector('#shared');
            const draft = root.querySelector('#draft');
            shared.value = 'stale local value';
            draft.value = 'my unsent draft';

            syncBroadcastTextareas(root);

            expect(shared.value).toBe('peer message'); // swept
            expect(draft.value).toBe('my unsent draft'); // opted out
        });

        it('tolerates a null root without throwing', () => {
            expect(() => syncBroadcastTextareas(null)).not.toThrow();
        });
    });

    describe('anti-drift pin (#1646) — neither sweep site inlines a raw textarea sweep', () => {
        // If a future edit re-inlines `querySelectorAll('textarea').forEach(...)`
        // at either call site instead of calling the shared helper, the opt-out
        // would silently drift back to being honored at only one site.
        const responseHandler = fs.readFileSync(
            './python/djust/static/djust/src/02-response-handler.js',
            'utf-8',
        );
        const vdomPatch = fs.readFileSync(
            './python/djust/static/djust/src/12-vdom-patch.js',
            'utf-8',
        );

        it('02-response-handler broadcast block calls syncBroadcastTextareas', () => {
            expect(responseHandler).toMatch(/syncBroadcastTextareas\(getLiveViewRoot\(\)\)/);
            // No inline sweep remains in the response handler.
            expect(responseHandler).not.toMatch(/querySelectorAll\('textarea'\)/);
        });

        it('12-vdom-patch defines the helper once and its broadcast branch uses it', () => {
            expect(vdomPatch).toMatch(/function syncBroadcastTextareas\(/);
            // The opt-out check lives exactly once, inside the shared helper.
            const optOutHits = (
                vdomPatch.match(/getAttribute\('dj-update'\) === 'ignore'/g) || []
            ).length;
            // One in the helper (#1991) + one in morphElement's pre-existing
            // dj-update="ignore" early-return. Exactly two — no third copy.
            expect(optOutHits).toBe(2);
        });
    });
});

// ---------------------------------------------------------------------------
// #1991 — site B: the REAL production broadcast path (handleServerResponse
// via the WebSocket message pump). This is the sweep site that actually runs
// (preserveFormValues is dead code per #1990's decoy note).
// ---------------------------------------------------------------------------
describe('#1991 site B — real broadcast frame does not wipe an opted-out draft', () => {
    function createMountedDom() {
        const html = `<!DOCTYPE html><html><body>
  <div dj-view="test.views.TestView" dj-root>
    <span id="counter">0</span>
    <textarea id="shared" name="shared">peer old</textarea>
    <textarea id="draft" name="draft" dj-update="ignore">draft old</textarea>
  </div>
</body></html>`;
        const d = new JSDOM(html, { runScripts: 'dangerously' });
        if (!d.window.CSS) d.window.CSS = {};
        if (!d.window.CSS.escape) {
            d.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        }
        class MockWebSocket {
            static CONNECTING = 0;
            static OPEN = 1;
            static CLOSING = 2;
            static CLOSED = 3;
            constructor() {
                this.readyState = MockWebSocket.OPEN;
                this.onopen = this.onclose = this.onmessage = this.onerror = null;
            }
            send() {}
            close() {}
        }
        d.window.WebSocket = MockWebSocket;
        d.window.eval(clientCode);
        return d;
    }

    async function makeMountedWS(d) {
        const ws = new d.window.djust.LiveViewWebSocket();
        ws.enabled = true;
        ws.ws = new d.window.WebSocket();
        ws.viewMounted = true;
        // Mount with html matching the SSR body so the morph is a no-op and
        // the textareas persist; this also initializes clientVdomVersion=1.
        await ws.handleMessage({
            type: 'mount',
            view: 'test.views.TestView',
            version: 1,
            html:
                '<div dj-view="test.views.TestView" dj-root>' +
                '<span id="counter">0</span>' +
                '<textarea id="shared" name="shared">peer old</textarea>' +
                '<textarea id="draft" name="draft" dj-update="ignore">draft old</textarea>' +
                '</div>',
        });
        return ws;
    }

    it('broadcast sweeps the shared textarea but preserves the dj-update="ignore" draft', async () => {
        const d = createMountedDom();
        const ws = await makeMountedWS(d);
        const doc = d.window.document;

        const shared = doc.querySelector('#shared');
        const draft = doc.querySelector('#draft');
        // User has typed into BOTH fields (value diverges from textContent).
        shared.value = 'shared local edit';
        draft.value = 'my unsent draft';

        // A peer's message lands in a DIFFERENT part of the view (the counter),
        // carried on a broadcast frame — exactly the #1991 scenario. The frame
        // mirrors the server shape (websocket.py sends both source + broadcast).
        await ws.handleMessage({
            type: 'patch',
            source: 'broadcast',
            broadcast: true,
            version: 2,
            patches: [{ type: 'SetText', path: [0, 0], text: '1' }],
        });

        expect(doc.querySelector('#counter').textContent).toBe('1'); // patch applied
        expect(shared.value).toBe('peer old'); // swept (no opt-out) — #1601 intact
        expect(draft.value).toBe('my unsent draft'); // opted out — #1991 fix
    });
});

// ---------------------------------------------------------------------------
// #1990 — dj-force-value: apply the server value even to a FOCUSED field
// ---------------------------------------------------------------------------
describe('#1990 dj-force-value — force server value onto a focused field', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    function buildTextarea(attrs, textContent) {
        const el = document.createElement('textarea');
        for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
        el.textContent = textContent;
        return el;
    }

    it('TEXTAREA: focused field WITHOUT dj-force-value keeps its value (unchanged default)', () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const existing = buildTextarea({ id: 'composer', name: 'composer' }, 'sent text');
        container.appendChild(existing);
        existing.value = 'sent text';
        existing.focus();
        expect(document.activeElement).toBe(existing);

        // Server cleared the field (handler set self.composer = "").
        const desired = buildTextarea({ id: 'composer', name: 'composer' }, '');

        morphElement(existing, desired);

        // Default focus protection: a focused field is NOT overwritten.
        expect(existing.value).toBe('sent text');
    });

    it('TEXTAREA: focused field WITH dj-force-value receives the cleared server value', () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const existing = buildTextarea(
            { id: 'composer', name: 'composer', 'dj-force-value': '' },
            'sent text',
        );
        container.appendChild(existing);
        existing.value = 'sent text';
        existing.focus();
        expect(document.activeElement).toBe(existing);

        const desired = buildTextarea(
            { id: 'composer', name: 'composer', 'dj-force-value': '' },
            '',
        );

        morphElement(existing, desired);

        // Opt-in override: the handler's clear takes effect despite focus.
        expect(existing.value).toBe('');
    });

    it('INPUT: focused field WITH dj-force-value receives the changed server value (parity)', () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const existing = document.createElement('input');
        existing.setAttribute('type', 'text');
        existing.setAttribute('name', 'q');
        existing.setAttribute('dj-force-value', '');
        existing.value = 'typed by user';
        container.appendChild(existing);
        existing.focus();
        expect(document.activeElement).toBe(existing);

        const desired = document.createElement('input');
        desired.setAttribute('type', 'text');
        desired.setAttribute('name', 'q');
        desired.setAttribute('dj-force-value', '');
        desired.setAttribute('value', 'server value');

        morphElement(existing, desired);

        expect(existing.value).toBe('server value');
    });

    it('INPUT: focused field WITHOUT dj-force-value keeps its value (unchanged default)', () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const existing = document.createElement('input');
        existing.setAttribute('type', 'text');
        existing.setAttribute('name', 'q');
        existing.value = 'typed by user';
        container.appendChild(existing);
        existing.focus();

        const desired = document.createElement('input');
        desired.setAttribute('type', 'text');
        desired.setAttribute('name', 'q');
        desired.setAttribute('value', 'server value');

        morphElement(existing, desired);

        expect(existing.value).toBe('typed by user');
    });
});
