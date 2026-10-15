# Contributing to horus

Thanks for contributing. horus is in early development; consult the current
capabilities and [architecture](docs/architecture.md) before proposing changes.
English is the primary language for code, issues, and public documentation.
Chinese documentation is welcome; keep both README entry points consistent.

## Development environment

Use Python 3.12 and uv 0.9.11. GitHub CLI is required by the engine installer.

```sh
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
uv build
uv run python -m tests.acceptance.verify_wheel
```

The installer fetches pinned official Hurl/k6 binaries and verifies their SHA-256
checksums. Existing binaries can be supplied through `HORUS_HURL_BIN` and
`HORUS_K6_BIN`. macOS and Linux are the current supported platforms.

## Changes and pull requests

- Keep changes small and explain the concrete behavior they change.
- Add a regression test for a defect or meaningful execution behavior. Use real
  HTTP fixtures when exercising engine behavior; use focused mocks for races.
- Keep MCP and CLI as thin callers of the same core. Backend business concepts
  belong in fixtures and plans, not in execution adapters.
- Keep credentials in the runtime environment. Do not commit `.env`, `.tools`,
  `.horus-demo`, private reports, or client authentication data.
- Preserve the approval boundary: displaying a plan, recording a human decision,
  and executing the approved plan are distinct operations. Fixture approvals must
  remain explicitly `test-only`; they never prove actual human acceptance.
- Update documentation when capabilities, limits, or public commands change.

Describe the problem, resulting behavior, relevant validation, and remaining
limitations in your pull request. Do not change expected results merely to make a
faulty backend pass. See [the test workflow](docs/testing.md) for acceptance evidence.

By contributing, you agree that your contribution is provided under the project's
[MIT license](LICENSE).
