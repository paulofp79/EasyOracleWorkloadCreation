import type { ItemState, RunStatus } from "../api/client";

// Never colour alone: every state has an icon and a text label.
export const ITEM_STATE: Record<ItemState, { icon: string; label: string; short: string }> = {
  pending: { icon: "○", label: "pending", short: "WAIT" },
  checking: { icon: "↻", label: "checking", short: "CHK" },
  running: { icon: "▶", label: "running", short: "RUN" },
  ok: { icon: "✓", label: "ok", short: "OK" },
  failed: { icon: "✕", label: "failed", short: "FAIL" },
  skipped: { icon: "↷", label: "skipped", short: "SKIP" },
  not_started: { icon: "–", label: "not started", short: "N/S" },
  cancelled: { icon: "⊘", label: "cancelled", short: "CXL" },
};

export const ITEM_ORDER: ItemState[] = ["pending", "checking", "running", "ok", "failed", "skipped", "not_started", "cancelled"];

export function ItemBadge({ state }: { state: ItemState }) {
  const s = ITEM_STATE[state];
  return (
    <span className={`st st-${state}`}>
      <span aria-hidden="true">{s.icon}</span> {s.label}
    </span>
  );
}

const RUN_STATUS: Record<RunStatus, { icon: string; label: string; cls: string }> = {
  running: { icon: "▶", label: "RUNNING", cls: "running" },
  ok: { icon: "✓", label: "OK", cls: "ok" },
  failed: { icon: "✕", label: "FAILED", cls: "failed" },
  cancelled: { icon: "⊘", label: "CANCELLED", cls: "cancelled" },
  interrupted: { icon: "!", label: "INTERRUPTED", cls: "interrupted" },
  blocked: { icon: "■", label: "BLOCKED", cls: "failed" },
};

export function RunBadge({ status }: { status: RunStatus }) {
  const s = RUN_STATUS[status] ?? { icon: "?", label: status, cls: "pending" };
  return (
    <span className={`st st-${s.cls} st-strong`}>
      <span aria-hidden="true">{s.icon}</span> {s.label}
    </span>
  );
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m${String(sec).padStart(2, "0")}s`;
}

export function secondsBetween(a: string | null, b: string | null): number | null {
  if (!a) return null;
  const end = b ? Date.parse(b) : Date.now();
  return (end - Date.parse(a)) / 1000;
}

export function fmtTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export const RUN_TYPE_LABEL: Record<string, string> = {
  check: "Connection check", create: "Create PDBs", drop: "Drop PDBs", load: "Load SOE", increase: "Increase SOE", workload: "Workload",
};
