#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PATTERN='(api_key|apikey|password|passwd|private_key|token)\s*[:=]\s*["'"'"'][^"'"'"']{6,}'

found=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  if [[ "$line" == *.example.yaml:* ]]; then
    continue
  fi
  if [[ "$line" == *secret_ref* ]]; then
    continue
  fi
  if [[ "$line" == *env_token* ]]; then
    continue
  fi
  if [[ "$line" == *REDACTED* ]]; then
    continue
  fi
  echo "possible inline secret: $line"
  found=1
done < <(rg -n "$PATTERN" src/rexecop examples docs 2>/dev/null || true)

if [[ "$found" -ne 0 ]]; then
  echo "secret scan failed — use secret_ref / REXECOP_SECRETS_FILE instead"
  exit 1
fi

echo "secret scan passed"
