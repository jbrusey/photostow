OXYGEN_HOST ?= oxygen
OXYGEN_DIR ?= /var/services/photo
OXYGEN_LEDGER ?= photos-oxygen-sha

.PHONY: test lint typecheck check update-oxygen-ledger clean

test:
	uv run pytest tests

lint:
	uv run ruff check photostow tests

typecheck:
	uv run mypy

check: test lint typecheck

update-oxygen-ledger:
	uv run photostow update-remote-ledger $(OXYGEN_HOST) $(OXYGEN_DIR) $(OXYGEN_LEDGER)

clean:
	-rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
