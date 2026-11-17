# PDB Swingbench Orchestrator — developer tasks
PY      ?= python3.11
VENV    := .venv
VPY     := $(VENV)/bin/python

.PHONY: venv frontend test test-backend test-frontend sim dev-backend dev-frontend sim-reset wheels bundle clean

venv:
	$(PY) -m venv $(VENV)
	$(VPY) -m pip install -q -e 'backend[dev]'

frontend:        # rebuild the committed UI after changing frontend/src (Node 20+), then commit frontend/dist
	cd frontend && npm ci --no-audit --no-fund && npm run build

test: test-backend test-frontend

test-backend:
	cd backend && ../$(VPY) -m pytest -q

test-frontend:
	cd frontend && npm test

# Built UI + backend in simulation mode on http://127.0.0.1:8080
sim:
	cd backend && ORCH_MODE=simulate ../$(VPY) -m app

# Live-reload development: run these two in separate terminals, open http://127.0.0.1:5173
dev-backend:
	cd backend && ORCH_MODE=simulate ../$(VPY) -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8080

dev-frontend:
	cd frontend && npm run dev

sim-reset:
	tools/stubs/simctl reset
	rm -f var/sim/orchestrator.db*

# Offline install: wheels only, or the complete bundle (dist/pdb-orchestrator-<version>.tgz)
wheels:
	scripts/build-wheels.sh

bundle:
	scripts/bundle.sh

clean:          # frontend/dist is committed: it is not removed here
	rm -rf dist var
