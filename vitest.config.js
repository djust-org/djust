import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['python/djust/static/**/*.js'],
      exclude: ['node_modules', 'tests', '**/*.test.js'],
    },
  },
});
