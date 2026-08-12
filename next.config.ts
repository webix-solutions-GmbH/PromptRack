import type { NextConfig } from "next";
import { BASE_PATH } from "./src/lib/base-path";

const nextConfig: NextConfig = {
  // Served at https://ki01.webix.de/agent-val (path-based routing in Caddy).
  basePath: BASE_PATH,
  experimental: {
    // Lets a page answer a refused role with `forbidden()` / `unauthorized()`
    // — a real 403/401 and our own page, instead of a thrown error's 500.
    authInterrupts: true,
  },
  // Emit `.next/standalone` so the Docker runner image does not need node_modules.
  output: "standalone",
  // `pg` is on Next's built-in server-external list, so it is never bundled.
  // These globs exist because scripts/init-db.mjs and scripts/seed-prompts.mjs
  // run *inside* the standalone image and import modules the app itself never
  // does (drizzle's migrator), which the tracer therefore cannot see.
  outputFileTracingIncludes: {
    "/*": [
      "node_modules/pg/**",
      "node_modules/pg-*/**",
      "node_modules/pgpass/**",
      "node_modules/postgres-*/**",
      "node_modules/split2/**",
      "node_modules/drizzle-orm/**",
    ],
  },
  // `/compare` became `/results` once the page could also show one model on its
  // own; query values are passed through, so bookmarked selections survive.
  async redirects() {
    return [{ source: "/compare", destination: "/results", permanent: false }];
  },
};

export default nextConfig;
