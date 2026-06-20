#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

echo "==> validate_public_truth"
"$PYTHON" scripts/validate_public_truth.py

echo "==> core boundary grep"
if rg -l 'tecrax_profile|import tecrax' src/rexecop; then
  echo "domain import detected in rexecop core"
  exit 1
fi
if rg -n 'rexecop-fixture-guard-key' src/rexecop/adapters/sclite_port/full_bundle.py; then
  echo "fixture guard key must not ship in production full_bundle module"
  exit 1
fi

echo "==> secret scan"
bash scripts/secret_scan.sh

echo "==> delivery pytest suite"
export REXECOP_SIGNOFF_INNER=1
"$PYTHON" -m pytest -q -m delivery

if [[ "${REXECOP_SIGNOFF_BUILD:-0}" == "1" ]] && "$PYTHON" -c "import build" >/dev/null 2>&1; then
  echo "==> package build smoke (REXECOP_SIGNOFF_BUILD=1)"
  rm -rf dist build *.egg-info
  "$PYTHON" -m build
  "$PYTHON" -m twine check dist/*
else
  echo "==> skip package build (set REXECOP_SIGNOFF_BUILD=1 locally; CI uses package-dry-run job)"
fi

echo "alpha_signoff_checks_ok"
