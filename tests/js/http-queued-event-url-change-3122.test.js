// PR #3122 review: an HTTP event queued behind an in-flight one was silently
// dropped after ANY URL change, including an in-page #anchor jump, because
// the ownership check compared the full href. The fragment never reaches the
// server, so it no longer counts; a queued event dropped for a real URL
// change on the same page now reports djust:error instead of vanishing.
import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function setup() {
    const dom = new JSDOM('<!doctype html><body><div dj-root><button id="a" dj-click="first">A</button><button id="b" dj-click="second">B</button></div></body>', {
        url: 'http://localhost/page/', runScripts: 'dangerously',
    });
    dom.window.eval(client);
    const sent = [];
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    dom.window.fetch = async (_url, options) => {
        const name = options.headers['X-Djust-Event'];
        sent.push(name);
        if (name === 'first') await gate;
        return {ok: true, json: async () => ({})};
    };
    const errors = [];
    dom.window.addEventListener('djust:error', e => errors.push(e.detail));
    const doc = dom.window.document;
    return {dom, sent, errors, release, a: doc.getElementById('a'), b: doc.getElementById('b')};
}

it('a queued event still goes out after an in-page #anchor jump', async () => {
    const {dom, sent, release, a, b} = setup();
    try {
        const first = dom.window.djust.handleEvent('first', {_targetElement: a});
        const second = dom.window.djust.handleEvent('second', {_targetElement: b});
        dom.window.location.hash = '#section';
        release();
        await first;
        await second;
        expect(sent).toEqual(['first', 'second']);
    } finally { dom.window.close(); }
});

it('a queued event dropped by a same-page URL change is reported, not silent', async () => {
    const {dom, sent, errors, release, a, b} = setup();
    try {
        const first = dom.window.djust.handleEvent('first', {_targetElement: a});
        const second = dom.window.djust.handleEvent('second', {_targetElement: b});
        dom.window.history.pushState({}, '', '/elsewhere/');
        release();
        await first;
        await second;
        expect(sent).toEqual(['first']);
        expect(errors.some(d => /second/.test(d.error))).toBe(true);
    } finally { dom.window.close(); }
});

it('a queued event dropped by real navigation stays quiet on the next page', async () => {
    const {dom, sent, errors, release, a, b} = setup();
    try {
        const first = dom.window.djust.handleEvent('first', {_targetElement: a});
        const second = dom.window.djust.handleEvent('second', {_targetElement: b});
        dom.window.dispatchEvent(new dom.window.CustomEvent('djust:before-navigate'));
        release();
        await first.catch(() => {});
        await second;
        expect(sent).toEqual(['first']);
        expect(errors).toEqual([]);
    } finally { dom.window.close(); }
});
