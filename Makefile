.PHONY: test lint typecheck check clean

test:
	uv run pytest tests

lint:
	uv run ruff check photostow tests

typecheck:
	uv run mypy

check: test lint typecheck

clean:
	-rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
