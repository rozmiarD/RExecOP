from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


def test_secret_scanner_allows_explicit_placeholder() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="example.yaml",
        data=b"api_token: REPLACE_ME",
    )
    assert findings == []


def test_secret_scanner_detects_compound_token_key() -> None:
    scanner = _load_scanner()
    findings = scanner.scan_data(
        scope="test",
        identity="fixture",
        path="environment.yaml",
        data=b"proxmox_api_" + b"token: " + b"actual-credential-value",
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
