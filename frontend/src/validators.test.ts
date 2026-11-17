import { describe, expect, it } from "vitest";
import vectors from "../../shared/validation-vectors.json";
import { RULES, validateParams } from "./validators";

const rules = Object.entries(vectors).filter(([k]) => !k.startsWith("_")) as [string, { valid: unknown[]; invalid: unknown[] }][];

describe("shared validation vectors", () => {
  it("covers every rule", () => {
    expect(new Set(rules.map(([k]) => k))).toEqual(new Set(Object.keys(RULES)));
  });
  for (const [rule, sets] of rules) {
    for (const v of sets.valid) {
      it(`${rule} accepts ${JSON.stringify(v)}`, () => expect(RULES[rule](v)).toBeNull());
    }
    for (const v of sets.invalid) {
      it(`${rule} rejects ${JSON.stringify(v)}`, () => expect(RULES[rule](v)).not.toBeNull());
    }
  }
});

describe("validateParams", () => {
  it("reports range order", () => {
    expect(validateParams({ load_first: "9", load_last: "3" }, { load_first: "pdb_number", load_last: "pdb_number" }))
      .toEqual({ load_last: "must be greater than or equal to load first" });
  });
});
