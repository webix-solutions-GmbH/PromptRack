import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import * as schema from './schema';

const dataDir = path.join(process.cwd(), 'data');
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

const dbPath = path.join(dataDir, 'app.db');

function createDb() {
  const sqlite = new Database(dbPath);
  sqlite.pragma('journal_mode = WAL');
  return drizzle(sqlite, { schema });
}

declare global {
  var __db: ReturnType<typeof createDb> | undefined;
}

export const db = globalThis.__db ?? createDb();

if (process.env.NODE_ENV !== 'production') {
  globalThis.__db = db;
}
