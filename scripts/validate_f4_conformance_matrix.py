from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

REPOS = ("sclite", "govengine", "rexecop", "tecrax")
DIST_NAMES = {
    "sclite": "sclite_core",
    "govengine": "govengine",
    "rexecop": "rexecop",
    "tecrax": "tecrax",
}
PIN_EDGES = {
    "govengine": {"sclite-core": "sclite"},
    "rexecop": {"sclite-core": "sclite", "govengine": "govengine"},
    "tecrax": {"sclite-core": "sclite", "govengine": "govengine", "rexecop": "rexecop"},
}
OWNER_SCHEMAS = (
    "automation_chain.v0.1.schema.json",
    "escalation_proposal.v0.1.schema.json",
    "finding.v0.1.schema.json",
    "observation_envelope.v0.1.schema.json",
    "reaction_plan.v0.1.schema.json",
    "trigger_decision.v0.1.schema.json",
    "watchdog_decision.v0.1.schema.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def _head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _pins(root: Path) -> dict[str, str]:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    return {name: version for name, version in re.findall(r'"([A-Za-z0-9_-]+)==([^";]+)', text)}


def build_report(projects_root: Path, wheelhouse: Path) -> dict[str, Any]:
    roots = {name: projects_root / name for name in REPOS}
    versions = {name: _version(root) for name, root in roots.items()}
    errors: list[str] = []
    repositories: dict[str, Any] = {}
    for name, root in roots.items():
        dist_name = DIST_NAMES[name]
        wheel = next(iter(sorted(wheelhouse.glob(f"{dist_name}-{versions[name]}-*.whl"))), None)
        sdist = next(iter(sorted(wheelhouse.glob(f"{dist_name}-{versions[name]}.tar.gz"))), None)
        if wheel is None or sdist is None:
            errors.append(f"candidate_artifact_missing:{name}")
        repositories[name] = {
            "commit": _head(root),
            "version": versions[name],
            "wheel": None if wheel is None else {"file": wheel.name, "sha256": _sha256(wheel)},
            "sdist": None if sdist is None else {"file": sdist.name, "sha256": _sha256(sdist)},
        }
    for consumer, edges in PIN_EDGES.items():
        pins = _pins(roots[consumer])
        for package, provider in edges.items():
            if pins.get(package) != versions[provider]:
                errors.append(
                    f"exact_pin_drift:{consumer}:{package}:{pins.get(package)}:{versions[provider]}"
                )
    parity: list[dict[str, str]] = []
    for filename in OWNER_SCHEMAS:
        legacy = roots["sclite"] / "sclite/schemas" / filename
        owner = roots["rexecop"] / "src/rexecop/contracts/schemas" / filename
        left, right = _sha256(legacy), _sha256(owner)
        parity.append(
            {
                "schema": filename,
                "sclite_legacy_sha256": left,
                "rexecop_owner_sha256": right,
            }
        )
        if left != right:
            errors.append(f"installed_resource_parity:{filename}")
    return {
        "schema": "govstack.f4_conformance_matrix.v1",
        "order": list(REPOS),
        "repositories": repositories,
        "owner_schema_parity": parity,
        "negative_vectors": ["resolver_unknown_namespace", "resolver_contract_collision"],
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.projects_root.resolve(), args.wheelhouse.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
