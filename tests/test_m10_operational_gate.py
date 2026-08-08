from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_m10_operational_gate.py"
RECORD = ROOT / "docs" / "release-qualification" / "m10-operational.json"


def _load():
    spec = importlib.util.spec_from_file_location("rexecop_m10_operational_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record() -> dict[str, object]:
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_m10_operational_record_matches_historical_qualified_source() -> None:
    gate = _load()

    assert gate.validate_record(_record(), source_version="0.3.0rc3") == []


def test_m10_operational_record_allows_mechanical_target_release_bump() -> None:
    gate = _load()

    assert gate.validate_record(_record(), source_version="1.0.0rc1") == []


def test_m10_operational_record_rejects_unqualified_rc2_target() -> None:
    gate = _load()

    errors = gate.validate_record(_record(), source_version="1.0.0rc2")

    assert "operational_qualification_version_drift" in errors


def test_m10_operational_record_rejects_live_mutation() -> None:
    gate = _load()
    record = copy.deepcopy(_record())
    record["scope"]["live_mutations_performed"] = True

    errors = gate.validate_record(record, source_version="0.3.0rc3")

    assert "operational_qualification_live_mutation_detected" in errors


def test_m10_operational_record_rejects_public_topology_disclosure() -> None:
    gate = _load()
    record = copy.deepcopy(_record())
    record["disclosure"]["public_projection_private_address_matches"] = 1

    errors = gate.validate_record(record, source_version="0.3.0rc3")

    assert (
        "operational_qualification_disclosure_detected:"
        "public_projection_private_address_matches"
    ) in errors


def test_m10_operational_record_requires_completed_post_io_window() -> None:
    gate = _load()
    record = copy.deepcopy(_record())
    record["recovery"]["post_io_crash"]["backend_io_completed_before_crash"] = False

    errors = gate.validate_record(record, source_version="0.3.0rc3")

    assert "operational_qualification_post_io_not_proven" in errors


def test_m10_operational_record_rejects_retained_post_io_output() -> None:
    gate = _load()
    record = copy.deepcopy(_record())
    record["recovery"]["post_io_crash"]["raw_output_retained"] = True

    errors = gate.validate_record(record, source_version="0.3.0rc3")

    assert "operational_qualification_post_io_raw_output_retained" in errors
