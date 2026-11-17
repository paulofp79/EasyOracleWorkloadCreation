#!/usr/bin/env bash
# Install the orchestrator for the current OS user (run as the user that will run it, e.g. oracle).
#
#   scripts/install.sh            online: pip installs from PyPI (or your pip mirror / PIP_INDEX_URL)
#   scripts/install.sh --offline  offline: from dist/wheels (built with scripts/bundle.sh or build-wheels.sh)
#
# Then start it with:  cd backend && ../venv/bin/python -m app     (or install the service as root:
#                      sudo scripts/install-service.sh)
set -Eeuo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$PWD"
OFFLINE=false
[[ "${1:-}" == "--offline" ]] && OFFLINE=true
[[ -d dist/wheels && "${1:-}" != "--online" ]] && OFFLINE=true

if [[ $EUID -eq 0 ]]; then
  echo "Run this as the OS user that will run the app (e.g. oracle), not as root." >&2
  exit 1
fi
PY="${PYTHON:-python3.11}"
command -v "$PY" >/dev/null || { echo "$PY not found: dnf install -y python3.11" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "$PY is older than 3.11" >&2; exit 1; }
[[ -f frontend/dist/index.html ]] || { echo "frontend/dist is missing (it is part of the repository and the bundle)" >&2; exit 1; }

[[ -x venv/bin/python ]] || "$PY" -m venv venv
if $OFFLINE; then
  [[ -f dist/requirements.lock ]] || { echo "dist/requirements.lock missing: build the bundle with scripts/bundle.sh" >&2; exit 1; }
  venv/bin/pip install -q --no-index --find-links dist/wheels -r dist/requirements.lock
  echo "Installed the dependencies from dist/wheels (the app itself runs from $APP_DIR/backend)."
else
  venv/bin/pip install -q --upgrade pip
  venv/bin/pip install -q -e ./backend
  echo "Installed from the package index."
fi
mkdir -p -m 700 "$HOME/.pdb-orchestrator"
cd backend && ../venv/bin/python -c "import app.main" && cd ..
cat <<MSG

Done. Next steps:
  start by hand:      cd $APP_DIR/backend && ../venv/bin/python -m app
  login for 0.0.0.0:  cd $APP_DIR/backend && ../venv/bin/python -m app set-password
  as a service:       sudo $APP_DIR/scripts/install-service.sh --user $(id -un)
MSG
