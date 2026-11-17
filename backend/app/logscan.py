"""Error text in charbench logs: exit code 0 does not prove the database had no errors."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Same pattern as run_charbench_all_pdbs.sh (grep -E).
ERR_REGEX = re.compile(r"ORA-[0-9]{5}|TNS-[0-9]{5}|SQLException|Exception|Connection refused|Connection reset")
FIRST_MAX = 150


@dataclass
class ScanResult:
    error_lines: int
    first_error: str


def scan_text(lines) -> ScanResult:
    count, first = 0, ""
    for line in lines:
        if ERR_REGEX.search(line):
            count += 1
            if not first:
                first = line.rstrip("\n").replace("\t", " ")[:FIRST_MAX]
    return ScanResult(count, first)


def scan_file(path: Path) -> ScanResult:
    if not path.is_file():
        return ScanResult(0, "")
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return scan_text(fh)
