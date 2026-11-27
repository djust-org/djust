// The coverage pass must measure the `default` project only. Merging the
// `split-build` project's (uninstrumented) records into it doubled every
// module's statement/branch/function totals after the jsdom 31 bump (#4337),
// dropping reported statement coverage from ~80% to ~46% with no code change.
import { describe, it, expect } from 'vitest';
import coverageConfig from '../../vitest.coverage.config.js';
import baseConfig from '../../vitest.config.js';

describe('vitest.coverage.config.js projects', () => {
  it('keeps split-build in the plain test run', () => {
    const names = baseConfig.test.projects.map((p) => p.test && p.test.name);
    expect(names).toContain('split-build');
  });

  it('measures coverage over the default project only', () => {
    const names = coverageConfig.test.projects.map((p) => p.test && p.test.name);
    expect(names).toEqual(['default']);
  });
});
