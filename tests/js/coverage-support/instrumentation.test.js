import { describe, it, expect } from 'vitest';
import { runInNewContext } from 'node:vm';
import path from 'node:path';
import * as libCoverage from '@vitest/istanbul-lib-coverage';
import * as libSourceMaps from '@vitest/istanbul-lib-source-maps';
import { collectWindowCoverage } from './collect.js';
import { instrumentBundle, instrumentSource } from './instrument.js';

function fixture() {
  const filename = path.resolve('scratch/coverage-fixture/client.js');
  const first = path.resolve('scratch/coverage-fixture/src/00-open.js');
  const second = path.resolve('scratch/coverage-fixture/src/01-close.js');
  const files = {
    [first]: 'function choose(flag) {\n  if (flag) { return "yes"; }\n',
    [second]: '  return "no";\n}\nglobalThis.result = choose(true);\n',
  };
  files[filename] = ';(function () {\n' + files[first] + files[second] + '\n})();\n';
  return {
    filename, first, second, files,
    fs: { readFileSync: name => files[name], readdirSync: () => ['00-open.js', '01-close.js'] },
  };
}

describe('dynamic script coverage attribution', () => {
  it('maps executed and unexecuted branches to original cross-file fragments', async () => {
    const f = fixture();
    const { code } = instrumentBundle(f.fs, f.filename);
    const context = {};
    runInNewContext(code, context);
    expect(context.result).toBe('yes');
    const mapped = await libSourceMaps.createSourceMapStore().transformCoverage(
      libCoverage.createCoverageMap(context.__VITEST_COVERAGE__),
    );
    expect(mapped.files().sort()).toEqual([f.first, f.second].sort());
    const first = mapped.fileCoverageFor(f.first).data;
    expect(Object.entries(first.statementMap).some(([id, loc]) => loc.start.line === 2 && first.s[id] === 1)).toBe(true);
    const second = mapped.fileCoverageFor(f.second).data;
    const unexecuted = Object.entries(second.statementMap).filter(([, loc]) => loc.start.line === 1);
    expect(unexecuted.length).toBeGreaterThan(0);
    expect(unexecuted.every(([id]) => second.s[id] === 0)).toBe(true);
    expect(Object.values(first.b).some(hits => hits.includes(0) && hits.includes(1))).toBe(true);
  });

  it('merges standalone and bundle execution without duplicating source locations', async () => {
    const f = fixture();
    f.files[f.first] = 'function choose(flag) { if (flag) return 1; return 2; }\n';
    f.files[f.second] = 'globalThis.result = choose(true);\n';
    f.files[f.filename] = ';(function () {\n' + f.files[f.first] + f.files[f.second] + '\n})();\n';
    const bundle = {};
    runInNewContext(instrumentBundle(f.fs, f.filename).code, bundle);
    const standalone = {};
    runInNewContext(instrumentSource(f.files[f.first], f.first) + '\nchoose(false);', standalone);
    const remap = data => libSourceMaps.createSourceMapStore().transformCoverage(libCoverage.createCoverageMap(data));
    const bundledMap = await remap(bundle.__VITEST_COVERAGE__);
    const mergedMap = await remap(collectWindowCoverage({}, [bundle, standalone]));
    const before = bundledMap.fileCoverageFor(f.first).toSummary().data;
    const after = mergedMap.fileCoverageFor(f.first).toSummary().data;
    for (const metric of ['statements', 'functions', 'branches', 'lines']) {
      expect(after[metric].total).toBe(before[metric].total);
    }
    expect(after.branches.covered).toBeGreaterThan(before.branches.covered);
  });

  it('collects independent contexts exactly once without covering the untaken branch', () => {
    const f = fixture();
    const { code } = instrumentBundle(f.fs, f.filename);
    const windows = [{}, {}];
    for (const window of windows) runInNewContext(code, window);
    const once = collectWindowCoverage({}, windows);
    expect(windows).toHaveLength(0);
    const twice = collectWindowCoverage(once, windows);
    expect(twice).toEqual(once);
    const data = libCoverage.createCoverageMap(twice).fileCoverageFor(f.filename).data;
    expect(Object.values(data.b).some(hits => hits.includes(0) && hits.includes(2))).toBe(true);
  });

  it('does not invent execution when instrumentation is disabled', () => {
    const f = fixture();
    const context = {};
    runInNewContext(f.files[f.filename], context);
    expect(context.result).toBe('yes');
    expect(context.__VITEST_COVERAGE__).toBeUndefined();
  });

  it('rejects stale bundles instead of assigning counts to the wrong source', () => {
    const f = fixture();
    f.files[f.filename] += '\n// stale output';
    expect(() => instrumentBundle(f.fs, f.filename)).toThrow('Stale bundle');
  });
});
