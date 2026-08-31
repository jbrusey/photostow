# Improvements

- [x] Document that migration `--limit` applies after discovery and recommend `--path` for small trials; acceptance: README and technical review state the bounded-processing limitation.
- [x] Add a regression assertion for the documented limit/discovery behavior; acceptance: the test fails if limit is applied before required discovery.
