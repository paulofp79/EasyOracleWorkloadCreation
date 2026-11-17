export class ApiError extends Error {
  constructor(public status: number, message: string, public errors: Record<string, string> = {}, public ref?: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok) {
    const msg = data?.error ?? `HTTP ${res.status}`;
    throw new ApiError(res.status, data?.ref ? `${msg} (ref ${data.ref})` : msg, data?.errors ?? {}, data?.ref);
  }
  return data as T;
}

export type Params = Record<string, string | number | boolean>;

export interface Profile {
  id: number;
  name: string;
  is_active: boolean;
  params: Params;
  created_at: string;
  updated_at: string;
}

export interface FieldMeta {
  rule: string;
  default: string | number | boolean;
}

export type CheckStatus = "pass" | "warn" | "fail" | "skip";

export interface EnvCheckItem {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
  error: string;
}

export interface EnvCheck {
  status: CheckStatus;
  mode: "real" | "simulate";
  profile: { id: number; name: string };
  checks: EnvCheckItem[];
}

export type ItemState = "pending" | "checking" | "running" | "ok" | "failed" | "skipped" | "not_started" | "cancelled";
export type RunStatus = "running" | "ok" | "failed" | "cancelled" | "interrupted" | "blocked";

export interface RunItem {
  pdb: string;
  ord: number;
  state: ItemState;
  con_uid: string | null;
  check_status: string | null;
  check_detail: string | null;
  instance: string | null;
  pid: number | null;
  pgid: number | null;
  exit_code: number | null;
  started_at: string | null;
  ended_at: string | null;
  detail: string | null;
  error_count: number | null;
  first_error: string | null;
}

export interface Run {
  id: string;
  type: string;
  status: RunStatus;
  phase: string | null;
  params: Record<string, any>;
  run_dir: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  summary: Record<string, any> | null;
  message: string | null;
  retry_of: string | null;
  counts: Partial<Record<ItemState, number>>;
  total: number;
  event_seq: number | null;
  live: boolean;
}

export interface InventoryPdb {
  name: string;
  number: number;
  con_uid: string;
  rw_instances: number;
  n_instances: number;
  restricted: boolean;
  instances: { inst_id: number; instance: string; open_mode: string; restricted: boolean }[];
  soe_loaded: { loaded_at: string; run_id: string; scale: string } | null;
  soe_marker_stale: boolean;
  last_check: { status: string; detail: string; instance: string | null; checked_at: string; run_id: string } | null;
}

export interface Inventory {
  prefix: string;
  fetched_at: string;
  instances: { inst_id: number; instance: string }[];
  pdbs: InventoryPdb[];
}

export interface Plan {
  type: string;
  pdbs: string[];
  count: number;
  missing?: string[];
  not_open?: string[];
  existing?: { name: string; con_uid: string; rw_instances: number }[];
  drop_token?: string | null;
  to_create?: number;
  sql_preview?: string;
  blocked?: boolean;
  already_loaded?: string[];
  to_load?: string[];
  command_preview?: string;
  load?: { processes: number; sessions: number; heavy: boolean; message: string };
  runtime_s?: number;
  launch_s?: number;
  warnings: string[];
  example_connect?: string;
  equivalent_command: string;
}

export interface LogFile { index: number; label: string; name: string; exists: boolean; size: number }

export const api = {
  health: () => request<{ status: string; version: string; mode: "real" | "simulate" }>("GET", "/api/health"),
  profiles: () => request<{ profiles: Profile[] }>("GET", "/api/profiles").then((r) => r.profiles),
  fields: () => request<{ fields: Record<string, FieldMeta> }>("GET", "/api/profiles/fields").then((r) => r.fields),
  createProfile: (name: string, params: Params) => request<Profile>("POST", "/api/profiles", { name, params }),
  updateProfile: (id: number, name: string, params: Params) => request<Profile>("PUT", `/api/profiles/${id}`, { name, params }),
  activateProfile: (id: number) => request<Profile>("POST", `/api/profiles/${id}/activate`),
  deleteProfile: (id: number) => request<void>("DELETE", `/api/profiles/${id}`),
  envCheck: (profileId?: number) =>
    request<EnvCheck>("GET", profileId ? `/api/env-check?profile_id=${profileId}` : "/api/env-check"),
  pdbs: (prefix?: string) => request<Inventory>("GET", prefix ? `/api/pdbs?prefix=${encodeURIComponent(prefix)}` : "/api/pdbs"),
  plan: (type: string, params: Record<string, unknown>) =>
    request<{ plan: Plan; params: Params }>("POST", "/api/runs", { type, params, dry_run: true }),
  startRun: (type: string, params: Record<string, unknown>, secrets: Record<string, string>) =>
    request<{ run_id: string }>("POST", "/api/runs", { type, params, secrets }),
  retryRun: (type: string, runId: string, secrets: Record<string, string>) =>
    request<{ run_id: string }>("POST", "/api/runs", { type, params: {}, secrets, retry_of: runId }),
  importMarkers: (directory: string) =>
    request<{ imported: string[]; stale: string[]; ignored: string[] }>("POST", "/api/pdbs/import-markers", { directory }),
  runs: (limit = 100) => request<{ runs: Run[] }>("GET", `/api/runs?limit=${limit}`).then((r) => r.runs),
  activeRun: () => request<{ run: Run | null }>("GET", "/api/runs/active").then((r) => r.run),
  run: (id: string) => request<Run>("GET", `/api/runs/${id}`),
  items: (id: string) => request<{ items: RunItem[]; event_seq: number | null }>("GET", `/api/runs/${id}/items`),
  cancel: (id: string) => request<{ cancelling: boolean }>("POST", `/api/runs/${id}/cancel`),
  itemLog: (id: string, pdb: string, file = 0, tail = 500) =>
    request<{ files: LogFile[]; file: number; lines: string[] }>(
      "GET", `/api/runs/${id}/items/${encodeURIComponent(pdb)}/log?file=${file}&tail=${tail}`),
  runLog: (id: string) => request<{ lines: string[] }>("GET", `/api/runs/${id}/log?tail=1000`),
};
