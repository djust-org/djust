import { defineConfig, mergeConfig } from 'vitest/config';
import base from './vitest.config.js';

const config = mergeConfig(base, defineConfig({
  test: {
    setupFiles: ['./tests/js/coverage-support/setup.js'],
    coverage: {
      provider: 'istanbul',
      reporter: ['text', 'json', 'html', 'json-summary'],
      include: ['python/djust/static/djust/src/**/*.js'],
      exclude: ['node_modules', 'tests', '**/*.test.js'],
      // Regression floors measured over the entire client + debug module tree.
      // The old 85% values were never measuring these dynamically loaded scripts.
      thresholds: { lines: 67, functions: 63, branches: 54, statements: 65 },
    },
  },
}));

// Measure the `default` project only. `split-build` (#4007) re-runs the chunk
// tests against client-core.js + chunks, which are not instrumented, so it adds
// no executed statements. Since the jsdom 31 bump (#4337), merging its records
// with `default`'s doubled the statement, branch and function totals of every
// module (lines were unchanged: 45.6% statements against 79.9% for `default`
// alone), which put main's javascript-coverage job under its floors.
config.test.projects = config.test.projects.filter((project) => project.test?.name !== 'split-build');

export default config;
