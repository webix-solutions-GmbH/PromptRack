import { defineConfig } from 'vitest/config';
import path from 'node:path';

// These suites share one scratch database and truncate it between tests, so
// they must never run concurrently. `fileParallelism: false` caps the worker
// count at one (Vitest 4 removed `poolOptions.forks.singleFork`); file
// isolation stays on, so each suite gets its own connection pool to close.
export default defineConfig({
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  test: {
    include: ['tests/integration/**/*.test.ts'],
    setupFiles: ['tests/integration/setup.ts'],
    pool: 'forks',
    fileParallelism: false,
  },
});
