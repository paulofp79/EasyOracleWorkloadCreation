import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
// @ts-expect-error plain ESM helper without types
import { sourceHash } from "../scripts/source-hash.mjs";

describe("committed UI build", () => {
  it("frontend/dist was built from the current source (run `npm run build` and commit dist/)", () => {
    const stamp = join(__dirname, "..", "dist", ".source-hash");
    expect(existsSync(stamp)).toBe(true);
    expect(readFileSync(stamp, "utf8").trim()).toBe(sourceHash());
  });
});
