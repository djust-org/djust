import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['python/djust/static/djust/decorators.js'],
      exclude: ['node_modules', 'tests', '**/*.test.js'],
      // Coverage thresholds
      thresholds: {
        lines: 85,
        functions: 85,
        branches: 85,
        statements: 85,
      },
      // Report uncovered lines
      all: true,
      // Skip full coverage check (we want to see what's missing)
      skipFull: false,
    },
  },
});
