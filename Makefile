OXYGEN_HOST ?= oxygen
OXYGEN_DIR ?= /var/services/photo
OXYGEN_LEDGER ?= photos-oxygen-sha
PHOTOS_LIBRARY ?= $(HOME)/Pictures/Photos Library.photoslibrary
PHOTOS_ORIGINALS ?= $(PHOTOS_LIBRARY)/originals
REVIEW_DIR ?= review
DUPLICATE_REPORT ?= duplicate-groups.txt
OXYGEN_LEDGER_REMOTE ?= /volume1/photostow/ledger/photos-oxygen-sha
LEDGER_BACKUPS ?= 5

.PHONY: test lint format typecheck check review update-oxygen-ledger install-oxygen-ledger prune-oxygen-ledger missing duplicate-groups delete-duplicates stage-review archive-reviewed clean

test:
	uv run pytest tests

lint:
	uv run ruff check photostow tests

format:
	uv run ruff format --check photostow tests

typecheck:
	uv run mypy

check: test lint format typecheck

review: check
	uv lock --check
	git diff --check

update-oxygen-ledger:
	uv run photostow update-remote-ledger $(OXYGEN_HOST) $(OXYGEN_DIR) $(OXYGEN_LEDGER)

install-oxygen-ledger:
	uv run photostow install-remote-ledger $(OXYGEN_HOST) $(OXYGEN_LEDGER) $(OXYGEN_LEDGER_REMOTE) --keep $(LEDGER_BACKUPS)

prune-oxygen-ledger:
	uv run photostow prune-remote-ledger $(OXYGEN_HOST) $(OXYGEN_DIR) $(OXYGEN_LEDGER)

missing:
	uv run photostow library-missing "$(PHOTOS_LIBRARY)" $(OXYGEN_LEDGER) > missing.tsv

duplicate-groups:
	uv run photostow duplicate-groups $(OXYGEN_HOST) $(OXYGEN_DIR) $(OXYGEN_LEDGER) --output $(DUPLICATE_REPORT)

delete-duplicates:
	uv run photostow delete-duplicates $(OXYGEN_HOST) $(DUPLICATE_REPORT)

stage-review:
	uv run photostow stage-review missing.tsv "$(PHOTOS_ORIGINALS)" $(REVIEW_DIR) --replace

# Copy first; only then refresh and install the archive ledger.
archive-reviewed:
	uv run photostow copy-tree $(REVIEW_DIR) $(OXYGEN_HOST) $(OXYGEN_DIR)
	$(MAKE) update-oxygen-ledger
	$(MAKE) install-oxygen-ledger

clean:
	-rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
