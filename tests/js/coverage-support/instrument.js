import path from 'node:path';
import fs from 'node:fs';
import MagicString, { Bundle } from 'magic-string';
import { createInstrumenter } from '@vitest/istanbul-lib-instrument';

function scriptInstrumenter() {
  return createInstrumenter({
    coverageVariable: '__VITEST_COVERAGE__', coverageGlobalScope: 'this',
    coverageGlobalScopeFunc: true, produceSourceMap: true,
    preserveComments: true, compact: false,
  });
}

// The build joins fragments that may share a block or class. Instrument that
// exact executable unit and use its concatenation map to recover module lines.
export function instrumentBundle(fs, filename) {
  const client = path.basename(filename) === 'client.js';
  const dir = path.join(path.dirname(filename), client ? 'src' : 'src/debug');
  const bundle = new Bundle({ separator: '', intro: client ? ';(function () {\n' : '' });
  const sources = fs.readdirSync(dir).filter(name => /^\d.*\.js$/.test(name)).sort().map(name => path.join(dir, name));
  for (const source of sources) {
    bundle.addSource({ filename: source, content: new MagicString(fs.readFileSync(source, 'utf8')) });
  }
  if (client) bundle.append('\n})();\n');
  const original = fs.readFileSync(filename, 'utf8');
  if (bundle.toString() !== original) throw new Error(`Stale bundle: ${filename}. Run make build-js.`);
  const map = bundle.generateMap({ hires: true, includeContent: true, file: filename });
  const instrumenter = scriptInstrumenter();
  const code = instrumenter.instrumentSync(original, filename, JSON.parse(map.toString()));
  return { code, coverage: instrumenter.lastFileCoverage(), sources };
}

// Use explicitly for independently executable modules. Source-inspection tests
// keep readFileSync so instrumentation never changes their text assertions.
export function readScript(filename) {
  const source = fs.readFileSync(filename, 'utf8');
  if (!globalThis.__VITEST_COVERAGE__) return source;
  return instrumentSource(source, filename);
}

export function instrumentSource(source, filename) {
  const absolute = path.resolve(filename);
  const map = new MagicString(source).generateMap({ hires: true, includeContent: true, source: absolute });
  return scriptInstrumenter().instrumentSync(source, absolute + '.instrumented', JSON.parse(map.toString()));
}
