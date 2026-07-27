import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
