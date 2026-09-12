import fs from 'node:fs';
import path from 'node:path';
import { createCoverageMap, createFileCoverage } from '@vitest/istanbul-lib-coverage';
import { createSourceMapStore } from '@vitest/istanbul-lib-source-maps';
import { instrumentBundle } from '../tests/js/coverage-support/instrument.js';

const output = path.resolve('.coverage-scripts');
fs.mkdirSync(output, { recursive: true });
const baseline = createCoverageMap({});
for (const name of ['client.js', 'debug-panel.js']) {
  const filename = path.resolve('python/djust/static/djust', name);
  const { code, coverage, sources } = instrumentBundle(fs, filename);
  fs.writeFileSync(path.join(output, name), code);
  const mapped = await createSourceMapStore().transformCoverage(createCoverageMap({ [filename]: coverage }));
  for (const source of sources) {
    // Delimiter-only fragments (e.g. the double-load guard's closing brace)
    // have no executable locations. Keep an empty record instead of asking
    // the provider to parse them as independent JavaScript programs.
    if (!mapped.files().includes(source)) mapped.addFileCoverage(createFileCoverage(source));
  }
  baseline.merge(mapped);
}
fs.writeFileSync(path.join(output, 'baseline.json'), JSON.stringify(baseline));
console.log(`Prepared coverage for ${baseline.files().length} source fragments.`);
