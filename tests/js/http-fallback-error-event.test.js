// The HTTP fallback reports a failed event with a `djust:error` event, as a
// socket error frame does (#1646 parity). Before, it only wrote to the
// console, so an HTTP-only page could not show the failure.
import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

async function failingEvent(response) {
    const dom = new JSDOM('<div dj-root dj-view="test.View"></div>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    try {
        dom.window.DJUST_USE_WEBSOCKET = false;
        dom.window.eval(client);
        dom.window.console.error = () => {};
        dom.window.fetch = async () => response;
        const errors = [];
        dom.window.addEventListener('djust:error', event => errors.push(event.detail));
        await dom.window.djust.handleEvent('save', {});
        return errors;
    } finally {
        dom.window.close();
    }
}

it('dispatches djust:error with the server error body', async () => {
    const errors = await failingEvent({
        ok: false, status: 500,
        json: async () => ({error: 'Error in View.save(): RuntimeError: boom', traceback: 'tb'}),
    });
    expect(errors).toEqual([{error: 'Error in View.save(): RuntimeError: boom', traceback: 'tb'}]);
});

it('falls back to the status when the body is not JSON', async () => {
    const errors = await failingEvent({
        ok: false, status: 502, json: async () => { throw new Error('not json'); },
    });
    expect(errors).toEqual([{error: 'HTTP error! status: 502', traceback: null}]);
});

it('a successful response dispatches nothing', async () => {
    const errors = await failingEvent({ok: true, status: 200, json: async () => ({patches: []})});
    expect(errors).toEqual([]);
});
