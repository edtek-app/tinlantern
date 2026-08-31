.PHONY: setup lint test run gate-m0 gate-m1 gate-m2 gate-m3 gate-m4 gate-m5 gate-m6 gate-m7

setup:
	pip install -e ".[dev]" && docker compose up -d db

lint:
	ruff check . && ruff format --check .

test:
	pytest -q

run:
	uvicorn app.main:app --reload

gate-m0:
	pytest -q -m m0 && $(MAKE) lint
gate-m1:
	pytest -q -m "m0 or m1"
gate-m2:
	pytest -q -m "m0 or m1 or m2"
gate-m3:
	pytest -q -m "m0 or m1 or m2 or m3"
gate-m4:
	pytest -q -m "m0 or m1 or m2 or m3 or m4"
gate-m5:
	pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m6:
	terraform -chdir=infra fmt -check && terraform -chdir=infra validate && pytest -q
gate-m7:
	pytest -q -m demo && pytest -q
