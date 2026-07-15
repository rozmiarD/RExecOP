from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from govengine.capabilities import (
    CapabilityInventoryBinding,
    capability_inventory_binding_digest,
)
from govengine.governance import requested_scope_digest

from rexecop import __version__
from rexecop.adapters.govengine_port.runtime_authority import (
    RuntimeAttemptGovernanceFacts,
)
from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.registry import list_connector_backend_descriptors
from rexecop.errors import RExecOpValidationError


def build_runtime_attempt_governance_facts(
    *,
    operation_id: str,
    step_id: str,
    attempt_id: str,
    target: str,
    execution_spec: Mapping[str, Any],
    lease: Mapping[str, Any],
    inventory_epoch: int = 0,
) -> RuntimeAttemptGovernanceFacts:
    runtime_instance_id = str(lease.get("process_instance_id") or "")
    lease_owner_ref = str(lease.get("owner_token") or "")
    lease_epoch = int(lease.get("lease_epoch") or 0)
    if not runtime_instance_id or not lease_owner_ref or lease_epoch < 1:
        raise RExecOpValidationError("canonical_governance_requires_active_lease")
    execution_spec_digest = str(execution_spec.get("digest") or "")
    if not execution_spec_digest.startswith("sha256:"):
        raise RExecOpValidationError("canonical_governance_missing_execution_spec_digest")
    inventory = _runtime_inventory(
        runtime_instance_id=runtime_instance_id,
        inventory_epoch=inventory_epoch,
    )
    return RuntimeAttemptGovernanceFacts(
        operation_id=operation_id,
        step_id=step_id,
        attempt_id=attempt_id,
        runtime_instance_id=runtime_instance_id,
        lease_id="sha256:" + canonical_digest({"owner_token": lease_owner_ref}),
        lease_epoch=lease_epoch,
        fencing_token_digest="sha256:"
        + canonical_digest(
            {
                "owner_token": lease_owner_ref,
                "lease_epoch": lease_epoch,
                "runtime_instance_id": runtime_instance_id,
            }
        ),
        execution_spec_digest=execution_spec_digest,
        payload_digest=execution_payload_digest(execution_spec),
        requested_scope_digest=requested_scope_digest(
            _requested_scope(target=target, execution_spec=execution_spec)
        ),
        capability_inventory_digest=capability_inventory_binding_digest(inventory),
        inventory_epoch=inventory.inventory_epoch,
    )


def execution_payload_digest(execution_spec: Mapping[str, Any]) -> str:
    payload = execution_spec.get("payload")
    if not isinstance(payload, Mapping):
        raise RExecOpValidationError("canonical_governance_missing_payload")
    for key in ("shape_digest", "argv_digest", "action_digest"):
        value = str(payload.get(key) or "")
        if value.startswith("sha256:"):
            return value
    raise RExecOpValidationError("canonical_governance_missing_payload_digest")


def _runtime_inventory(
    *,
    runtime_instance_id: str,
    inventory_epoch: int,
) -> CapabilityInventoryBinding:
    descriptors = list_connector_backend_descriptors()
    backend_classes = tuple(sorted(item.backend_class for item in descriptors))
    capabilities = tuple(
        sorted(
            {
                capability
                for item in descriptors
                for capability in item.capability_descriptors
            }
        )
    )
    side_effect_classes = {"read_only"}
    if any("apply" in item.supported_modes for item in descriptors):
        side_effect_classes.add("mutation")
    source_digest = "sha256:" + canonical_digest(
        [item.as_dict() for item in descriptors]
    )
    return CapabilityInventoryBinding(
        inventory_id=f"rexecop-inventory:{source_digest[7:23]}",
        runtime_instance_id=runtime_instance_id,
        runtime_version=__version__,
        inventory_epoch=inventory_epoch,
        source_ref="rexecop.connector_registry",
        attestation_ref=source_digest,
        backend_classes=backend_classes,
        side_effect_classes=tuple(sorted(side_effect_classes)),
        capabilities=capabilities,
    )


def _requested_scope(
    *,
    target: str,
    execution_spec: Mapping[str, Any],
) -> dict[str, Any]:
    scope: dict[str, Any] = {"target_namespace": target}
    payload = execution_spec.get("payload")
    destination = payload.get("destination_binding") if isinstance(payload, Mapping) else None
    if not isinstance(destination, Mapping):
        return scope
    address_class = str(destination.get("address_class") or "")
    address_class = {"public_ip": "public"}.get(address_class, address_class)
    if address_class not in {"public", "private", "loopback", "link_local"}:
        raise RExecOpValidationError(
            "canonical_governance_unresolved_destination_address_class"
        )
    scope["requested_destination"] = {
        "scheme": str(destination.get("scheme") or ""),
        "effective_port": int(destination.get("effective_port") or 0),
        "address_class": address_class,
        "origin_binding_digest": str(destination.get("origin_binding_digest") or ""),
    }
    return scope
