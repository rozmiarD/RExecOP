from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.atomic import atomic_write_text, secure_directory


class FileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".rexecop"
        self.operations_dir = self.root / "operations"
        self.plans_dir = self.root / "plans"
        self.evidence_dir = self.root / "evidence"
        self.receipts_dir = self.root / "receipts"
        self.sclite_dir = self.root / "sclite"
        self.approvals_dir = self.root / "approvals"

    def ensure_layout(self) -> None:
        secure_directory(self.root)
        for path in (
            self.operations_dir,
            self.plans_dir,
            self.evidence_dir,
            self.receipts_dir,
            self.sclite_dir,
            self.approvals_dir,
        ):
            secure_directory(path)

    def operation_sclite_dir(self, operation_id: str) -> Path:
        self.ensure_layout()
        path = self.sclite_dir / operation_id
        secure_directory(path)
        return path

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        atomic_write_text(
            path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    def save_operation(self, operation: Operation) -> None:
        self.ensure_layout()
        path = self.operations_dir / f"{operation.id}.json"
        self._write_json(path, operation.as_dict())

    def load_operation(self, operation_id: str) -> Operation:
        path = self.operations_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"operation not found: {operation_id}")
        data = json.loads(path.read_text())
        return Operation.from_dict(data)

    def list_operations(self) -> list[Operation]:
        self.ensure_layout()
        operations: list[Operation] = []
        for path in sorted(self.operations_dir.glob("*.json")):
            operations.append(Operation.from_dict(json.loads(path.read_text())))
        return operations

    def save_plan(self, plan: OperationPlan) -> None:
        self.ensure_layout()
        path = self.plans_dir / f"{plan.operation_id}.json"
        self._write_json(path, plan.as_dict())

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
        secure_directory(op_dir)
        path = op_dir / f"{event_id}.json"
        self._write_json(path, event)

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
        self._write_json(path, export)
        return path

    def load_receipt_export(self, operation_id: str) -> dict[str, Any]:
        path = self.receipts_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"receipt export not found: {operation_id}")
        return json.loads(path.read_text())

    def save_approval(self, operation_id: str, approval: dict[str, Any]) -> Path:
        self.ensure_layout()
        path = self.approvals_dir / f"{operation_id}.json"
        self._write_json(path, approval)
        return path

    def load_approval(self, operation_id: str) -> dict[str, Any]:
        path = self.approvals_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"approval not found: {operation_id}")
        return json.loads(path.read_text())
