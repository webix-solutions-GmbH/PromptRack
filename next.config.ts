import type { NextConfig } from "next";
import { BASE_PATH } from "./src/lib/base-path";

const nextConfig: NextConfig = {
  // Served at https://ki01.webix.de/agent-val (path-based routing in Caddy).
  basePath: BASE_PATH,
  // Emit `.next/standalone` so the Docker runner image does not need node_modules.
  output: "standalone",
  // better-sqlite3 is a native module: never bundle it, and make sure the
  // dynamically required prebuilt binding is traced into the standalone output.
  serverExternalPackages: ["better-sqlite3"],
  outputFileTracingIncludes: {
    "/*": ["node_modules/better-sqlite3/prebuilds/**"],
  },
};

export default nextConfig;
