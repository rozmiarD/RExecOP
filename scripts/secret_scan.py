#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 5 * 1024 * 1024

SECRET_PATTERNS = {
    "private_key": re.compile(
        rb"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
    ),
    "aws_access_key": re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "github_token": re.compile(
        rb"(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})"
    ),
    "gitlab_token": re.compile(rb"glpat-[A-Za-z0-9_-]{20,}"),
    "slack_token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "pypi_token": re.compile(rb"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}"),
    "npm_token": re.compile(rb"npm_[A-Za-z0-9]{36}"),
    "google_api_key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    "jwt": re.compile(
        rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    ),
    "authorization": re.compile(
        rb"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"
    ),
    "credential_url": re.compile(rb"(?i)https?://[^\s/@:]{1,}:[^\s/@]{1,}@"),
}

CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z0-9]+[_-])*(?:password|passwd|pwd|secret|"
    rb"token|api[_-]?key|access[_-]?key|"
    rb"private[_-]?key|client[_-]?secret|authorization)"
    rb"(?![A-Za-z0-9_])\s*[:=]\s*[\"']?([^\s\"'#,}\]]{4,})"
)
PLACEHOLDER = re.compile(
    rb"(?i)^(?:example|sample|dummy|fake|test|fixture|placeholder|redacted|"
    rb"changeme|replace(?:_me)?|plaintext|value|abc|tok|token-value|"
    rb"pbs-secret|from-file|secret-value|secret-token|pbs-token-value|"
    rb"user@pam!token-id=uuid|rexecop|\$[A-Za-z_{].*|\{[A-Za-z_{].*|<.*)"
)
SENSITIVE_FILENAMES = re.compile(
    r"(?i)^(?:\.env(?:\..+)?|credentials(?:\..+)?\.json|secrets?\.(?:ya?ml|json)|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)|known_hosts|.*\.(?:pem|key|p12|pfx|jks|keystore|kdbx))$"
)


@dataclass(frozen=True)
class Finding:
    scope: str
    identity: str
    path: str
    line: int
    rule: str
    fingerprint: str

    def render(self) -> str:
        safe_path = _redact_path(self.path)
        return (
            f"{self.scope}:{self.identity[:12]}:{safe_path}:{self.line}:"
            f"{self.rule}:sha256={self.fingerprint}"
        )


def _fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:12]


def _redact_path(path: str) -> str:
    value = path.encode("utf-8", "replace")
    for pattern in SECRET_PATTERNS.values():
        value = pattern.sub(b"[REDACTED]", value)
    return value.decode("utf-8", "replace")


def _entropy(value: bytes) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def scan_data(*, scope: str, identity: str, path: str, data: bytes) -> list[Finding]:
    if b"\0" in data[:8192]:
        return []
    findings: list[Finding] = []
    seen: set[tuple[int, str, str]] = set()
    for line_number, line in enumerate(data.splitlines(), 1):
        for rule, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group(0)
                key = (line_number, rule, _fingerprint(value))
                if key not in seen:
                    findings.append(
                        Finding(scope, identity, path, line_number, rule, key[2])
                    )
                    seen.add(key)
        for match in CREDENTIAL_ASSIGNMENT.finditer(line):
            value = match.group(1).rstrip(b";)")
            if PLACEHOLDER.match(value):
                continue
            if path.endswith(".py") and (
                value.startswith((b"_", b"self.", b"os.", b"str(", b"dict(", b"getattr("))
                or b"(" in value
            ):
                continue
            rule = (
                "high_entropy_credential"
                if len(value) >= 20 and _entropy(value) >= 4.0
                else "credential_assignment"
            )
            key = (line_number, rule, _fingerprint(value))
            if key not in seen:
                findings.append(Finding(scope, identity, path, line_number, rule, key[2]))
                seen.add(key)
    return findings


def scan_path(*, scope: str, identity: str, path: str) -> list[Finding]:
    name = Path(path).name
    if not SENSITIVE_FILENAMES.fullmatch(name):
        return []
    if name.endswith(".example") or ".example." in name:
        return []
    return [
        Finding(
            scope=scope,
            identity=identity,
            path=path,
            line=0,
            rule="sensitive_filename",
            fingerprint=_fingerprint(path.encode()),
        )
    ]


def _git(*args: str, input_data: bytes | None = None) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT, input=input_data)


def scan_worktree() -> list[Finding]:
    findings: list[Finding] = []
    for raw_path in _git("ls-files", "-z").split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", "surrogateescape")
        path = ROOT / relative
        if path.is_file() and path.stat().st_size <= MAX_BLOB_BYTES:
            findings.extend(scan_path(scope="worktree", identity="HEAD", path=relative))
            findings.extend(
                scan_data(
                    scope="worktree",
                    identity="HEAD",
                    path=relative,
                    data=path.read_bytes(),
                )
            )
    return findings


def scan_history() -> list[Finding]:
    objects: dict[str, str] = {}
    for line in _git("rev-list", "--objects", "--all", "--reflog").splitlines():
        raw_oid, _, raw_path = line.partition(b" ")
        objects.setdefault(
            raw_oid.decode(),
            raw_path.decode("utf-8", "replace") or "(unknown)",
        )
    checks = _git(
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_data=("\n".join(objects) + "\n").encode(),
    ).decode().splitlines()
    findings: list[Finding] = []
    for check in checks:
        oid, kind, raw_size = check.split()
        if kind != "blob" or int(raw_size) > MAX_BLOB_BYTES:
            continue
        findings.extend(
            scan_path(scope="history", identity=oid, path=objects[oid])
        )
        findings.extend(
            scan_data(
                scope="history",
                identity=oid,
                path=objects[oid],
                data=_git("cat-file", "blob", oid),
            )
        )
    return findings


def scan_commit_messages() -> list[Finding]:
    findings: list[Finding] = []
    for raw_oid in _git("rev-list", "--all", "--reflog").splitlines():
        oid = raw_oid.decode()
        commit = _git("cat-file", "commit", oid)
        _, _, message = commit.partition(b"\n\n")
        findings.extend(
            scan_data(
                scope="commit",
                identity=oid,
                path="(commit-message)",
                data=message,
            )
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan RExecOp without printing secret values.")
    parser.add_argument(
        "--history",
        action="store_true",
        help="scan every blob reachable from refs and reflogs",
    )
    args = parser.parse_args()
    findings = scan_worktree()
    if args.history:
        findings.extend(scan_history())
        findings.extend(scan_commit_messages())
    unique = sorted(set(findings), key=lambda item: item.render())
    if unique:
        for finding in unique:
            print(f"possible_secret:{finding.render()}")
        return 1
    print("secret_scan_ok:no_candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
