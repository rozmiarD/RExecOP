from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate_f4_conformance_matrix.py"


def test_conformance_matrix_declares_fixed_train_and_negative_vectors() -> None:
    spec = importlib.util.spec_from_file_location("f4_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.REPOS == ("sclite", "govengine", "rexecop", "tecrax")
    assert set(module.PIN_EDGES["tecrax"]) == {"sclite-core", "govengine", "rexecop"}
    assert len(module.OWNER_SCHEMAS) == 7
