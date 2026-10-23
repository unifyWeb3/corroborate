#!/usr/bin/env bash
# Lint and validate both contracts against the pinned SDK.
#
# Usage:
#   scripts/lint.sh                 # uses genvm-lint from PATH
#   GENVM_LINT_BIN=.rcvenv/bin/genvm-lint scripts/lint.sh
#
# The script refuses to run with any other linter version or SDK version.  The
# header of each contract is `py-genlayer:latest` because the target network
# rejects explicit runner hashes (see state/REVIEW-FIX.md); the version pin here
# and the import test in tests/test_invariants.py are what make the run
# reproducible.
set -euo pipefail

cd "$(dirname "$0")/.."

PINNED_LINTER="0.11.1rc2"
PINNED_SDK="v0.6.0-rc10"
LINT_BIN="${GENVM_LINT_BIN:-genvm-lint}"

if ! command -v "$LINT_BIN" >/dev/null 2>&1; then
  echo "genvm-lint not found: $LINT_BIN" >&2
  echo "install the pinned toolchain with:" >&2
  echo "  uv venv --python 3.12 .rcvenv" >&2
  echo "  uv pip install --python .rcvenv/bin/python -r requirements-dev.txt" >&2
  exit 1
fi

version="$("$LINT_BIN" --version | awk '{print $NF}')"
if [[ "$version" != "$PINNED_LINTER" ]]; then
  echo "refusing to lint with genvm-lint $version; the pin is $PINNED_LINTER" >&2
  exit 1
fi

export GENVM_VERSION="${GENVM_VERSION:-$PINNED_SDK}"
if [[ "$GENVM_VERSION" != "$PINNED_SDK" ]]; then
  echo "refusing to lint with GENVM_VERSION=$GENVM_VERSION; the pin is $PINNED_SDK" >&2
  exit 1
fi

echo "genvm-lint $version, GENVM_VERSION=$GENVM_VERSION"
echo "runners: py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0"
echo "         py-lib-genlayer-std:z7fv8bqpetj3wnjd8sn5s5xfqve3rz8nmtq4npwf2qdvns57zjgg"
echo

for contract in contracts/corroborate.py contracts/example_consumer.py; do
  echo "== $contract"
  "$LINT_BIN" check "$contract"
  echo
done

echo "both contracts passed lint and source validation with the pinned SDK"
