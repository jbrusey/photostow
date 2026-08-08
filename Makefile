OXYGEN_HOST ?= oxygen
OXYGEN_DIR ?= /var/services/photo
OXYGEN_LEDGER ?= photos-oxygen-sha
PHOTOS_ORIGINALS ?= $(HOME)/Pictures/Photos Library.photoslibrary/originals

.PHONY: test lint typecheck check update-oxygen-ledger copy-missing-to-oxygen clean

test:
	uv run pytest tests

lint:
	uv run ruff check photostow tests

typecheck:
	uv run mypy

check: test lint typecheck

update-oxygen-ledger:
	uv run photostow update-remote-ledger $(OXYGEN_HOST) $(OXYGEN_DIR) $(OXYGEN_LEDGER)

copy-missing-to-oxygen:
	uv run photostow copy-missing missing.tsv "$(PHOTOS_ORIGINALS)" $(OXYGEN_HOST) $(OXYGEN_DIR)/incoming/$$(hostname -s)

clean:
	-rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
