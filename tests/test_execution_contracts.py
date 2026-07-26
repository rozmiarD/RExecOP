from __future__ import annotations

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.execution.model import (
    ExecutionRequest,
    ResourceLimits,
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


@pytest.mark.parametrize(
    "value",
    [-1, 0, True, 1.0, "1", float("nan"), float("inf")],
)
def test_resource_limits_reject_non_exact_positive_output_budget(value: object) -> None:
    with pytest.raises(RExecOpValidationError, match="invalid execution resource limits"):
        ResourceLimits(max_output_bytes=value)  # type: ignore[arg-type]
    with pytest.raises(RExecOpValidationError, match="invalid execution resource limits"):
        ResourceLimits.from_mapping({"max_output_bytes": value})


@pytest.mark.parametrize("value", [1, 1024 * 1024])
def test_resource_limits_accept_exact_positive_output_budget(value: int) -> None:
    assert ResourceLimits(max_output_bytes=value).max_output_bytes == value
    assert ResourceLimits.from_mapping({"max_output_bytes": value}).max_output_bytes == value


def test_resource_limits_default_output_budget_only_when_omitted() -> None:
    assert ResourceLimits.from_mapping({}).max_output_bytes == 65536
