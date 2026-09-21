import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Guardrail for the invariant in lib/session.tsx / netlify.toml:
 * NEXT_PUBLIC_ALLOW_MOCK_SESSION is fine to ship because it is not a
 * secret, but nothing that IS secret (a service-role key, a database
 * URL, a privileged token) may ever be read by client-bundled code —
 * only NEXT_PUBLIC_-prefixed env vars are safe there, since anything
 * else silently resolves to `undefined` in the browser bundle rather
 * than raising, which is exactly the kind of mistake this test is
 * meant to catch before it ships.
 */

const ROOT = join(__dirname, "..", "..");
const CLIENT_BUNDLED_DIRS = ["app", "components", "lib"];
const ALLOWED_UNPREFIXED_ENV_VARS = new Set(["NODE_ENV"]);

function collectSourceFiles(dir: string): string[] {
  const entries = readdirSync(dir);
  const files: string[] = [];
  for (const entry of entries) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      files.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

describe("no server-only secrets in client-bundled source", () => {
  const files = CLIENT_BUNDLED_DIRS.flatMap((dir) => collectSourceFiles(join(ROOT, dir)));

  it("found source files to scan", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it("every process.env reference in app/, components/, and lib/ is NEXT_PUBLIC_-prefixed (or NODE_ENV)", () => {
    const violations: string[] = [];

    for (const file of files) {
      const source = readFileSync(file, "utf8");
      const matches = source.matchAll(/process\.env\.([A-Z0-9_]+)/g);
      for (const match of matches) {
        const name = match[1];
        if (!ALLOWED_UNPREFIXED_ENV_VARS.has(name) && !name.startsWith("NEXT_PUBLIC_")) {
          violations.push(`${file.replace(ROOT + "/", "")}: process.env.${name}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
