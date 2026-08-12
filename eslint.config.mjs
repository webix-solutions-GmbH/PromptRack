import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // The `db` handle belongs to the data-access layer. Everything else goes
    // through a scoped repository in src/db/repo/*, so no query can be written
    // without a Scope. `@/db/schema` stays allowed — components legitimately
    // import table row types.
    files: ["src/**/*.ts", "src/**/*.tsx"],
    ignores: [
      "src/db/**",
      // Infrastructure, not data access: run-lock.ts takes a Postgres advisory
      // lock on a dedicated connection, which needs the pool itself.
      "src/lib/run-lock.ts",
    ],
    rules: {
      "no-restricted-imports": ["error", {
        paths: [{
          name: "@/db",
          message:
            "Import a scoped repository from @/db/repo/* instead. Every query takes a Scope; see docs/superpowers/plans/phase-3-data-layer.md.",
        }],
        patterns: [{
          group: ["**/db/index", "**/db/index.ts"],
          message: "Import a scoped repository from @/db/repo/* instead.",
        }],
      }],
    },
  },
]);

export default eslintConfig;
