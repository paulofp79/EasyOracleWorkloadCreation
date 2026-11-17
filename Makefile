# PDB Swingbench Orchestrator — developer tasks
PY      ?= python3.11
VENV    := .venv
VPY     := $(VENV)/bin/python

.PHONY: venv frontend test test-backend test-frontend sim dev-backend dev-frontend sim-reset wheels clean

venv:
	$(PY) -m venv $(VENV)
	$(VPY) -m pip install -q -e 'backend[dev]'

frontend:
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

# Offline install bundle for the Oracle host (python3.11, linux x86_64)
wheels:
	scripts/build-wheels.sh

clean:
	rm -rf frontend/dist dist var
