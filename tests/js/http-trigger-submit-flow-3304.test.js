import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

// #3304: the sign-in-from-a-LiveView-form recipe in guides/authentication.md.
// A form with dj-submit sends its fields to the handler (a button's dj-click
// outside the form would send none), and the handler's trigger_submit() makes
// the client submit the dj-trigger-action form natively.
const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function page() {
    const dom = new JSDOM(
        '<!doctype html><body><div dj-root>' +
        '<form id="login-form" action="/accounts/login/" method="POST" dj-submit="check" dj-trigger-action>' +
        '<input name="username" value="ann"><input name="password" type="password" value="pw">' +
        '<button type="submit">Sign in</button></form>' +
        '<form id="other" action="/other/" method="POST"><input name="x"></form>' +
        '</div></body>',
        {url: 'http://localhost/', runScripts: 'dangerously'},
    );
    dom.window.eval(client);
    dom.window.djust.bindLiveViewEvents();
    return dom;
}

it('dj-submit sends the form fields, then the push event submits the form natively', async () => {
    const dom = page();
    try {
        const sent = [];
        dom.window.fetch = async (_url, options) => {
            sent.push(JSON.parse(options.body));
            return {ok: true, json: async () => ({})};
        };
        const form = dom.window.document.getElementById('login-form');
        let nativeSubmits = 0;
        form.submit = () => { nativeSubmits += 1; };
        form.dispatchEvent(new dom.window.Event('submit', {bubbles: true, cancelable: true}));
        await new Promise(resolve => setTimeout(resolve, 50));
        expect(sent.length).toBe(1);
        expect(sent[0].username).toBe('ann');

        // What `self.trigger_submit("#login-form")` pushes after the handler.
        dom.window.dispatchEvent(new dom.window.CustomEvent('djust:push_event', {
            detail: {event: 'djust:trigger-submit', payload: {selector: '#login-form'}},
        }));
        expect(nativeSubmits).toBe(1);

        // A form without dj-trigger-action is refused.
        const other = dom.window.document.getElementById('other');
        other.submit = () => { nativeSubmits += 1; };
        dom.window.dispatchEvent(new dom.window.CustomEvent('djust:push_event', {
            detail: {event: 'djust:trigger-submit', payload: {selector: '#other'}},
        }));
        expect(nativeSubmits).toBe(1);
    } finally { dom.window.close(); }
});

it('a dj-click button outside the form sends no field values', async () => {
    const dom = new JSDOM(
        '<!doctype html><body><div dj-root><button id="b" dj-click="check">Sign in</button>' +
        '<form id="login-form"><input name="username" value="ann"></form></div></body>',
        {url: 'http://localhost/', runScripts: 'dangerously'},
    );
    try {
        dom.window.eval(client);
    dom.window.djust.bindLiveViewEvents();
        const sent = [];
        dom.window.fetch = async (_url, options) => {
            sent.push(JSON.parse(options.body));
            return {ok: true, json: async () => ({})};
        };
        dom.window.document.getElementById('b').dispatchEvent(
            new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
        await new Promise(resolve => setTimeout(resolve, 50));
        expect(sent.length).toBe(1);
        expect(sent[0].username).toBeUndefined();
    } finally { dom.window.close(); }
});
