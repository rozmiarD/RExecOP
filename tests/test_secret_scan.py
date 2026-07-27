from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from rexecop.security import secret_scan as packaged_scanner

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "secret_scan.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("rexecop_secret_scan", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_secret_scanner_reports_fingerprint_without_value() -> None:
    scanner = _load_scanner()
    secret = b"github_pat_" + b"A" * 60
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="fixture.txt",
        data=b"credential=" + secret,
    )
    assert findings
    rendered = "\n".join(item.render() for item in findings)
    assert secret.decode() not in rendered
    assert "sha256=" in rendered


def test_secret_scanner_allows_github_oidc_permission_line() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path=".github/workflows/publish.yml",
        data=b"  id-token: write\n",
    )
    assert findings == []


def test_secret_scanner_allows_explicit_placeholder() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="example.yaml",
        data=b"api_token: REPLACE_ME",
    )
    assert findings == []


def test_secret_scanner_detects_former_domain_placeholder_exemptions() -> None:
    scanner = _load_scanner()
    former_domain_values = (b"p" + b"bs-secret", b"p" + b"bs-token-value")

    for value in former_domain_values:
        findings = scanner.scan_data(
            scope="test",
            identity="fixture",
            path="environment.yaml",
            data=b"api_token=" + value,
        )
        assert [finding.rule for finding in findings] == ["credential_assignment"]


def test_secret_scanner_detects_compound_token_key() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="environment.yaml",
        data=b"fixture_api_" + b"token: " + b"actual-credential-value",
    )
    assert findings


def test_secret_scanner_allows_unquoted_python_reference() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="runtime.py",
        data=b"author" + b"ization = decision.author" + b"ization\n",
    )
    assert findings == []


def test_secret_scanner_detects_quoted_python_credential_literal() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="runtime.py",
        data=b"author" + b'ization = "actual-credential-value"\n',
    )
    assert findings


def test_secret_scanner_detects_sensitive_filename() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_path(
        scope="test",
        identity="fixture",
        path="operator/private.pem",
    )
    assert findings[0].rule == "sensitive_filename"


def test_secret_scanner_redacts_provider_token_from_reported_path() -> None:
    scanner = _load_scanner()
    provider_value = "github_" + "pat_" + "A" * 60
    finding = scanner.Finding(
        scope="test",
        identity="fixture",
        path=f"leaked-{provider_value}.txt",
        line=0,
        rule="sensitive_filename",
        fingerprint="abc",
    )
    assert provider_value not in finding.render()
    assert "[REDACTED]" in finding.render()


def test_worktree_wrapper_uses_packaged_scanner_types_and_functions() -> None:
    scanner = _load_scanner()

    assert scanner.Finding is packaged_scanner.Finding
    assert scanner.scan_data is packaged_scanner.scan_data
    assert scanner.scan_path is packaged_scanner.scan_path
    assert scanner.scan_worktree is packaged_scanner.scan_worktree
    assert scanner.scan_history is packaged_scanner.scan_history
    assert scanner.scan_commit_messages is packaged_scanner.scan_commit_messages


def test_worktree_wrapper_and_packaged_scanner_preserve_findings() -> None:
    scanner = _load_scanner()
    secret = b"github_pat_" + b"A" * 60
    cases = (
        ("fixture.txt", b"credential=" + secret),
        ("example.yaml", b"api_token: REPLACE_ME"),
        ("runtime.py", b"approval = decision.approval\n"),
    )

    for path, data in cases:
        assert scanner.scan_data(
            scope="test", identity="fixture", path=path, data=data
        ) == packaged_scanner.scan_data(scope="test", identity="fixture", path=path, data=data)


def _baseline_finding(scanner, **changes: object):
    identity, path, line, rule, fingerprint, scope = next(iter(scanner.IMMUTABLE_HISTORY_BASELINE))
    fields: dict[str, object] = {
        "identity": identity,
        "path": path,
        "line": line,
        "rule": rule,
        "fingerprint": fingerprint,
        "scope": scope,
    }
    fields.update(changes)
    return scanner.Finding(**fields)


def test_immutable_history_baseline_is_exact_and_history_only() -> None:
    scanner = _load_scanner()
    finding = _baseline_finding(scanner)

    assert len(scanner.IMMUTABLE_HISTORY_BASELINE) == 15
    assert scanner._is_immutable_history_baseline(finding)
    for field, value in (
        ("identity", "f" * 40),
        ("path", "new-path.md"),
        ("line", finding.line + 1),
        ("rule", "high_entropy_credential"),
        ("fingerprint", "0" * 12),
        ("scope", "worktree"),
    ):
        assert not scanner._is_immutable_history_baseline(
            _baseline_finding(scanner, **{field: value})
        )
    assert not scanner._is_immutable_history_baseline(
        _baseline_finding(scanner, scope="commit", path="(commit-message)")
    )


def test_secret_scan_cli_suppresses_only_current_immutable_history_baseline() -> None:
    scanner = _load_scanner()

    assert scanner.main(root=ROOT, argv=["--history"]) == 0
