import type { CheckStatus } from "../api/client";

const ICON: Record<CheckStatus, string> = { pass: "✓", warn: "!", fail: "✕", skip: "–" };
const LABEL: Record<CheckStatus, string> = { pass: "PASS", warn: "WARN", fail: "FAIL", skip: "SKIP" };

/** Never colour alone: icon + text label. */
export default function StatusBadge({ status }: { status: CheckStatus }) {
  return (
    <span className={`status status-${status}`}>
      <span aria-hidden="true">{ICON[status]}</span> {LABEL[status]}
    </span>
  );
}
