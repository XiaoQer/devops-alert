#!/usr/bin/env bash
set -euo pipefail

ii_repository_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ii_repository_dir/backend"

ii_python_bin=${II_PYTHON_BIN:-.venv/bin/python}

"$ii_python_bin" -m ruff check src tests
"$ii_python_bin" -m ruff format --check src tests
"$ii_python_bin" -m mypy src
"$ii_python_bin" -m pytest \
  --cov=incident_intelligence \
  --cov-report=term-missing \
  --cov-fail-under=90
