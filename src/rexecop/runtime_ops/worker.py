from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation


def drain_queue(controller: OperationController) -> list[str]:
    """Drain the run-now queue once (same semantics as after `start`)."""
    return controller.process_queue()


def run_worker(
    controller: OperationController,
    *,
    once: bool = False,
    poll_interval: float = 5.0,
    max_iterations: int | None = None,
    watch_inbox: bool = False,
) -> list[str]:
    """Poll queue (and optional inbox) and start admitted operations."""
    if poll_interval <= 0:
        raise RExecOpValidationError("poll_interval must be positive")

    started: list[str] = []
    iterations = 0
    while True:
        if watch_inbox:
            started.extend(_process_inbox(controller))

        started.extend(controller.process_queue())

        iterations += 1
        if once:
            break
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(poll_interval)

    return started


def trigger_operation(
    controller: OperationController,
    *,
    profile: str,
    environment_path: Path,
    intent: str,
    target: str,
    mode: str = "dry_run",
    source: str = "stdin",
    auto_start: bool = False,
) -> Operation:
    operation = controller.plan(
        profile_path=profile,
        environment_path=environment_path,
        intent=intent,
        target=target,
        mode=mode,
        requested_by=f"trigger:{source}",
    )
    event = controller.evidence.emit(
        operation_id=operation.id,
        event_type=EvidenceEventType.OPERATION_TRIGGERED,
        correlation_id=operation.correlation_id,
        state_before=operation.state,
        state_after=operation.state,
        payload={
            "source": source,
            "profile": profile,
            "intent": intent,
            "target": target,
            "mode": mode,
        },
    )
    operation.evidence_event_ids.append(event)
    operation.metadata["trigger"] = {"source": source}
    controller.store.save_operation(operation)
    if auto_start:
        return controller.start(operation.id)
    return controller.get_operation(operation.id)


def parse_trigger_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = str(payload.get("profile") or "").strip()
    env = str(payload.get("env") or payload.get("environment") or "").strip()
    intent = str(payload.get("intent") or "").strip()
    target = str(payload.get("target") or "").strip()
    mode = str(payload.get("mode") or "dry_run").strip()
    if not profile or not env or not intent or not target:
        raise RExecOpValidationError(
            "trigger payload requires profile, env/environment, intent, and target"
        )
    return {
        "profile": profile,
        "environment_path": Path(env),
        "intent": intent,
        "target": target,
        "mode": mode,
        "auto_start": bool(payload.get("auto_start", False)),
        "source": str(payload.get("source") or "stdin"),
    }


def _process_inbox(controller: OperationController) -> list[str]:
    root = controller.store.root
    if root is None:
        return []
    inbox = root / "inbox"
    if not inbox.is_dir():
        return []

    started: list[str] = []
    for path in sorted(inbox.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise RExecOpValidationError(f"invalid inbox trigger: {path.name}")
            parsed = parse_trigger_payload(payload)
            operation = trigger_operation(
                controller,
                profile=parsed["profile"],
                environment_path=parsed["environment_path"],
                intent=parsed["intent"],
                target=parsed["target"],
                mode=parsed["mode"],
                source=f"inbox:{path.name}",
                auto_start=parsed["auto_start"],
            )
            if parsed["auto_start"]:
                started.append(operation.id)
            path.unlink(missing_ok=True)
        except Exception:
            path.rename(inbox / f"failed-{path.name}")
    return started
