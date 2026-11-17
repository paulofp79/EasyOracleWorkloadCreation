import { memo } from "react";
import type { RunItem } from "../api/client";
import { ITEM_STATE } from "./states";

const Tile = memo(function Tile({ item, selected, onSelect }: {
  item: RunItem; selected: boolean; onSelect: (pdb: string) => void;
}) {
  const s = ITEM_STATE[item.state];
  const detail = item.detail || item.check_detail || "";
  const num = item.pdb.slice(-3);
  return (
    <button
      type="button"
      className={`tile st-${item.state}${selected ? " tile-selected" : ""}`}
      title={`${item.pdb}: ${s.label}${detail ? ` — ${detail}` : ""}`}
      aria-label={`${item.pdb}, ${s.label}${detail ? `, ${detail}` : ""}`}
      aria-pressed={selected}
      onClick={() => onSelect(item.pdb)}
    >
      <span className="tile-num">{num}</span>
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
