from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.adapters.govengine_port.client import GovEngineClient
from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType, GovEngineRequest
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def _allowed_preview(operation_id: str) -> dict[str, Any]:
    return {
        "admission_compose": {
            "admission_id": f"adm-{operation_id}",
            "subject_ref": f"rexecop:{operation_id}",
            "prepared_execution_contract": {"status": "prepared", "digest": "sha256:contract"},
            "policy_decision": {"decision": "allow", "policy_id": "policy-1"},
            "execution_ticket": {
                "status": "passed",
                "ticket_id": "ticket-1",
                "digest": "sha256:ticket",
            },
            "trust_decision": {
                "status": "passed",
                "trust_status": "trusted",
                "verifier_id": "fixture",
            },
            "runner_profile": {"name": "rexecop", "allowed": True, "live_backend_enabled": True},
            "receipt_obligation": {"required": True, "binds": ["admission", "ticket"]},
        }
    }


class AllowedGovEngineClient(GovEngineClient):
    def evaluate(self, request: GovEngineRequest):  # type: ignore[override]
        preview = dict(request.preview)
        preview.update(_allowed_preview(request.operation_id))
        enriched = GovEngineRequest(
            operation_id=request.operation_id,
            profile=request.profile,
            environment=request.environment,
            intent=request.intent,
            target=request.target,
            mode=request.mode,
            risk=request.risk,
            preview=preview,
        )
        return super().evaluate(enriched)


def test_real_adapter_apply_allowed(tmp_path: Path) -> None:
    controller = OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=AllowedGovEngineClient(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="apply",
    )
    assert operation.state == OperationState.APPROVED.value
    assert operation.govengine_decision_type == GovEngineDecisionType.ALLOWED.value
    assert controller.allows_mutating_execution(operation.id)
