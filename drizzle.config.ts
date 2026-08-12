import { defineConfig } from 'drizzle-kit';

export default defineConfig({
  dialect: 'postgresql',
  schema: './src/db/schema.ts',
  out: './drizzle',
  // `generate` needs no credentials; this is only for `drizzle-kit studio`.
  dbCredentials: {
    url: process.env.DATABASE_URL ?? 'postgres://agentval:dev@127.0.0.1:5433/agentval',
  },
});
