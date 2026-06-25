from __future__ import annotations

from rexecop.execution.model import (
    ExecutionRequest,
    execution_receipt_from_results,
    execution_request_from_workflow,
)


def test_execution_request_from_workflow_is_domain_neutral() -> None:
    request = execution_request_from_workflow(
        operation_id="op-1",
        target="fixture-target",
        mode="dry_run",
        planned_steps=[
            {
                "id": "query",
                "type": "connector",
                "connector": "fixture_source",
                "action": "read_fixture_state",
            },
        ],
        max_steps=1,
        max_output_bytes=128,
    )

    assert isinstance(request, ExecutionRequest)
    assert request.source == "approved_workflow_plan"
    assert request.steps[0].step_id == "query"
    assert request.steps[0].connector == "fixture_source"
    assert request.resource_limits.max_output_bytes == 128


def test_execution_receipt_carries_digest_refs_without_raw_output() -> None:
    request = execution_request_from_workflow(
        operation_id="op-2",
        target="local",
        mode="dry_run",
        planned_steps=[{"id": "probe", "type": "connector", "action": "uptime"}],
    )
    receipt = execution_receipt_from_results(
        request=request,
        success=True,
        executed_steps=["probe"],
        step_results={
            "probe": {
                "success": True,
                "output": {
                    "data": {
                        "stdout": "raw text stays in step result",
                        "output_digests": {"stdout": "sha256:abc"},
                        "output_truncated": {"stdout": False},
                    }
                },
            }
        },
    )
    payload = receipt.as_dict()

    assert payload["success"] is True
    assert payload["step_receipts"][0]["output_digest_refs"] == {"stdout": "sha256:abc"}
    assert "raw text stays in step result" not in repr(payload["step_receipts"])
