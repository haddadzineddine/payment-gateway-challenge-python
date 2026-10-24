.PHONY: install
install:
	@poetry install

.PHONY: run
run:
	@PYTHONUNBUFFERED=1 poetry run python main.py  # flush each log line, even when piped

.PHONY: test
test:
	@poetry run python -m pytest -vv

.PHONY: test-unit
test-unit:
	@poetry run python -m pytest -vv -o python_files='*_unit_spec.py'

.PHONY: test-integration
test-integration:
	@poetry run python -m pytest -vv -o python_files='*_integration_spec.py'

.PHONY: test-e2e
test-e2e:
	@poetry run python -m pytest -vv -o python_files='*_e2e_spec.py'

.PHONY: coverage
coverage:
	@poetry run python -m pytest --cov=src --cov-report=term-missing

.PHONY: format
format:
	@poetry run ruff check --fix --select I .
	@poetry run ruff format .

.PHONY: lint
lint:
	@poetry run ruff check .
	@poetry run ruff format --check .
