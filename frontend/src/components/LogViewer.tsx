import { useEffect, useRef, useState } from "react";
import { api, type LogFile } from "../api/client";

const MAX_LINES = 3000;

/** Tail of one item log: snapshot via REST, then live lines over SSE while the run is live. */
export default function LogViewer({ runId, pdb, live }: { runId: string; pdb: string; live: boolean }) {
  const [files, setFiles] = useState<LogFile[]>([]);
  const [file, setFile] = useState(0);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let es: EventSource | null = null;
    let cancelled = false;
    setLines([]);
    setError(null);
    if (live) {
      api.itemLog(runId, pdb, file, 1).then((r) => !cancelled && setFiles(r.files)).catch(() => undefined);
      es = new EventSource(`/api/runs/${runId}/items/${encodeURIComponent(pdb)}/log/stream?file=${file}&tail=500`);
      es.addEventListener("lines", (e) => {
        const d = JSON.parse((e as MessageEvent).data) as { lines: string[]; reset?: boolean };
        setLines((prev) => (d.reset ? d.lines : [...prev, ...d.lines]).slice(-MAX_LINES));
      });
      es.addEventListener("end", () => es?.close());
    } else {
      api.itemLog(runId, pdb, file, 2000)
        .then((r) => { if (!cancelled) { setFiles(r.files); setLines(r.lines); } })
        .catch((e) => !cancelled && setError((e as Error).message));
    }
    return () => {
      cancelled = true;
      es?.close();
    };
  }, [runId, pdb, file, live]);

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines, follow]);

  return (
    <div className="logviewer">
      <div className="logviewer-head">
        {files.length > 1 ? (
          <select value={file} onChange={(e) => setFile(Number(e.target.value))} aria-label="Log file">
            {files.map((f) => <option key={f.index} value={f.index}>{f.label} ({f.name})</option>)}
          </select>
        ) : (
          <span className="muted small mono">{files[0]?.name ?? "log"}</span>
        )}
        <label className="small"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
        {live && <span className="small muted">live</span>}
      </div>
      {error && <div className="text-fail small">{error}</div>}
      <pre ref={boxRef} className="log" tabIndex={0} aria-label={`Log of ${pdb}`}>
        {lines.length ? lines.join("\n") : <span className="muted">(empty)</span>}
      </pre>
    </div>
  );
}
