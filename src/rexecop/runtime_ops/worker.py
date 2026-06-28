from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.runtime_ops.watchdog import (
    DEFAULT_INBOX_RETRY_BUDGET,
    DEFAULT_STALE_OPERATION_SECONDS,
    DEFAULT_WORKER_ID,
    WatchdogService,
)
from rexecop.storage.atomic import secure_directory, secure_file
from rexecop.triggers.service import TriggerService


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
    watchdog: bool = False,
    worker_id: str = DEFAULT_WORKER_ID,
    stale_inbox_seconds: float = 3600.0,
    stale_operation_seconds: float = DEFAULT_STALE_OPERATION_SECONDS,
    inbox_retry_budget: int = DEFAULT_INBOX_RETRY_BUDGET,
) -> list[str]:
    """Poll queue (and optional inbox) and start admitted operations."""
    if poll_interval <= 0:
        raise RExecOpValidationError("poll_interval must be positive")
    if stale_inbox_seconds <= 0:
        raise RExecOpValidationError("stale_inbox_seconds must be positive")
    if stale_operation_seconds <= 0:
        raise RExecOpValidationError("stale_operation_seconds must be positive")
    if inbox_retry_budget <= 0:
        raise RExecOpValidationError("inbox_retry_budget must be positive")

    started: list[str] = []
    iterations = 0
    watchdog_service = WatchdogService(controller.store) if watchdog else None
    while True:
        if watchdog_service is not None:
            watchdog_service.record_heartbeat(worker_id=worker_id)
            if watch_inbox:
                watchdog_service.move_stale_inbox_items(
                    max_age_seconds=stale_inbox_seconds
                )
            watchdog_service.record_stale_active_operations(
                max_age_seconds=stale_operation_seconds
            )

        if watch_inbox:
            started.extend(
                _process_inbox(
                    controller,
                    watchdog_service=watchdog_service,
                    inbox_retry_budget=inbox_retry_budget,
                )
            )

        started.extend(controller.process_queue())
        if watchdog_service is not None:
            watchdog_service.record_queue_depth(
                depth=len(controller.runtime.queue.list_pending())
            )

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
    auto_react: str | None = None,
) -> Operation:
    operation = controller.plan(
        profile_path=profile,
        environment_path=environment_path,
        intent=intent,
        target=target,
        mode=mode,
        requested_by=f"trigger:{source}",
        auto_react=auto_react,
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
            "auto_react": auto_react,
        },
    )
    operation.evidence_event_ids.append(event)
    operation.metadata["trigger"] = {"source": source}
    controller.store.save_operation(operation)
    if auto_start:
        return controller.start(operation.id)
    return controller.get_operation(operation.id)


def parse_trigger_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "trigger_event" in payload:
        return parse_trigger_event_payload(payload)
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
        "auto_react": (
            str(payload["auto_react"]).strip()
            if payload.get("auto_react") is not None
            else None
        ),
        "source": str(payload.get("source") or "stdin"),
    }


def parse_trigger_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = str(payload.get("profile") or "").strip()
    env = str(payload.get("env") or payload.get("environment") or "").strip()
    catalog = str(payload.get("catalog") or "").strip()
    event = payload.get("trigger_event")
    if not profile or not isinstance(event, dict):
        raise RExecOpValidationError("trigger event requires profile and trigger_event")
    if not env and not catalog:
        raise RExecOpValidationError("trigger event requires env/environment or catalog")
    return {
        "kind": "trigger_event",
        "profile": profile,
        "environment_path": Path(env) if env else None,
        "catalog_path": Path(catalog) if catalog else None,
        "trigger_event": event,
        "source": str(payload.get("source") or "stdin"),
    }


def trigger_event(
    controller: OperationController,
    *,
    profile: str,
    environment_path: Path | None,
    event_payload: dict[str, Any],
    catalog_path: Path | None = None,
    source: str = "stdin",
) -> dict[str, Any]:
    return TriggerService(controller).process_event(
        profile_path=profile,
        environment_path=environment_path,
        catalog_path=catalog_path,
        event_payload=event_payload,
        source=source,
    )


def _process_inbox(
    controller: OperationController,
    *,
    watchdog_service: WatchdogService | None = None,
    inbox_retry_budget: int = DEFAULT_INBOX_RETRY_BUDGET,
) -> list[str]:
    root = controller.store.root
    if root is None:
        return []
    inbox = root / "inbox"
    if not inbox.is_dir():
        return []
    secure_directory(inbox)

    started: list[str] = []
    for path in sorted(inbox.glob("*.json")):
        try:
            secure_file(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise RExecOpValidationError(f"invalid inbox trigger: {path.name}")
            parsed = parse_trigger_payload(payload)
            if parsed.get("kind") == "trigger_event":
                trigger_event(
                    controller,
                    profile=parsed["profile"],
                    environment_path=parsed["environment_path"],
                    catalog_path=parsed["catalog_path"],
                    event_payload=parsed["trigger_event"],
                    source=f"inbox:{path.name}",
                )
            else:
                operation = trigger_operation(
                    controller,
                    profile=parsed["profile"],
                    environment_path=parsed["environment_path"],
                    intent=parsed["intent"],
                    target=parsed["target"],
                    mode=parsed["mode"],
                    source=f"inbox:{path.name}",
                    auto_start=parsed["auto_start"],
                    auto_react=parsed["auto_react"],
                )
                if parsed["auto_start"]:
                    started.append(operation.id)
            path.unlink(missing_ok=True)
            if watchdog_service is not None:
                watchdog_service.clear_inbox_retry_budget(path.name)
        except Exception as exc:
            if watchdog_service is not None:
                watchdog_service.record_inbox_processing_failure(
                    path,
                    error_type=exc.__class__.__name__,
                    max_attempts=inbox_retry_budget,
                )
            else:
                path.rename(inbox / f"failed-{path.name}")
    return started
