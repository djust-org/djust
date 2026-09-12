import * as libCoverage from '@vitest/istanbul-lib-coverage';

// Keep references even after JSDOM.close(); drain once so a later test cannot
// count the same window's execution again.
export function collectWindowCoverage(current, windows) {
  const coverage = libCoverage.createCoverageMap(current || {});
  for (const window of windows.splice(0)) {
    const data = window.__VITEST_COVERAGE__;
    if (data && data !== current) coverage.merge(data);
  }
  return coverage.toJSON();
}
