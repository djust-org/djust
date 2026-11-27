import { afterEach, vi } from 'vitest';
import { collectWindowCoverage } from './collect.js';
import { installLoadingReadyState } from '../_jsdom_ready_state.js';

const state = vi.hoisted(() => ({ windows: [] }));

async function mockFs() {
  const fs = await vi.importActual('node:fs');
  const path = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  function readFileSync(file, options) {
    const filename = path.resolve(file instanceof URL ? fileURLToPath(file) : String(file));
    const name = path.basename(filename);
    if (['client.js', 'debug-panel.js'].includes(name)
        && filename === path.resolve('python/djust/static/djust', name)) {
      return fs.readFileSync(path.resolve('.coverage-scripts', name), options);
    }
    return fs.readFileSync(file, options);
  }
  return { ...fs, readFileSync, default: { ...fs.default, readFileSync } };
}
vi.mock('node:fs', () => mockFs());
vi.mock('fs', () => mockFs());
// This mock replaces (does not wrap) the one in _jsdom_compat_setup.js, so it
// must apply the same jsdom 31 readyState shim itself (#4247).
vi.mock('jsdom', async () => {
  const actual = await vi.importActual('jsdom');
  class JSDOM extends actual.JSDOM {
    constructor(...args) {
      super(...args);
      installLoadingReadyState(this.window);
      state.windows.push(this.window);
    }
  }
  return { ...actual, JSDOM };
});

const fs = await vi.importActual('node:fs');
globalThis.__VITEST_COVERAGE__ = JSON.parse(fs.readFileSync('.coverage-scripts/baseline.json', 'utf8'));

afterEach(() => {
  globalThis.__VITEST_COVERAGE__ = collectWindowCoverage(globalThis.__VITEST_COVERAGE__, state.windows);
});
