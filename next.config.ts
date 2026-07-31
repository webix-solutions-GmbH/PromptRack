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
  // `/compare` became `/results` once the page could also show one model on its
  // own; query values are passed through, so bookmarked selections survive.
  async redirects() {
    return [{ source: "/compare", destination: "/results", permanent: false }];
  },
};

export default nextConfig;
