import { memo } from "react";
import type { RunItem } from "../api/client";
import { ITEM_STATE } from "./states";

const Tile = memo(function Tile({ item, selected, onSelect }: {
  item: RunItem; selected: boolean; onSelect: (pdb: string) => void;
}) {
  const s = ITEM_STATE[item.state];
  const detail = item.detail || item.check_detail || "";
  // Increase SOE with several schemas: items are PDBPP_001:BATCH1_SOE
  const [pdbPart, schemaPart] = item.pdb.split(":");
  const num = pdbPart.slice(-3);
  const errs = item.error_count ?? 0;
  const errText = errs ? `, ${errs} error line(s) in the log: ${item.first_error ?? ""}` : "";
  return (
    <button
      type="button"
      className={`tile st-${item.state}${selected ? " tile-selected" : ""}${errs ? " tile-errors" : ""}`}
      title={`${item.pdb}: ${s.label}${detail ? ` — ${detail}` : ""}${errText}`}
      aria-label={`${item.pdb}, ${s.label}${detail ? `, ${detail}` : ""}${errText}`}
      aria-pressed={selected}
      onClick={() => onSelect(item.pdb)}
    >
      <span className="tile-num">{num}{schemaPart ? <span className="tile-schema">{schemaPart === "SOE" ? "SOE" : schemaPart.replace(/_SOE$/, "")}</span> : null}{errs ? <span className="tile-err" aria-hidden="true">!</span> : null}</span>
      <span className="tile-state">
        <span aria-hidden="true">{s.icon}</span>
        {s.short}
      </span>
    </button>
  );
});

export default function TileGrid({ items, selected, onSelect }: {
  items: RunItem[]; selected: string | null; onSelect: (pdb: string) => void;
}) {
  return (
    <div className="tiles" role="group" aria-label="PDBs in this run">
      {items.map((it) => (
        <Tile key={it.pdb} item={it} selected={selected === it.pdb} onSelect={onSelect} />
      ))}
    </div>
  );
}
