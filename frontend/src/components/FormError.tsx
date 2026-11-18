import { Link } from "react-router-dom";

/** Form-level error; a "run <id> (...) is still in progress" conflict links to that run. */
export default function FormError({ text }: { text: string }) {
  if (!text) return null;
  const m = text.match(/^run (\d{8}-\d{6}-[0-9a-f]+) \(/) ?? text.match(/^\d+ runs are already in progress/);
  const runId = m && m[1];
  return (
    <div className="error-box">
      {text}
      {m && <> <Link to={runId ? `/runs/${runId}` : "/runs?status=running"}>{runId ? "Open that run →" : "See the active runs →"}</Link></>}
    </div>
  );
}
