import { useQuery } from "@tanstack/react-query";
import { api, fileUrl, type Artifact } from "../api/client";

function size(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function FileList({ runId, files }: { runId: string; files: Artifact[] }) {
  return (
    <ul className="filelist">
      {files.map((f) => (
        <li key={f.path}>
          <a className="mono small" href={fileUrl(runId, f.path)} target="_blank" rel="noreferrer">{f.path}</a>{" "}
          <span className="muted small">{size(f.size)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Downloads of a run: zip of the whole run directory, CSV of the items, and each file. */
export default function Artifacts({ runId, live }: { runId: string; live: boolean }) {
  const { data, error } = useQuery({ queryKey: ["artifacts", runId, live], queryFn: () => api.artifacts(runId),
    refetchInterval: live ? 10_000 : false });
  const summary = data?.files.filter((f) => f.kind === "summary") ?? [];
  const results = data?.files.filter((f) => f.kind === "results") ?? [];
  const logs = data?.files.filter((f) => f.kind === "log") ?? [];
  return (
    <section className="card" style={{ marginTop: "1rem" }}>
      <div className="page-head">
        <h2>Artifacts {data && <span className="muted small">{data.count} files · {size(data.total_bytes)}</span>}</h2>
        <div className="actions">
          <a className="btn" href={`/api/runs/${runId}/summary.csv`}>Export CSV</a>
          <a className={`btn btn-primary${live ? " disabled" : ""}`} href={live ? undefined : `/api/runs/${runId}/artifacts.zip`}
             aria-disabled={live} title={live ? "Available when the run has finished" : undefined}>Download all (zip)</a>
        </div>
      </div>
      {error && <p className="text-fail small">{(error as Error).message}</p>}
      {data && (
        <>
          <p className="muted small mono">{data.run_dir}</p>
          <FileList runId={runId} files={summary} />
          {results.length > 0 && (
            <details><summary>Swingbench result files ({results.length})</summary><FileList runId={runId} files={results} /></details>
          )}
          {logs.length > 0 && (
            <details><summary>Logs ({logs.length})</summary><FileList runId={runId} files={logs} /></details>
          )}
        </>
      )}
    </section>
  );
}
