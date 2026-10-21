#!/usr/bin/env python3
"""Pre-push sweep: no keys, no endpoint secrets, no other project's names.

- every tracked file is listed
- every 64 hex candidate in the working tree and in every git ref is compared against the
  real private keys (they are never printed)
- with `--forbidden-names FILE` the tree is searched for the names listed in that file
  (one per line).  The file is deliberately kept outside this repository so that the
  repository itself never mentions another project.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS_FILE = ROOT / "secrets" / "keys.json"

HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT)] + list(args), capture_output=True, text=True
    ).stdout


def real_keys():
    if not KEYS_FILE.exists():
        return set()
    data = json.loads(KEYS_FILE.read_text())
    return {value["private_key"].lower() for value in data.values()}


def main() -> int:
    failures = []

    tracked = git("ls-files").split()
    print(f"tracked files: {len(tracked)}")
    for name in tracked:
        if name.startswith("secrets/") or name.endswith(".key") or name.endswith(".pem"):
            failures.append(f"tracked key-like file: {name}")
        if name == ".env" or name.endswith(".env"):
            failures.append(f"tracked env file: {name}")
    print("tracked key-like files:", [n for n in tracked if "secret" in n or n.endswith(".env")] or "none")

    keys = real_keys()
    print(f"real private keys loaded for comparison: {len(keys)} (values not printed)")

    # working tree
    candidates = set()
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git/" in str(path) or path.is_relative_to(ROOT / ".git"):
            continue
        if any(part in (".rcvenv", ".rcnode", ".research", "node_modules", "__pycache__") for part in path.parts):
            continue
        if path.is_relative_to(ROOT / "secrets"):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in HEX64.findall(text):
            candidates.add(match.lower())
    print(f"64-hex candidates in the working tree: {len(candidates)}")
    leaked = candidates & keys
    if leaked:
        failures.append(f"{len(leaked)} private key(s) found in the working tree")

    # every ref
    refs = git("for-each-ref", "--format=%(refname)").split()
    print(f"refs inspected: {refs}")
    ref_candidates = set()
    for ref in refs:
        blobs = git("ls-tree", "-r", "--name-only", ref).split()
        for name in blobs:
            content = git("show", f"{ref}:{name}")
            for match in HEX64.findall(content):
                ref_candidates.add(match.lower())
    print(f"64-hex candidates in all refs: {len(ref_candidates)}")
    leaked_refs = ref_candidates & keys
    if leaked_refs:
        failures.append(f"{len(leaked_refs)} private key(s) found in a git ref")

    # endpoint secrets
    secret_patterns = [
        (re.compile(r"api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"), "api key assignment"),
        (re.compile(r"(?i)(secret|password|passwd|token)\s*[:=]\s*['\"][^'\"]{8,}"), "secret assignment"),
        (re.compile(r"https?://[^\s'\"]*:[^\s'\"]*@[^\s'\"]*"), "url with credentials"),
    ]
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.is_relative_to(ROOT / ".git"):
            continue
        if any(part in (".rcvenv", ".rcnode", ".research", "node_modules", "__pycache__", "secrets") for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for pattern, label in secret_patterns:
            for match in pattern.finditer(text):
                failures.append(f"{label} in {path.relative_to(ROOT)}")

    # other projects' names, from a list kept outside this repository
    names_file = None
    if len(sys.argv) > 2 and sys.argv[1] == "--forbidden-names":
        names_file = Path(sys.argv[2])
    forbidden = []
    if names_file and names_file.exists():
        forbidden = [
            line.strip().lower()
            for line in names_file.read_text().splitlines()
            if line.strip()
        ]
        print(f"forbidden names loaded: {len(forbidden)} (from {names_file})")
    else:
        print("forbidden names: none loaded (pass --forbidden-names FILE to check)")

    for path in ROOT.rglob("*"):
        if not path.is_file() or path.is_relative_to(ROOT / ".git"):
            continue
        if path == Path(__file__).resolve():
            continue
        if any(part in (".rcvenv", ".rcnode", ".research", "node_modules", "__pycache__", "secrets") for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore").lower()
        except OSError:
            continue
        for name in forbidden:
            if name in text:
                failures.append(f"other project name in {path.relative_to(ROOT)}")

    print()
    if failures:
        print("SWEEP FAILED")
        for item in failures:
            print(" -", item)
        return 1
    print("SWEEP PASSED: no key file tracked, no private key in the tree or in any ref, "
          "no endpoint secret, no other project name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
