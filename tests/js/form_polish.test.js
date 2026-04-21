/**
 * Tests for v0.5.1 form-polish features:
 *   - dj-no-submit="enter" — suppress Enter-key form submission from text inputs
 *   - dj-trigger-action — native form POST bridge (via server-pushed event)
 *   - dj-loading="event_name" — shorthand scoped loading indicator
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${bodyHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

describe('dj-no-submit="enter"', () => {
    let dom;

    it('suppresses Enter-key submit from a text input inside a marked form', () => {
        dom = createDom(`
            <form dj-submit="save" dj-no-submit="enter" id="f">
                <input type="text" id="t" />
            </form>
        `);

        const { document } = dom.window;
        const input = document.getElementById('t');

        // Spy the form submit by listening to the submit event.
        let submitted = false;
        document.getElementById('f').addEventListener('submit', () => {
            submitted = true;
        });

        const event = new dom.window.KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true,
        });
        input.dispatchEvent(event);

        expect(event.defaultPrevented).toBe(true);
        expect(submitted).toBe(false);
    });

    it('does NOT suppress Enter when the form lacks dj-no-submit', () => {
        dom = createDom(`
            <form dj-submit="save" id="f">
                <input type="text" id="t" />
            </form>
        `);

        const { document } = dom.window;
        const input = document.getElementById('t');

        const event = new dom.window.KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true,
        });
        input.dispatchEvent(event);

        expect(event.defaultPrevented).toBe(false);
    });

    it('does NOT suppress Enter inside a textarea (multi-line input)', () => {
        dom = createDom(`
            <form dj-submit="save" dj-no-submit="enter" id="f">
                <textarea id="t"></textarea>
            </form>
        `);

        const { document } = dom.window;
        const ta = document.getElementById('t');

        const event = new dom.window.KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true,
        });
        ta.dispatchEvent(event);

        expect(event.defaultPrevented).toBe(false);
    });

    it('allows submit button clicks to submit (only suppresses Enter keydown)', () => {
        dom = createDom(`
            <form dj-submit="save" dj-no-submit="enter" id="f">
                <input type="text" id="t" />
                <button type="submit" id="b">Save</button>
            </form>
        `);

        const { document } = dom.window;
        let submitted = false;
        document.getElementById('f').addEventListener('submit', (e) => {
            submitted = true;
            e.preventDefault(); // avoid jsdom "not implemented" warning
        });

        document.getElementById('b').click();
        expect(submitted).toBe(true);
    });

    it('does not suppress Enter when modifier keys are pressed', () => {
        dom = createDom(`
            <form dj-submit="save" dj-no-submit="enter" id="f">
                <input type="text" id="t" />
            </form>
        `);

        const { document } = dom.window;
        const input = document.getElementById('t');

        const event = new dom.window.KeyboardEvent('keydown', {
            key: 'Enter',
            shiftKey: true,
            bubbles: true,
            cancelable: true,
        });
        input.dispatchEvent(event);

        expect(event.defaultPrevented).toBe(false);
    });
});

describe('dj-trigger-action', () => {
    let dom;

    it('submits a marked form when the server pushes dj:trigger-submit', () => {
        dom = createDom(`
            <form id="login" action="/oauth/callback/" method="post" dj-trigger-action>
                <input type="hidden" name="token" value="abc" />
            </form>
        `);

        const { document, window } = dom.window.document.defaultView ? { document: dom.window.document, window: dom.window } : dom;

        const form = document.getElementById('login');
        let submitted = false;
        form.submit = () => {
            submitted = true;
        };

        window.dispatchEvent(new window.CustomEvent('djust:push_event', {
            detail: { event: 'dj:trigger-submit', payload: { selector: '#login' } },
        }));

        expect(submitted).toBe(true);
    });

    it('ignores the push event when the form lacks dj-trigger-action', () => {
        dom = createDom(`
            <form id="login" action="/oauth/callback/" method="post">
                <input type="hidden" name="token" value="abc" />
            </form>
        `);

        const { window } = { window: dom.window };
        const form = dom.window.document.getElementById('login');
        let submitted = false;
        form.submit = () => {
            submitted = true;
        };

        window.dispatchEvent(new window.CustomEvent('djust:push_event', {
            detail: { event: 'dj:trigger-submit', payload: { selector: '#login' } },
        }));

        expect(submitted).toBe(false);
    });

    it('ignores the push event when the selector matches no form', () => {
        dom = createDom('<div id="not-a-form" dj-trigger-action></div>');
        const { window } = { window: dom.window };
        expect(() => {
            window.dispatchEvent(new window.CustomEvent('djust:push_event', {
                detail: { event: 'dj:trigger-submit', payload: { selector: '#not-a-form' } },
            }));
        }).not.toThrow();
    });

    it('ignores push events for unrelated event names', () => {
        dom = createDom(`
            <form id="login" dj-trigger-action>
                <input type="hidden" name="token" value="abc" />
            </form>
        `);

        const { window } = { window: dom.window };
        const form = dom.window.document.getElementById('login');
        let submitted = false;
        form.submit = () => {
            submitted = true;
        };

        window.dispatchEvent(new window.CustomEvent('djust:push_event', {
            detail: { event: 'flash', payload: { message: 'saved' } },
        }));

        expect(submitted).toBe(false);
    });
});

describe('dj-loading="event_name" shorthand', () => {
    let dom;

    it('hides the element on register (no inline style required)', () => {
        dom = createDom('<div dj-loading="search" id="l">Searching...</div>');

        // scanAndRegister runs during djustInit; the shorthand should have
        // hidden the element.
        const el = dom.window.document.getElementById('l');
        expect(el.style.display).toBe('none');
    });

    it('registers under the shorthand event name', () => {
        dom = createDom('<div dj-loading="my_search" id="l">Searching...</div>');

        const manager = dom.window.djust && dom.window.djust._globalLoadingManager;
        // Not every build exposes the manager globally; fall back to DOM side-effect.
        if (manager) {
            let matched = false;
            manager.registeredElements.forEach((config) => {
                if (config.eventName === 'my_search') matched = true;
            });
            expect(matched).toBe(true);
        } else {
            // At minimum, the element was hidden — proof it was registered.
            expect(dom.window.document.getElementById('l').style.display).toBe('none');
        }
    });
});
