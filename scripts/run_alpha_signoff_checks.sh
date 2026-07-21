#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" && -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -z "$PYTHON" ]]; then
  PYTHON=python
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

echo "==> validate_public_truth"
"$PYTHON" scripts/validate_public_truth.py

echo "==> validate_stack_contracts"
"$PYTHON" scripts/validate_stack_contracts.py

echo "==> validate_profile_conformance"
"$PYTHON" scripts/validate_profile_conformance.py

echo "==> validate_first_run_smoke"
"$PYTHON" scripts/validate_first_run_smoke.py

echo "==> validate_operator_journeys"
"$PYTHON" scripts/validate_operator_journeys.py

echo "==> validate_cross_repo_golden_fixture"
"$PYTHON" scripts/validate_cross_repo_golden_fixture.py

echo "==> validate_workflow_security"
"$PYTHON" scripts/validate_workflow_security.py

echo "==> validate_stack_invariants"
"$PYTHON" scripts/validate_stack_invariants.py

echo "==> validate_external_review_gate"
"$PYTHON" scripts/validate_external_review_gate.py

echo "==> validate_m86_security_gate"
"$PYTHON" scripts/validate_m86_security_gate.py

echo "==> validate_m9_runtime_gate"
"$PYTHON" scripts/validate_m9_runtime_gate.py

echo "==> validate_m95n_gate"
"$PYTHON" scripts/validate_m95n_gate.py

echo "==> validate_m10_readonly_gate"
"$PYTHON" scripts/validate_m10_readonly_gate.py

echo "==> validate_m10_runtime_gate"
"$PYTHON" scripts/validate_m10_runtime_gate.py

echo "==> validate_m10_public_api_gate"
"$PYTHON" scripts/validate_m10_public_api_gate.py

echo "==> validate_m10_release_gate"
"$PYTHON" scripts/validate_m10_release_gate.py

echo "==> validate_g3_runtime_governance_gate"
"$PYTHON" scripts/validate_g3_runtime_governance_gate.py

echo "==> validate_governance_conformance"
"$PYTHON" scripts/validate_governance_conformance.py

echo "==> validate_g6_release_candidate_gate"
"$PYTHON" scripts/validate_g6_release_candidate_gate.py

echo "==> core boundary grep"
if rg -il '\b(tecrax|proxmox|pbs|zabbix|adguard|frigate|hillstone|docker|ubuntu|ntp)\b' src/rexecop --glob '!**/connectors/command_shape.py'; then
  echo "domain token detected in rexecop core"
  exit 1
fi
if rg -n 'rexecop-fixture-guard-key' src/rexecop/adapters/sclite_port/full_bundle.py; then
  echo "fixture guard key must not ship in production full_bundle module"
  exit 1
fi

echo "==> secret scan"
bash scripts/secret_scan.sh

echo "==> ruff"
"$PYTHON" -m ruff check . --exclude ci-deps

echo "==> mypy"
"$PYTHON" -m mypy src/rexecop

echo "==> delivery pytest suite"
export REXECOP_SIGNOFF_INNER=1
"$PYTHON" -m pytest -q -m delivery

if [[ "${REXECOP_SIGNOFF_BUILD:-0}" == "1" ]] && "$PYTHON" -c "import build" >/dev/null 2>&1; then
  echo "==> package build smoke (REXECOP_SIGNOFF_BUILD=1)"
  rm -rf dist build *.egg-info
  "$PYTHON" -m build
  "$PYTHON" -m twine check dist/*
  "$PYTHON" scripts/validate_distribution.py dist
  echo "==> artifact install smoke"
  "$PYTHON" scripts/validate_artifact_install_smoke.py --dist dist
else
  echo "==> skip package build (set REXECOP_SIGNOFF_BUILD=1 locally; CI uses package-dry-run job)"
fi

printf '%s\n' \
  'GATE_REPORT: public_truth=OK stack_contracts=OK profile_conformance=OK first_run_smoke=OK operator_journeys=OK cross_repo_golden_fixture=OK workflow_security=OK stack_invariants=OK external_review=OK m86_security=OK m9_runtime=OK m95n=OK m10_readonly=OK m10_runtime=OK m10_public_api=OK m10_release=OK g3_runtime_governance=OK governance_conformance=OK g6_release_candidate=OK core_boundary=OK secret_scan=OK ruff=OK mypy=OK delivery_pytest=OK'
echo "alpha_signoff_checks_ok"
