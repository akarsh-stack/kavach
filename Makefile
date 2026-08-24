# Common tasks. `make help` lists them.
.PHONY: help setup test batch eval sens web all clean

help:
	@echo "setup   install python deps + node deps"
	@echo "test    run the test suite (includes the boundary proof)"
	@echo "batch   generate a batch and verify its calibration"
	@echo "eval    compare the credential-free policies"
	@echo "sens    sweep the assumptions; does the conclusion hold?"
	@echo "web     start the dashboard (api :5174, ui :5173)"
	@echo "all     batch + eval + sens + test, in order"

setup:
	pip install -r requirements.txt
	cd web && npm run install:all && npm install

test:
	python -m pytest tests/ -q

batch:
	python scripts/inspect_batch.py

eval:
	python scripts/run_eval.py --no-llm --limit 300

sens:
	python scripts/run_sensitivity.py --limit 300

web:
	cd web && npm run dev

all: batch eval sens test

clean:
	rm -rf data/runs/*.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
