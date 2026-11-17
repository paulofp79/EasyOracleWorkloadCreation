"""Compare two workload runs: parameters, outcome metrics and per-PDB results.

Swingbench result XML is not parsed (its format is not verified, PROJECT_CONTEXT gotcha 10), so the
comparison uses what the orchestrator itself measured: exit codes, durations and log error lines.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

PARAM_KEYS: list[tuple[str, str]] = [
    ("prefix", "Prefix"), ("first", "First"), ("last", "Last"), ("pdbs", "Selected PDBs"),
    ("users", "Users per PDB"), ("runtime", "Runtime"), ("start_delay", "Start delay (s)"),
    ("disable_txns", "Disabled transactions"), ("config_xml", "Config XML"), ("charbench_path", "charbench"),
    ("skip_unreachable", "Skip unreachable"), ("check_timeout", "Check timeout (s)"),
    ("check_parallel", "Check parallelism"), ("scan_host", "SCAN host"), ("scan_port", "SCAN port"),
    ("service_suffix", "Service suffix"),
]


def _dur(it: dict[str, Any]) -> float | None:
    if not it.get("started_at") or not it.get("ended_at"):
        return None
    try:
        return (datetime.fromisoformat(it["ended_at"]) - datetime.fromisoformat(it["started_at"])).total_seconds()
    except ValueError:
        return None


def _workload_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items whose charbench actually ran (they have a pid and an exit code)."""
    return [it for it in items if it.get("exit_code") is not None and it["state"] in ("ok", "failed", "cancelled")]


def _metrics(run: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    ran = _workload_items(items)
    durs = [d for d in (_dur(it) for it in ran) if d is not None]
    s = run.get("summary") or {}
    return {
        "status": run["status"],
        "pdbs": len(items),
        "sessions": len(ran) * int(run["params"].get("users", 0)),
        "started": len(ran),
        "ok": sum(1 for it in ran if it["state"] == "ok"),
        "failed": sum(1 for it in ran if it["state"] == "failed"),
        "skipped": sum(1 for it in items if it["state"] == "skipped"),
        "check_failed": (s.get("check") or {}).get("failed"),
        "with_errors": sum(1 for it in ran if (it.get("error_count") or 0) > 0),
        "error_lines": sum(it.get("error_count") or 0 for it in ran),
        "dur_min": round(min(durs), 1) if durs else None,
        "dur_avg": round(statistics.fmean(durs), 1) if durs else None,
        "dur_max": round(max(durs), 1) if durs else None,
        "elapsed_s": s.get("elapsed_s"),
    }


METRIC_LABELS = [
    ("status", "Status"), ("pdbs", "PDBs in run"), ("started", "Workloads started"), ("sessions", "Sessions (users × started)"),
    ("ok", "Exited 0"), ("failed", "Exited non-zero"), ("skipped", "Skipped (check failed)"),
    ("with_errors", "PDBs with error text"), ("error_lines", "Error lines in logs"),
    ("dur_min", "Workload duration min (s)"), ("dur_avg", "Workload duration avg (s)"),
    ("dur_max", "Workload duration max (s)"), ("elapsed_s", "Run elapsed (s)"),
]


def _brief(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run.get(k) for k in ("id", "status", "created_at", "started_at", "ended_at", "message")}


def _param(run: dict[str, Any], key: str) -> Any:
    val = run["params"].get(key)
    if key == "pdbs":
        return f"{len(val)} selected" if val else "range"
    return val


def compare_runs(a: dict, items_a: list[dict], b: dict, items_b: list[dict]) -> dict[str, Any]:
    ma, mb = _metrics(a, items_a), _metrics(b, items_b)
    params = [{"key": k, "label": label, "a": _param(a, k), "b": _param(b, k),
               "differs": _param(a, k) != _param(b, k)} for k, label in PARAM_KEYS]
    by_a = {it["pdb"]: it for it in items_a}
    by_b = {it["pdb"]: it for it in items_b}

    def cell(it: dict | None) -> dict | None:
        if it is None:
            return None
        return {"state": it["state"], "exit_code": it.get("exit_code"), "error_count": it.get("error_count"),
                "duration_s": round(_dur(it), 1) if _dur(it) is not None and it.get("exit_code") is not None else None,
                "detail": it.get("detail")}

    pdbs = []
    for name in sorted(set(by_a) | set(by_b)):
        ca, cb = cell(by_a.get(name)), cell(by_b.get(name))
        differs = (ca is None or cb is None or ca["state"] != cb["state"] or ca["exit_code"] != cb["exit_code"]
                   or bool(ca["error_count"]) != bool(cb["error_count"]))
        pdbs.append({"pdb": name, "a": ca, "b": cb, "differs": differs})
    return {
        "a": _brief(a), "b": _brief(b), "params": params,
        "metrics": [{"key": k, "label": label, "a": ma[k], "b": mb[k]} for k, label in METRIC_LABELS],
        "pdbs": pdbs,
    }
