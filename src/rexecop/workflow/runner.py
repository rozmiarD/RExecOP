from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from rexecop.execution.backend import StepExecutionContext, StepExecutionResult
from rexecop.execution.executor import StepExecutor


class WorkflowEvidenceSink(Protocol):
    def on_step_started(self, *, step_id: str, correlation_id: str) -> None: ...

    def on_step_completed(
        self,
        *,
        step_id: str,
        result: StepExecutionResult,
        correlation_id: str,
    ) -> None: ...

    def on_step_failed(
        self,
        *,
        step_id: str,
        result: StepExecutionResult,
        correlation_id: str,
    ) -> None: ...


@dataclass
class WorkflowRunResult:
    operation_id: str
    success: bool
    executed_steps: list[str] = field(default_factory=list)
    step_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    shared_state: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "success": self.success,
            "executed_steps": list(self.executed_steps),
            "step_results": dict(self.step_results),
            "shared_state": dict(self.shared_state),
            "error": self.error,
        }


class WorkflowRunner:
    """Execute declared workflow steps only; never invent steps."""

    def __init__(self, executor: StepExecutor | None = None) -> None:
        self.executor = executor or StepExecutor()

    def run(
        self,
        *,
        operation_id: str,
        target: str,
        mode: str,
        planned_steps: list[dict[str, Any]],
        correlation_id: str,
        evidence_sink: WorkflowEvidenceSink | None = None,
        shared_state: dict[str, Any] | None = None,
    ) -> WorkflowRunResult:
        state = dict(shared_state or {})
        executed: list[str] = []
        results: dict[str, dict[str, Any]] = {}

        for step in planned_steps:
            step_id = str(step.get("id") or "")
            if evidence_sink is not None:
                evidence_sink.on_step_started(step_id=step_id, correlation_id=correlation_id)

            context = StepExecutionContext(
                operation_id=operation_id,
                target=target,
                mode=mode,
                step=step,
                shared_state=state,
            )
            result = self.executor.execute(context)
            results[step_id] = result.as_dict()

            if result.success:
                executed.append(step_id)
                if evidence_sink is not None:
                    evidence_sink.on_step_completed(
                        step_id=step_id,
                        result=result,
                        correlation_id=correlation_id,
                    )
                continue

            if evidence_sink is not None:
                evidence_sink.on_step_failed(
                    step_id=step_id,
                    result=result,
                    correlation_id=correlation_id,
                )
            return WorkflowRunResult(
                operation_id=operation_id,
                success=False,
                executed_steps=executed,
                step_results=results,
                shared_state=state,
                error=result.error or f"step failed: {step_id}",
            )

        return WorkflowRunResult(
            operation_id=operation_id,
            success=True,
            executed_steps=executed,
            step_results=results,
            shared_state=state,
        )
