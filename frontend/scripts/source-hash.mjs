// Hash of everything the built UI is made from. `node scripts/source-hash.mjs write` stores it in
// dist/.source-hash after a build; src/build-fresh.test.ts fails when the committed dist is stale.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const INPUTS = ["src", "index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json"];

function files(p) {
  const st = statSync(p);
  if (!st.isDirectory()) return [p];
  return readdirSync(p).sort().flatMap((n) => files(join(p, n)));
}

export function sourceHash() {
  const h = createHash("sha256");
  for (const f of INPUTS.flatMap((i) => files(join(root, i))).filter((f) => !/\.test\.tsx?$/.test(f))) {
    h.update(relative(root, f).replaceAll("\\", "/") + "\0");
    h.update(readFileSync(f));
  }
  return h.digest("hex");
}

if (process.argv[2] === "write") {
  writeFileSync(join(root, "dist", ".source-hash"), sourceHash() + "\n");
  console.log("dist/.source-hash written");
}
