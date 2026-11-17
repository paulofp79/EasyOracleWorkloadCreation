#!/usr/bin/env bash
# Build an offline wheel bundle for an Oracle host with python3.11 and no internet access.
#
#   scripts/build-wheels.sh            -> dist/wheels/*.whl + dist/requirements.lock
#
# On the host:
#   python3.11 -m venv ~/pdb-orchestrator/venv
#   ~/pdb-orchestrator/venv/bin/pip install --no-index --find-links dist/wheels -r dist/requirements.lock
#   ~/pdb-orchestrator/venv/bin/pip install --no-index --find-links dist/wheels --no-deps ./backend
set -Eeuo pipefail
cd "$(dirname "$0")/.."
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
OUT=dist/wheels
mkdir -p "$OUT"

# Resolve the runtime dependency set with the local python3.11, then pin it.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
python3.11 -m venv "$tmp/venv"
"$tmp/venv/bin/pip" install -q ./backend
"$tmp/venv/bin/pip" freeze --exclude pdb-orchestrator > dist/requirements.lock

# Download binary wheels for the target platform (works from any build machine).
python3.11 -m pip download -q --only-binary=:all: --python-version 3.11 --implementation cp \
  --platform "$PLATFORM" --platform manylinux_2_17_x86_64 --platform any \
  -r dist/requirements.lock -d "$OUT"
python3.11 -m pip wheel -q --no-deps -w "$OUT" ./backend
echo "Wheels in $OUT ($(ls "$OUT" | wc -l) files); lock file dist/requirements.lock"
