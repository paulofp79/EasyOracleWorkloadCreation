// Mirror of backend/app/validators.py. Convenience only: the server validates everything again.
// Both are tested against shared/validation-vectors.json.

export type Rule = (v: unknown) => string | null;

const isNum = (v: unknown): v is number => typeof v === "number";

function text(v: unknown): string | null {
  if (typeof v === "string") return v;
  if (isNum(v)) return String(v);
  return null;
}

function re(rx: RegExp, msg: string, extra?: (s: string) => string | null): Rule {
  return (v) => {
    const s = text(v);
    if (s === null) return "is required";
    if (!rx.test(s)) return msg;
    return extra ? extra(s) : null;
  };
}

const CONTROL = /[\x00-\x1f\x7f]/;

const positiveInt: Rule = (v) => {
  if (isNum(v)) return Number.isInteger(v) && v >= 1 ? null : "must be a positive integer";
  return re(/^[1-9][0-9]*$/, "must be a positive integer without leading zeros")(v);
};

const PDB_NAME = /^[A-Za-z][A-Za-z0-9_]{0,25}_[0-9]{3}$/;

export const RULES: Record<string, Rule> = {
  existing_action: (v) => (v === "drop" || v === "skip" || v === "abort" ? null : "must be drop, skip or abort"),
  confirm_token: (v) => (typeof v === "string" && /^[0-9a-f]{16}$/.test(v) ? null : "is not a valid confirmation token; run the plan again"),
  confirm_delete: (v) => (v === "DELETE" ? null : "type DELETE to confirm"),
  pdb_names: (v) => {
    if (!Array.isArray(v) || v.length === 0) return "must be a non-empty list of PDB names";
    if (v.length > 999) return "at most 999 PDBs";
    const bad = v.find((x) => typeof x !== "string" || !PDB_NAME.test(x) || x.endsWith("_000"));
    return bad === undefined ? null : `${JSON.stringify(bad)} is not a PDB name like PDBPP_001`;
  },
  prefix: re(/^[A-Za-z][A-Za-z0-9_]*$/, "must start with a letter and use only letters, digits and underscore",
    (s) => (s.length > 26 ? "must be at most 26 characters" : null)),
  pdb_number: (v) => positiveInt(v) ?? (Number(v) > 999 ? "must be between 1 and 999" : null),
  positive_int: positiveInt,
  scan_host: re(/^[A-Za-z0-9._-]+$/, "may contain only letters, digits, '.', '_' and '-'"),
  scan_port: (v) => {
    const s = text(v);
    if (s === null || !/^[0-9]+$/.test(s)) return "must be a number between 1 and 65535";
    const n = Number(s);
    return n >= 1 && n <= 65535 ? null : "must be a number between 1 and 65535";
  },
  service_suffix: (v) => (v == null ? null : re(/^[A-Za-z0-9._-]*$/, "may contain only letters, digits, '.', '_' and '-'")(v)),
  scale: re(/^(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$/, "must be a positive number, e.g. 0.1 or 5",
    (s) => (/[1-9]/.test(s) ? null : "must be a positive number, e.g. 0.1 or 5")),
  tablespace: re(/^[A-Za-z][A-Za-z0-9_$#]*$/, "is not a valid tablespace name"),
  runtime: re(/^[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?$/, "must look like HH:MM, e.g. 00:30"),
  disable_txns: (v) => (v == null ? null : re(/^[A-Za-z0-9,]*$/, "must be a comma-separated list, e.g. SQ,WQ,WA")(v)),
  start_delay: (v) => {
    if (isNum(v)) return v >= 0 ? null : "must be a non-negative number of seconds";
    return re(/^[0-9]+(?:\.[0-9]+)?$/, "must be a non-negative number of seconds, e.g. 0 or 0.5")(v);
  },
  boolean: (v) => (typeof v === "boolean" || v === "true" || v === "false" ? null : "must be true or false"),
  abs_path: re(/^\//, "must be an absolute path", (s) => (CONTROL.test(s) ? "contains invalid characters" : null)),
  password: (v) => {
    if (typeof v !== "string" || v === "") return "must not be empty";
    return CONTROL.test(v) ? "must not contain control characters or line breaks" : null;
  },
  quoted_password: (v) => RULES.password(v) ?? (String(v).includes('"') ? 'must not contain a double quote (")' : null),
  profile_name: (v) => {
    const s = text(v);
    return s !== null && /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$/.test(s.trim())
      ? null : "must be 1-64 characters: letters, digits, space, '.', '_' or '-'";
  },
};

export const RANGE_PAIRS: [string, string][] = [
  ["create_first", "create_last"], ["load_first", "load_last"], ["workload_first", "workload_last"], ["first", "last"],
];

/** Validate a params object against {field: rule}; returns {field: message}. */
export function validateParams(params: Record<string, unknown>, spec: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [field, rule] of Object.entries(spec)) {
    const msg = RULES[rule]?.(params[field]);
    if (msg) errors[field] = msg;
  }
  for (const [a, b] of RANGE_PAIRS) {
    if (!errors[a] && !errors[b] && a in params && b in params && Number(params[a]) > Number(params[b])) {
      errors[b] = `must be greater than or equal to ${a.replace("_", " ")}`;
    }
  }
  return errors;
}
