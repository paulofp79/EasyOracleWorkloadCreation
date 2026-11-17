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
};
