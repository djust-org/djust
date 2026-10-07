/**
 * #2985 batch 5 — VoiceInput answers its dj-hook with a privacy-first protocol,
 * and an app's own hook of the same name still wins.
 *
 * The markup is what the component renders (python/djust/tests/
 * test_component_batch5_voice_2985.py pins it on every render path). jsdom has
 * no speech recognition: a fake with the real API's shape (start, stop, abort,
 * result and error events) stands in, so what is checked is WHEN it is started,
 * WHAT is sent, WHEN it is stopped and what the reader is told. The real
 * browser UI and real server delivery, with simulated recognition callbacks
 * and an unsupported-browser case, are checked by
 * tests/playwright/test_component_batch5_voice_2985.py.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');
const SCRIPT = read('voice-input.js');

const VOICE = ({ event = 'transcribe', lang = 'en-US', continuous = false, maxSeconds = 60, extra = '' } = {}) =>
    '<span class="dj-voice-field">' +
    `<button type="button" class="dj-voice-input" dj-hook="VoiceInput" data-event="${event}" data-lang="${lang}" data-continuous="${continuous}" data-max-seconds="${maxSeconds}" aria-label="Voice input" aria-pressed="false"${extra}>` +
    '<svg class="dj-voice-input__icon"></svg><span class="dj-voice-input__pulse"></span></button>' +
    '<span class="dj-voice-input__disclosure">Voice input uses your browser\'s speech recognition.</span>' +
    '<span class="dj-voice-input__interim" aria-hidden="true"></span>' +
    '<span class="dj-voice-input__status" role="status" aria-live="polite"></span></span>';

class FakeSR {
    constructor() {
        if (FakeSR.throwOnConstruct) throw new Error('NotAllowedError');
        FakeSR.instances.push(this);
        this.calls = [];
        this.running = false;
        this.lang = '';
        this.continuous = null;
        this.interimResults = null;
        this.maxAlternatives = null;
    }

    start() {
        if (FakeSR.throwOnStart) throw new Error('InvalidStateError');
        this.calls.push('start');
        this.running = true;
    }

    // stop(): what was said so far becomes final, then the session ends.
    stop() {
        this.calls.push('stop');
        if (FakeSR.throwOnStop) throw new Error('InvalidStateError');
        if (this.pendingFinal) this.say([[this.pendingFinal, true]]);
        this.finish();
    }

    // abort(): the real API reports "aborted" and ends; nothing pending is delivered.
    abort() {
        this.calls.push('abort');
        this.running = false;
        if (FakeSR.silentAbort) return;
        if (this.onerror) this.onerror({ error: 'aborted' });
        if (this.onend) this.onend();
    }

    finish() {
        this.running = false;
        if (this.onend) this.onend();
    }

    // results: [[transcript, isFinal], ...]
    say(items, resultIndex = 0) {
        const results = items.map(([text, isFinal]) => Object.assign([{ transcript: text, confidence: 0.9 }], { isFinal }));
        if (this.onresult) this.onresult({ resultIndex, results });
    }

    fail(code) {
        this.running = false;
        if (this.onerror) this.onerror({ error: code });
    }
}
FakeSR.instances = [];

function createEnv(bodyHtml, { api = true, standard = false, preRegister, timers = false } = {}) {
    FakeSR.instances = [];
    FakeSR.throwOnStart = false;
    FakeSR.throwOnConstruct = false;
    FakeSR.throwOnStop = false;
    FakeSR.silentAbort = false;
    const dom = new JSDOM(
        `<!DOCTYPE html><html lang="de-DE"><body><div dj-root>${bodyHtml}<input id="elsewhere"></div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = { log: () => {}, error: () => {}, debug: () => {}, info: () => {}, warn: (...a) => warnings.push(a.join(' ')) };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    if (api) window[standard ? 'SpeechRecognition' : 'webkitSpeechRecognition'] = FakeSR;
    const env = { window, warnings, sent: [], timers: [] };
    if (timers) {
        window.setTimeout = (fn, ms) => { env.timers.push({ fn, ms, live: true }); return env.timers.length; };
        window.clearTimeout = (id) => { if (env.timers[id - 1]) env.timers[id - 1].live = false; };
    }
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    window.djust.handleEvent = (name, params) => { env.sent.push({ name, params }); };
    return env;
}

const ch = (...codes) => String.fromCharCode(...codes);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function boot(markup, opts = {}) {
    const env = createEnv(markup, opts);
    if (opts.preRegister) opts.preRegister(env.window);
    env.window.eval(SCRIPT);
    env.window.djust.mountHooks();
    await tick();
    const $ = (s) => env.window.document.querySelector(s);
    Object.assign(env, { $, $$: (s) => Array.from(env.window.document.querySelectorAll(s)) });
    env.btn = $('.dj-voice-input');
    env.wrap = $('.dj-voice-field');
    env.hook = () => env.window.djust.getHook(env.btn);
    env.status = () => $('.dj-voice-input__status').textContent.trim();
    env.interim = () => $('.dj-voice-input__interim').textContent;
    env.said = [];
    const voiceHook = env.hook();
    if (voiceHook && voiceHook._say) {
        const say = voiceHook._say.bind(voiceHook);
        voiceHook._say = (m) => { env.said.push(m); say(m); };
    }
    env.rec = () => FakeSR.instances[FakeSR.instances.length - 1];
    env.press = () => env.btn.click();
    env.pressed = () => env.btn.getAttribute('aria-pressed');
    env.listening = () => env.pressed() === 'true';
    return env;
}

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

describe('the shipped hook never replaces an app hook (#2985 batch 5)', () => {
    const probe = (env) => env.btn.hasAttribute('aria-describedby');

    it('registers a hook, so mounting logs no "No hook registered"', async () => {
        const env = await boot(VOICE());
        expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
        expect(probe(env)).toBe(true);
    });

    it('without the script the warning is logged (gate-off)', () => {
        const env = createEnv(VOICE());
        env.window.djust.mountHooks();
        expect(env.warnings.some((w) => w.includes('No hook registered for "VoiceInput"'))).toBe(true);
    });

    it('an app hook in either registry, registered first, is the one that runs', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = await boot(VOICE(), { preRegister: (w) => { if (where === 'DjustHooks') w.DjustHooks = { VoiceInput: { mounted } }; else w.djust.hooks = { VoiceInput: { mounted } }; } });
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(probe(env)).toBe(false);
        }
    });

    it('an app hook registered AFTER the script still wins, in either registry', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = createEnv(VOICE());
            env.window.eval(SCRIPT);
            if (where === 'djust.hooks') env.window.djust.hooks = { VoiceInput: { mounted } };
            else env.window.DjustHooks.VoiceInput = { mounted };
            env.window.djust.mountHooks();
            await tick();
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(env.window.document.querySelector('.dj-voice-input').hasAttribute('aria-describedby')).toBe(false);
        }
    });

    it("an app hook's pushEvent/handleEvent API is untouched", async () => {
        const seen = [];
        await boot(VOICE(), { preRegister: (w) => { w.djust.hooks = { VoiceInput: { mounted() { seen.push(typeof this.pushEvent, typeof this.handleEvent); } } }; } });
        expect(seen).toEqual(['function', 'function']);
    });
});

// ---------------------------------------------------------------------------
// Opt-in
// ---------------------------------------------------------------------------

describe('nothing starts until the reader presses the microphone', () => {
    it('mounting, patching and waiting create no recognition object and ask for nothing', async () => {
        const env = await boot(VOICE());
        for (let i = 0; i < 3; i++) env.window.djust.updateHooks();
        env.window.dispatchEvent(new env.window.Event('focus'));
        env.btn.focus();
        env.window.document.dispatchEvent(new env.window.Event('visibilitychange'));
        expect(FakeSR.instances).toHaveLength(0);
        expect(env.listening()).toBe(false);
    });

    it('a press starts exactly one session: language, interim results, one alternative', async () => {
        const env = await boot(VOICE({ lang: 'fr-CA' }));
        env.press();
        expect(FakeSR.instances).toHaveLength(1);
        const rec = env.rec();
        expect(rec.calls).toEqual(['start']);
        expect(rec.lang).toBe('fr-CA');
        expect(rec.interimResults).toBe(true);
        expect(rec.continuous).toBe(false);
        expect(rec.maxAlternatives).toBe(1);
        expect(env.listening()).toBe(true);
        expect(env.status()).toBe('Listening. Speak now.');
    });

    it('works with the unprefixed SpeechRecognition too', async () => {
        const env = await boot(VOICE(), { standard: true });
        env.press();
        expect(FakeSR.instances).toHaveLength(1);
    });

    it('data-continuous asks the browser for a continuous session', async () => {
        const env = await boot(VOICE({ continuous: true }));
        env.press();
        expect(env.rec().continuous).toBe(true);
    });

    it('a language that is not a BCP 47 tag falls back to the page language, then to en-US', async () => {
        for (const bad of ['', 'x', 'en_US', '<img src=x>', 'en-US; drop', 'a'.repeat(40)]) {
            const env = await boot(VOICE({ lang: bad }));
            env.press();
            expect(env.rec().lang, bad).toBe('de-DE');
        }
        const none = createEnv(VOICE({ lang: '' }));
        none.window.document.documentElement.removeAttribute('lang');
        none.window.eval(SCRIPT);
        none.window.djust.mountHooks();
        await tick();
        none.window.document.querySelector('.dj-voice-input').click();
        expect(FakeSR.instances[FakeSR.instances.length - 1].lang).toBe('en-US');
    });

    it('a start that throws is reported and leaves the button idle', async () => {
        const env = await boot(VOICE(), { timers: true });
        FakeSR.throwOnStart = true;
        env.press();
        expect(env.status()).toBe('Voice input stopped because of a problem.');
        expect(env.listening()).toBe(false);
        expect(env.btn.getAttribute('aria-pressed')).toBe('false');
        expect(env.timers.every((t) => !t.live)).toBe(true);
        env.press();
        expect(FakeSR.instances).toHaveLength(2);
    });
});

describe('start edge cases', () => {
    it('a recognition object that cannot be built is reported and leaves the button idle', async () => {
        const env = await boot(VOICE());
        FakeSR.throwOnConstruct = true;
        env.press();
        expect(env.status()).toBe('Voice input stopped because of a problem.');
        expect(env.listening()).toBe(false);
    });

    it('start() while already listening does not open a second session', async () => {
        const env = await boot(VOICE());
        env.press();
        env.hook()._start();
        expect(FakeSR.instances).toHaveLength(1);
    });

    it('an invalid page language falls back to en-US', async () => {
        const env = createEnv(VOICE({ lang: '' }));
        env.window.document.documentElement.setAttribute('lang', 'x_y!');
        env.window.eval(SCRIPT);
        env.window.djust.mountHooks();
        await tick();
        env.window.document.querySelector('.dj-voice-input').click();
        expect(FakeSR.instances[0].lang).toBe('en-US');
    });
});

describe('a browser without the API', () => {
    it('gets a disabled button and "not supported", and nothing happens when it is pressed', async () => {
        const env = await boot(VOICE(), { api: false });
        expect(env.btn.disabled).toBe(true);
        expect(env.btn.getAttribute('aria-disabled')).toBe('true');
        expect(env.status()).toBe('Voice input is not supported in this browser.');
        env.press();
        expect(FakeSR.instances).toHaveLength(0);
        expect(env.listening()).toBe(false);
        expect(env.sent).toEqual([]);
    });

    it('keeps saying so after a patch', async () => {
        const env = await boot(VOICE(), { api: false });
        env.$('.dj-voice-input__status').textContent = '';
        env.btn.disabled = false;
        env.window.djust.updateHooks();
        expect(env.status()).toBe('Voice input is not supported in this browser.');
        expect(env.btn.disabled).toBe(true);
    });
});

// ---------------------------------------------------------------------------
// What is sent
// ---------------------------------------------------------------------------

describe('final transcripts', () => {
    it('are sent as {text} only, to the configured event; interim guesses are shown and never sent', async () => {
        const env = await boot(VOICE({ event: 'say' }));
        env.press();
        env.rec().say([['hel', false]]);
        expect(env.interim()).toBe('hel');
        env.rec().say([['hello wor', false]]);
        expect(env.interim()).toBe('hello wor');
        expect(env.sent).toEqual([]);
        env.rec().say([['hello world', true]]);
        expect(env.sent).toEqual([{ name: 'say', params: { text: 'hello world' } }]);
        expect(Object.keys(env.sent[0].params)).toEqual(['text']);
        expect(env.interim()).toBe('');
    });

    it('several finals in one event are each sent, in order, and a later interim is kept apart', async () => {
        const env = await boot(VOICE({ continuous: true }));
        env.press();
        env.rec().say([['one', true], ['two', true], ['thr', false]]);
        expect(env.sent.map((s) => s.params.text)).toEqual(['one', 'two']);
        expect(env.interim()).toBe('thr');
        env.rec().say([['one', true], ['two', true], ['three', true]], 2);
        expect(env.sent.map((s) => s.params.text)).toEqual(['one', 'two', 'three']);
    });

    it('are cleaned: control and bidirectional characters, whitespace, length', async () => {
        const env = await boot(VOICE());
        env.press();
        const rec = env.rec();
        rec.say([['  hello   world  ', true]]);
        rec.say([['a' + ch(0x202e) + 'b' + ch(0x200b) + 'c' + ch(0) + 'd\te\nf', true]]);
        rec.say([['x'.repeat(10000), true]]);
        rec.say([['   ', true]]);
        rec.say([[ch(0x202e, 0x200b), true]]);
        rec.say([['c1' + ch(0x85) + 'split' + ch(0x2028) + 'line' + ch(0x2029) + 'end' + ch(0xfeff) + 'bom', true]]);
        expect(env.sent.map((s) => s.params.text)).toEqual(['hello world', 'a b c d e f', 'x'.repeat(2000), 'c1 split line end bom']);
        rec.say([[12345, true], [null, true], [{ toString: () => 'obj' }, true]]);
        expect(env.sent).toHaveLength(4);
    });

    it('bounds Unicode transcripts without emitting an unpaired surrogate', async () => {
        const env = await boot(VOICE());
        env.press();
        env.rec().say([['x'.repeat(1999) + '😀', true]]);
        env.rec().say([['a' + String.fromCharCode(0xd800) + 'b', true]]);
        expect(env.sent[0].params.text).toBe('x'.repeat(1999) + '😀');
        expect(env.sent[1].params.text).toBe('a b');
    });

    it('stay text: markup in a transcript creates nothing', async () => {
        const env = await boot(VOICE());
        env.press();
        const hostile = '<img src=x onerror=window.__pwn=1>';
        env.rec().say([[hostile, false]]);
        env.rec().say([[hostile, true]]);
        expect(env.window.__pwn).toBeUndefined();
        expect(env.$$('.dj-voice-field img')).toHaveLength(0);
        expect(env.sent[0].params.text).toBe(hostile);
    });

    it('final transcripts keep their nearest component and embedded-view address', async () => {
        const env = await boot('<div data-component-id="outer" data-djust-embedded="outer-view">' +
            '<div data-component-id="voice-component" data-djust-embedded="voice-child">' + VOICE() + '</div></div>');
        env.press();
        env.rec().say([['hello', true]]);
        expect(env.sent).toEqual([{ name: 'transcribe', params: {
            text: 'hello', component_id: 'voice-component', view_id: 'voice-child',
        } }]);
    });

    it('strict transcript bindings receive the context element', async () => {
        const env = await boot(VOICE());
        env.window.djust._strictBinding = vi.fn(() => ({ text: 'hello', component_id: 'voice-component' }));
        env.press();
        env.rec().say([['hello', true]]);
        expect(env.window.djust._strictBinding).toHaveBeenCalledWith(env.btn, 'transcribe', { text: 'hello' }, [], env.btn);
        expect(env.sent[0].params.component_id).toBe('voice-component');
    });

    it('are sent through the strict-parameter gate like dj-click, and a veto is honoured', async () => {
        const env = await boot(VOICE());
        env.window.djust._strictBinding = vi.fn(() => false);
        env.press();
        env.rec().say([['hi', true]]);
        expect(env.window.djust._strictBinding).toHaveBeenCalledTimes(1);
        expect(env.sent).toEqual([]);
    });

    it('fall back to the hook pushEvent when the client API is absent; no event name sends nothing', async () => {
        const env = await boot(VOICE());
        delete env.window.djust.handleEvent;
        const push = vi.fn();
        env.hook().pushEvent = push;
        env.press();
        env.rec().say([['hi', true]]);
        expect(push).toHaveBeenCalledWith('transcribe', { text: 'hi' });
        const none = await boot(VOICE({ event: '' }));
        none.press();
        none.rec().say([['hi', true]]);
        expect(none.sent).toEqual([]);
    });

    it('a strict-binding rewrite of the parameters is what is sent, and the slot is marked on the sent object', async () => {
        const env = await boot(VOICE());
        env.window.djust._strictBinding = vi.fn(() => ({ text: 'rewritten' }));
        env.window.djust._markSlotOf = vi.fn();
        env.press();
        env.rec().say([['hi', true]]);
        expect(env.sent).toEqual([{ name: 'transcribe', params: { text: 'rewritten' } }]);
        expect(env.window.djust._markSlotOf).toHaveBeenCalledWith({ text: 'rewritten' }, env.btn);
    });

    it('interim phrases in one event are shown together', async () => {
        const env = await boot(VOICE({ continuous: true }));
        env.press();
        env.rec().say([['one', false], ['two', false]]);
        expect(env.interim()).toBe('one two');
    });

    it('a phrase ends the session when the browser ends it (one phrase at a time)', async () => {
        const env = await boot(VOICE());
        env.press();
        env.rec().say([['hi', true]]);
        env.rec().finish();
        expect(env.listening()).toBe(false);
        expect(env.status()).toBe('Stopped listening.');
        env.press();
        expect(FakeSR.instances).toHaveLength(2);
    });
});

// ---------------------------------------------------------------------------
// Pressing again
// ---------------------------------------------------------------------------

describe('pressing the microphone while listening', () => {
    it('stops (not aborts), so the phrase in progress is kept and sent', async () => {
        const env = await boot(VOICE());
        env.press();
        const rec = env.rec();
        rec.pendingFinal = 'almost done';
        env.press();
        expect(rec.calls).toEqual(['start', 'stop']);
        expect(env.sent.map((s) => s.params.text)).toEqual(['almost done']);
        expect(env.listening()).toBe(false);
        expect(env.status()).toBe('Stopped listening.');
    });
});

// ---------------------------------------------------------------------------
// Problems
// ---------------------------------------------------------------------------

describe('problems are announced in plain words', () => {
    const cases = {
        'not-allowed': 'Microphone access was denied. Allow the microphone for this site in your browser\'s settings to use voice input.',
        'service-not-allowed': 'Your browser does not allow speech recognition for this page.',
        'no-speech': 'No speech was heard. Press the microphone and try again.',
        'audio-capture': 'No microphone was found. Check that one is connected and not in use.',
        network: 'The speech service could not be reached. Voice input needs a network connection.',
        'language-not-supported': 'This language is not supported for voice input.',
        'bad-grammar': 'Voice input could not be started.',
        something_new: 'Voice input stopped because of a problem.',
    };
    for (const [code, message] of Object.entries(cases)) {
        it(`${code}: "${message.slice(0, 40)}..."`, async () => {
            const env = await boot(VOICE());
            env.press();
            env.rec().fail(code);
            env.rec().finish();
            expect(env.status()).toBe(message);
            expect(env.listening()).toBe(false);
            expect(env.sent).toEqual([]);
        });
    }

    it('an error code from the browser is never shown as it is (it could be anything)', async () => {
        const env = await boot(VOICE());
        env.press();
        env.rec().fail('<img src=x onerror=window.__pwn=1>');
        env.rec().fail('constructor');
        env.rec().fail('__proto__');
        expect(env.status()).toBe('Voice input stopped because of a problem.');
        expect(env.window.__pwn).toBeUndefined();
        expect(env.$$('.dj-voice-field img')).toHaveLength(0);
    });

    it('only the API codes get their own message: the extension\'s own state names do not', async () => {
        for (const code of ['listening', 'stopped', 'focus', 'hidden', 'timeout', 'unsupported', 'aborted']) {
            const env = await boot(VOICE());
            env.press();
            env.rec().fail(code);
            expect(env.status(), code).toBe('Voice input stopped because of a problem.');
        }
    });

    it('a problem and the end of the session clear the interim guess', async () => {
        const env = await boot(VOICE());
        env.press();
        env.rec().say([['half a sen', false]]);
        env.rec().fail('network');
        expect(env.interim()).toBe('');
        const other = await boot(VOICE());
        other.press();
        other.rec().say([['half a sen', false]]);
        other.rec().finish();
        expect(other.interim()).toBe('');
    });

    it('a problem is not overwritten by the "stopped" message that follows it', async () => {
        const env = await boot(VOICE());
        env.press();
        env.rec().fail('not-allowed');
        env.rec().finish();
        expect(env.status()).toMatch(/^Microphone access was denied/);
    });

    it('a new press after a problem starts a fresh session', async () => {
        const env = await boot(VOICE());
        env.press();
        env.rec().fail('no-speech');
        env.rec().finish();
        env.press();
        expect(FakeSR.instances).toHaveLength(2);
        expect(env.listening()).toBe(true);
    });
});

// ---------------------------------------------------------------------------
// Stopping when the reader is no longer there
// ---------------------------------------------------------------------------

describe('listening stops, by aborting, whenever the reader is no longer there', () => {
    const aborted = (env, message) => {
        expect(env.rec().calls).toEqual(['start', 'abort']);
        expect(env.listening()).toBe(false);
        if (message) expect(env.status()).toBe(message);
    };

    it('focus leaves the component', async () => {
        const env = await boot(VOICE());
        env.press();
        const out = new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: env.$('#elsewhere') });
        env.btn.dispatchEvent(out);
        aborted(env, 'Stopped listening because you moved away from the microphone.');
    });

    it('focus moving to a control inside the component, or the mic itself, does not', async () => {
        const env = await boot(VOICE());
        env.press();
        env.btn.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: env.$('.dj-voice-input__status') }));
        expect(env.listening()).toBe(true);
        expect(env.rec().calls).toEqual(['start']);
    });

    it('focus leaving to nothing (a click on the page background) stops it', async () => {
        const env = await boot(VOICE());
        env.press();
        env.btn.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
        aborted(env);
    });

    it('the window loses focus', async () => {
        const env = await boot(VOICE());
        env.press();
        env.window.dispatchEvent(new env.window.Event('blur'));
        aborted(env, 'Stopped listening because you moved away from the microphone.');
    });

    it('the tab is hidden (and a visibility change that leaves it visible does nothing)', async () => {
        const env = await boot(VOICE());
        env.press();
        env.window.document.dispatchEvent(new env.window.Event('visibilitychange'));
        expect(env.listening()).toBe(true);
        Object.defineProperty(env.window.document, 'hidden', { value: true, configurable: true });
        env.window.document.dispatchEvent(new env.window.Event('visibilitychange'));
        aborted(env, 'Stopped listening because the page is no longer in view.');
    });

    for (const [name, make] of [
        ['pagehide', (w) => new w.Event('pagehide')],
        ['popstate', (w) => new w.Event('popstate')],
        ['djust:before-navigate', (w) => new w.CustomEvent('djust:before-navigate', { detail: {} })],
    ]) {
        it(`${name}: the page is being left`, async () => {
            const env = await boot(VOICE());
            env.press();
            env.window.dispatchEvent(make(env.window));
            aborted(env);
        });
    }

    it('the component is removed (destroyed)', async () => {
        const env = await boot(VOICE());
        env.press();
        const rec = env.rec();
        env.hook().destroyed();
        expect(rec.calls).toEqual(['start', 'abort']);
    });

    it('the time limit passes (data-max-seconds, never more than 600)', async () => {
        const env = await boot(VOICE({ maxSeconds: 20 }), { timers: true });
        env.press();
        const timer = env.timers.find((t) => t.live);
        expect(timer.ms).toBe(20000);
        timer.fn();
        aborted(env, 'Stopped listening: the time limit was reached.');
        const big = await boot(VOICE({ maxSeconds: 99999 }), { timers: true });
        big.press();
        expect(big.timers.find((t) => t.live).ms).toBe(600000);
        const bad = await boot(VOICE({ maxSeconds: 'x' }), { timers: true });
        bad.press();
        expect(bad.timers.find((t) => t.live).ms).toBe(60000);
    });

    it('the time limit is cancelled when listening ends another way', async () => {
        const env = await boot(VOICE(), { timers: true });
        env.press();
        env.press();
        expect(env.timers.every((t) => !t.live)).toBe(true);
    });

    it('an abort is never reported as a problem, and a page that stays gets "Stopped listening."', async () => {
        const env = await boot(VOICE());
        env.press();
        env.window.dispatchEvent(new env.window.Event('popstate'));
        expect(env.said).not.toContain('Voice input stopped because of a problem.');
        expect(env.status()).toBe('Stopped listening.');
        expect(env.listening()).toBe(false);
    });

    it('an abort settles at once even if the browser never reports the end', async () => {
        const env = await boot(VOICE());
        env.press();
        FakeSR.silentAbort = true;
        env.window.dispatchEvent(new env.window.Event('blur'));
        expect(env.listening()).toBe(false);
        expect(env.status()).toMatch(/^Stopped listening because you moved away/);
    });

    it('a stop() that throws still ends the session', async () => {
        const env = await boot(VOICE());
        env.press();
        FakeSR.throwOnStop = true;
        env.press();
        expect(env.listening()).toBe(false);
    });

    it('the end of an old session cannot end the new one', async () => {
        const env = await boot(VOICE());
        env.press();
        const old = env.rec();
        env.window.dispatchEvent(new env.window.Event('blur'));
        env.press();
        expect(FakeSR.instances).toHaveLength(2);
        old.finish();
        expect(env.listening()).toBe(true);
    });

    it('what the browser says after an abort is not sent, and the old session cannot restart anything', async () => {
        const env = await boot(VOICE());
        env.press();
        const rec = env.rec();
        env.window.dispatchEvent(new env.window.Event('blur'));
        rec.say([['too late', true]]);
        rec.fail('network');
        rec.finish();
        expect(env.sent).toEqual([]);
        expect(env.status()).toBe('Stopped listening because you moved away from the microphone.');
        expect(FakeSR.instances).toHaveLength(1);
    });

    it('none of these starts anything when nobody is listening', async () => {
        const env = await boot(VOICE());
        env.window.dispatchEvent(new env.window.Event('blur'));
        env.window.dispatchEvent(new env.window.Event('pagehide'));
        env.btn.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
        expect(FakeSR.instances).toHaveLength(0);
        expect(env.status()).toBe('');
    });
});

// ---------------------------------------------------------------------------
// Server patches and teardown
// ---------------------------------------------------------------------------

describe('server re-renders', () => {
    it('re-derives what a patch reset, without interrupting a session', async () => {
        const env = await boot(VOICE());
        env.press();
        env.btn.setAttribute('aria-pressed', 'false');
        env.btn.removeAttribute('aria-describedby');
        env.window.djust.updateHooks();
        expect(env.listening()).toBe(true);
        expect(env.btn.getAttribute('aria-describedby')).toBe(env.$('.dj-voice-input__disclosure').id);
        expect(env.rec().calls).toEqual(['start']);
        env.rec().say([['still here', true]]);
        expect(env.sent).toHaveLength(1);
    });

    it('describes the button by the disclosure, with one id per component', async () => {
        const env = await boot(VOICE() + VOICE());
        const [a, b] = env.$$('.dj-voice-input');
        const [da, db] = env.$$('.dj-voice-input__disclosure');
        expect(a.getAttribute('aria-describedby')).toBe(da.id);
        expect(b.getAttribute('aria-describedby')).toBe(db.id);
        expect(da.id).not.toBe(db.id);
        expect(da.id).toMatch(/^dj-voice-disclosure-\d+$/);
    });

    it('two microphones have their own sessions', async () => {
        const env = await boot(VOICE() + VOICE());
        const [a, b] = env.$$('.dj-voice-input');
        a.click();
        expect(FakeSR.instances).toHaveLength(1);
        expect(a.getAttribute('aria-pressed')).toBe('true');
        expect(b.getAttribute('aria-pressed')).toBe('false');
    });
});

describe('teardown', () => {
    it('destroyed() removes every listener, aborts the session and ignores later input', async () => {
        const env = await boot(VOICE());
        const winRemoved = [];
        const orig = env.window.removeEventListener.bind(env.window);
        env.window.removeEventListener = (t, f, o) => { winRemoved.push(t); return orig(t, f, o); };
        const docRemoved = [];
        const origDoc = env.window.document.removeEventListener.bind(env.window.document);
        env.window.document.removeEventListener = (t, f, o) => { docRemoved.push(t); return origDoc(t, f, o); };
        const btnRemoved = [];
        const origBtn = env.btn.removeEventListener.bind(env.btn);
        env.btn.removeEventListener = (t, f, o) => { btnRemoved.push(t); return origBtn(t, f, o); };
        const wrapRemoved = [];
        const origWrap = env.wrap.removeEventListener.bind(env.wrap);
        env.wrap.removeEventListener = (t, f, o) => { wrapRemoved.push(t); return origWrap(t, f, o); };
        env.press();
        const hook = env.hook();
        hook.destroyed();
        expect(winRemoved.sort()).toEqual(['blur', 'djust:before-navigate', 'pagehide', 'popstate']);
        expect(docRemoved).toEqual(['visibilitychange']);
        expect(btnRemoved).toEqual(['click']);
        expect(wrapRemoved).toEqual(['focusout']);
        env.press();
        expect(FakeSR.instances).toHaveLength(1);
        hook.destroyed();
    });

    it('binds once however often the page patches: one press, one session', async () => {
        const env = await boot(VOICE());
        for (let i = 0; i < 4; i++) env.window.djust.updateHooks();
        env.press();
        expect(FakeSR.instances).toHaveLength(1);
        env.press();
        expect(FakeSR.instances).toHaveLength(1);
    });

    it('removing the element through a patch destroys the hook and stops listening', async () => {
        const env = await boot(VOICE());
        env.press();
        const rec = env.rec();
        env.wrap.remove();
        env.window.djust.updateHooks();
        expect(rec.calls).toEqual(['start', 'abort']);
    });
});

describe('what the script may not do', () => {
    it('records, stores and sends nothing itself', () => {
        const code = SCRIPT.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, '');
        for (const banned of [/getUserMedia/, /MediaRecorder/, /AudioContext/, /\bBlob\b/, /indexedDB/, /localStorage|sessionStorage/, /document\.cookie/, /\bfetch\s*\(/, /XMLHttpRequest/, /new WebSocket/, /sendBeacon/, /\binnerHTML\b/, /\beval\s*\(/, /createElement/]) {
            expect(code).not.toMatch(banned);
        }
        expect((code.match(/\.start\(\)/g) || []).length).toBe(1);
    });
});
