#!/usr/bin/env bash
set -e  # stop at the first failure

echo "→ Lint";        uv run ruff check .
echo "→ Format";      uv run ruff format --check .
echo "→ Types";       uv run pyright
echo "→ Migrations";  uv run alembic check
echo "→ Tests";       uv run pytest -q

echo "✅ All checks passed"
