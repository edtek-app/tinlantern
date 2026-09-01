.PHONY: setup lint test run gate-m0 gate-m1 gate-m2 gate-m3 gate-m4 gate-m5 gate-m6 gate-m7

setup:
	pip install -e ".[dev]" && docker compose up -d db

lint:
	ruff check . && ruff format --check .

test:
	pytest -q

run:
	uvicorn app.main:app --reload

# Every gate runs lint first, then the cumulative milestone-marked tests.
gate-m0:
	$(MAKE) lint && pytest -q -m m0
gate-m1:
	$(MAKE) lint && pytest -q -m "m0 or m1"
gate-m2:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2"
gate-m3:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3"
gate-m4:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4"
gate-m5:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m6:
	$(MAKE) lint && terraform -chdir=infra fmt -check && terraform -chdir=infra validate && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m7:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5 or demo"
