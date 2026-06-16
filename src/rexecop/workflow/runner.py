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
    error_class: str = ""
    stopped_step_id: str = ""
    next_step_index: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "success": self.success,
            "executed_steps": list(self.executed_steps),
            "step_results": dict(self.step_results),
            "shared_state": dict(self.shared_state),
            "error": self.error,
            "error_class": self.error_class,
            "stopped_step_id": self.stopped_step_id,
            "next_step_index": self.next_step_index,
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
        start_index: int = 0,
        max_steps: int | None = None,
    ) -> WorkflowRunResult:
        state = dict(shared_state or {})
        executed = list(state.get("executed_steps") or [])
        results = dict(state.get("step_results") or {})
        index = start_index
        steps_run = 0

        while index < len(planned_steps):
            if max_steps is not None and steps_run >= max_steps:
                break

            step = planned_steps[index]
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
                if step_id not in executed:
                    executed.append(step_id)
                if evidence_sink is not None:
                    evidence_sink.on_step_completed(
                        step_id=step_id,
                        result=result,
                        correlation_id=correlation_id,
                    )
                index += 1
                steps_run += 1
                continue

            if evidence_sink is not None:
                evidence_sink.on_step_failed(
                    step_id=step_id,
                    result=result,
                    correlation_id=correlation_id,
                )
            error_class = str(result.output.get("error_class") or "")
            return WorkflowRunResult(
                operation_id=operation_id,
                success=False,
                executed_steps=executed,
                step_results=results,
                shared_state=state,
                error=result.error or f"step failed: {step_id}",
                error_class=error_class,
                stopped_step_id=step_id,
                next_step_index=index,
            )

        state["executed_steps"] = executed
        state["step_results"] = results
        return WorkflowRunResult(
            operation_id=operation_id,
            success=True,
            executed_steps=executed,
            step_results=results,
            shared_state=state,
            next_step_index=index,
        )

    def run_single_step(
        self,
        *,
        operation_id: str,
        target: str,
        mode: str,
        planned_steps: list[dict[str, Any]],
        correlation_id: str,
        evidence_sink: WorkflowEvidenceSink | None = None,
        shared_state: dict[str, Any] | None = None,
        start_index: int = 0,
    ) -> WorkflowRunResult:
        return self.run(
            operation_id=operation_id,
            target=target,
            mode=mode,
            planned_steps=planned_steps,
            correlation_id=correlation_id,
            evidence_sink=evidence_sink,
            shared_state=shared_state,
            start_index=start_index,
            max_steps=1,
        )
