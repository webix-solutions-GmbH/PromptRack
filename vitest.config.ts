import { defineConfig } from 'vitest/config';
import path from 'node:path';

// The pure suite: no database, no docker, milliseconds. Everything that needs a
// real Postgres lives in tests/integration and runs from
// vitest.integration.config.ts instead.
export default defineConfig({
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  test: { include: ['src/**/*.test.ts'], exclude: ['tests/integration/**'] },
});
