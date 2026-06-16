from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan


class FileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".rexecop"
        self.operations_dir = self.root / "operations"
        self.plans_dir = self.root / "plans"
        self.evidence_dir = self.root / "evidence"
        self.receipts_dir = self.root / "receipts"

    def ensure_layout(self) -> None:
        self.operations_dir.mkdir(parents=True, exist_ok=True)
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.receipts_dir.mkdir(parents=True, exist_ok=True)

    def save_operation(self, operation: Operation) -> None:
        self.ensure_layout()
        path = self.operations_dir / f"{operation.id}.json"
        path.write_text(json.dumps(operation.as_dict(), indent=2, sort_keys=True) + "\n")

    def load_operation(self, operation_id: str) -> Operation:
        path = self.operations_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"operation not found: {operation_id}")
        data = json.loads(path.read_text())
        return Operation.from_dict(data)

    def save_plan(self, plan: OperationPlan) -> None:
        self.ensure_layout()
        path = self.plans_dir / f"{plan.operation_id}.json"
        path.write_text(json.dumps(plan.as_dict(), indent=2, sort_keys=True) + "\n")

    def load_plan(self, operation_id: str) -> OperationPlan:
        path = self.plans_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"operation plan not found: {operation_id}")
        data = json.loads(path.read_text())
        return OperationPlan.from_dict(data)

    def save_evidence_event(self, operation_id: str, event: dict[str, Any]) -> None:
        self.ensure_layout()
        event_id = str(event["event_id"])
        op_dir = self.evidence_dir / operation_id
        op_dir.mkdir(parents=True, exist_ok=True)
        path = op_dir / f"{event_id}.json"
        path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n")

    def list_evidence_events(self, operation_id: str) -> list[dict[str, Any]]:
        op_dir = self.evidence_dir / operation_id
        if not op_dir.is_dir():
            return []
        events: list[dict[str, Any]] = []
        for path in sorted(op_dir.glob("*.json")):
            events.append(json.loads(path.read_text()))
        return events

    def save_receipt_export(self, operation_id: str, export: dict[str, Any]) -> Path:
        self.ensure_layout()
        path = self.receipts_dir / f"{operation_id}.json"
        path.write_text(json.dumps(export, indent=2, sort_keys=True) + "\n")
        return path

    def load_receipt_export(self, operation_id: str) -> dict[str, Any]:
        path = self.receipts_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"receipt export not found: {operation_id}")
        return json.loads(path.read_text())
