from __future__ import annotations

from pathlib import Path

import yaml

from rexecop.errors import RExecOpValidationError


def load_profile_connector_capabilities(profile_root: Path, connector_name: str) -> frozenset[str]:
  path = profile_root / "connectors" / f"{connector_name}.yaml"
  if not path.is_file():
    raise RExecOpValidationError(f"profile connector contract not found: {connector_name}")
  data = yaml.safe_load(path.read_text())
  if not isinstance(data, dict):
    raise RExecOpValidationError(f"invalid connector contract: {path}")
  connector = data.get("connector")
  if not isinstance(connector, dict):
    raise RExecOpValidationError(f"connector mapping missing in: {path}")
  capabilities = connector.get("capabilities")
  if not isinstance(capabilities, list) or not capabilities:
    raise RExecOpValidationError(f"connector.capabilities required in: {path}")
  return frozenset(str(item) for item in capabilities)


def connector_action_allowed(
  *,
  profile_root: Path | None,
  connector_name: str,
  action: str,
) -> bool:
  if profile_root is None:
    return True
  allowed = load_profile_connector_capabilities(profile_root, connector_name)
  return action in allowed
