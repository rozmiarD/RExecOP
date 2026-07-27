from __future__ import annotations

import json
import os
import stat
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from rexecop.catalog.loader import _load_catalog_environments_for_secret_inspection
from rexecop.environment.loader import _load_environment_for_secret_inspection
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpError, RExecOpValidationError
from rexecop.evidence.redaction import (
    REDACTED,
    clear_registered_secret_values,
    redact_payload,
    redact_text,
    register_secret_value,
)
from rexecop.secrets.reference import (
    collect_secret_ref_bindings,
    env_key_for_secret_ref,
    secret_ref_env_collisions,
)
from rexecop.secrets.resolver import MAX_SECRETS_FILE_BYTES

SECRETS_DOCTOR_SCHEMA = "rexecop.secrets_doctor.v0.1"
CHECK_PASSED = "passed"
CHECK_WARNING = "warning"
CHECK_BLOCKER = "blocker"
REDACTION_PROBE = "rexecop-secrets-doctor-redaction-probe-7c4f91"


def run_secrets_doctor(
    *,
    env_path: Path | None = None,
    catalog_path: Path | None = None,
    secrets_file: Path | None = None,
) -> dict[str, Any]:
    documents: list[tuple[str, dict[str, Any]]] = []
    seen_environment_paths: set[Path] = set()
    if env_path is not None:
        resolved_environment = env_path.expanduser().resolve()
        environment = _load_environment_for_secret_inspection(resolved_environment)
        seen_environment_paths.add(resolved_environment)
        documents.append(("environment", environment.as_dict()))
    if catalog_path is not None:
        referenced = _load_catalog_environments_for_secret_inspection(catalog_path)
        for index, (environment_path, environment) in enumerate(referenced):
            resolved_environment = environment_path.expanduser().resolve()
            if resolved_environment in seen_environment_paths:
                continue
            seen_environment_paths.add(resolved_environment)
            documents.append((f"catalog_environment[{index}]", environment.as_dict()))

    configured_file = secrets_file or _configured_secrets_file()
    checks = [
        _check_inline_secrets(documents),
        _check_secret_ref_bindings(documents),
        _check_secret_ref_env_collisions(documents),
        _check_missing_refs(documents, configured_file),
        _check_duplicate_refs(documents),
        _check_secrets_file_permissions(configured_file, documents),
        _check_orphan_file_refs(documents, configured_file),
        _check_redaction_self_test(),
    ]
    blockers = [check["id"] for check in checks if check["status"] == CHECK_BLOCKER]
    warnings = [check["id"] for check in checks if check["status"] == CHECK_WARNING]
    status = CHECK_BLOCKER if blockers else CHECK_WARNING if warnings else CHECK_PASSED
    return {
        "schema": SECRETS_DOCTOR_SCHEMA,
        "status": status,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": sorted(
            {
                str(check["next_action"])
                for check in checks
                if check.get("next_action")
            }
        ),
        "summary": {
            "documents_checked": [name for name, _ in documents],
            "secrets_file_configured": configured_file is not None,
            "secret_ref_count": sum(
                int((check.get("details") or {}).get("binding_count", 0))
                for check in checks
                if check["id"] == "secret_ref_bindings"
            ),
        },
    }


def _check(
    check_id: str,
    status: str,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    next_action: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "summary": summary,
    }
    if details:
        payload["details"] = details
    if next_action:
        payload["next_action"] = next_action
    return payload


def _check_inline_secrets(documents: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    if not documents:
        return _check(
            "inline_secrets",
            CHECK_WARNING,
            "no environment or catalog was provided",
            next_action="rerun secrets doctor with --env and/or --catalog",
        )
    try:
        for name, document in documents:
            validate_no_inline_secrets(document)
    except RExecOpError as exc:
        return _check(
            "inline_secrets",
            CHECK_BLOCKER,
            str(exc),
            next_action="replace inline secret material with secret_ref fields",
        )
    return _check("inline_secrets", CHECK_PASSED, "no inline secret material detected")


def _check_secret_ref_bindings(documents: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    if not documents:
        return _check(
            "secret_ref_bindings",
            CHECK_WARNING,
            "no secret_ref bindings to inspect",
        )
    bindings: list[dict[str, str]] = []
    for name, document in documents:
        for binding in collect_secret_ref_bindings(document):
            bindings.append(
                {
                    "document": name,
                    "path": binding["path"],
                    "ref": binding["ref"],
                }
            )
    empty = [item for item in bindings if not item["ref"]]
    if empty:
        return _check(
            "secret_ref_bindings",
            CHECK_BLOCKER,
            "one or more secret_ref bindings are empty",
            details={"binding_count": len(bindings), "empty_bindings": empty},
            next_action="populate every secret_ref with a bounded reference name",
        )
    return _check(
        "secret_ref_bindings",
        CHECK_PASSED,
        "secret_ref bindings are present and non-empty",
        details={
            "binding_count": len(bindings),
            "refs": sorted({item["ref"] for item in bindings}),
        },
    )


def _check_missing_refs(
    documents: list[tuple[str, dict[str, Any]]],
    secrets_file: Path | None,
) -> dict[str, Any]:
    refs = _collect_refs(documents)
    if not refs:
        return _check("missing_refs", CHECK_PASSED, "no secret_ref bindings require resolution")

    file_keys, file_error = _load_secrets_file_keys(secrets_file)
    missing: list[dict[str, Any]] = []
    for ref in sorted(refs):
        if _ref_available_in_env(ref) or ref in file_keys:
            continue
        missing.append(
            {
                "ref": ref,
                "env_key": env_key_for_secret_ref(ref),
                "paths": sorted(refs[ref]),
            }
        )
    if missing:
        next_action = "export REXECOP_SECRET_<REF> or add refs to REXECOP_SECRETS_FILE"
        if file_error:
            next_action = f"fix secrets file policy: {file_error}"
        elif secrets_file is None:
            next_action = "set REXECOP_SECRETS_FILE or provide REXECOP_SECRET_<REF> variables"
        return _check(
            "missing_refs",
            CHECK_BLOCKER,
            "one or more secret_ref values are not resolvable",
            details={"missing": missing, "secrets_file_error": file_error or ""},
            next_action=next_action,
        )
    if file_error:
        return _check(
            "missing_refs",
            CHECK_WARNING,
            "secret refs resolve via environment variables; secrets file policy failed",
            details={"secrets_file_error": file_error},
            next_action="fix REXECOP_SECRETS_FILE permissions and ownership",
        )
    return _check("missing_refs", CHECK_PASSED, "all declared secret_ref values are resolvable")


def _check_secret_ref_env_collisions(
    documents: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    bindings: list[dict[str, str]] = []
    for name, document in documents:
        for binding in collect_secret_ref_bindings(document):
            bindings.append({**binding, "document": name})
    collisions = secret_ref_env_collisions(bindings)
    if collisions:
        return _check(
            "secret_ref_env_collision",
            CHECK_BLOCKER,
            "distinct secret_ref names map to the same environment key",
            details={"collisions": collisions},
            next_action="rename colliding refs explicitly before runtime use",
        )
    return _check(
        "secret_ref_env_collision",
        CHECK_PASSED,
        "secret_ref environment-key mappings are collision free",
    )


def _check_duplicate_refs(documents: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    by_ref: dict[str, list[str]] = defaultdict(list)
    for name, document in documents:
        for binding in collect_secret_ref_bindings(document):
            location = f"{name}:{binding['path']}"
            by_ref[binding["ref"]].append(location)
    duplicates = {
        ref: sorted(paths)
        for ref, paths in by_ref.items()
        if len(paths) > 1
    }
    if duplicates:
        return _check(
            "duplicate_refs",
            CHECK_WARNING,
            "one or more secret_ref names are reused across multiple bindings",
            details={"duplicates": duplicates},
            next_action="review whether duplicate secret_ref reuse is intentional",
        )
    return _check("duplicate_refs", CHECK_PASSED, "no duplicate secret_ref reuse detected")


def _check_secrets_file_permissions(
    secrets_file: Path | None,
    documents: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    refs = _collect_refs(documents)
    if secrets_file is None:
        if refs and not all(_ref_available_in_env(ref) for ref in refs):
            return _check(
                "secrets_file_permissions",
                CHECK_WARNING,
                "REXECOP_SECRETS_FILE is not configured",
                next_action="set REXECOP_SECRETS_FILE to an operator-managed secrets YAML",
            )
        return _check(
            "secrets_file_permissions",
            CHECK_PASSED,
            "secrets file is not required for the current bindings",
        )
    try:
        _validate_secrets_file_policy(secrets_file)
    except RExecOpValidationError as exc:
        return _check(
            "secrets_file_permissions",
            CHECK_BLOCKER,
            str(exc),
            next_action="chmod 600 the secrets file and ensure it is owned by the current user",
        )
    return _check(
        "secrets_file_permissions",
        CHECK_PASSED,
        "secrets file policy is acceptable",
        details={"path": str(secrets_file)},
    )


def _check_orphan_file_refs(
    documents: list[tuple[str, dict[str, Any]]],
    secrets_file: Path | None,
) -> dict[str, Any]:
    if secrets_file is None:
        return _check(
            "orphan_file_refs",
            CHECK_PASSED,
            "secrets file orphan refs were not checked",
        )
    file_keys, file_error = _load_secrets_file_keys(secrets_file)
    if file_error:
        return _check(
            "orphan_file_refs",
            CHECK_WARNING,
            "secrets file keys could not be inspected",
            details={"secrets_file_error": file_error},
        )
    declared = set(_collect_refs(documents))
    orphans = sorted(key for key in file_keys if key not in declared)
    if orphans:
        return _check(
            "orphan_file_refs",
            CHECK_WARNING,
            "secrets file contains refs not referenced by the inspected documents",
            details={"orphans": orphans},
            next_action="remove unused refs or reference them via secret_ref fields",
        )
    return _check("orphan_file_refs", CHECK_PASSED, "no orphan secrets file refs detected")


def _check_redaction_self_test() -> dict[str, Any]:
    clear_registered_secret_values()
    try:
        register_secret_value(REDACTION_PROBE)
        sample = {
            "auth_header": f"Bearer {REDACTION_PROBE}",
            "nested": {"secret_ref": REDACTION_PROBE},
            "message": REDACTION_PROBE,
        }
        redacted = redact_payload(sample)
        rendered = json.dumps(redacted, sort_keys=True)
        text_redacted = redact_text(f"prefix {REDACTION_PROBE} suffix")
        if REDACTION_PROBE in rendered or REDACTION_PROBE in text_redacted:
            return _check(
                "redaction_self_test",
                CHECK_BLOCKER,
                "redaction self-test failed to remove probe material",
            )
        if REDACTED not in rendered or REDACTED not in text_redacted:
            return _check(
                "redaction_self_test",
                CHECK_BLOCKER,
                "redaction self-test did not emit bounded redaction markers",
            )
    finally:
        clear_registered_secret_values()
    return _check(
        "redaction_self_test",
        CHECK_PASSED,
        "redaction self-test passed without exposing probe values",
    )


def _collect_refs(documents: list[tuple[str, dict[str, Any]]]) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = defaultdict(list)
    for name, document in documents:
        for binding in collect_secret_ref_bindings(document):
            if not binding["ref"]:
                continue
            refs[binding["ref"]].append(f"{name}:{binding['path']}")
    return refs


def _configured_secrets_file() -> Path | None:
    configured = os.environ.get("REXECOP_SECRETS_FILE")
    if not configured:
        return None
    return Path(configured).expanduser()


def _ref_available_in_env(ref: str) -> bool:
    return bool(os.environ.get(env_key_for_secret_ref(ref), "").strip())


def _validate_secrets_file_policy(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RExecOpValidationError("REXECOP_SECRETS_FILE is not configured") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RExecOpValidationError("REXECOP_SECRETS_FILE must be a regular file")
    if info.st_uid != os.getuid():
        raise RExecOpValidationError("REXECOP_SECRETS_FILE must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RExecOpValidationError(
            "REXECOP_SECRETS_FILE permissions must be 0600 or stricter"
        )
    if info.st_size > MAX_SECRETS_FILE_BYTES:
        raise RExecOpValidationError("REXECOP_SECRETS_FILE exceeds the size limit")


def _load_secrets_file_keys(path: Path | None) -> tuple[frozenset[str], str]:
    if path is None:
        return frozenset(), ""
    try:
        _validate_secrets_file_policy(path)
    except RExecOpValidationError as exc:
        return frozenset(), str(exc)
    try:
        data = yaml.safe_load(_read_secrets_file(path))
    except (UnicodeError, yaml.YAMLError):
        return frozenset(), "invalid REXECOP_SECRETS_FILE"
    if not isinstance(data, dict):
        return frozenset(), "invalid REXECOP_SECRETS_FILE"
    secrets = data.get("secrets")
    if not isinstance(secrets, dict):
        return frozenset(), "secrets mapping missing in REXECOP_SECRETS_FILE"
    return frozenset(str(key) for key in secrets), ""


def _read_secrets_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            return handle.read(MAX_SECRETS_FILE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
