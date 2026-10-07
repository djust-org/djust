import {describe, it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
function page(debug = true) {
    const dom = new JSDOM('<!doctype html><body><div dj-root dj-view="test.View">old</div></body>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    dom.window.DEBUG_MODE = debug;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.console.error = () => {};
    dom.window.eval(client);
    return dom;
}
function error(dom, fields = {}) {
    dom.window.dispatchEvent(new dom.window.CustomEvent('djust:error', {detail: {
        error: '<img src=x onerror=alert(1)> still saving', code: 'state_error',
        transient: true, view: 'test.View', ...fields,
    }}));
}
const status = dom => dom.window.document.getElementById('djust-save-status');
const overlay = dom => dom.window.document.getElementById('djust-error-overlay');

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s transient saves', name => {
    it('preserves metadata and observability, then clears status after a real recovery render', async () => {
        const dom = page();
        try {
            const transport = new dom.window.djust[name]();
            const errors = [];
            dom.window.addEventListener('djust:error', e => errors.push(e.detail));
            await transport.handleMessage({type: 'error', error: 'still saving', code: 'state_error',
                transient: true, source: 'url_change', view: 'test.View'});
            expect(errors).toHaveLength(1);
            expect(errors[0].transient).toBe(true);
            expect(errors[0].view).toBe('test.View');
            expect(status(dom)?.textContent).toContain('still saving');
            expect(overlay(dom)).toBeNull();
            await transport.handleMessage({type: 'html_update', source: 'async', view: 'test.View',
                html: '<div dj-root dj-view="test.View">saved</div>'});
            expect(dom.window.document.querySelector('[dj-root]').textContent).toBe('saved');
            expect(status(dom)).toBeNull();
            await transport.handleMessage({type: 'error', error: 'reload', code: 'state_error',
                transient: false, view: 'test.View'});
            expect(errors).toHaveLength(2);
            expect(overlay(dom)?.textContent).toContain('reload');
        } finally { dom.window.close(); }
    });
    it('preserves slot ownership and clears only that slot after a real response', async () => {
        const dom = page();
        try {
            dom.window.document.body.insertAdjacentHTML('beforeend',
                '<div dj-root dj-view="test.Child" data-djust-target="child">child</div>');
            const transport = new dom.window.djust[name]();
            transport.primaryViewPath = 'test.View';
            const errors = [];
            dom.window.addEventListener('djust:error', e => errors.push(e.detail));
            await transport.handleMessage({type: 'error', error: 'saving child', code: 'state_error',
                transient: true, view: 'test.Child', target_id: 'child'});
            expect(errors[0]).toMatchObject({transient: true, view: 'test.Child', target_id: 'child'});
            await transport.handleMessage({type: 'patch', patches: [], view: 'test.View'});
            expect(status(dom)).not.toBeNull();
            await transport.handleMessage({type: 'patch', patches: [], view: 'test.Child', target_id: 'child'});
            expect(status(dom)).toBeNull();
        } finally { dom.window.close(); }
    });
    it.each([undefined, false, 'true', 1])('does not promote %s into a trusted transient flag', async flag => {
        const dom = page();
        try {
            const transport = new dom.window.djust[name]();
            const errors = [];
            dom.window.addEventListener('djust:error', e => errors.push(e.detail));
            await transport.handleMessage({type: 'error', error: 'failure', code: 'state_error',
                transient: flag, view: 'test.View'});
            expect(errors[0].transient).toBe(false);
            expect(status(dom)).toBeNull();
            expect(overlay(dom)).not.toBeNull();
        } finally { dom.window.close(); }
    });
});

describe('transient save display', () => {
    it.each([true, false])('uses nonblocking escaped status with DEBUG=%s', debug => {
        const dom = page(debug);
        try {
            error(dom);
            expect(status(dom)?.getAttribute('role')).toBe('status');
            expect(status(dom)?.style.pointerEvents).toBe('none');
            expect(status(dom)?.textContent).toContain('<img');
            expect(status(dom)?.querySelector('img')).toBeNull();
            expect(overlay(dom)).toBeNull();
        } finally { dom.window.close(); }
    });
    it.each([{}, {transient: 'true'}, {transient: false}, {code: 'other_error'}])(
        'keeps persistent and unrelated errors blocking: %j', fields => {
            const dom = page();
            try {
                error(dom);
                error(dom, {transient: false, ...fields});
                expect(overlay(dom)).not.toBeNull();
                expect(status(dom)).toBeNull();
            } finally { dom.window.close(); }
        });
    it('does not let a transient save replace an existing persistent overlay', () => {
        const dom = page();
        try {
            error(dom, {error: 'persistent', transient: false});
            const original = overlay(dom);
            error(dom);
            expect(overlay(dom)).toBe(original);
            expect(overlay(dom).textContent).toContain('persistent');
        } finally { dom.window.close(); }
    });
    it('clears only the recovered view and target', () => {
        const dom = page();
        try {
            error(dom, {view: 'other.View', target_id: 'child'});
            const rendered = detail => dom.window.dispatchEvent(new dom.window.CustomEvent('djust:rendered', {detail}));
            rendered({view: 'test.View', target_id: 'child'});
            expect(status(dom)).not.toBeNull();
            rendered({view: 'other.View', target_id: 'different'});
            expect(status(dom)).not.toBeNull();
            rendered({view: 'other.View', target_id: 'child'});
            expect(status(dom)).toBeNull();
        } finally { dom.window.close(); }
    });
    it('updates a single status per owner and clears outgoing statuses on navigation', () => {
        const dom = page();
        try {
            error(dom, {error: 'first'});
            error(dom, {error: 'second'});
            expect(status(dom).children).toHaveLength(1);
            expect(status(dom).textContent).toBe('second');
            error(dom, {view: 'other.View'});
            expect(status(dom).children).toHaveLength(2);
            dom.window.dispatchEvent(new dom.window.Event('djust:before-navigate'));
            expect(status(dom)).toBeNull();
            error(dom, {view: null, error: null});
            expect(status(dom).textContent).toBe('Saving your change…');
            dom.window.dispatchEvent(new dom.window.Event('pagehide'));
            expect(status(dom)).toBeNull();
        } finally { dom.window.close(); }
    });
    it('does not clear status when a recovery version is rejected', async () => {
        const dom = page();
        try {
            const transport = new dom.window.djust.LiveViewWebSocket();
            await transport.handleMessage({type: 'patch', patches: [], version: 1, view: 'test.View'});
            error(dom);
            await transport.handleMessage({type: 'patch', patches: [], version: 3, view: 'test.View'});
            expect(status(dom)).not.toBeNull();
            await transport.handleMessage({type: 'patch', patches: [], version: 2, view: 'test.View'});
            expect(status(dom)).toBeNull();
        } finally { dom.window.close(); }
    });
    it('does not clear on a malformed or broadcast response', async () => {
        const dom = page();
        try {
            const transport = new dom.window.djust.LiveViewWebSocket();
            error(dom);
            await transport.handleMessage({type: 'html_update', view: 'test.View'});
            expect(status(dom)).not.toBeNull();
            await transport.handleMessage({type: 'patch', patches: [], broadcast: true, view: 'test.View'});
            expect(status(dom)).not.toBeNull();
            await transport.handleMessage({type: 'patch', patches: [], view: 'test.View'});
            expect(status(dom)).toBeNull();
        } finally { dom.window.close(); }
    });
});
