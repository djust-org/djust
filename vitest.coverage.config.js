import { defineConfig, mergeConfig } from 'vitest/config';
import base from './vitest.config.js';

export default mergeConfig(base, defineConfig({
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
