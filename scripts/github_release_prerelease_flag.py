#!/usr/bin/env python3
"""Print the GitHub CLI prerelease flag for a valid PEP 440 version."""

from __future__ import annotations

import argparse

from packaging.version import InvalidVersion, Version


def github_release_prerelease_flag(version: str) -> str:
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise ValueError(f"invalid_pep440_version:{version}") from exc
    return "--prerelease" if parsed.is_prerelease else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    args = parser.parse_args(argv)
    try:
        flag = github_release_prerelease_flag(args.version)
    except ValueError as exc:
        parser.error(str(exc))
    print(flag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
