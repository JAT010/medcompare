import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

const passwordPatterns = [
  /["'`]password["'`]\s*[:=]\s*["'`][^"'`]{3,}["'`]/i,
  /seed_password\s*=\s*["'`][^"'`]+["'`]/i,
  /admin_password\s*=\s*["'`][^"'`]+["'`]/i,
  /user_password\s*=\s*["'`][^"'`]+["'`]/i,
  /reviewer_password\s*=\s*["'`][^"'`]+["'`]/i,
];

describe("no hardcoded demo passwords in frontend", () => {
  const files = walk(ROOT).filter(
    (f) => /\.(ts|tsx|js|jsx|json|html|css)$/.test(f) && !f.includes("node_modules"),
  );

  it("scans a non-trivial number of files", () => {
    expect(files.length).toBeGreaterThan(5);
  });

  it("contains no password literal assignments", () => {
    const offenders: string[] = [];
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      for (const re of passwordPatterns) {
        if (re.test(text)) offenders.push(`${f}: ${re}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("does not embed env var values (MEDCOMPARE_DEMO_*_PASSWORD values)", () => {
    const offenders: string[] = [];
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      // Allow the *name* of the env var in comments/docs, but not a literal value assignment.
      if (/MEDCOMPARE_DEMO_\w+_PASSWORD\s*=\s*["'`][^"'`]+["'`]/.test(text)) {
        offenders.push(f);
      }
    }
    expect(offenders).toEqual([]);
  });
});
