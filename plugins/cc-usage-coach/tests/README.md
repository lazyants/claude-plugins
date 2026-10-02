# cc-usage-coach — tests

Pytest suite over synthetic fixtures — no real session logs, no network. GitHub Actions runs the full suite before release with `bash tests/run-all.sh`, which invokes `pytest` over this directory. Locally run only the test file covering a change. The pipeline integration test runs the shipped extractor and signal builder against synthetic session logs, checking their schema contract and output privacy.
