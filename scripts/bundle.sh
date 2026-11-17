#!/usr/bin/env bash
# Build dist/pdb-orchestrator-<version>.tgz: everything needed on a server without internet access
# (code, built UI, Python wheels for python3.11 / x86_64, scripts, docs). Run on a Linux machine with
# python3.11 and internet access (PyPI or your mirror).
set -Eeuo pipefail
cd "$(dirname "$0")/.."
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' backend/app/__init__.py)"
scripts/build-wheels.sh
OUT="dist/pdb-orchestrator-$VERSION.tgz"
tar czf "$OUT" --transform "s#^#pdb-orchestrator/#" \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*.egg-info' --exclude='backend/build' \
  README.md Makefile backend frontend/dist shared tools reference docs deploy scripts \
  dist/wheels dist/requirements.lock
echo "Bundle: $OUT ($(du -h "$OUT" | cut -f1))"
echo "On the server, as the app user:  tar xzf $(basename "$OUT") && cd pdb-orchestrator && scripts/install.sh --offline"
