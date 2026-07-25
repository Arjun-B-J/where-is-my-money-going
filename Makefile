# Where Is My Money Going? — development helpers.
#
# The venv path differs between platforms, so every recipe goes through $(PY).
# Override on non-Windows: make test PY=backend/.venv/bin/python

PY ?= backend/.venv/Scripts/python.exe

.PHONY: help install model dev backend frontend test test-backend test-frontend \
        lint typecheck check e2e demo report clean docker

help:
	@echo "Where Is My Money Going? — make targets"
	@echo ""
	@echo "  make install        Create the backend venv and install both sides"
	@echo "  make model          Pull the local model via Ollama (~18 GB)"
	@echo ""
	@echo "  make backend        Run the API on :8000"
	@echo "  make frontend       Run the UI on :3000"
	@echo ""
	@echo "  make demo           Load 12 months of synthetic data and categorise it"
	@echo "  make report         Write the Spend Analysis PDF"
	@echo ""
	@echo "  make check          Everything CI runs: lint, types, tests (both sides)"
	@echo "  make test           Test suites only"
	@echo "  make lint           ruff + eslint"
	@echo "  make typecheck      mypy + tsc"
	@echo "  make e2e            Playwright (needs both servers running)"
	@echo ""
	@echo "  make clean          Remove venv, node_modules, build output, local DB"

install:
	cd backend && python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "backend[dev]"
	cd frontend && npm install --no-fund --no-audit

model:
	ollama pull gemma4:26b

backend:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

demo:
	$(PY) -m app.cli demo --months 12
	@echo ""
	@echo "Open http://localhost:3000/dashboard (run 'make frontend' if it is not up)."

report:
	$(PY) -m app.cli report

test: test-backend test-frontend

test-backend:
	cd backend && ../$(PY) -m pytest -q

test-frontend:
	cd frontend && npm test

lint:
	cd backend && ../$(PY) -m ruff check app tests
	cd frontend && npx eslint .

typecheck:
	cd backend && ../$(PY) -m mypy app
	cd frontend && npx tsc --noEmit

# Same gates as CI, so a green `make check` means a green pipeline.
check: lint typecheck test

e2e:
	cd frontend && npx playwright test

clean:
	rm -rf backend/.venv backend/storage backend/.pytest_cache backend/.ruff_cache
	rm -rf backend/.mypy_cache backend/*.egg-info
	rm -rf frontend/node_modules frontend/.next frontend/playwright-report frontend/test-results

docker:
	docker compose up --build
