#!/usr/bin/env python3
"""Check every GenLayer import and API used by a contract against the pinned SDK.

The allow-list is not hand-written.  The script resolves the SDK pinned by
GENVM_VERSION with the same loader the linter uses, imports that SDK, and then
walks the contract's AST: every ``import genlayer...``, every
``from genlayer... import ...`` and every attribute chain rooted at one of those
aliases is resolved with ``getattr`` against the real package.

Usage:
    GENVM_VERSION=v0.6.0-rc10 python scripts/check_sdk_surface.py contracts/corroborate.py

Exit code 0 means every name exists in the pinned SDK.  Exit code 1 lists the
missing names.  This is deliberately a failure, not a skip: if the SDK cannot be
resolved the script exits 1 with the loader error.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path


def _collect(tree: ast.AST):
    """Return (attribute chains, from-imports, import aliases).

    from-imports maps the local name to (module, original name), because
    ``from genlayer.storage import allow as allow_storage`` must be resolved as
    ``genlayer.storage.allow``.
    """
    import_aliases: dict[str, str] = {}
    from_imports: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "genlayer" or alias.name.startswith("genlayer."):
                    import_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "genlayer" or module.startswith("genlayer."):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    from_imports[alias.asname or alias.name] = (module, alias.name)

    roots = set(import_aliases) | set(from_imports)
    chains: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name) or cur.id not in roots:
            continue
        parts.append(cur.id)
        parts.reverse()
        if len(parts) >= 2:
            chains.add(".".join(parts))
    return sorted(chains), from_imports, import_aliases


def _resolve_chain(chain: str, from_imports, import_aliases):
    head, _, rest = chain.partition(".")
    if head in from_imports:
        module, original = from_imports[head]
        obj = getattr(__import__(module, fromlist=[original]), original)
    else:
        obj = __import__(import_aliases[head], fromlist=["__all__"])
    for part in rest.split("."):
        obj = getattr(obj, part)
    return obj


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_sdk_surface.py <contract.py>", file=sys.stderr)
        return 2
    contract = Path(sys.argv[1])
    if not contract.is_file():
        print(f"contract not found: {contract}", file=sys.stderr)
        return 2

    os.environ.setdefault("GENVM_VERSION", "v0.6.0-rc10")

    try:
        from genvm_linter.validate.sdk_loader import load_sdk

        load_sdk(contract)
    except Exception as exc:  # noqa: BLE001 - report the loader failure verbatim
        print(f"SDK load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    import genlayer  # noqa: F401 - proves the pinned package is importable

    tree = ast.parse(contract.read_text(), filename=str(contract))
    chains, from_imports, import_aliases = _collect(tree)

    failures: list[str] = []
    checked = 0

    for local, (module, original) in sorted(from_imports.items()):
        checked += 1
        try:
            getattr(__import__(module, fromlist=[original]), original)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                f"from {module} import {original} (as {local}): "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            print(f"ok  from {module} import {original}" + (
                f" as {local}" if local != original else ""
            ))

    for chain in chains:
        checked += 1
        try:
            _resolve_chain(chain, from_imports, import_aliases)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{chain}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok  {chain}")

    if checked == 0:
        print("no GenLayer imports or API calls found", file=sys.stderr)
        return 1
    if failures:
        print("", file=sys.stderr)
        for failure in failures:
            print(f"MISSING {failure}", file=sys.stderr)
        return 1
    print(f"\n{checked} names checked against the pinned SDK; all present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
