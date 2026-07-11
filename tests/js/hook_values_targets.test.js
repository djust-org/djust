/**
 * ADR-025 Feature D: dj-hook typed values + scoped targets.
 *
 * - JSON-first coercion matrix for dj-hook-value-* attributes
 * - camelCase -> kebab-case attribute mapping
 * - live reads: attribute changes (server morph) are visible immediately
 * - read-only: assignment throws TypeError
 * - target()/targets(): subtree-scoped lookup by dj-hook-target
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    try {
        window.eval(clientCode);
    } catch (e) { /* client.js may throw on missing DOM APIs */ }
    return { window, document: window.document };
}

/** Mount a single hook and capture its instance via mounted(). */
function mountHook(env, hookName = 'Probe') {
    let captured = null;
    env.window.djust.hooks = {
        [hookName]: {
            mounted() { captured = this; },
        },
    };
    env.window.djust.mountHooks(env.document);
    expect(captured).not.toBeNull();
    return captured;
}

describe('this.values coercion matrix', () => {
    it('JSON-parses arrays, objects, numbers, booleans; falls back to string', () => {
        const env = createEnv(`
            <div dj-hook="Probe" dj-view="t"
                 dj-hook-value-points="[1,2,3]"
                 dj-hook-value-config='{"animate":true}'
                 dj-hook-value-count="7"
                 dj-hook-value-animated="true"
                 dj-hook-value-title="Sales"
                 dj-hook-value-code='"007"'></div>`);
        const hook = mountHook(env);
        expect(hook.values.points).toEqual([1, 2, 3]);
        expect(hook.values.config).toEqual({ animate: true });
        expect(hook.values.count).toBe(7);
        expect(hook.values.animated).toBe(true);
        expect(hook.values.title).toBe('Sales');   // JSON.parse fails -> raw string
        expect(hook.values.code).toBe('007');      // explicit JSON string wins
    });

    it('absent attribute -> undefined; "in" reflects presence', () => {
        const env = createEnv('<div dj-hook="Probe" dj-view="t" dj-hook-value-present="1"></div>');
        const hook = mountHook(env);
        expect(hook.values.missing).toBeUndefined();
        expect('present' in hook.values).toBe(true);
        expect('missing' in hook.values).toBe(false);
    });

    it('maps camelCase property to kebab-case attribute', () => {
        const env = createEnv('<div dj-hook="Probe" dj-view="t" dj-hook-value-points-per-page="25"></div>');
        const hook = mountHook(env);
        expect(hook.values.pointsPerPage).toBe(25);
    });
});

describe('this.values liveness + read-only', () => {
    it('reads the CURRENT attribute after a morph-style update', () => {
        const env = createEnv('<div id="h" dj-hook="Probe" dj-view="t" dj-hook-value-count="1"></div>');
        const hook = mountHook(env);
        expect(hook.values.count).toBe(1);
        env.document.getElementById('h').setAttribute('dj-hook-value-count', '2');
        expect(hook.values.count).toBe(2); // live read, no re-mount needed
    });

    it('assignment throws TypeError (read-only in v1)', () => {
        // NOTE: this JSDOM instance is a separate realm from the outer test
        // file (window.eval(clientCode) runs the Proxy's `throw new
        // TypeError(...)` inside that realm), so `toThrow(TypeError)`
        // (identity/instanceof-based) cannot pass here — same reason
        // tests/js/js_ext_commands.test.js asserts thrown errors by message
        // regex rather than by class. Assert both the message AND the
        // cross-realm-safe `.name === 'TypeError'` to pin the error type.
        const env = createEnv('<div dj-hook="Probe" dj-view="t"></div>');
        const hook = mountHook(env);
        let caught;
        try {
            hook.values.count = 5;
        } catch (e) {
            caught = e;
        }
        expect(caught).toBeDefined();
        expect(caught.name).toBe('TypeError');
        expect(() => { hook.values.count = 5; }).toThrow(/read-only/);
    });
});

describe('target() / targets()', () => {
    it('finds first and all dj-hook-target descendants, scoped to the hook subtree', () => {
        const env = createEnv(`
            <div dj-hook="Probe" dj-view="t">
                <canvas dj-hook-target="canvas" id="c1"></canvas>
                <canvas dj-hook-target="canvas" id="c2"></canvas>
                <span dj-hook-target="label"></span>
            </div>
            <canvas dj-hook-target="canvas" id="outside"></canvas>`);
        const hook = mountHook(env);
        expect(hook.target('canvas').id).toBe('c1');
        expect(hook.targets('canvas').map((el) => el.id)).toEqual(['c1', 'c2']);
        expect(hook.targets('canvas')).not.toContainEqual(
            env.document.getElementById('outside')
        );
        expect(hook.target('nope')).toBeNull();
        expect(hook.targets('nope')).toEqual([]);
    });
});
